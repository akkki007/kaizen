"""S1 end to end, no manual step: telemetry -> detection -> investigation -> agent -> revert PR
-> staging verification -> merge -> incident resolved and remembered (PLAN section 15)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mcp import Client
from sqlalchemy import select

from causaldeploy.agent.loop import run_agent
from causaldeploy.agent.persist import write_trace
from causaldeploy.db.models import (
    IncidentRow,
    Installation,
    Investigation,
    RemediationRun,
    Repository,
)
from causaldeploy.db.session import session_scope
from causaldeploy.detect.detector import detect_repo
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.investigate.run import investigate
from causaldeploy.jobs import handlers
from causaldeploy.mcp_server.server import build_server
from causaldeploy.remediate.service import finalize_merged, propose_revert, record_verification
from tests.test_agent import Scripted, call, final
from tests.test_remediate import FakeOps, remote  # noqa: F401  (fixture)

T0 = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


async def test_s1_end_to_end(
    db: None,
    builder: GraphBuilder,
    remote: tuple[Path, str],  # noqa: F811
    tmp_path: Path,
) -> None:
    bare, culprit = remote
    store, repo = builder.store, builder.store.repo
    sc = builder.s1_payment_validation()
    # the fixture's culprit commit is the real commit in the fake remote
    store.graph.query(
        "MATCH (c:Commit {sha: $o}) SET c.sha = $n", {"o": sc.culprit_commit, "n": culprit}
    )
    store.graph.query(
        "MATCH (d:Deployment {id: 'dep-842'}) SET d.started_at = $t, d.finished_at = $t",
        {"t": int(T0.timestamp())},
    )
    async with session_scope() as s:
        s.add(Installation(id=1, account="acme"))
        await s.flush()
        s.add(Repository(full_name=repo, installation_id=1))

    # 1. production telemetry (ShopLite shape): healthy, then POST /payments fails after the deploy
    stack = [
        "validatePayment (/app/src/payment/validation.js:10:5)",
        "handlePayment (/app/src/routes.js:20:3)",
    ]
    for w in range(14):
        failing = w >= 12
        for i in range(20):
            err = failing and i < 14
            ev = {
                "type": "telemetry",
                "repo": repo,
                "service": "payments",
                "env": "prod",
                "ts": (T0 + timedelta(seconds=30 * w + i)).isoformat(),
                "kind": "request",
                "endpoint": "POST /payments",
                "status_code": 500 if err else 200,
                "latency_ms": 40.0,
            }
            await handlers.ingest_event({"event": ev})
        if failing:
            await handlers.ingest_event(
                {
                    "event": {
                        "type": "telemetry",
                        "repo": repo,
                        "service": "payments",
                        "env": "prod",
                        "ts": (T0 + timedelta(seconds=30 * w)).isoformat(),
                        "kind": "error",
                        "endpoint": "POST /payments",
                        "error_type": "TypeError",
                        "message": "Cannot read properties of undefined (reading 'currency')",
                        "stack": stack,
                    }
                }
            )

    # 2. detection opens one incident with the true onset
    opened = await detect_repo(repo, T0 + timedelta(minutes=7, seconds=40))
    assert len(opened) == 1
    incident_id = opened[0]
    async with session_scope() as s:
        row = (await s.execute(select(IncidentRow))).scalar_one()
    assert row.onset_ts == T0 + timedelta(seconds=30 * 12)

    # 3. investigation: deterministic ranking persisted (the live agent is skipped without a key)
    result = await investigate(repo, incident_id)
    assert result.verdict == "confident" and result.candidates[0].sha == culprit
    assert [c.rank for c in result.candidates][0] == 1
    async with session_scope() as s:  # must leave "investigating" once a suspect is named
        status = (await s.execute(select(IncidentRow.status))).scalar_one()
    assert status == "identified"
    assert store.read(
        "MATCH (:Incident {id: $i})-[:likely_caused_by]->(c:Commit) RETURN c.sha",
        {"i": incident_id},
    ) == [[culprit]]

    # the guilty path is highlighted in the evidence graph the UI renders
    from causaldeploy.graph.evidence import evidence_graph

    g = evidence_graph(store, incident_id)
    assert g is not None
    hl_nodes = {n.id for n in g.nodes if n.highlight}
    assert f"Commit:{culprit}" in hl_nodes
    assert "Function:payments:src/payment/validation.js:validatePayment" in hl_nodes
    assert any(e.rel == "modifies_fn" and e.highlight for e in g.edges)

    # 4. the agent explains it with cited, validated evidence
    good = {
        "verdict": "confident",
        "culprit_sha": culprit,
        "claims": [
            {
                "text": "validatePayment threw and was changed in the culprit commit",
                "cited_node_ids": [f"Commit:{culprit}"],
            }
        ],
        "suggested_fix": f"revert {culprit[:7]}",
        "responders": [],
    }
    llm = Scripted(
        [
            call(1, "get_incident", incident_id=incident_id),
            call(2, "rank_candidates", incident_id=incident_id),
            call(3, "shortest_call_path", sha=culprit, incident_id=incident_id),
            final(good),
        ]
    )
    async with Client(build_server(repo, store)) as client:
        agent = await run_agent(client, store, incident_id, llm)  # type: ignore[arg-type]
    assert agent.status == "explained" and len(agent.steps) == 3
    write_trace(store, agent, "inv-e2e")
    async with session_scope() as s:
        assert (await s.execute(select(Investigation))).scalars().first() is not None

    # 5. revert PR + staging verification + human merge
    ops = FakeOps(bare, tmp_path)
    run = await propose_revert(repo, incident_id, 1, ops)
    assert (
        run is not None
        and run.status == "verifying"
        and ops.pushed == [f"kaizen/fix-{incident_id}"]
        and ops.prs[0]["labels"] == ["kaizen", f"incident:{incident_id}"]
    )
    verdict = await record_verification(
        repo,
        run.result["branch"],  # type: ignore[index]
        "success",
        "https://run/9",
        {"error_rate": 0.0, "p95_ms": 41},
        ops=ops,
        installation_id=1,
    )
    assert verdict == "verified" and ops.checks[0]["passed"] is True
    async with session_scope() as s:  # approval, as POST /remediations/{id}/approve records it
        rem = await s.get(RemediationRun, run.id)
        assert rem is not None
        rem.status, rem.approved_by = "approved", "yash"
    assert await finalize_merged(repo, run.result["branch"], "yash") == incident_id  # type: ignore[index]

    # 6. the graph remembers: resolved incident, confirmed cause, verified fix, resolver
    mem = store.read(
        "MATCH (i:Incident {id: $i})-[:fixed_by]->(f:Fix)-[:reverts]->(c:Commit) "
        "OPTIONAL MATCH (d:Developer)-[:resolved]->(i) "
        "RETURN i.status, i.confirmed_root_cause, f.verified, c.sha, d.id",
        {"i": incident_id},
    )
    assert mem == [["resolved", culprit, True, culprit, "yash"]]
    chain = store.read(
        "MATCH (i:Incident {id: $i})-[:confirmed_cause]->(c:Commit)<-[:reverts]-(:Fix)"
        "-[:verified_by]->(t:Test) RETURN c.sha, t.id",
        {"i": incident_id},
    )
    assert chain == [[culprit, f"test-{run.id}"]]
    async with session_scope() as s:
        row = (await s.execute(select(IncidentRow))).scalar_one()
    memory = row.memory or {}
    assert row.confirmed_root_cause == culprit and memory["fix"]["merged_by"] == "yash"
    assert memory["ttd_s"] == 100.0  # detected at onset + 100 s (step 2)
    assert memory["ttdiagnose_s"] is not None and memory["ttr_s"] is not None
    assert json.dumps(agent.model_dump(mode="json"))  # trace is serializable for the UI
