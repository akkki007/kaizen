"""Read-only graph tools served over MCP (PLAN section 7.1).

Each tool wraps a core query or a ranking feature, validates its input with a Pydantic model
(which is also the tool's JSON schema), is scoped to one repository's graph, and returns compact
JSON with `node_ids` in the same `Label:key` form the evidence graph uses, so every claim can cite
what a tool actually returned. Every payload goes through `cap()`: bounded lists and bytes, with
`truncated` saying whether anything was cut.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from causaldeploy.graph import queries as q
from causaldeploy.graph.evidence import node_id
from causaldeploy.graph.store import GraphStore
from causaldeploy.investigate import features as F
from causaldeploy.investigate.scorer import rank_graph

MAX_ITEMS = 50  # per list in a payload
MAX_NODE_IDS = 200
MAX_BYTES = 24_000  # serialized payload


def _ids(label: str, keys: list[str | None]) -> list[str]:
    return [node_id(label, k) for k in keys if k]


# -- output bounds ----------------------------------------------------------------------------
def _cap_lists(value: Any, max_items: int) -> tuple[Any, bool]:
    if isinstance(value, list):
        cut = len(value) > max_items
        items = []
        for v in value[:max_items]:
            capped, c = _cap_lists(v, max_items)
            items.append(capped)
            cut |= c
        return items, cut
    if isinstance(value, dict):
        out, cut = {}, False
        for k, v in value.items():
            out[k], c = _cap_lists(v, max_items)
            cut |= c
        return out, cut
    return value, False


def _size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, default=str).encode())


def cap(
    payload: dict[str, Any], max_items: int = MAX_ITEMS, max_bytes: int = MAX_BYTES
) -> dict[str, Any]:
    """Bound a tool payload: at most `max_items` per list (node_ids: MAX_NODE_IDS) and
    `max_bytes` serialized (largest lists halved, then long strings shortened). Sets `truncated`."""
    node_ids = list(payload.get("node_ids", []))
    body = {k: v for k, v in payload.items() if k not in ("node_ids", "truncated")}
    body, truncated = _cap_lists(body, max_items)
    if len(node_ids) > MAX_NODE_IDS:
        node_ids, truncated = node_ids[:MAX_NODE_IDS], True
    out = {**body, "node_ids": node_ids, "truncated": truncated}
    while _size(out) > max_bytes:
        lists = [
            (len(json.dumps(v, default=str)), k)
            for k, v in out.items()
            if isinstance(v, list) and k != "node_ids" and len(v) > 1
        ]
        if lists:
            _, k = max(lists)
            out[k] = out[k][: len(out[k]) // 2]
        else:
            strings = [(len(v), k) for k, v in out.items() if isinstance(v, str) and len(v) > 64]
            if not strings:
                out["node_ids"] = out["node_ids"][: len(out["node_ids"]) // 2]
                if not out["node_ids"]:
                    out["truncated"] = True
                    break
            else:
                _, k = max(strings)
                out[k] = out[k][: len(out[k]) // 2] + "..."
        out["truncated"] = True
    return out


# -- input models (also the tools' JSON schemas) ----------------------------------------------
_ID = r"^[A-Za-z0-9_.:\-]+$"


class IncidentArgs(BaseModel):
    incident_id: str = Field(min_length=1, max_length=64, pattern=_ID, description="Incident id")


class ShaArgs(BaseModel):
    sha: str = Field(
        min_length=4, max_length=64, pattern=r"^[A-Za-z0-9]+$", description="Commit sha"
    )


class FunctionArgs(BaseModel):
    function_id: str = Field(
        min_length=3, max_length=512, description="Function id: service:path:qualifiedName"
    )


class CallPathArgs(ShaArgs, IncidentArgs):
    pass


class BlastArgs(BaseModel):
    function_id: str | None = Field(None, max_length=512, description="Function id")
    sha: str | None = Field(
        None, max_length=64, pattern=r"^[A-Za-z0-9]+$", description="or a commit: all it changed"
    )


class CallersArgs(FunctionArgs):
    depth: int = Field(1, ge=1, le=3, description="How many call hops up to follow")


class DiffArgs(ShaArgs):
    function_id: str | None = Field(None, max_length=512, description="Limit to one function")


class ErrorSamplesArgs(IncidentArgs):
    limit: int = Field(5, ge=1, le=20, description="Max error signatures")


# -- tools --------------------------------------------------------------------------------------
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
    if inc.endpoint_id and store.read(
        "MATCH (e:Endpoint {id: $e}) RETURN 1 LIMIT 1", {"e": inc.endpoint_id}
    ):
        ids += _ids("Endpoint", [inc.endpoint_id])
    ids += _ids("Service", [inc.service])
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
    top = r.candidates[:5]
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
                "deployment": c.deployment,
                "features": {f.name: f.value for f in c.features},
                "evidence": [e.model_dump() for e in c.evidence],
            }
            for c in top
        ],
        "node_ids": list(
            dict.fromkeys(
                _ids("Commit", [c.sha for c in top])
                + _ids("Function", [e.changed_function for c in top for e in c.evidence])
                + _ids("Function", [e.thrown_function for c in top for e in c.evidence])
                + _ids("Deployment", [c.deployment for c in top])
            )
        ),
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


def _changes(store: GraphStore, sha: str, function_id: str | None) -> list[dict[str, Any]]:
    rows = store.read(
        "MATCH (:Commit {sha: $s})-[m:modifies_fn]->(f:Function) "
        "WHERE $f IS NULL OR f.id = $f "
        "RETURN f.id, f.path, f.service, f.start_line, f.end_line, m.added, m.removed, "
        "m.moved_from ORDER BY f.id",
        {"s": sha, "f": function_id},
    )
    return [
        {
            "function_id": r[0],
            "path": r[1],
            "service": r[2],
            "start_line": r[3],
            "end_line": r[4],
            "lines_added": r[5],
            "lines_removed": r[6],
            "moved_from": r[7],
        }
        for r in rows
    ]


def _file_changes(store: GraphStore, sha: str) -> list[dict[str, Any]]:
    rows = store.read(
        "MATCH (:Commit {sha: $s})-[m:modifies]->(f:File) "
        "RETURN f.id, f.path, m.fallback, m.added, m.removed ORDER BY f.id",
        {"s": sha},
    )
    return [
        {"file_id": r[0], "path": r[1], "reason": r[2], "lines_added": r[3], "lines_removed": r[4]}
        for r in rows
    ]


def get_changed_functions(store: GraphStore, sha: str) -> dict[str, Any]:
    commit = store.read("MATCH (c:Commit {sha: $s}) RETURN c.message, c.ts", {"s": sha})
    if not commit:
        return {"error": f"unknown commit {sha}", "node_ids": []}
    fns, files = _changes(store, sha, None), _file_changes(store, sha)
    return {
        "sha": sha,
        "message": commit[0][0],
        "functions": fns,
        "files_without_function_mapping": files,
        "node_ids": _ids("Commit", [sha])
        + _ids("Function", [f["function_id"] for f in fns])
        + _ids("File", [f["file_id"] for f in files]),
    }


def get_callers(store: GraphStore, function_id: str, depth: int = 1) -> dict[str, Any]:
    seen: dict[str, dict[str, Any]] = {}
    for hops in range(1, max(1, min(depth, 3)) + 1):
        for fid, service in store.read(
            f"MATCH (c:Function)-[:calls*{hops}..{hops}]->(:Function {{id: $id}}) "
            "WHERE c.id <> $id RETURN DISTINCT c.id, c.service ORDER BY c.id",
            {"id": function_id},
        ):
            seen.setdefault(fid, {"function_id": fid, "service": service, "distance": hops})
    callers = list(seen.values())
    return {
        "function_id": function_id,
        "callers": callers,
        "node_ids": _ids("Function", [function_id] + [c["function_id"] for c in callers]),
    }


def get_diff(store: GraphStore, sha: str, function_id: str | None = None) -> dict[str, Any]:
    """What a commit changed, as stored. Raw diff text is not kept in the graph."""
    if not store.read("MATCH (c:Commit {sha: $s}) RETURN 1 LIMIT 1", {"s": sha}):
        return {"error": f"unknown commit {sha}", "node_ids": []}
    fns = _changes(store, sha, function_id)
    files = [] if function_id else _file_changes(store, sha)
    return {
        "sha": sha,
        "diff_text_available": False,
        "note": (
            "Kaizen stores hunk summaries, not diff text: for each changed function its line "
            "range and lines added/removed (and a prior location if it moved)."
        ),
        "hunks": fns,
        "files_without_function_mapping": files,
        "node_ids": _ids("Commit", [sha])
        + _ids("Function", [f["function_id"] for f in fns])
        + _ids("File", [f["file_id"] for f in files]),
    }


def get_error_samples(store: GraphStore, incident_id: str, limit: int = 5) -> dict[str, Any]:
    """Error signatures behind an incident (from Error nodes), most frequent first."""
    rows = store.read(
        "MATCH (:Incident {id: $id})-[:evidenced_by]->(e:Error) "
        "OPTIONAL MATCH (e)-[t:thrown_in]->(f:Function) "
        "OPTIONAL MATCH (e)-[:observed_on]->(ep:Endpoint) "
        "WITH e, f, t, ep ORDER BY t.frame_depth "
        "RETURN e.fingerprint, e.error_type, e.message, e.count, e.first_seen, e.last_seen, "
        "collect(DISTINCT f.id), collect(DISTINCT ep.id) "
        "ORDER BY coalesce(e.count, 0) DESC, e.fingerprint LIMIT $n",
        {"id": incident_id, "n": max(1, min(limit, 20))},
    )
    samples = [
        {
            "fingerprint": r[0],
            "type": r[1],
            "message": r[2],
            "count": r[3],
            "first_seen": r[4],
            "last_seen": r[5],
            "stack_functions": [x for x in r[6] if x],
            "endpoints": [x for x in r[7] if x],
        }
        for r in rows
    ]
    return {
        "incident_id": incident_id,
        "samples": samples,
        "node_ids": list(
            dict.fromkeys(
                _ids("Error", [s["fingerprint"] for s in samples])
                + _ids("Function", [f for s in samples for f in s["stack_functions"]])
                + _ids("Endpoint", [e for s in samples for e in s["endpoints"]])
            )
        ),
    }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable[..., dict[str, Any]]
    args: type[BaseModel]
    description: str


TOOLS: list[ToolSpec] = [
    ToolSpec(
        "get_incident",
        get_incident,
        IncidentArgs,
        "Incident facts: service, endpoint, onset, errors, where thrown.",
    ),
    ToolSpec(
        "get_deployment_commits",
        get_deployment_commits,
        IncidentArgs,
        "Deployments before the incident and the commits in each.",
    ),
    ToolSpec(
        "shortest_call_path",
        shortest_call_path,
        CallPathArgs,
        "Shortest call path from a commit's changed to thrown functions.",
    ),
    ToolSpec(
        "get_call_path",
        shortest_call_path,
        CallPathArgs,
        "Alias of shortest_call_path.",
    ),
    ToolSpec(
        "blast_radius",
        blast_radius,
        BlastArgs,
        "Functions, endpoints, services affected by changing a function or commit.",
    ),
    ToolSpec(
        "dependency_impact",
        dependency_impact,
        ShaArgs,
        "Dependencies a commit changed and the functions/services using them.",
    ),
    ToolSpec(
        "similar_incidents",
        similar_incidents,
        IncidentArgs,
        "Resolved incidents resembling this one, with the fix that worked.",
    ),
    ToolSpec(
        "function_incident_history",
        function_incident_history,
        FunctionArgs,
        "Past incidents that blamed or threw in a function.",
    ),
    ToolSpec(
        "rank_candidates",
        rank_candidates,
        IncidentArgs,
        "Deterministic ranking of suspect commits with per-feature scores.",
    ),
    ToolSpec(
        "get_candidates",
        rank_candidates,
        IncidentArgs,
        "Alias of rank_candidates.",
    ),
    ToolSpec(
        "suggest_responders",
        suggest_responders,
        IncidentArgs,
        "Developers to involve: recent authors, resolvers of similar ones.",
    ),
    ToolSpec(
        "get_changed_functions",
        get_changed_functions,
        ShaArgs,
        "Functions (and unmapped files) a commit changed, with line ranges and lines +/-.",
    ),
    ToolSpec(
        "get_callers",
        get_callers,
        CallersArgs,
        "Functions that call a function, up to `depth` hops.",
    ),
    ToolSpec(
        "get_diff",
        get_diff,
        DiffArgs,
        "Hunk summaries of a commit (optionally one function); diff text is not stored.",
    ),
    ToolSpec(
        "get_error_samples",
        get_error_samples,
        ErrorSamplesArgs,
        "Error signatures behind an incident: type, message, counts, stack functions.",
    ),
]
READ_ONLY_TOOL_NAMES = frozenset(t.name for t in TOOLS)
