"""#91 incident memory: computed similarity, root cause confirmation, demo history."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from causaldeploy.api.auth import User, make_session
from causaldeploy.api.main import create_app
from causaldeploy.db.models import AuditLog, IncidentRow, RemediationRun, Repository
from causaldeploy.db.session import session_scope
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.queries import similar_resolved_incidents
from causaldeploy.graph.store import Edge
from causaldeploy.memory.similarity import compute, error_family, update_similarity
from causaldeploy.remediate.service import pr_body, prior_fixes

FN = {
    "pay_post": "payments:services/payments/src/app.js:POST /payments",
    "pay_currency": "payments:services/payments/src/payment/validation.js:validateCurrency",
    "orders_post": "orders:services/orders/src/app.js:POST /orders",
    "orders_line": "orders:services/orders/src/pricing.js:lineCents",
    "gateway_post": "gateway:services/gateway/src/app.js:POST /orders",
}
UPPER = "Cannot read properties of undefined (reading 'toUpperCase')"


def incident(
    b: GraphBuilder,
    inc: str,
    *,
    service: str,
    endpoint: str,
    etype: str,
    msg: str,
    thrown: list[str],
    changed: list[str],
    status: str,
) -> str:
    """An incident shaped the way detection + investigation leave it (no similarity edges)."""
    s = b.store
    fp = f"fp-{inc}"
    b.error(fp, thrown_in=thrown, message=msg, service=service)
    s.graph.query("MATCH (e:Error {fingerprint: $f}) SET e.error_type = $t", {"f": fp, "t": etype})
    sha = b.commit(f"c-{inc}", 100, modifies_fn=changed, message=f"change for {inc}")
    b.incident(inc, service=service, errors=[fp], endpoint=endpoint, status=status)
    s.upsert_edges([Edge("Incident", inc, "likely_caused_by", "Commit", sha)])
    if status == "resolved":
        fix = f"fix-{inc}"
        s.upsert_nodes("Fix", [{"id": fix, "kind": "revert", "pr_url": f"https://gh/pull/{inc}"}])
        s.upsert_edges(
            [
                Edge("Incident", inc, "fixed_by", "Fix", fix),
                Edge("Fix", fix, "reverts", "Commit", sha),
            ]
        )
    return inc


@pytest.fixture
def shoplite(builder: GraphBuilder) -> GraphBuilder:
    money = builder.dependency("money-utils", "1.4.0")
    for key, fid in FN.items():
        service, path, name = fid.split(":", 2)
        builder.function(service, path, name, deps=[money] if key != "gateway_post" else [])
    # S1-like: resolved TypeError in payments currency validation
    incident(
        builder,
        "inc-s1",
        service="payments",
        endpoint="POST /payments",
        etype="TypeError",
        msg=UPPER,
        thrown=[FN["pay_currency"], FN["pay_post"]],
        changed=[FN["pay_currency"]],
        status="resolved",
    )
    # unrelated, but sharing service + endpoint + a thrown function with the new incident
    incident(
        builder,
        "inc-rounding",
        service="orders",
        endpoint="POST /orders",
        etype="RangeError",
        msg="amount must be an integer number of cents, got 1999.5",
        thrown=[FN["orders_line"], FN["orders_post"]],
        changed=[FN["orders_line"]],
        status="resolved",
    )
    incident(
        builder,
        "inc-timeout",
        service="gateway",
        endpoint="POST /orders",
        etype="Error",
        msg="upstream orders timed out after 3000ms",
        thrown=[FN["gateway_post"]],
        changed=[FN["gateway_post"]],
        status="resolved",
    )
    # S8-like: the S1 bug shape recurs in another service
    incident(
        builder,
        "inc-s8",
        service="orders",
        endpoint="POST /orders",
        etype="TypeError",
        msg=UPPER.replace("toUpperCase", "trim"),
        thrown=[FN["orders_post"]],
        changed=[FN["orders_post"]],
        status="identified",
    )
    return builder


def test_error_family_masks_values() -> None:
    assert error_family("TypeError", UPPER) == error_family(
        "TypeError", "Cannot read properties of undefined (reading 'currency')"
    )
    assert error_family("RangeError", "got 1999.5") == error_family("RangeError", "got 12")


async def test_recurrence_ranks_the_prior_incident_with_its_fix_first(
    shoplite: GraphBuilder,
) -> None:
    store = shoplite.store
    assert store.read("MATCH ()-[r:resembles]->() RETURN count(r)") == [[0]]  # nothing seeded
    ranked = compute(store, "inc-s8")
    assert ranked[0].incident_id == "inc-s1"
    sims = {m.incident_id: m.similarity for m in ranked}
    assert sims["inc-s1"] > sims["inc-rounding"] > sims.get("inc-timeout", 0.0)
    assert any(b.startswith("error_family:typeerror") for b in ranked[0].basis)
    assert "dependency:money-utils" in ranked[0].basis

    written = await update_similarity(store.repo, "inc-s8")
    assert [m.incident_id for m in written] == ["inc-s1"]  # above the default threshold
    similar = similar_resolved_incidents(store, "inc-s8")
    assert similar[0].incident_id == "inc-s1" and similar[0].fix_kind == "revert"
    assert similar[0].similarity == sims["inc-s1"] and similar[0].basis == ranked[0].basis
    # both directions, so the history is found whichever incident is newer
    assert store.read(
        "MATCH (:Incident {id: 'inc-s1'})-[r:resembles]->(:Incident {id: 'inc-s8'}) "
        "RETURN r.similarity"
    ) == [[sims["inc-s1"]]]
    # recomputing replaces, never duplicates
    await update_similarity(store.repo, "inc-s8")
    assert store.read("MATCH (:Incident {id: 'inc-s8'})-[r:resembles]->() RETURN count(r)") == [[1]]


async def test_pr_body_cites_the_prior_fix(shoplite: GraphBuilder) -> None:
    from causaldeploy.investigate.scorer import RankedCandidate, RankResult

    store = shoplite.store
    await update_similarity(store.repo, "inc-s8")
    prior = prior_fixes(store, "inc-s8")
    assert prior[0]["incident_id"] == "inc-s1" and prior[0]["fix_sha"] == "c-inc-s1"
    result = RankResult(
        incident_id="inc-s8",
        method="graph",
        verdict="confident",
        margin=0.5,
        candidates=[
            RankedCandidate(
                sha="c-inc-s8",
                rank=1,
                score=1.0,
                confidence=0.9,
                features=[],
                message="change",
                author="dev",
                deployment=None,
                evidence=[],
            )
        ],
    )
    body = pr_body(result, "inc-s8", "http://app", prior)
    assert "Seen before:** incident `inc-s1`" in body and "https://gh/pull/inc-s1" in body


@pytest_asyncio.fixture
async def client(db: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr("causaldeploy.jobs.queue.get_pool", AsyncMock(return_value=AsyncMock()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        yield c


def login(c: httpx.AsyncClient, repo: str, role: str) -> dict[str, str]:
    c.cookies.set("cd_session", make_session(User(login="ann", repos={repo: role})))  # type: ignore[arg-type]
    c.cookies.set("cd_csrf", "t")
    return {"x-csrf-token": "t"}


async def test_root_cause_confirm_and_reject(
    client: httpx.AsyncClient, builder: GraphBuilder
) -> None:
    sc = builder.s1_payment_validation()
    store, repo = builder.store, builder.store.repo
    builder.candidates(sc.incident_id, [(sc.culprit_commit, 1.0), ("n0000001", 0.1)])
    now = datetime.now(UTC)
    async with session_scope() as s:
        s.add(Repository(full_name=repo))
        await s.flush()
        s.add(
            IncidentRow(
                id=sc.incident_id,
                repo=repo,
                service="payments",
                env="prod",
                status="identified",
                onset_ts=now,
                detected_at=now,
            )
        )
    url = f"/api/v1/incidents/{sc.incident_id}/root-cause"
    body = {"sha": sc.culprit_commit, "confirmed": True, "reason": "verified in logs"}

    h = login(client, repo, "viewer")
    assert (await client.post(url, json=body, headers=h)).status_code == 403
    login(client, repo, "approver")
    assert (await client.post(url, json=body)).status_code == 403  # CSRF
    h = login(client, repo, "approver")
    unknown = {"sha": "deadbeef00", "confirmed": True}
    assert (await client.post(url, json=unknown, headers=h)).status_code == 422

    r = await client.post(url, json=body, headers=h)
    assert r.status_code == 200 and r.json() == {
        "incident_id": sc.incident_id,
        "sha": sc.culprit_commit,
        "root_cause_status": "confirmed",
        "confirmed_root_cause": sc.culprit_commit,
    }
    assert store.read(
        "MATCH (i:Incident {id: $i})-[:confirmed_cause]->(c:Commit) RETURN c.sha, i.confirmed_root_cause",
        {"i": sc.incident_id},
    ) == [[sc.culprit_commit, sc.culprit_commit]]

    r = await client.post(url, json={"sha": sc.culprit_commit, "confirmed": False}, headers=h)
    assert r.json()["root_cause_status"] == "rejected" and r.json()["confirmed_root_cause"] is None
    assert store.read(
        "MATCH (:Incident {id: $i})-[r:likely_caused_by]->(:Commit {sha: $s}) RETURN r.rejected",
        {"i": sc.incident_id, "s": sc.culprit_commit},
    ) == [[True]]
    assert store.read(
        "MATCH (:Incident {id: $i})-[r:confirmed_cause]->() RETURN count(r)", {"i": sc.incident_id}
    ) == [[0]]
    async with session_scope() as s:
        row = await s.get(IncidentRow, sc.incident_id)
        actions = [a.action for a in (await s.execute(select(AuditLog))).scalars()]
    assert (
        row is not None and row.root_cause_status == "rejected" and row.confirmed_root_cause is None
    )
    assert actions == ["incident.root_cause.confirm", "incident.root_cause.reject"]


async def test_demo_history_is_deterministic_and_idempotent(
    db: None, builder: GraphBuilder
) -> None:
    from causaldeploy.memory.demo import seed_history

    store = builder.store
    first = await seed_history(store.repo, store)
    counts = store.counts()
    second = await seed_history(store.repo, store)
    assert first == second and store.counts() == counts
    async with session_scope() as s:
        n_inc = (await s.execute(select(func.count()).select_from(IncidentRow))).scalar_one()
        runs = (await s.execute(select(RemediationRun))).scalars().all()
    assert n_inc == 3 and {r.status for r in runs} == {"merged"} and len(runs) == 3
    assert store.read(
        "MATCH (i:Incident {status: 'resolved'})-[:confirmed_cause]->(:Commit)<-[:reverts]-(:Fix)"
        "-[:verified_by]->(:Test) RETURN count(i)"
    ) == [[3]]

    # a new S8-like incident now finds the seeded payments incident through computed edges
    b = builder
    b.error("fp-new", thrown_in=[FN["orders_post"]], message=UPPER, service="orders")
    store.graph.query("MATCH (e:Error {fingerprint: 'fp-new'}) SET e.error_type = 'TypeError'")
    b.incident("inc-new", service="orders", errors=["fp-new"], endpoint="POST /orders")
    await update_similarity(store.repo, "inc-new")
    similar = similar_resolved_incidents(store, "inc-new")
    assert similar and similar[0].incident_id == "hist-payments-currency"
