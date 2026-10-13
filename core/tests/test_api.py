import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from causaldeploy.api.main import create_app
from causaldeploy.api.routes import create_api_key
from causaldeploy.db.models import IncidentRow, JobRecord, RawEvent
from causaldeploy.db.session import session_scope
from causaldeploy.graph.builder import GraphBuilder

FX = Path(__file__).parent / "fixtures" / "events"
SECRET = b"whsec-test"


@pytest_asyncio.fixture
async def client(db: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    # no worker in these tests: capture the arq enqueue call instead of hitting Redis
    pool = AsyncMock()
    monkeypatch.setattr("causaldeploy.jobs.queue.get_pool", AsyncMock(return_value=pool))
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


def login_as(client: httpx.AsyncClient, login: str, repos: dict[str, str]) -> dict[str, str]:
    """Attach a session + CSRF cookie; returns the CSRF header for state-changing calls."""
    from causaldeploy.api.auth import User, make_session

    client.cookies.set("cd_session", make_session(User(login=login, repos=repos)))  # type: ignore[arg-type]
    client.cookies.set("cd_csrf", "csrf-token")
    return {"x-csrf-token": "csrf-token"}


def sign(body: bytes, secret: bytes = SECRET) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


async def jobs_count() -> int:
    async with session_scope() as s:
        return (await s.execute(select(func.count()).select_from(JobRecord))).scalar_one()


async def raw_count() -> int:
    async with session_scope() as s:
        return (await s.execute(select(func.count()).select_from(RawEvent))).scalar_one()


async def test_health_and_ready(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json()["status"] == "ok"
    r = await client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"postgres": True, "redis": True, "falkordb": True}


async def test_readiness_fails_when_dependency_down(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom() -> None:
        raise ConnectionError

    monkeypatch.setattr("causaldeploy.api.main._redis", boom)
    r = await client.get("/readyz")
    assert r.status_code == 503 and r.json()["redis"] is False and r.json()["postgres"] is True


async def test_error_envelope_and_request_id(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/incidents", headers={"x-request-id": "rid-1"})
    assert r.status_code == 401 and r.headers["x-request-id"] == "rid-1"
    assert r.json()["error"] == {
        "code": "unauthorized",
        "message": "authentication required",
        "request_id": "rid-1",
    }
    login_as(client, "al", {"a/b": "viewer"})
    r = await client.get("/api/v1/jobs/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = await client.get("/api/v1/incidents?limit=0")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


async def test_event_ingest_requires_key_and_dedupes(client: httpx.AsyncClient) -> None:
    body = [json.loads((FX / "commit.json").read_text())]
    assert (await client.post("/api/v1/events", json=body)).status_code == 401
    assert (
        await client.post("/api/v1/events", json=body, headers={"x-api-key": "bad"})
    ).status_code == 401
    key = await create_api_key("acme/shoplite")
    h = {"x-api-key": key}
    r = await client.post("/api/v1/events", json=body, headers=h)
    assert r.status_code == 202 and r.json()["accepted"] == 1
    await client.post("/api/v1/events", json=body, headers=h)  # duplicate -> same effect
    assert await jobs_count() == 1
    bad = [{"type": "commit", "repo": "acme/shoplite"}]
    r = await client.post("/api/v1/events", json=bad, headers=h)
    assert r.status_code == 422
    other = [{**body[0], "repo": "other/repo"}]
    assert (await client.post("/api/v1/events", json=other, headers=h)).status_code == 403


async def test_webhook_signature_dedupe(client: httpx.AsyncClient) -> None:
    payload = {
        "action": "created",
        "repository": {"full_name": "acme/shoplite"},
        "deployment": {
            "id": 842,
            "sha": "8f31c2a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7",
            "environment": "prod",
            "created_at": "2026-10-09T10:10:00Z",
        },
        "deployment_status": {"state": "success", "created_at": "2026-10-09T10:12:00Z"},
    }
    body = json.dumps(payload).encode()
    base = {"x-github-event": "deployment_status", "x-github-delivery": "d-1"}

    r = await client.post(
        "/webhooks/github",
        content=body,
        headers={**base, "x-hub-signature-256": sign(body, b"wrong")},
    )
    assert r.status_code == 401
    assert await raw_count() == 0 and await jobs_count() == 0  # bad signature: nothing stored

    h = {**base, "x-hub-signature-256": sign(body)}
    assert (await client.post("/webhooks/github", content=body, headers=h)).json()[
        "status"
    ] == "accepted"
    assert (await client.post("/webhooks/github", content=body, headers=h)).json() == {
        "status": "duplicate"
    }
    assert await raw_count() == 1 and await jobs_count() == 1


async def test_evidence_graph_endpoint(client: httpx.AsyncClient, builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    builder.candidates(sc.incident_id, [(sc.culprit_commit, 0.9)])
    now = datetime.now(UTC)
    async with session_scope() as s:
        from causaldeploy.db.models import Repository

        s.add(Repository(full_name=builder.store.repo))
        await s.flush()
        s.add(
            IncidentRow(
                id="inc-1",
                repo=builder.store.repo,
                service="payments",
                env="prod",
                status="open",
                onset_ts=now,
                detected_at=now,
            )
        )
    login_as(client, "al", {builder.store.repo: "viewer"})
    r = await client.get("/api/v1/incidents/inc-1/evidence-graph")
    assert r.status_code == 200
    ids = {n["id"] for n in r.json()["nodes"]}
    assert {"Incident:inc-1", "Commit:c0ffee01"} <= ids
    assert (await client.get("/api/v1/incidents/ghost/evidence-graph")).status_code == 404
    page = (await client.get("/api/v1/incidents")).json()
    assert page["total"] == 1 and page["items"][0]["id"] == "inc-1"


async def seed_incident(repo: str, inc_id: str = "inc-1") -> None:
    now = datetime.now(UTC)
    async with session_scope() as s:
        from causaldeploy.db.models import Repository

        if (await s.get(Repository, repo)) is None:
            s.add(Repository(full_name=repo))
            await s.flush()
        s.add(
            IncidentRow(
                id=inc_id,
                repo=repo,
                service="payments",
                env="prod",
                status="open",
                onset_ts=now,
                detected_at=now,
            )
        )


async def test_cross_repo_requests_are_forbidden(
    client: httpx.AsyncClient, builder: GraphBuilder
) -> None:
    sc = builder.s1_payment_validation()
    builder.candidates(sc.incident_id, [(sc.culprit_commit, 0.9)])
    await seed_incident(builder.store.repo)
    assert (await client.get("/api/v1/incidents")).status_code == 401  # no session

    login_as(client, "mallory", {"other/repo": "approver"})
    page = (await client.get("/api/v1/incidents")).json()
    assert page["total"] == 0
    assert (await client.get("/api/v1/incidents/inc-1/evidence-graph")).status_code == 404
    assert (await client.get("/api/v1/incidents/inc-1/llm-cost")).status_code == 404

    login_as(client, "alice", {builder.store.repo: "viewer"})
    assert (await client.get("/api/v1/incidents")).json()["total"] == 1


async def test_only_approvers_can_approve_a_remediation(client: httpx.AsyncClient) -> None:
    from causaldeploy.db.models import AuditLog, RemediationRun

    await seed_incident("acme/shop")
    async with session_scope() as s:
        s.add(RemediationRun(id="rem-1", incident_id="inc-1", kind="revert", status="verified"))

    h = login_as(client, "viewer-vic", {"acme/shop": "viewer"})
    r = await client.post("/api/v1/remediations/rem-1/approve", headers=h)
    assert r.status_code == 403 and "approver" in r.json()["error"]["message"]

    # CSRF: an approver without the token header is rejected
    login_as(client, "approver-ann", {"acme/shop": "approver"})
    assert (await client.post("/api/v1/remediations/rem-1/approve")).status_code == 403

    h = login_as(client, "approver-ann", {"acme/shop": "approver"})
    r = await client.post("/api/v1/remediations/rem-1/approve", headers=h)
    assert r.status_code == 200 and r.json()["approved_by"] == "approver-ann"
    assert (await client.post("/api/v1/remediations/rem-1/approve", headers=h)).status_code == 409
    async with session_scope() as s:
        log = (await s.execute(select(AuditLog))).scalars().all()
    assert [(a.actor, a.action) for a in log] == [("approver-ann", "remediation.approve")]


async def test_api_key_rotation(client: httpx.AsyncClient) -> None:
    h = login_as(client, "ann", {"acme/shop": "approver"})
    old = (await client.post("/api/v1/repos/acme/shop/api-keys?label=ci", headers=h)).json()
    commit = json.loads((FX / "commit.json").read_text()) | {"repo": "acme/shop"}
    ok = await client.post("/api/v1/events", json=[commit], headers={"x-api-key": old["key"]})
    assert ok.status_code == 202

    new = (await client.post("/api/v1/repos/acme/shop/api-keys?label=rotated", headers=h)).json()
    assert (
        await client.delete(f"/api/v1/repos/acme/shop/api-keys/{old['id']}", headers=h)
    ).status_code == 200
    revoked = await client.post("/api/v1/events", json=[commit], headers={"x-api-key": old["key"]})
    assert revoked.status_code == 401
    assert (
        await client.post("/api/v1/events", json=[commit], headers={"x-api-key": new["key"]})
    ).status_code == 202
    listed = (await client.get("/api/v1/repos/acme/shop/api-keys")).json()
    assert [(k["id"] == old["id"], k["revoked_at"] is not None) for k in listed] == [
        (True, True),
        (False, False),
    ]
    assert "key" not in listed[0] and "key_hash" not in listed[0]

    viewer = login_as(client, "vic", {"acme/shop": "viewer"})
    assert (
        await client.post("/api/v1/repos/acme/shop/api-keys", headers=viewer)
    ).status_code == 403


async def test_llm_cost_per_investigation(client: httpx.AsyncClient) -> None:
    from causaldeploy.db.models import LlmCall

    await seed_incident("acme/shop")
    async with session_scope() as s:
        s.add_all(
            [
                LlmCall(
                    incident_id="inc-1",
                    model="m",
                    prompt_version="v1",
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=0.01,
                    latency_ms=800,
                ),
                LlmCall(
                    incident_id="inc-1",
                    model="m",
                    prompt_version="v1",
                    input_tokens=200,
                    output_tokens=70,
                    cost_usd=0.02,
                    latency_ms=1200,
                ),
            ]
        )
    login_as(client, "ann", {"acme/shop": "viewer"})
    r = (await client.get("/api/v1/incidents/inc-1/llm-cost")).json()
    assert (r["calls"], r["input_tokens"], r["output_tokens"]) == (2, 300, 120)
    assert round(r["cost_usd"], 4) == 0.03


async def test_oauth_login_flow_sets_session_and_roles(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import respx

    monkeypatch.setenv("CD_GITHUB_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("CD_GITHUB_OAUTH_CLIENT_SECRET", "csecret")
    from causaldeploy.config import get_settings

    get_settings.cache_clear()
    try:
        r = await client.get("/auth/login", follow_redirects=False)
        assert r.status_code == 302 and "client_id=cid" in r.headers["location"]
        state = client.cookies.get("cd_oauth_state")
        assert state

        wrong = await client.get("/auth/callback?code=c&state=wrong", follow_redirects=False)
        assert wrong.status_code == 302 and wrong.headers["location"].endswith("/login?error=expired")
        # a failed attempt consumed the OAuth state: sign in again
        await client.get("/auth/login", follow_redirects=False)
        state = client.cookies.get("cd_oauth_state")
        assert state

        with respx.mock(assert_all_called=False) as m:
            m.post("https://github.com/login/oauth/access_token").mock(
                return_value=httpx.Response(200, json={"access_token": "gho_user"})
            )
            m.get("https://api.github.com/user").mock(
                return_value=httpx.Response(200, json={"login": "yash"})
            )
            m.get("https://api.github.com/user/installations").mock(
                return_value=httpx.Response(200, json={"installations": [{"id": 9}]})
            )
            m.get("https://api.github.com/user/installations/9/repositories").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "repositories": [
                            {"full_name": "acme/shop", "permissions": {"push": True}},
                            {"full_name": "acme/docs", "permissions": {"pull": True}},
                        ]
                    },
                )
            )
            r = await client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)
        assert r.status_code == 302
        me = (await client.get("/auth/me")).json()
        assert me == {"login": "yash", "repos": {"acme/shop": "approver", "acme/docs": "viewer"}}

        h = {"x-csrf-token": client.cookies.get("cd_csrf")}
        assert (await client.post("/auth/logout", headers=h)).status_code == 200
        assert (await client.get("/auth/me")).status_code == 401
    finally:
        get_settings.cache_clear()


async def test_tampered_or_expired_sessions_are_rejected(client: httpx.AsyncClient) -> None:
    from causaldeploy.api.auth import User, make_session

    expired = make_session(User(login="a", repos={"x/y": "approver"}), now=1)
    client.cookies.set("cd_session", expired)
    assert (await client.get("/auth/me")).status_code == 401
    good = make_session(User(login="a", repos={"x/y": "viewer"}))
    client.cookies.set("cd_session", good[:-4] + "AAAA")
    assert (await client.get("/auth/me")).status_code == 401


def test_prod_refuses_to_start_without_real_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from causaldeploy.config import Settings

    with pytest.raises(ValidationError, match="CD_SESSION_SECRET.*CD_GITHUB_OAUTH_CLIENT_ID"):
        Settings(_env_file=None, env="prod")  # type: ignore[arg-type,call-arg]


async def test_metrics_endpoint(client: httpx.AsyncClient) -> None:
    key = await create_api_key("acme/shop")
    commit = json.loads((FX / "commit.json").read_text()) | {"repo": "acme/shop"}
    await client.post("/api/v1/events", json=[commit], headers={"x-api-key": key})
    text = (await client.get("/metrics")).text
    assert 'cd_events_ingested_total{type="commit"}' in text
    assert "cd_queue_depth" in text and "cd_http_request_seconds_bucket" in text


async def test_trace_context_flows_from_enqueue_to_job(
    monkeypatch: pytest.MonkeyPatch, db: None
) -> None:
    """An investigation is one trace: the span active at enqueue parents the job span."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from causaldeploy.config import get_settings
    from causaldeploy.jobs import handlers
    from causaldeploy.jobs.queue import IngestEventPayload, enqueue
    from causaldeploy.jobs.worker import run_job
    from causaldeploy.observability import init_tracing, span

    exporter = InMemorySpanExporter()
    init_tracing(get_settings(), exporter)
    monkeypatch.setitem(handlers.HANDLERS, "ingest_event", AsyncMock())
    event = json.loads((FX / "commit.json").read_text())
    with span("api.request") as root:
        job_id, _ = await enqueue(
            "ingest_event", IngestEventPayload(event=event), "trace-1", AsyncMock()
        )
        trace_id = root.get_span_context().trace_id
    assert await run_job({}, job_id) == "ok"
    spans = {s.name: s for s in exporter.get_finished_spans()}
    job = spans["job.ingest_event"]
    assert job.context.trace_id == trace_id
    assert job.parent is not None and job.parent.span_id == spans["api.request"].context.span_id


async def test_webhook_acknowledges_within_a_second(client: httpx.AsyncClient) -> None:
    import time

    payload = {
        "repository": {"full_name": "acme/shoplite"},
        "after": "abc",
        "installation": {"id": 1},
    }
    body = json.dumps(payload).encode()
    h = {
        "x-github-event": "push",
        "x-github-delivery": "speed-1",
        "x-hub-signature-256": sign(body),
    }
    started = time.perf_counter()
    r = await client.post("/webhooks/github", content=body, headers=h)
    assert r.status_code == 202 and time.perf_counter() - started < 1.0


async def test_replay_reapplies_stored_deliveries_without_double_effects(
    client: httpx.AsyncClient,
) -> None:
    from causaldeploy.cli import replay

    payload = {
        "repository": {"full_name": "acme/shoplite"},
        "deployment": {
            "id": 7,
            "sha": "8f31c2a1b2c3d4e5",
            "environment": "prod",
            "created_at": "2026-10-09T10:10:00Z",
        },
        "deployment_status": {"state": "success", "created_at": "2026-10-09T10:12:00Z"},
    }
    body = json.dumps(payload).encode()
    h = {
        "x-github-event": "deployment_status",
        "x-github-delivery": "replay-1",
        "x-hub-signature-256": sign(body),
    }
    assert (await client.post("/webhooks/github", content=body, headers=h)).status_code == 202
    assert await jobs_count() == 1
    assert (
        await replay(None) == 1
    )  # re-enqueued under a replay key: a new job, same idempotent effect
    assert await jobs_count() == 2
    assert await replay(None) == 1
    assert await jobs_count() == 2  # replaying again does not multiply jobs


async def test_installation_event_records_repos_and_requests_initial_backfill(
    client: httpx.AsyncClient,
) -> None:
    from causaldeploy.db.models import Installation, Repository

    payload = {
        "action": "created",
        "installation": {"id": 4242, "account": {"login": "yashchandnani07"}},
        "repositories": [{"full_name": "yashchandnani07/shoplite"}],
    }
    body = json.dumps(payload).encode()
    h = {
        "x-github-event": "installation",
        "x-github-delivery": "inst-1",
        "x-hub-signature-256": sign(body),
    }
    r = await client.post("/webhooks/github", content=body, headers=h)
    assert r.status_code == 202 and len(r.json()["jobs"]) == 1
    async with session_scope() as s:
        inst = (await s.execute(select(Installation))).scalar_one()
        repo = (await s.execute(select(Repository))).scalar_one()
        job = (await s.execute(select(JobRecord))).scalar_one()
    assert (inst.id, inst.account) == (4242, "yashchandnani07")
    assert (repo.full_name, repo.installation_id) == ("yashchandnani07/shoplite", 4242)
    assert job.kind == "backfill_repo" and job.payload["installation_id"] == 4242

    # removal events import nothing
    gone = json.dumps({**payload, "action": "deleted"}).encode()
    h2 = {**h, "x-github-delivery": "inst-2", "x-hub-signature-256": sign(gone)}
    assert (await client.post("/webhooks/github", content=gone, headers=h2)).json()["jobs"] == []


async def test_repositories_outside_the_allowlist_are_never_acted_on(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from causaldeploy.config import get_settings
    from causaldeploy.db.models import Repository

    monkeypatch.setenv("CD_ALLOWED_REPOS", "acme/shoplite")
    get_settings.cache_clear()
    try:
        # installation lists two repos: only the allowed one is recorded and imported
        install = {
            "action": "created",
            "installation": {"id": 9, "account": {"login": "acme"}},
            "repositories": [{"full_name": "acme/shoplite"}, {"full_name": "acme/private-notes"}],
        }
        body = json.dumps(install).encode()
        h = {
            "x-github-event": "installation",
            "x-github-delivery": "al-1",
            "x-hub-signature-256": sign(body),
        }
        r = await client.post("/webhooks/github", content=body, headers=h)
        assert len(r.json()["jobs"]) == 1
        async with session_scope() as s:
            names = [x.full_name for x in (await s.execute(select(Repository))).scalars()]
        assert names == ["acme/shoplite"]

        # a push from a non-allowed repo is recorded raw and ignored
        push = json.dumps(
            {
                "repository": {"full_name": "acme/private-notes"},
                "after": "abc",
                "installation": {"id": 9},
            }
        ).encode()
        h2 = {
            "x-github-event": "push",
            "x-github-delivery": "al-2",
            "x-hub-signature-256": sign(push),
        }
        assert (await client.post("/webhooks/github", content=push, headers=h2)).json()[
            "jobs"
        ] == []
        assert await raw_count() == 2 and await jobs_count() == 1
    finally:
        get_settings.cache_clear()


async def test_jobs_queued_for_other_repositories_do_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from causaldeploy.config import get_settings
    from causaldeploy.jobs import handlers

    monkeypatch.setenv("CD_ALLOWED_REPOS", "acme/shoplite")
    get_settings.cache_clear()
    called = AsyncMock()
    monkeypatch.setattr("causaldeploy.github.backfill_job.run_backfill", called)
    try:
        await handlers.backfill_repo({"repo": "acme/private-notes", "installation_id": 1})
        called.assert_not_awaited()
        await handlers.backfill_repo({"repo": "acme/shoplite", "installation_id": 1})
        called.assert_awaited_once()
    finally:
        get_settings.cache_clear()


async def _add_repo(full_name: str, installation_id: int | None) -> None:
    from sqlalchemy.dialects.postgresql import insert

    from causaldeploy.db.models import Installation, Repository

    async with session_scope() as s:
        if installation_id is not None:
            await s.execute(
                insert(Installation)
                .values(id=installation_id, account="acme")
                .on_conflict_do_nothing(index_elements=["id"])
            )
        await s.execute(
            insert(Repository)
            .values(full_name=full_name, installation_id=installation_id)
            .on_conflict_do_nothing(index_elements=["full_name"])
        )


async def test_backfill_status_needs_access_and_never_leaks_repos(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get("/api/v1/repos/a/b/backfill")).status_code == 401
    login_as(client, "al", {"a/b": "viewer"})
    r = await client.get("/api/v1/repos/secret/private/backfill")
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


async def test_backfill_status_shows_job_and_reimport_enqueues(
    client: httpx.AsyncClient,
) -> None:
    from causaldeploy.db.models import JobRecord

    await _add_repo("acme/shop", installation_id=9)
    login_as(client, "al", {"acme/shop": "viewer"})
    body = (await client.get("/api/v1/repos/acme/shop/backfill")).json()
    assert body == {
        "repo": "acme/shop",
        "installed": True,
        "backfilled_at": None,
        "job": None,
    }

    h = {"x-csrf-token": "csrf-token"}
    # viewers never see the trigger, and the trigger needs the CSRF header
    assert (await client.post("/api/v1/repos/acme/shop/backfill", headers=h)).status_code == 403
    login_as(client, "al", {"acme/shop": "approver"})
    assert (await client.post("/api/v1/repos/acme/shop/backfill")).status_code == 403

    r = await client.post("/api/v1/repos/acme/shop/backfill", headers=h)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    async with session_scope() as s:
        job = (
            await s.execute(select(JobRecord).where(JobRecord.kind == "backfill_repo"))
        ).scalar_one()
    assert job.payload["repo"] == "acme/shop" and job.payload["installation_id"] == 9
    assert job.idempotency_key.startswith("reimport:acme/shop:")
    assert (await client.get("/api/v1/jobs/" + job.id)).status_code == 200

    # a second import while the first is queued is refused
    again = await client.post("/api/v1/repos/acme/shop/backfill", headers=h)
    assert again.status_code == 409 and "already" in again.json()["error"]["message"]

    body = (await client.get("/api/v1/repos/acme/shop/backfill")).json()
    assert body["job"] is not None and body["job"]["status"] == "queued"


async def test_reimport_requires_the_installed_app(client: httpx.AsyncClient) -> None:
    await _add_repo("acme/orphan", installation_id=None)
    login_as(client, "al", {"acme/orphan": "approver"})
    r = await client.post(
        "/api/v1/repos/acme/orphan/backfill", headers={"x-csrf-token": "csrf-token"}
    )
    assert r.status_code == 409 and "not installed" in r.json()["error"]["message"]


async def test_login_passes_next_and_origin_through_to_the_callback(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import respx

    monkeypatch.setenv("CD_GITHUB_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("CD_GITHUB_OAUTH_CLIENT_SECRET", "csecret")
    from causaldeploy.config import get_settings

    get_settings.cache_clear()
    try:
        # unsafe `next` and origins fall back to sane defaults
        await client.get("/auth/login?next=https://evil.example", follow_redirects=False)
        assert client.cookies.get("cd_oauth_next", "").strip('"') == "/"
        await client.get("/auth/login?origin=javascript:alert(1)", follow_redirects=False)
        assert client.cookies.get("cd_oauth_origin", "").strip('"') == "http://localhost:8000"

        r = await client.get(
            "/auth/login?next=/repositories/acme/shop&origin=http://localhost:3000",
            follow_redirects=False,
        )
        assert "redirect_uri=http://localhost:3000/auth/callback" in r.headers["location"]
        assert client.cookies.get("cd_oauth_next", "").strip('"') == "/repositories/acme/shop"

        with respx.mock(assert_all_called=False) as m:
            m.post("https://github.com/login/oauth/access_token").mock(
                return_value=httpx.Response(200, json={"access_token": "gho_user"})
            )
            m.get("https://api.github.com/user").mock(
                return_value=httpx.Response(200, json={"login": "yash"})
            )
            m.get("https://api.github.com/user/installations").mock(
                return_value=httpx.Response(200, json={"installations": [{"id": 9}]})
            )
            m.get("https://api.github.com/user/installations/9/repositories").mock(
                return_value=httpx.Response(
                    200,
                    json={"repositories": [{"full_name": "acme/shop", "permissions": {"push": True}}]},
                )
            )
            state = client.cookies.get("cd_oauth_state")
            r = await client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "http://localhost:3000/repositories/acme/shop"
        # the one-time OAuth cookies are consumed
        assert not client.cookies.get("cd_oauth_state")
    finally:
        get_settings.cache_clear()


async def test_callback_without_installations_lands_on_the_no_repos_login(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import respx

    monkeypatch.setenv("CD_GITHUB_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("CD_GITHUB_OAUTH_CLIENT_SECRET", "csecret")
    from causaldeploy.config import get_settings

    get_settings.cache_clear()
    try:
        await client.get("/auth/login", follow_redirects=False)
        with respx.mock(assert_all_called=False) as m:
            m.post("https://github.com/login/oauth/access_token").mock(
                return_value=httpx.Response(200, json={"access_token": "gho_user"})
            )
            m.get("https://api.github.com/user").mock(
                return_value=httpx.Response(200, json={"login": "yash"})
            )
            m.get("https://api.github.com/user/installations").mock(
                return_value=httpx.Response(200, json={"installations": []})
            )
            state = client.cookies.get("cd_oauth_state")
            r = await client.get(f"/auth/callback?code=c&state={state}", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].endswith("/login?error=no_repos")
        # the user is signed in, just with nothing to see yet
        assert (await client.get("/auth/me")).json() == {"login": "yash", "repos": {}}
    finally:
        get_settings.cache_clear()


async def test_callback_denied_or_rejected_redirects_to_login(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/auth/callback?error=access_denied&state=x", follow_redirects=False)
    assert r.headers["location"].endswith("/login?error=denied")
    r = await client.get("/auth/callback?state=x", follow_redirects=False)
    assert r.headers["location"].endswith("/login?error=invalid")


async def test_install_url(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from causaldeploy.config import get_settings

    assert (await client.get("/auth/install-url")).json() == {"url": None}
    monkeypatch.setenv("CD_GITHUB_APP_SLUG", "causaldeploy-shoplite-demo")
    get_settings.cache_clear()
    try:
        body = (await client.get("/auth/install-url")).json()
        assert body["url"] == (
            "https://github.com/apps/causaldeploy-shoplite-demo/installations/new"
        )
    finally:
        get_settings.cache_clear()
