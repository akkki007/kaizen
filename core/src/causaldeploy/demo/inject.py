"""Demo: simulate a bad deploy on a real repository graph and let the real pipeline find it.

`inject` picks the most recently changed function that serves an endpoint, records a deployment
containing its commit, then sends the same telemetry a production service would (healthy windows,
then failing ones with a stack that throws in that function) through the normal ingest path.
Detection, linking, ranking and alerting are the production code; only the traffic is synthetic,
and every response says so.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from causaldeploy.db.session import session_scope
from causaldeploy.detect.detector import detect_repo
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.store import GraphStore
from causaldeploy.jobs import handlers

TARGET = """
MATCH (c:Commit)-[:modifies_fn]->(f:Function)
WHERE NOT f.path CONTAINS 'test' AND NOT f.path CONTAINS 'spec'
MATCH (h:Function)-[:handles]->(e:Endpoint)
MATCH p = (h)-[:calls*0..3]->(f)
RETURN c.sha, c.message, c.ts, f.id, f.name, f.path, f.start_line, h.id, h.name, h.path,
       h.start_line, e.id, length(p)
ORDER BY c.ts DESC, length(p) LIMIT 1
""".strip()

RUNTIME = (
    "Incident Error Metric Trace Fix Test VerificationRun Investigation ToolCall "
    "ConfigChange DependencyChange"
).split()


class DemoError(Exception):
    pass


def _target(store: GraphStore) -> dict[str, Any]:
    rows = store.read(TARGET)
    if not rows:
        raise DemoError(
            "this repository's graph has no recently changed function behind an endpoint"
        )
    r = rows[0]
    return {
        "sha": r[0], "message": r[1] or "", "fn": r[3], "fn_name": r[4], "fn_path": r[5],
        "fn_line": int(r[6] or 1) + 1, "handler": r[7], "handler_name": r[8],
        "handler_path": r[9], "handler_line": int(r[10] or 1) + 1,
        "endpoint_id": r[11], "hops": int(r[12]),
    }  # fmt: skip


async def inject(repo: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    store = GraphStore(repo)
    t = await asyncio.to_thread(_target, store)
    service, endpoint = t["endpoint_id"].split(":", 1)
    base = datetime.fromtimestamp((int(now.timestamp()) - 450) // 30 * 30, UTC)
    onset = base + timedelta(seconds=360)
    dep_id = f"demo-{int(now.timestamp())}"
    await asyncio.to_thread(
        GraphBuilder(store).deployment,
        dep_id, "prod", [t["sha"]], int(onset.timestamp()) - 20,
    )  # fmt: skip
    stack = [
        f"{t['fn_name']} (/app/{t['fn_path']}:{t['fn_line']}:5)",
        f"{t['handler_name']} (/app/{t['handler_path']}:{t['handler_line']}:3)",
    ]
    for w in range(14):
        failing = w >= 12
        for i in range(20):
            err = failing and i < 14
            ev = {
                "type": "telemetry", "repo": repo, "service": service, "env": "prod",
                "ts": (base + timedelta(seconds=30 * w + i)).isoformat(), "kind": "request",
                "endpoint": endpoint, "status_code": 500 if err else 200, "latency_ms": 40.0,
            }  # fmt: skip
            await handlers.ingest_event({"event": ev})
        if failing:
            await handlers.ingest_event(
                {
                    "event": {
                        "type": "telemetry",
                        "repo": repo,
                        "service": service,
                        "env": "prod",
                        "ts": (base + timedelta(seconds=30 * w)).isoformat(),
                        "kind": "error",
                        "endpoint": endpoint,
                        "error_type": "TypeError",
                        "message": "Cannot read properties of undefined (simulated demo failure)",
                        "stack": stack,
                    }
                }
            )
    opened = await detect_repo(repo, now)
    if not opened:
        raise DemoError("an incident is already open for this endpoint; reset the demo first")
    return {
        "incident_id": opened[0],
        "simulated": True,
        "service": service,
        "endpoint": endpoint,
        "function": t["fn"],
        "commit": t["sha"],
        "commit_message": t["message"],
    }


async def reset(repo: str) -> dict[str, int]:
    """Remove this repository's incident state and demo deployments (code graph is kept)."""
    incidents = "SELECT id FROM incidents WHERE repo = :r"
    statements = [
        f"DELETE FROM incident_transitions WHERE incident_id IN ({incidents})",
        f"DELETE FROM investigations WHERE incident_id IN ({incidents})",
        f"DELETE FROM remediation_runs WHERE incident_id IN ({incidents})",
        f"DELETE FROM llm_calls WHERE incident_id IN ({incidents})",
        "DELETE FROM incidents WHERE repo = :r",
        "DELETE FROM telemetry WHERE repo = :r",
    ]
    removed = 0
    async with session_scope() as s:
        for sql in statements:
            res = await s.execute(text(sql), {"r": repo})
            removed += int(res.rowcount or 0)  # type: ignore[attr-defined]
    store = GraphStore(repo)
    where = " OR ".join(f"n:{label}" for label in RUNTIME)
    await asyncio.to_thread(store.graph.query, f"MATCH (n) WHERE {where} DETACH DELETE n")
    await asyncio.to_thread(
        store.graph.query, "MATCH (d:Deployment) WHERE d.id STARTS WITH 'demo-' DETACH DELETE d"
    )
    return {"rows_removed": removed}
