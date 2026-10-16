"""Ranking features. Every feature is a FalkorDB query (PLAN section 5, Cypher-first).

Python only binds parameters and reads what each query returns. Queries are module-level
constants with stable names so the API and UI can show the exact Cypher behind every number.

Evidence contract (#89): every feature query returns three columns

    value          the number the scorer combines (0 when the query matches nothing)
    evidence_ids   the graph nodes that support the value, as stable "<Label>:<key>" ids
                   (the same ids the evidence graph and the agent's citations use)
    detail         a small scalar/list the explanation sentence is built from

and Python turns them into a `FeatureResult`. A feature that does not apply to a candidate kind
(e.g. `path` for a config change) is 0 with no evidence and says so.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from causaldeploy.graph.store import GraphStore

CandidateKind = Literal["commit", "config", "dependency"]
KIND_LABEL: dict[CandidateKind, str] = {
    "commit": "Commit",
    "config": "ConfigChange",
    "dependency": "DependencyChange",
}
EVIDENCE_CAP = 25  # keeps a blast-radius list readable; the value is computed on all of it

# -- candidate selection ------------------------------------------------------------------
# The deployment linked to the incident (if any) plus every successful deployment to the same
# environment that started within the lookback window before onset.
DEPLOYMENTS = """
MATCH (i:Incident {id: $inc})
OPTIONAL MATCH (t:Deployment)-[:triggers]->(i)
WITH i, collect(DISTINCT t.id) AS triggered
OPTIONAL MATCH (d:Deployment {env: i.env})
WHERE d.status = 'success' AND d.started_at <= i.onset_ts
  AND d.started_at >= i.onset_ts - $lookback
WITH triggered, collect(DISTINCT d.id) AS windowed
RETURN triggered, windowed
""".strip()

CANDIDATES = """
MATCH (d:Deployment)-[:includes]->(c:Commit) WHERE d.id IN $deps
OPTIONAL MATCH (c)-[:authored_by]->(dev:Developer)
RETURN DISTINCT c.sha, c.message, c.ts, dev.id, d.id
ORDER BY c.ts DESC, c.sha
""".strip()

CONFIG_CANDIDATES = """
MATCH (d:Deployment)-[:changes_config]->(x:ConfigChange) WHERE d.id IN $deps
RETURN DISTINCT x.id, x.service, x.key, x.file, d.started_at, d.id
ORDER BY x.id
""".strip()

DEPENDENCY_CANDIDATES = """
MATCH (d:Deployment)-[:bumps]->(x:DependencyChange) WHERE d.id IN $deps
RETURN DISTINCT x.id, x.name, x.from_version, x.to_version, d.started_at, d.id
ORDER BY x.id
""".strip()


# -- shared query fragments -----------------------------------------------------------------
def _temporal(anchor: str) -> str:
    return f"""
MATCH (i:Incident {{id: $inc}})
MATCH {anchor}
WITH i, d, coalesce(d.finished_at, d.started_at) AS deployed
WITH i, d, deployed, CASE WHEN i.onset_ts < deployed THEN 0.0
                          ELSE exp(-(toFloat(i.onset_ts) - deployed) / $tau) END AS v
ORDER BY v DESC, d.id LIMIT 1
RETURN v, ['Deployment:' + d.id, 'Incident:' + i.id], i.onset_ts - deployed
""".strip()


def _sequence(anchor: str) -> str:
    return f"""
MATCH (i:Incident {{id: $inc}})-[:evidenced_by]->(e:Error)
WITH i, min(e.first_seen) AS first
MATCH (d:Deployment {{env: i.env}})
WHERE d.status = 'success' AND d.started_at <= first
WITH i, d ORDER BY d.started_at DESC, d.id LIMIT 1
MATCH {anchor}
RETURN 1.0, ['Deployment:' + d.id, 'Incident:' + i.id], d.id
""".strip()


def _triggered(anchor: str) -> str:
    """The candidate shipped in the deployment the detector linked to the incident."""
    return f"""
MATCH {anchor}
MATCH (d)-[:triggers]->(i:Incident {{id: $inc}})
WITH d, i ORDER BY d.id LIMIT 1
RETURN 1.0, ['Deployment:' + d.id, 'Incident:' + i.id], d.id
""".strip()


def _service(functions: str, head: str) -> str:
    """Changed code's service reaches the failing service over service-level calls."""
    return f"""
{functions}
MATCH (f)-[:belongs_to]->(s:Service)
MATCH p = (s)-[:calls*0..3]->(t:Service {{id: $svc}})
WITH f, p ORDER BY length(p), f.id LIMIT 1
RETURN 1.0 / (1 + length(p)), [{head}, 'Function:' + f.id] + [n IN nodes(p) | 'Service:' + n.id],
       length(p)
""".strip()


def _dependency(changed_dep: str, head: str) -> str:
    return f"""
MATCH {changed_dep}<-[:depends_on]-(x:Function)
MATCH (:Incident {{id: $inc}})-[:evidenced_by]->(e:Error)-[:thrown_in]->(g:Function)
MATCH p = (g)-[:calls*0..3]-(x)
WITH dep, e, p ORDER BY length(p), dep.id, e.fingerprint LIMIT 1
RETURN 1.0, [{head}, 'Dependency:' + dep.id, 'Error:' + e.fingerprint]
            + [n IN nodes(p) | 'Function:' + n.id], dep.id
""".strip()


def _config(anchor: str, head: str) -> str:
    """A config change on the failing service (1.0) or a service it talks to directly (0.5)."""
    return f"""
MATCH {anchor}
MATCH (t:Service {{id: $svc}})
MATCH (s:Service) WHERE s.id = x.service
OPTIONAL MATCH (s)-[r:calls]-(t)
WITH x, s, t, CASE WHEN s.id = t.id THEN 1.0 WHEN r IS NOT NULL THEN 0.5 ELSE 0.0 END AS v
ORDER BY v DESC, x.id LIMIT 1
RETURN v, CASE WHEN v > 0 THEN [{head}, 'ConfigChange:' + x.id, 'Service:' + s.id,
                                'Service:' + t.id] ELSE [] END, x.service + ' ' + x.key
""".strip()


_COMMIT = "'Commit:' + $sha"
_CHANGED = "MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)"

FEATURE_QUERIES: dict[str, str] = {
    "temporal": _temporal("(:Commit {sha: $sha})<-[:includes]-(d:Deployment)"),
    "path": """
MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)
MATCH (:Incident {id: $inc})-[:evidenced_by]->(e:Error)-[:thrown_in]->(g:Function)
WITH e, f, g,
     shortestPath((f)-[:calls*1..6]->(g)) AS down,
     shortestPath((g)-[:calls*1..6]->(f)) AS up
WITH e, f, g,
     CASE WHEN f = g THEN [f]
          WHEN down IS NOT NULL AND (up IS NULL OR length(down) <= length(up)) THEN nodes(down)
          WHEN up IS NOT NULL THEN nodes(up)
          ELSE null END AS p
WHERE p IS NOT NULL
WITH e, f, g, p ORDER BY size(p), f.id, g.id, e.fingerprint LIMIT 1
RETURN 1.0 / size(p), ['Commit:' + $sha, 'Error:' + e.fingerprint] + [n IN p | 'Function:' + n.id],
       [size(p) - 1, f.id, g.id]
""".strip(),
    "endpoint": """
MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)
MATCH (h:Function)-[:handles]->(ep:Endpoint {id: $ep})
MATCH p = (h)-[:calls*0..4]->(f)
WITH ep, f, p ORDER BY length(p), f.id LIMIT 1
RETURN 1.0, ['Commit:' + $sha, 'Endpoint:' + ep.id] + [n IN nodes(p) | 'Function:' + n.id],
       [length(p), f.id]
""".strip(),
    "service": _service(_CHANGED, _COMMIT),
    "centrality": """
CALL algo.pageRank('Function', 'calls') YIELD node, score
WITH max(score) AS top, collect([node, score]) AS ranked
MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)
WITH f, top, [r IN ranked WHERE r[0] = f | r[1]] AS s
WITH f, CASE WHEN size(s) = 0 OR top = 0 THEN 0.0 ELSE s[0] / top END AS v
ORDER BY v DESC, f.id LIMIT 1
RETURN v, ['Commit:' + $sha, 'Function:' + f.id], f.id
""".strip(),
    "dependency": _dependency("(c:Commit {sha: $sha})-[:changes_dep]->(dep:Dependency)", _COMMIT),
    "msg_sim": """
MATCH (c:Commit {sha: $sha})
OPTIONAL MATCH (c)-[:modifies_fn]->(f:Function)
WITH c, collect(toLower(f.name)) AS fns
MATCH (:Incident {id: $inc})-[:evidenced_by]->(e:Error)
WITH c, e, split(toLower(coalesce(c.message, '')), ' ') + fns AS ctoks,
     split(toLower(coalesce(e.message, '') + ' ' + coalesce(e.error_type, '')), ' ') AS etoks
WITH c, e, etoks, [t IN etoks WHERE t IN ctoks AND size(t) > 3] AS shared
WITH c, e, shared, toFloat(size(shared)) / (size(etoks) + 1) AS v
ORDER BY v DESC, e.fingerprint LIMIT 1
RETURN v, CASE WHEN v > 0 THEN ['Commit:' + c.sha, 'Error:' + e.fingerprint] ELSE [] END, shared
""".strip(),
    "history": """
MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)
OPTIONAL MATCH (:Incident {id: $inc})-[:resembles]->(old:Incident {status: 'resolved'})
               -[:likely_caused_by]->(:Commit)-[:modifies_fn]->(f)
WITH f, collect(DISTINCT old.id) AS similar
OPTIONAL MATCH (f)<-[:modifies_fn]-(:Commit)<-[:likely_caused_by]-(past:Incident)
WHERE past.status = 'resolved' AND past.id <> $inc
WITH f, similar, collect(DISTINCT past.id) AS blamed
WITH f, similar, blamed,
     CASE WHEN size(similar) > 0 THEN 1.0 WHEN size(blamed) > 0 THEN 0.5 ELSE 0.0 END AS v
ORDER BY v DESC, f.id LIMIT 1
RETURN v, CASE WHEN v > 0 THEN ['Function:' + f.id] + [x IN similar + blamed | 'Incident:' + x]
               ELSE [] END, [size(similar), size(blamed)]
""".strip(),
    "sequence": _sequence("(d)-[:includes]->(:Commit {sha: $sha})"),
    "triggered": _triggered("(:Commit {sha: $sha})<-[:includes]-(d:Deployment)"),
    "blast": """
MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)
OPTIONAL MATCH (caller:Function)-[:calls*1..3]->(f)
WITH collect(DISTINCT f.id) AS changed, collect(DISTINCT caller.id) AS callers
WHERE size(changed) > 0
RETURN toFloat(size(changed) + size(callers)) / (size(changed) + size(callers) + 10),
       [x IN changed + callers | 'Function:' + x], [size(changed), size(callers)]
""".strip(),
    # a commit that (also) changed config of the failing service, linked via introduced_by
    "config": _config("(x:ConfigChange)-[:introduced_by]->(:Commit {sha: $sha})", _COMMIT),
}

# Non-code candidates: the subset of features that has a meaning for them ($key = node id).
_CFG = "'ConfigChange:' + $key"
_DEPC = "'DependencyChange:' + $key"
KIND_QUERIES: dict[CandidateKind, dict[str, str]] = {
    "commit": FEATURE_QUERIES,
    "config": {
        "temporal": _temporal("(:ConfigChange {id: $key})<-[:changes_config]-(d:Deployment)"),
        "service": """
MATCH (x:ConfigChange {id: $key})
MATCH (s:Service) WHERE s.id = x.service
MATCH p = (s)-[:calls*0..3]->(t:Service {id: $svc})
WITH p ORDER BY length(p) LIMIT 1
RETURN 1.0 / (1 + length(p)), ['ConfigChange:' + $key] + [n IN nodes(p) | 'Service:' + n.id],
       length(p)
""".strip(),
        "sequence": _sequence("(d)-[:changes_config]->(:ConfigChange {id: $key})"),
        "triggered": _triggered("(:ConfigChange {id: $key})<-[:changes_config]-(d:Deployment)"),
        "config": _config("(x:ConfigChange {id: $key})", _CFG),
    },
    "dependency": {
        "temporal": _temporal("(:DependencyChange {id: $key})<-[:bumps]-(d:Deployment)"),
        "service": _service(
            "MATCH (:DependencyChange {id: $key})-[:to_version]->(:Dependency)"
            "<-[:depends_on]-(f:Function)",
            _DEPC,
        ),
        "dependency": _dependency(
            "(:DependencyChange {id: $key})-[:to_version]->(dep:Dependency)", _DEPC
        ),
        "sequence": _sequence("(d)-[:bumps]->(:DependencyChange {id: $key})"),
        "triggered": _triggered("(:DependencyChange {id: $key})<-[:bumps]-(d:Deployment)"),
    },
}

# The changed -> thrown function pairs the UI highlights as the evidence path.
EVIDENCE_PAIRS = """
MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)
MATCH (:Incident {id: $inc})-[:evidenced_by]->(e:Error)-[:thrown_in]->(g:Function)
RETURN DISTINCT f.id, g.id, e.fingerprint
""".strip()


class FeatureResult(BaseModel):
    """One feature for one candidate: the number, the nodes behind it, and a sentence."""

    value: float
    evidence_ids: list[str]
    explanation: str


def _short(node: Any) -> str:
    s = str(node)
    return s.rsplit(":", 1)[-1] if s.count(":") >= 2 else s


def _explain_temporal(v: float, d: Any) -> str:
    if v <= 0:
        return "deployed after the incident started (or never deployed): no temporal link"
    return f"deployed {int(d)}s before onset (decay weight {v:.2f})"


def _explain_path(v: float, d: Any) -> str:
    if v <= 0:
        return "no call path between the changed code and where the error was thrown"
    dist, f, g = d
    if int(dist) == 0:
        return f"the error is thrown inside the changed function {_short(f)}"
    return f"changed {_short(f)} is {int(dist)} call hop(s) from {_short(g)} where it is thrown"


def _explain_endpoint(v: float, d: Any) -> str:
    if v <= 0:
        return "changed code is not reachable from the failing endpoint's handler"
    return f"the failing endpoint's handler reaches changed {_short(d[1])} in {int(d[0])} call(s)"


def _explain_service(v: float, d: Any) -> str:
    if v <= 0:
        return "changed code's service does not reach the failing service"
    return (
        "changed code is in the failing service"
        if int(d) == 0
        else f"changed code's service calls the failing service ({int(d)} service hop(s))"
    )


def _explain_centrality(v: float, d: Any) -> str:
    return f"{_short(d)} has PageRank {v:.2f} of the most central function" if d else "no code"


def _explain_dependency(v: float, d: Any) -> str:
    if v <= 0:
        return "no changed dependency is used near the failing code"
    return f"changed dependency {d} is used by code within 3 calls of the thrown function"


def _explain_msg(v: float, d: Any) -> str:
    return f"shares words with the error: {', '.join(d)}" if v > 0 and d else "no shared words"


def _explain_history(v: float, d: Any) -> str:
    if v <= 0:
        return "no resolved incident blamed this code before"
    similar, blamed = d
    if similar:
        return f"a similar resolved incident ({similar}) was caused by a change to this code"
    return f"{blamed} earlier resolved incident(s) blamed this code"


def _explain_sequence(v: float, d: Any) -> str:
    return (
        f"part of {d}, the last deployment before the first error"
        if v > 0
        else "not in the last deployment before the first error"
    )


def _explain_triggered(v: float, d: Any) -> str:
    return (
        f"shipped in {d}, the deployment linked to the incident"
        if v > 0
        else "not in the deployment linked to the incident (lookback window only)"
    )


def _explain_blast(v: float, d: Any) -> str:
    if not d:
        return "changes no functions"
    return f"{d[0]} changed function(s) with {d[1]} transitive caller(s) (size penalty)"


def _explain_config(v: float, d: Any) -> str:
    if v <= 0:
        return "no config change on the failing service or a service it talks to"
    where = "the failing service" if v >= 1.0 else "a service the failing service talks to"
    return f"config {d} changed on {where}"


EXPLAIN: dict[str, Callable[[float, Any], str]] = {
    "temporal": _explain_temporal,
    "path": _explain_path,
    "endpoint": _explain_endpoint,
    "service": _explain_service,
    "centrality": _explain_centrality,
    "dependency": _explain_dependency,
    "msg_sim": _explain_msg,
    "history": _explain_history,
    "sequence": _explain_sequence,
    "triggered": _explain_triggered,
    "blast": _explain_blast,
    "config": _explain_config,
}


@dataclass(frozen=True)
class IncidentContext:
    id: str
    service: str
    env: str
    endpoint: str
    onset_ts: int

    @property
    def endpoint_id(self) -> str:
        return f"{self.service}:{self.endpoint}"


def load_incident(store: GraphStore, incident_id: str) -> IncidentContext | None:
    rows = store.read(
        "MATCH (i:Incident {id: $id}) RETURN i.service, i.env, i.endpoint, i.onset_ts",
        {"id": incident_id},
    )
    if not rows or rows[0][3] is None:
        return None
    service, env, endpoint, onset = rows[0]
    return IncidentContext(incident_id, service or "", env or "", endpoint or "", int(onset))


def candidate_deployments(store: GraphStore, inc: IncidentContext, lookback_s: int) -> list[str]:
    """Triggering deployment first, then the lookback window's successful deployments."""
    rows = store.read(DEPLOYMENTS, {"inc": inc.id, "lookback": lookback_s})
    if not rows:
        return []
    triggered, windowed = rows[0]
    out: list[str] = []
    for d in [*sorted(triggered or []), *sorted(windowed or [])]:
        if d not in out:
            out.append(d)
    return out


def candidates(store: GraphStore, inc: IncidentContext, lookback_s: int) -> list[dict[str, Any]]:
    """Every suspect a deployment in scope shipped: commits, config changes, dependency bumps.

    A commit in several deployments keeps the first one (the triggering deployment wins).
    """
    deps = candidate_deployments(store, inc, lookback_s)
    if not deps:
        return []
    order = {d: i for i, d in enumerate(deps)}
    seen: dict[str, dict[str, Any]] = {}
    rows = store.read(CANDIDATES, {"deps": deps})
    for sha, message, ts, author, dep in sorted(
        rows, key=lambda r: (order[r[4]], -(r[2] or 0), r[0])
    ):
        seen.setdefault(
            sha,
            {
                "kind": "commit",
                "sha": sha,
                "message": message or "",
                "ts": ts,
                "author": author,
                "deployment": dep,
            },
        )
    out = sorted(seen.values(), key=lambda c: (-(c["ts"] or 0), c["sha"]))
    for xid, service, key, file, started, dep in store.read(CONFIG_CANDIDATES, {"deps": deps}):
        out.append(
            {
                "kind": "config",
                "sha": xid,
                "message": f"config {key} changed for {service}" + (f" ({file})" if file else ""),
                "ts": started,
                "author": None,
                "deployment": dep,
            }
        )
    for xid, name, old, new, started, dep in store.read(DEPENDENCY_CANDIDATES, {"deps": deps}):
        out.append(
            {
                "kind": "dependency",
                "sha": xid,
                "message": f"dependency {name} {old or '?'} -> {new}",
                "ts": started,
                "author": None,
                "deployment": dep,
            }
        )
    return out


def feature_results(
    store: GraphStore,
    inc: IncidentContext,
    key: str,
    tau: float,
    kind: CandidateKind = "commit",
) -> dict[str, FeatureResult]:
    """All features for one candidate; features without a query for its kind are 0."""
    params = {
        "sha": key,
        "key": key,
        "inc": inc.id,
        "ep": inc.endpoint_id,
        "svc": inc.service,
        "tau": tau,
    }
    queries = KIND_QUERIES[kind]
    out: dict[str, FeatureResult] = {}
    for name in FEATURE_QUERIES:
        cypher = queries.get(name)
        if cypher is None:
            out[name] = FeatureResult(
                value=0.0, evidence_ids=[], explanation=f"does not apply to a {kind} change"
            )
            continue
        rows = store.read(cypher, params)
        raw, ids, detail = rows[0] if rows else (None, [], None)
        value = float(raw) if raw is not None else 0.0  # bool, int and float all coerce
        uniq = list(dict.fromkeys(ids or []))[:EVIDENCE_CAP] if value != 0 else []
        out[name] = FeatureResult(
            value=value,
            evidence_ids=uniq,
            explanation=EXPLAIN[name](value, detail) if rows else EXPLAIN[name](0.0, None),
        )
    return out


def feature_values(
    store: GraphStore, inc: IncidentContext, sha: str, tau: float
) -> dict[str, float]:
    """Values only (kept for callers that do not need the evidence)."""
    return {k: r.value for k, r in feature_results(store, inc, sha, tau).items()}
