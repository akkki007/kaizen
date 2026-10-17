"""#93 incident page views: metrics, similar incidents, SSE status stream, node inspector and
evidence-graph overlays. All read-only and scoped to the incident's repository."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from causaldeploy.api import routes
from causaldeploy.db.models import IncidentRow, RemediationRun, Repository, Telemetry
from causaldeploy.db.session import session_scope
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.store import Edge
from causaldeploy.incidents import lifecycle
from causaldeploy.memory import similarity
from tests.test_api import client, login_as  # noqa: F401  (fixture re-export)

ONSET = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
VALIDATE = "payments:src/payment/validation.js:validatePayment"
HANDLE = "payments:src/routes.js:handlePayment"
REFUND = "payments:src/admin.js:refundPayment"


async def seed_pg(repo: str, inc_id: str = "inc-1", status: str = "open", **kw: object) -> None:
    async with session_scope() as s:
        if (await s.get(Repository, repo)) is None:
            s.add(Repository(full_name=repo))
            await s.flush()
        s.add(
            IncidentRow(
                id=inc_id,
                repo=repo,
                service="payments",
                env="prod",
                endpoint="POST /payments",
                status=status,
                onset_ts=ONSET,
                detected_at=ONSET + timedelta(minutes=1),
                **kw,
            )
        )
        await s.flush()
        await lifecycle.record_opened(s, inc_id)


def tel(repo: str, minute: int, code: int, **kw: object) -> Telemetry:
    return Telemetry(
        repo=repo,
        service="payments",
        env="prod",
        ts=ONSET + timedelta(minutes=minute, seconds=10),
        kind=str(kw.pop("kind", "request")),
        endpoint="POST /payments",
        status_code=code,
        latency_ms=float(kw.pop("latency", 20.0)),
        fingerprint=kw.pop("fingerprint", None),  # type: ignore[arg-type]
        sha=None,
        body=dict(kw.pop("body", {})),  # type: ignore[call-overload]
    )


async def test_metrics_buckets_error_rate_and_deployments(
    client: httpx.AsyncClient,  # noqa: F811
    builder: GraphBuilder,
) -> None:
    repo = builder.store.repo
    builder.s1_payment_validation()
    # dep-842 in S1 started at epoch 300; add one inside the window and one in another env
    builder.deployment("dep-900", "prod", ["c0ffee01"], int(ONSET.timestamp()) - 120)
    builder.deployment("dep-901", "staging", ["c0ffee01"], int(ONSET.timestamp()) - 60)
    await seed_pg(repo)
    async with session_scope() as s:
        s.add_all([tel(repo, -5, 200), tel(repo, -5, 200, latency=40.0)])
        s.add_all([tel(repo, 2, 500), tel(repo, 2, 200), tel(repo, 2, 503, latency=900.0)])
        s.add(tel(repo, 3, 500))
        s.add(tel(repo, -200, 500))  # outside the window
    login_as(client, "al", {repo: "viewer"})
    r = await client.get("/api/v1/incidents/inc-1/metrics?minutes=60")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["since"].startswith("2026-10-14T11:20") and m["until"].startswith("2026-10-14T12:20")
    by_min = {p["ts"][11:16]: p for p in m["series"]}
    assert set(by_min) == {"11:55", "12:02", "12:03"}
    assert by_min["11:55"]["requests"] == 2 and by_min["11:55"]["error_rate"] == 0
    assert by_min["12:02"]["errors"] == 2 and by_min["12:02"]["error_rate"] == pytest.approx(0.6667)
    assert by_min["12:02"]["p95_ms"] > 500
    assert [d["id"] for d in m["deployments"]] == ["dep-900"]
    assert m["deployments"][0]["sha"] is None  # builder deployments carry no sha_to
    assert (await client.get("/api/v1/incidents/inc-1/metrics?minutes=5")).status_code == 422


async def test_similar_lists_resembling_incidents_with_their_fix(
    client: httpx.AsyncClient,  # noqa: F811
    builder: GraphBuilder,
) -> None:
    repo = builder.store.repo
    builder.s1_payment_validation()
    builder.candidates("inc-1", [("c0ffee01", 0.9)])
    builder.incident(
        "inc-8", service="payments", errors=["fp-undefined-currency"], endpoint="POST /payments"
    )
    s = builder.store
    s.upsert_nodes("Fix", [{"id": "fix-rem-1", "kind": "revert", "pr_url": "https://gh/pr/14"}])
    s.upsert_edges(
        [
            Edge("Incident", "inc-1", "fixed_by", "Fix", "fix-rem-1"),
            Edge("Fix", "fix-rem-1", "reverts", "Commit", "c0ffee01"),
            Edge("Incident", "inc-1", "confirmed_cause", "Commit", "c0ffee01"),
        ]
    )
    matches = similarity.write(s, "inc-8", 0.1)
    assert [m.incident_id for m in matches] == ["inc-1"]
    await seed_pg(repo, "inc-1", status="resolved", confirmed_root_cause="c0ffee01")
    await seed_pg(repo, "inc-8")
    login_as(client, "al", {repo: "viewer"})
    out = (await client.get("/api/v1/incidents/inc-8/similar")).json()
    assert len(out) == 1
    top = out[0]
    assert top["incident_id"] == "inc-1" and top["status"] == "resolved"
    assert top["similarity"] > 0.1 and "fingerprint:fp-undefined-currency" in top["basis"]
    assert top["fix"] == {"id": "fix-rem-1", "kind": "revert", "pr_url": "https://gh/pr/14"}
    assert top["confirmed_root_cause"] == "c0ffee01"

    # history overlay: the resembling incident, its fix and the commit the fix reverted
    g = (await client.get("/api/v1/incidents/inc-8/evidence-graph?overlay=history")).json()
    hist = {n["id"] for n in g["nodes"] if n["overlay"] == "history"}
    assert {"Fix:fix-rem-1", "Commit:c0ffee01"} <= hist
    # the resembles edge itself is already part of the base graph (an Incident out-edge)
    assert "Incident:inc-1" in {n["id"] for n in g["nodes"]}
    rels = {(e["source"], e["rel"], e["target"]) for e in g["edges"] if e["overlay"]}
    assert ("Incident:inc-1", "fixed_by", "Fix:fix-rem-1") in rels
    assert ("Fix:fix-rem-1", "reverts", "Commit:c0ffee01") in rels

    login_as(client, "mallory", {"other/repo": "approver"})
    assert (await client.get("/api/v1/incidents/inc-8/similar")).status_code == 404


async def test_blast_overlay_marks_reachable_code_and_keeps_the_base_graph(
    client: httpx.AsyncClient,  # noqa: F811
    builder: GraphBuilder,
) -> None:
    repo = builder.store.repo
    builder.s1_payment_validation()
    builder.function(
        "payments", "src/admin.js", "refundPayment", calls=[VALIDATE], endpoint="POST /refunds"
    )
    builder.candidates("inc-1", [("c0ffee01", 0.9)])
    await seed_pg(repo)
    login_as(client, "al", {repo: "viewer"})
    base = (await client.get("/api/v1/incidents/inc-1/evidence-graph")).json()
    assert all(n["overlay"] is None for n in base["nodes"])
    g = (await client.get("/api/v1/incidents/inc-1/evidence-graph?overlay=blast")).json()
    blast = {n["id"]: n for n in g["nodes"] if n["overlay"] == "blast"}
    assert f"Function:{REFUND}" in blast
    assert blast["Endpoint:payments:POST /refunds"]["props"]["impact"] == "potential"
    assert {n["id"] for n in base["nodes"]} <= {n["id"] for n in g["nodes"]}
    # base nodes keep their highlight and are not re-marked as overlay
    ids = {n["id"]: n for n in g["nodes"]}
    assert ids["Commit:c0ffee01"]["highlight"] and ids["Commit:c0ffee01"]["overlay"] is None
    edges = {(e["source"], e["rel"], e["target"]) for e in g["edges"] if e["overlay"] == "blast"}
    assert (f"Function:{REFUND}", "calls", f"Function:{VALIDATE}") in edges
    nodes = {n["id"] for n in g["nodes"]}
    assert all(e["source"] in nodes and e["target"] in nodes for e in g["edges"])
    bad = await client.get("/api/v1/incidents/inc-1/evidence-graph?overlay=nope")
    assert bad.status_code == 422


async def test_node_inspector_commit_function_error(
    client: httpx.AsyncClient,  # noqa: F811
    builder: GraphBuilder,
) -> None:
    repo = builder.store.repo
    builder.s1_payment_validation()
    builder.candidates("inc-1", [("c0ffee01", 0.9)])
    await seed_pg(repo)
    async with session_scope() as s:
        for i in range(12):
            s.add(
                tel(
                    repo,
                    i,
                    500,
                    kind="error",
                    fingerprint="fp-undefined-currency",
                    body={"error_type": "TypeError", "message": f"boom {i}"},
                )
            )
    login_as(client, "al", {repo: "viewer"})
    base = "/api/v1/incidents/inc-1/nodes/"

    c = (await client.get(base + "Commit:c0ffee01")).json()
    assert c["label"] == "Commit" and c["commit"]["author"] == "dev"
    assert c["commit"]["message"] == "Refactor payment validation"
    fn = c["commit"]["changed_functions"][0]
    assert fn["function_id"] == VALIDATE and (fn["start_line"], fn["end_line"]) == (1, 200)
    assert any(
        n["id"] == f"Function:{VALIDATE}" and n["direction"] == "out" for n in c["neighbours"]
    )

    f = (await client.get(base + f"Function:{VALIDATE}")).json()
    assert f["function"]["path"] == "src/payment/validation.js"
    assert f["function"]["callers"] == [HANDLE] and f["function"]["callees"] == []
    assert any(n["rel"] == "modifies_fn" and n["direction"] == "in" for n in f["neighbours"])

    e = (await client.get(base + "Error:fp-undefined-currency")).json()
    assert len(e["error_samples"]) == 10  # capped, newest first
    assert e["error_samples"][0]["message"] == "boom 11"
    assert e["error_samples"][0]["error_type"] == "TypeError"

    d = (await client.get("/api/v1/incidents/inc-1")).json()
    assert d["severity"] is None and d["acknowledged_by"] is None
    assert d["confirmed_root_cause"] is None and d["root_cause_status"] is None

    assert (await client.get(base + "Commit:ghost")).status_code == 404
    assert (await client.get(base + "Bogus:x")).status_code == 404
    assert (await client.get(base + "SchemaMeta:x")).status_code == 404
    login_as(client, "mallory", {"other/repo": "approver"})
    assert (await client.get(base + "Commit:c0ffee01")).status_code == 404


def parse_sse(raw: str) -> list[tuple[str, dict[str, object]]]:
    out = []
    for block in raw.split("\n\n"):
        lines = [x for x in block.splitlines() if x and not x.startswith(":")]
        ev = next((x[7:] for x in lines if x.startswith("event: ")), None)
        data = next((x[6:] for x in lines if x.startswith("data: ")), None)
        if ev and data:
            out.append((ev, json.loads(data)))
    return out


async def test_stream_emits_first_status_and_changes(
    client: httpx.AsyncClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_pg("acme/shop")
    monkeypatch.setattr(routes, "STREAM_POLL_S", 0.05)
    monkeypatch.setattr(routes, "STREAM_HEARTBEAT_S", 0.2)
    monkeypatch.setattr(routes, "STREAM_MAX_S", 1.5)
    login_as(client, "al", {"acme/shop": "viewer"})

    async def change() -> None:
        await asyncio.sleep(0.3)
        await lifecycle.transition("inc-1", "investigating")
        await asyncio.sleep(0.3)
        async with session_scope() as s:
            s.add(
                RemediationRun(id="rem-1", incident_id="inc-1", kind="revert", status="verifying")
            )

    task = asyncio.create_task(change())
    async with client.stream("GET", "/api/v1/incidents/inc-1/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        raw = "".join([chunk async for chunk in r.aiter_text()])
    await task
    events = parse_sse(raw)
    assert events[0][0] == "status"
    assert events[0][1]["status"] == "open"
    assert events[0][1]["investigation_status"] == "not_run"
    assert events[0][1]["remediation_status"] is None
    statuses = [(e["status"], e["remediation_status"]) for _, e in events]
    assert ("investigating", None) in statuses and ("investigating", "verifying") in statuses
    assert len(events) == len({json.dumps(e, sort_keys=True) for _, e in events})  # no repeats
    assert ": heartbeat" in raw

    login_as(client, "mallory", {"other/repo": "approver"})
    assert (await client.get("/api/v1/incidents/inc-1/stream")).status_code == 404
