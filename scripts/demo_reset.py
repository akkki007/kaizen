"""Clean one repository's incident state for a demo (called by demo-reset.sh / demo-reset.ps1).

Run from core/ inside the project environment:

    uv run python ../scripts/demo_reset.py <owner/name> [--yes] [--drop-graph]

Deletes, for <owner/name> only:
  Postgres  incidents (and their transitions, investigations, remediation runs, LLM calls),
            telemetry, and jobs whose payload names the repository
  FalkorDB  runtime + memory nodes of the repository graph (Incident, Error, Metric, Trace, Fix,
            Test, VerificationRun, Investigation, ToolCall, ConfigChange, DependencyChange);
            code, commits and deployments stay, so no re-import is needed.
            --drop-graph drops the whole graph instead (re-import with `make backfill`).
Kept: repositories, installations, API keys, raw webhook deliveries, the audit log.

Refuses CD_ENV=prod and repositories outside CD_ALLOWED_REPOS. Asks for confirmation (type the
repository name) unless --yes.
"""

import argparse
import asyncio
import sys

RUNTIME_LABELS = (
    "Incident",
    "Error",
    "Metric",
    "Trace",
    "Fix",
    "Test",
    "VerificationRun",
    "Investigation",
    "ToolCall",
    "ConfigChange",
    "DependencyChange",
)


async def clean_postgres(repo: str) -> dict[str, int]:
    from sqlalchemy import text

    from causaldeploy.db.session import get_engine, session_scope

    incidents = "SELECT id FROM incidents WHERE repo = :r"
    transitions = f"DELETE FROM incident_transitions WHERE incident_id IN ({incidents})"
    statements = {
        "incident_transitions": transitions,
        "investigations": f"DELETE FROM investigations WHERE incident_id IN ({incidents})",
        "remediation_runs": f"DELETE FROM remediation_runs WHERE incident_id IN ({incidents})",
        "llm_calls": f"DELETE FROM llm_calls WHERE incident_id IN ({incidents})",
        "incidents": "DELETE FROM incidents WHERE repo = :r",
        "telemetry": "DELETE FROM telemetry WHERE repo = :r",
        "jobs": "DELETE FROM jobs WHERE payload->>'repo' = :r OR payload->'event'->>'repo' = :r",
    }
    counts: dict[str, int] = {}
    try:
        async with session_scope() as s:  # one transaction: all or nothing
            for table, sql in statements.items():
                res = await s.execute(text(sql), {"r": repo})
                counts[table] = int(res.rowcount or 0)  # type: ignore[attr-defined]
    finally:
        await get_engine().dispose()
    return counts


def clean_graph(repo: str, drop: bool) -> str:
    from causaldeploy.graph.store import GraphStore

    store = GraphStore(repo)
    if drop:
        store.drop()
        return f"dropped graph {store.name}"
    before = store.counts()[0]
    where = " OR ".join(f"n:{label}" for label in RUNTIME_LABELS)
    store.graph.query(f"MATCH (n) WHERE {where} DETACH DELETE n")
    return f"removed {before - store.counts()[0]} runtime nodes from {store.name}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("repo", help="owner/name")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--drop-graph", action="store_true", help="drop the whole repository graph")
    a = p.parse_args()

    from causaldeploy.config import get_settings

    s = get_settings()
    if s.env == "prod":
        print("refusing: CD_ENV=prod. demo-reset never runs against production.", file=sys.stderr)
        return 2
    if "/" not in a.repo or not s.is_allowed(a.repo):
        print(
            f"refusing: {a.repo!r} is not an allowed owner/name (CD_ALLOWED_REPOS)", file=sys.stderr
        )
        return 2
    target = s.postgres_dsn.rsplit("@", 1)[-1]  # host:port/db, never the credentials
    print(f"demo-reset {a.repo}: Postgres {target}, FalkorDB {s.falkordb_host}:{s.falkordb_port}")
    if not a.yes:
        answer = input(f"Delete incident state for {a.repo}? Type the repository name: ")
        if answer.strip() != a.repo:
            print("aborted")
            return 1
    counts = asyncio.run(clean_postgres(a.repo))
    print("postgres: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print("graph: " + clean_graph(a.repo, a.drop_graph))
    return 0


if __name__ == "__main__":
    sys.exit(main())
