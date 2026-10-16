"""Incident memory (#91): what Kaizen keeps once an incident is resolved.

Postgres `incidents.memory` gets a self-contained snapshot (the ranking as it stood, the fix,
its verification and the timings); the graph gets the Fix-[:verified_by]->Test chain so
Incident -> Commit <- Fix -> Test can be walked, and a fresh similarity pass so later incidents
find this one through `resembles` edges.
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select

from causaldeploy.db.models import IncidentRow, RemediationRun
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import Edge, GraphStore

log = logging.getLogger(__name__)


def _secs(a: datetime | None, b: datetime | None) -> float | None:
    return round((a - b).total_seconds(), 3) if a and b else None


def timings(row: IncidentRow) -> dict[str, float | None]:
    """ttd = detected - onset, ttdiagnose = diagnosed - detected, ttr = resolved - detected."""
    return {
        "ttd_s": _secs(row.detected_at, row.onset_ts),
        "ttdiagnose_s": _secs(row.diagnosed_at, row.detected_at),
        "ttr_s": _secs(row.resolved_at, row.detected_at),
    }


def candidates_snapshot(store: GraphStore, incident_id: str) -> list[dict[str, Any]]:
    rows = store.read(
        "MATCH (:Incident {id: $i})-[r:candidate]->(c:Commit) "
        "RETURN c.sha, r.rank, r.score, r.confidence, c.message ORDER BY r.rank",
        {"i": incident_id},
    )
    if not rows:  # never persisted (e.g. a fix proposed straight from the API): rank now
        from causaldeploy.investigate.scorer import rank_graph

        result = rank_graph(store, incident_id)
        rows = [[c.sha, c.rank, c.score, c.confidence, c.message] for c in result.candidates]
    return [
        {"sha": sha, "rank": rank, "score": score, "confidence": conf, "message": msg}
        for sha, rank, score, conf, msg in rows
    ]


def test_nodes(run_id: str, verification: dict[str, Any]) -> list[dict[str, Any]]:
    """Test nodes from the verification report's tests, else one for the verification run."""
    tests = verification.get("tests") or []
    out: list[dict[str, Any]] = []
    for i, t in enumerate(tests):
        name = str(t.get("name") if isinstance(t, dict) else t)
        passed = bool(t.get("passed", True)) if isinstance(t, dict) else True
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or str(i)
        out.append({"id": f"test-{run_id}-{i}-{slug}", "name": name, "passed": passed})
    if not out:
        out.append(
            {
                "id": f"test-{run_id}",
                "name": "staging verification",
                "passed": bool(verification.get("passed")),
                "run_url": verification.get("run_url") or "",
            }
        )
    return out


def _write_graph(store: GraphStore, run_id: str, verification: dict[str, Any]) -> list[str]:
    nodes = test_nodes(run_id, verification)
    store.upsert_nodes("Test", nodes)
    store.upsert_edges(
        [Edge("Fix", f"fix-{run_id}", "verified_by", "Test", n["id"]) for n in nodes]
    )
    return [n["id"] for n in nodes]


async def write_memory(repo: str, incident_id: str, run_id: str, merged_by: str) -> dict[str, Any]:
    """Persist the snapshot and the graph chain for a resolved incident. Returns the snapshot."""
    store = GraphStore(repo)
    cands = await asyncio.to_thread(candidates_snapshot, store, incident_id)
    async with session_scope() as s:
        inc = (
            await s.execute(select(IncidentRow).where(IncidentRow.id == incident_id))
        ).scalar_one()
        run = await s.get(RemediationRun, run_id)
        assert run is not None
        r = run.result or {}
        verification = dict(r.get("verification") or {})
        snapshot: dict[str, Any] = {
            "version": 1,
            "candidates": cands,
            "root_cause": {"sha": inc.confirmed_root_cause, "status": inc.root_cause_status},
            "fix": {
                "remediation_id": run.id,
                "kind": run.kind,
                "sha": r.get("sha"),
                "branch": r.get("branch"),
                "pr_url": r.get("pr_url"),
                "approved_by": run.approved_by,
                "merged_by": merged_by,
            },
            "verification": verification,
            **timings(inc),
        }
        inc.memory = snapshot
    test_ids = await asyncio.to_thread(_write_graph, store, run_id, verification)
    snapshot["tests"] = test_ids
    try:
        from causaldeploy.memory.similarity import update_similarity

        await update_similarity(repo, incident_id)
    except Exception:  # memory must not undo a resolution
        log.warning("similarity pass failed", extra={"ctx": {"incident": incident_id}})
    return snapshot
