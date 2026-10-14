"""Read-only graph tools served over MCP (PLAN section 7.1).

Each tool wraps a core query or a ranking feature, validates its input, is scoped to one
repository's graph, and returns compact JSON with `node_ids` in the same `Label:key` form the
evidence graph uses, so every claim can cite what a tool actually returned.
"""

from typing import Any

from causaldeploy.graph import queries as q
from causaldeploy.graph.evidence import node_id
from causaldeploy.graph.store import GraphStore
from causaldeploy.investigate import features as F
from causaldeploy.investigate.scorer import rank_graph


def _ids(label: str, keys: list[str | None]) -> list[str]:
    return [node_id(label, k) for k in keys if k]


def get_incident(store: GraphStore, incident_id: str) -> dict[str, Any]:
    inc = F.load_incident(store, incident_id)
    if inc is None:
        return {"error": f"unknown incident {incident_id}", "node_ids": []}
    errors = store.read(
        "MATCH (:Incident {id: $id})-[:evidenced_by]->(e:Error) "
        "OPTIONAL MATCH (e)-[t:thrown_in]->(f:Function) "
        "RETURN e.fingerprint, e.error_type, e.message, f.id, t.frame_depth "
        "ORDER BY e.fingerprint, t.frame_depth",
        {"id": incident_id},
    )
    sigs: dict[str, dict[str, Any]] = {}
    for fp, etype, msg, fn, _depth in errors:
        e = sigs.setdefault(fp, {"fingerprint": fp, "type": etype, "message": msg, "thrown_in": []})
        if fn:
            e["thrown_in"].append(fn)
    ids = [node_id("Incident", incident_id), *_ids("Error", list(sigs))]
    ids += _ids("Function", [fn for e in sigs.values() for fn in e["thrown_in"]])
    return {
        "id": incident_id,
        "service": inc.service,
        "env": inc.env,
        "endpoint": inc.endpoint,
        "endpoint_id": inc.endpoint_id,
        "onset_ts": inc.onset_ts,
        "errors": list(sigs.values()),
        "node_ids": list(dict.fromkeys(ids)),
    }


def get_deployment_commits(store: GraphStore, incident_id: str) -> dict[str, Any]:
    rows = q.incident_deployment_commits(store, incident_id)
    ids = _ids("Deployment", [r.deployment_id for r in rows]) + _ids(
        "Commit", [c for r in rows for c in r.commits]
    )
    return {"deployments": [r.model_dump() for r in rows], "node_ids": ids}


def shortest_call_path(store: GraphStore, sha: str, incident_id: str) -> dict[str, Any]:
    fps = [
        r[0]
        for r in store.read(
            "MATCH (:Incident {id: $id})-[:evidenced_by]->(e:Error) RETURN e.fingerprint",
            {"id": incident_id},
        )
    ]
    paths = [p for fp in fps for p in q.changed_to_thrown_paths(store, sha, fp)]
    paths.sort(key=lambda p: (p.distance, p.changed_function, p.thrown_function))
    ids = _ids("Commit", [sha]) + _ids("Function", [fn for p in paths for fn in p.path])
    return {"paths": [p.model_dump() for p in paths], "node_ids": list(dict.fromkeys(ids))}


def blast_radius(
    store: GraphStore, function_id: str | None = None, sha: str | None = None
) -> dict[str, Any]:
    if sha and not function_id:
        fns = [
            r[0]
            for r in store.read(
                "MATCH (:Commit {sha: $s})-[:modifies_fn]->(f:Function) RETURN f.id ORDER BY f.id",
                {"s": sha},
            )
        ]
    else:
        fns = [function_id] if function_id else []
    results = [q.blast_radius(store, f) for f in fns]
    ids = (
        _ids("Function", fns + [x for r in results for x in r.affected_functions])
        + _ids("Endpoint", [x for r in results for x in r.affected_endpoints])
        + _ids("Service", [x for r in results for x in r.affected_services])
    )
    return {"radius": [r.model_dump() for r in results], "node_ids": list(dict.fromkeys(ids))}


def dependency_impact(store: GraphStore, sha: str) -> dict[str, Any]:
    deps = [
        r[0]
        for r in store.read(
            "MATCH (:Commit {sha: $s})-[:changes_dep]->(d:Dependency) RETURN d.id ORDER BY d.id",
            {"s": sha},
        )
    ]
    results = [q.dependency_impact(store, d) for d in deps]
    ids = (
        _ids("Dependency", deps)
        + _ids("Function", [x for r in results for x in r.functions])
        + _ids("Service", [x for r in results for x in r.services])
    )
    return {"impact": [r.model_dump() for r in results], "node_ids": list(dict.fromkeys(ids))}


def similar_incidents(store: GraphStore, incident_id: str) -> dict[str, Any]:
    rows = q.similar_resolved_incidents(store, incident_id)
    ids = _ids("Incident", [r.incident_id for r in rows]) + _ids("Fix", [r.fix_id for r in rows])
    return {"similar": [r.model_dump() for r in rows], "node_ids": ids}


def function_incident_history(store: GraphStore, function_id: str) -> dict[str, Any]:
    rows = q.function_incident_history(store, function_id)
    return {
        "history": [r.model_dump() for r in rows],
        "node_ids": _ids("Function", [function_id])
        + _ids("Incident", [r.incident_id for r in rows]),
    }


def rank_candidates(store: GraphStore, incident_id: str) -> dict[str, Any]:
    r = rank_graph(store, incident_id)
    return {
        "verdict": r.verdict,
        "margin": r.margin,
        "candidates": [
            {
                "sha": c.sha,
                "rank": c.rank,
                "score": c.score,
                "confidence": c.confidence,
                "message": c.message,
                "features": {f.name: f.value for f in c.features},
                "evidence": [e.model_dump() for e in c.evidence],
            }
            for c in r.candidates[:5]
        ],
        "node_ids": _ids("Commit", [c.sha for c in r.candidates[:5]])
        + _ids("Function", [e.changed_function for c in r.candidates[:5] for e in c.evidence])
        + _ids("Function", [e.thrown_function for c in r.candidates[:5] for e in c.evidence]),
    }


def suggest_responders(store: GraphStore, incident_id: str) -> dict[str, Any]:
    """Who touched the likely culprit's functions recently, plus who fixed similar incidents."""
    top = rank_graph(store, incident_id).candidates
    sha = top[0].sha if top else None
    rows = (
        store.read(
            "MATCH (:Commit {sha: $s})-[:modifies_fn]->(f:Function)<-[:modifies_fn]-(c:Commit)"
            "-[:authored_by]->(d:Developer) WHERE c.sha <> $s "
            "RETURN d.id, count(DISTINCT c) AS n ORDER BY n DESC, d.id LIMIT 5",
            {"s": sha or ""},
        )
        if sha
        else []
    )
    resolvers = store.read(
        "MATCH (:Incident {id: $id})-[:resembles]->(r:Incident {status: 'resolved'}) "
        "MATCH (r)<-[:resolved]-(d:Developer) "
        "RETURN DISTINCT d.id ORDER BY d.id LIMIT 5",
        {"id": incident_id},
    )
    people = [
        {"developer": r[0], "reason": f"changed the same functions in {r[1]} other commit(s)"}
        for r in rows
    ]
    people += [{"developer": r[0], "reason": "fixed a similar incident"} for r in resolvers]
    return {"responders": people, "node_ids": _ids("Developer", [p["developer"] for p in people])}
