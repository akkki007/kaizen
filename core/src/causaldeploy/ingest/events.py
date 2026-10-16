"""Apply normalized events to the graph. Idempotent: replaying events is a no-op."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from causaldeploy.contracts import BuildEvent, CommitEvent, DeploymentEvent, Event
from causaldeploy.graph.store import Edge, GraphStore


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def apply_commit(
    store: GraphStore, e: CommitEvent, service_of: Callable[[str], str] | None = None
) -> None:
    store.ensure_schema()
    added = sum(h.added for f in e.files for h in f.hunks)
    removed = sum(h.removed for f in e.files for h in f.hunks)
    store.upsert_nodes(
        "Commit",
        [
            {
                "sha": e.sha,
                "ts": _epoch(e.ts),
                "message": e.message,
                "files_changed": len(e.files),
                "loc_added": added,
                "loc_removed": removed,
            }
        ],
    )
    store.upsert_nodes("Developer", [{"id": e.author}])
    edges = [Edge("Commit", e.sha, "authored_by", "Developer", e.author)]
    edges += [Edge("Commit", e.sha, "parent", "Commit", p) for p in e.parents]
    if e.branch:
        store.upsert_nodes("Branch", [{"id": e.branch}])
        edges.append(Edge("Commit", e.sha, "on_branch", "Branch", e.branch))
    for f in e.files:
        svc = service_of(f.path) if service_of else ""
        fid = f"{svc}:{f.path}" if svc else f.path
        store.upsert_nodes("File", [{"id": fid, "path": f.path, "service": svc}])
        props: dict[str, Any] = {"status": f.status}
        if f.old_path:
            props["renamed_from"] = f.old_path
        edges.append(Edge("Commit", e.sha, "modifies", "File", fid, props))
    store.upsert_edges(edges)


def apply_build(store: GraphStore, e: BuildEvent) -> None:
    store.ensure_schema()
    store.upsert_nodes(
        "Build", [{"id": e.id, "status": e.status, "started_at": _epoch(e.started_at)}]
    )
    store.upsert_edges([Edge("Commit", e.sha, "creates", "Build", e.id)])


def commits_between(store: GraphStore, sha_from: str | None, sha_to: str) -> list[str]:
    """Commits reachable from sha_to but not from sha_from, oldest first."""
    q = "MATCH (:Commit {sha: $s})-[:parent*0..2000]->(c:Commit) RETURN DISTINCT c.sha, c.ts"
    to = {r[0]: r[1] or 0 for r in store.read(q, {"s": sha_to})}
    excluded = {r[0] for r in store.read(q, {"s": sha_from})} if sha_from else set()
    return sorted((s for s in to if s not in excluded), key=lambda s: (to[s], s))


def _previous_success(store: GraphStore, env: str, before: int, own_id: str) -> str | None:
    rows = store.read(
        "MATCH (d:Deployment {env: $env}) WHERE d.status = 'success' AND d.id <> $id "
        "AND d.started_at <= $t RETURN d.sha_to ORDER BY d.started_at DESC LIMIT 1",
        {"env": env, "t": before, "id": own_id},
    )
    return rows[0][0] if rows else None


def apply_deployment(store: GraphStore, e: DeploymentEvent) -> list[str]:
    """Record the deployment, compute its commit range, detect rollbacks. Returns the commits."""
    store.ensure_schema()
    started = _epoch(e.started_at)
    sha_from = e.sha_from or _previous_success(store, e.env, started, e.id)
    status = e.status
    commits = commits_between(store, sha_from, e.sha_to)
    if sha_from and e.status == "success" and e.sha_to != sha_from:
        # target is an ancestor of the previous deployment => this is a rollback
        if e.sha_to in {
            r[0]
            for r in store.read(
                "MATCH (:Commit {sha: $s})-[:parent*0..2000]->(c:Commit) RETURN c.sha",
                {"s": sha_from},
            )
        }:
            status, commits = "rollback", []
    store.upsert_nodes(
        "Deployment",
        [
            {
                "id": e.id,
                "env": e.env,
                "status": status,
                "started_at": started,
                "finished_at": _epoch(e.finished_at) if e.finished_at else None,
                "sha_from": sha_from,
                "sha_to": e.sha_to,
                "services": e.services,
            }
        ],
    )
    store.upsert_nodes("Environment", [{"id": e.env}])
    edges = [Edge("Deployment", e.id, "deployed_to", "Environment", e.env)]
    edges += [Edge("Deployment", e.id, "includes", "Commit", c) for c in commits]
    if e.build_id:
        store.upsert_nodes("Build", [{"id": e.build_id}])
        edges.append(Edge("Build", e.build_id, "produces", "Deployment", e.id))
    store.upsert_edges(edges)
    return commits


def refresh_deployments(store: GraphStore) -> int:
    """Recompute every stored deployment's commit range.

    Deployment events can arrive before the commits they contain are imported (a deploy right
    after a push, before the backfill finished). Run after each backfill; it is idempotent.
    """
    rows = store.read("MATCH (d:Deployment) RETURN d.id, d.status, d.sha_from, d.sha_to")
    for dep_id, status, sha_from, sha_to in rows:
        if status == "rollback" or not sha_to:
            continue
        commits = commits_between(store, sha_from, sha_to)
        store.upsert_edges([Edge("Deployment", dep_id, "includes", "Commit", c) for c in commits])
    return len(rows)


def apply_event(
    store: GraphStore, event: Event, service_of: Callable[[str], str] | None = None
) -> None:
    if isinstance(event, CommitEvent):
        apply_commit(store, event, service_of)
    elif isinstance(event, BuildEvent):
        apply_build(store, event)
    elif isinstance(event, DeploymentEvent):
        apply_deployment(store, event)
    # telemetry / health go to detection; verification_result is routed by the ingest job
    # (jobs.handlers.ingest_event) to remediate.service.record_verification_event
