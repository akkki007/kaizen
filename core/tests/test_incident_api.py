"""#88 API: ingest limits (413/429/per-item rejection) and incident actions + history."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from causaldeploy.api.deps import RateLimiter
from causaldeploy.api.routes import create_api_key
from causaldeploy.config import get_settings
from causaldeploy.db.models import AuditLog, IncidentRow, Repository
from causaldeploy.db.session import session_scope
from causaldeploy.incidents import lifecycle
from tests.test_api import client, login_as  # noqa: F401  (fixture re-export)

REPO = "acme/shop"


def telemetry(i: int, repo: str = REPO) -> dict[str, object]:
    return {
        "type": "telemetry",
        "repo": repo,
        "service": "payments",
        "ts": f"2026-10-14T12:00:{i % 60:02d}Z",
        "kind": "request",
        "endpoint": "POST /payments",
        "status_code": 200,
        "latency_ms": 12.5 + i,
        "sha": "abc1234",
    }


async def key_headers() -> dict[str, str]:
    return {"x-api-key": await create_api_key(REPO)}


# -- ingest -------------------------------------------------------------------------------


async def test_mixed_batch_accepts_valid_items_and_reports_rejected(
    client: httpx.AsyncClient,  # noqa: F811
) -> None:
    h = await key_headers()
    body = [telemetry(1), {"type": "telemetry", "repo": REPO}, telemetry(2), "not-an-object"]
    r = await client.post("/api/v1/events", json=body, headers=h)
    assert r.status_code == 202
    out = r.json()
    assert out["accepted"] == 2  # plain request telemetry: stored inline, no graph job needed
    assert [x["index"] for x in out["rejected"]] == [1, 3]
    assert "service" in out["rejected"][0]["error"]
    # nothing valid at all: 422 with the per-item reasons
    r = await client.post("/api/v1/events", json=[{"type": "nope"}], headers=h)
    assert r.status_code == 422 and r.json()["error"]["details"][0]["index"] == 0


async def test_oversized_batches_and_bodies_are_413(
    client: httpx.AsyncClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await key_headers()
    s = get_settings()
    monkeypatch.setattr(s, "ingest_max_batch", 3)
    r = await client.post("/api/v1/events", json=[telemetry(i) for i in range(4)], headers=h)
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"

    monkeypatch.setattr(s, "ingest_max_bytes", 2048)
    big = json.dumps([telemetry(i) for i in range(3)] + [{"pad": "x" * 4000}]).encode()
    r = await client.post(
        "/api/v1/events", content=big, headers={**h, "content-type": "application/json"}
    )
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
    assert r.headers.get("x-request-id")

    async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: the bytes are counted
        for i in range(0, len(big), 512):
            yield big[i : i + 512]

    r = await client.post(
        "/api/v1/events", content=chunks(), headers={**h, "content-type": "application/json"}
    )
    assert r.status_code == 413
    ok = await client.post("/api/v1/events", json=[telemetry(1)], headers=h)
    assert ok.status_code == 202  # small bodies pass through the limiter untouched


async def test_rate_limit_counts_events_and_sets_retry_after(
    client: httpx.AsyncClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = await key_headers()
    monkeypatch.setattr(get_settings(), "ingest_rate_limit_per_min", 5)
    first = await client.post("/api/v1/events", json=[telemetry(i) for i in range(3)], headers=h)
    assert first.status_code == 202
    r = await client.post("/api/v1/events", json=[telemetry(i) for i in range(3, 6)], headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(r.headers["Retry-After"]) <= 60
    two = await client.post("/api/v1/events", json=[telemetry(7), telemetry(8)], headers=h)
    assert two.status_code == 202  # 3 + 2 == 5 events: exactly the limit
    r = await client.post("/api/v1/events", json=[telemetry(i) for i in range(6)], headers=h)
    assert r.status_code == 413  # can never fit in one minute


def test_rate_limiter_retry_after_is_when_enough_events_expire() -> None:
    rl = RateLimiter()
    assert rl.take("k", 10, 6, now=0.0) == 0.0
    assert rl.take("k", 10, 4, now=20.0) == 0.0
    assert rl.take("k", 10, 3, now=30.0) == pytest.approx(30.0)  # first 6 expire at t=60
    assert rl.take("k", 10, 3, now=60.0) == 0.0


# -- incident actions + history -----------------------------------------------------------


async def seed(status: str = "open", inc_id: str = "inc-1") -> None:
    now = datetime.now(UTC)
    async with session_scope() as s:
        if (await s.get(Repository, REPO)) is None:
            s.add(Repository(full_name=REPO))
            await s.flush()
        s.add(
            IncidentRow(
                id=inc_id,
                repo=REPO,
                service="payments",
                env="prod",
                endpoint="POST /payments",
                status=status,
                onset_ts=now,
                detected_at=now,
                severity="sev2",
            )
        )
        await s.flush()
        await lifecycle.record_opened(s, inc_id)


async def act(
    client: httpx.AsyncClient,  # noqa: F811
    h: dict[str, str],
    action: str,
    reason: str | None = None,
    inc: str = "inc-1",
) -> httpx.Response:
    body = {"action": action} | ({"reason": reason} if reason else {})
    return await client.post(f"/api/v1/incidents/{inc}/actions", json=body, headers=h)


async def test_acknowledge_close_reopen_and_history(client: httpx.AsyncClient) -> None:  # noqa: F811
    await seed()
    viewer = login_as(client, "vic", {REPO: "viewer"})
    r = await act(client, viewer, "acknowledge")
    assert r.status_code == 200
    assert r.json() == {
        "id": "inc-1",
        "status": "open",
        "severity": "sev2",
        "acknowledged_by": "vic",
    }
    assert (await act(client, viewer, "close")).status_code == 403  # approver only

    approver = login_as(client, "ann", {REPO: "approver"})
    assert (await act(client, {}, "close")).status_code == 403  # CSRF header missing
    r = await act(client, approver, "close", "false alarm")
    assert r.status_code == 200 and r.json()["status"] == "closed"
    r = await act(client, approver, "close")
    assert r.status_code == 409 and "already closed" in r.json()["error"]["message"]
    assert (await act(client, approver, "acknowledge")).status_code == 409
    r = await act(client, approver, "reopen", "still failing")
    assert r.json()["status"] == "open" and r.json()["acknowledged_by"] is None

    hist = (await client.get("/api/v1/incidents/inc-1/history")).json()
    assert [(x["from_status"], x["to_status"], x["actor"], x["reason"]) for x in hist] == [
        (None, "open", "system", "detected"),
        ("open", "closed", "ann", "false alarm"),
        ("closed", "open", "ann", "still failing"),
    ]
    stamps = [x["at"] for x in hist]
    assert stamps == sorted(stamps)
    async with session_scope() as s:
        log = (await s.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    assert [(a.actor, a.action) for a in log] == [
        ("vic", "incident.acknowledge"),
        ("ann", "incident.close"),
        ("ann", "incident.reopen"),
    ]


async def test_illegal_transitions_are_409_and_other_repos_404(
    client: httpx.AsyncClient,  # noqa: F811
) -> None:
    await seed("open")
    h = login_as(client, "ann", {REPO: "approver"})
    r = await act(client, h, "reopen")  # open -> open
    assert r.status_code == 409
    await lifecycle.transition("inc-1", "investigating")
    await lifecycle.transition("inc-1", "identified")
    await lifecycle.transition("inc-1", "remediating")
    await lifecycle.transition("inc-1", "verifying")
    await lifecycle.transition("inc-1", "resolved")
    r = await act(client, h, "close")  # resolved -> closed is not a legal move
    assert (
        r.status_code == 409 and "resolved -> closed is not allowed" in r.json()["error"]["message"]
    )
    r = await act(client, h, "bogus")
    assert r.status_code == 422

    login_as(client, "mallory", {"other/repo": "approver"})
    assert (await client.get("/api/v1/incidents/inc-1/history")).status_code == 404
    assert (await act(client, h, "close")).status_code == 404
