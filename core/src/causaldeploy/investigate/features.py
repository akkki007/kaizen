"""Ranking features. Every feature is a FalkorDB query (PLAN section 5, Cypher-first).

Python only binds parameters and reads the single value each query returns. Queries are
module-level constants with stable names so the API and UI can show the exact Cypher behind
every number.
"""

from dataclasses import dataclass
from typing import Any

from causaldeploy.graph.store import GraphStore

CANDIDATES = """
MATCH (i:Incident {id: $inc})
OPTIONAL MATCH (d:Deployment)-[:triggers]->(i)
WITH i, collect(DISTINCT d) AS triggered
OPTIONAL MATCH (d2:Deployment {env: i.env})
  WHERE d2.status = 'success' AND d2.started_at <= i.onset_ts
    AND d2.started_at >= i.onset_ts - $lookback
WITH i, CASE WHEN size(triggered) > 0 THEN triggered ELSE collect(DISTINCT d2) END AS deps
UNWIND deps AS d
MATCH (d)-[:includes]->(c:Commit)
OPTIONAL MATCH (c)-[:authored_by]->(dev:Developer)
RETURN DISTINCT c.sha, c.message, c.ts, dev.id, d.id
ORDER BY c.ts DESC, c.sha
""".strip()

FEATURE_QUERIES: dict[str, str] = {
    "temporal": """
MATCH (i:Incident {id: $inc})
MATCH (c:Commit {sha: $sha})<-[:includes]-(d:Deployment)
WITH i, coalesce(d.finished_at, d.started_at) AS deployed
RETURN max(CASE WHEN i.onset_ts < deployed THEN 0.0
                ELSE exp(-(toFloat(i.onset_ts) - deployed) / $tau) END)
""".strip(),
    "path": """
MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)
MATCH (:Incident {id: $inc})-[:evidenced_by]->(:Error)-[:thrown_in]->(g:Function)
WITH f, g,
     shortestPath((f)-[:calls*1..6]->(g)) AS down,
     shortestPath((g)-[:calls*1..6]->(f)) AS up
WITH CASE WHEN f = g THEN 0
          WHEN down IS NOT NULL AND (up IS NULL OR length(down) <= length(up)) THEN length(down)
          WHEN up IS NOT NULL THEN length(up)
          ELSE null END AS d
RETURN 1.0 / (1 + min(d))
""".strip(),
    "endpoint": """
MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)
MATCH (h:Function)-[:handles]->(:Endpoint {id: $ep})
MATCH p = (h)-[:calls*0..4]->(f)
RETURN count(p) > 0
""".strip(),
    "dependency": """
MATCH (c:Commit {sha: $sha})-[:changes_dep]->(:Dependency)<-[:depends_on]-(x:Function)
MATCH (:Incident {id: $inc})-[:evidenced_by]->(:Error)-[:thrown_in]->(g:Function)
MATCH p = (g)-[:calls*0..3]-(x)
RETURN count(p) > 0
""".strip(),
    "msg_sim": """
MATCH (c:Commit {sha: $sha})
OPTIONAL MATCH (c)-[:modifies_fn]->(f:Function)
WITH c, collect(toLower(f.name)) AS fns
MATCH (:Incident {id: $inc})-[:evidenced_by]->(e:Error)
WITH split(toLower(coalesce(c.message, '')), ' ') + fns AS ctoks,
     split(toLower(coalesce(e.message, '') + ' ' + coalesce(e.error_type, '')), ' ') AS etoks
RETURN max(toFloat(size([t IN etoks WHERE t IN ctoks AND size(t) > 3])) / (size(etoks) + 1))
""".strip(),
    "history": """
MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)
OPTIONAL MATCH (:Incident {id: $inc})-[:resembles]->(:Incident {status: 'resolved'})
               -[:likely_caused_by]->(:Commit)-[:modifies_fn]->(f)
WITH f, count(*) AS similar
OPTIONAL MATCH (f)<-[:modifies_fn]-(:Commit)<-[:likely_caused_by]-(past:Incident)
WHERE past.status = 'resolved' AND past.id <> $inc
WITH f, similar, count(DISTINCT past) AS blamed
RETURN max(CASE WHEN similar > 0 THEN 1.0 WHEN blamed > 0 THEN 0.5 ELSE 0.0 END)
""".strip(),
    "sequence": """
MATCH (i:Incident {id: $inc})-[:evidenced_by]->(e:Error)
WITH i, min(e.first_seen) AS first
MATCH (d:Deployment {env: i.env})
WHERE d.status = 'success' AND d.started_at <= first
WITH d ORDER BY d.started_at DESC LIMIT 1
MATCH (d)-[:includes]->(:Commit {sha: $sha})
RETURN 1.0
""".strip(),
    "blast": """
MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)
OPTIONAL MATCH (caller:Function)-[:calls*1..3]->(f)
WITH count(DISTINCT f) AS changed, count(DISTINCT caller) AS callers
RETURN toFloat(changed + callers) / (changed + callers + 10)
""".strip(),
}

# The changed -> thrown function pairs the UI highlights as the evidence path.
EVIDENCE_PAIRS = """
MATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)
MATCH (:Incident {id: $inc})-[:evidenced_by]->(e:Error)-[:thrown_in]->(g:Function)
RETURN DISTINCT f.id, g.id, e.fingerprint
""".strip()


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


def candidates(store: GraphStore, inc: IncidentContext, lookback_s: int) -> list[dict[str, Any]]:
    rows = store.read(CANDIDATES, {"inc": inc.id, "lookback": lookback_s})
    return [
        {"sha": r[0], "message": r[1] or "", "ts": r[2], "author": r[3], "deployment": r[4]}
        for r in rows
    ]


def feature_values(
    store: GraphStore, inc: IncidentContext, sha: str, tau: float
) -> dict[str, float]:
    params = {"sha": sha, "inc": inc.id, "ep": inc.endpoint_id, "tau": tau}
    out: dict[str, float] = {}
    for name, cypher in FEATURE_QUERIES.items():
        rows = store.read(cypher, params)
        raw = rows[0][0] if rows else None
        out[name] = float(raw) if raw is not None else 0.0  # bool, int and float all coerce
    return out
