"""Incident similarity (#91): weighted Jaccard over incident signatures -> `resembles` edges.

A signature is a set of elements per category, read from the graph:
  fingerprint   exact error signatures the incident is evidenced by
  error_family  error type + message with quoted values / numbers masked (recurrences of the
                same bug in another service share the family, not the fingerprint)
  thrown_in     functions the errors were thrown in
  changed_fn    functions modified by the culprit (confirmed cause, else the top suspect)
  dependency    dependencies (name, version-free) the culprit changed or the code depends on
  service / endpoint

similarity = sum_k w_k * J_k / sum_k w_k, over categories non-empty on either side, where J_k is
the Jaccard index of the two sets in category k. Edges above the threshold are written both ways
as `(:Incident)-[:resembles {similarity, basis}]->(:Incident)`; `basis` lists the shared elements
("category:value"). The history feature reads these edges, so nothing here is hand-seeded.
"""

import asyncio
import re
from dataclasses import dataclass

from causaldeploy.graph.store import Edge, GraphStore

WEIGHTS: dict[str, float] = {
    "fingerprint": 3.0,
    "error_family": 4.0,  # the strongest recurrence signal: same bug shape, any service
    "thrown_in": 2.0,
    "changed_fn": 2.0,
    "dependency": 1.0,
    "service": 1.0,
    "endpoint": 1.0,
}
MAX_INCIDENTS = 1000

Signature = dict[str, set[str]]


@dataclass(frozen=True)
class Match:
    incident_id: str
    similarity: float
    basis: list[str]


_QUOTED = re.compile(r"""(['"`]).*?\1""")
_NUM = re.compile(r"\b0x[0-9a-f]+\b|\b\d+(\.\d+)?\b")


def error_family(error_type: str | None, message: str | None) -> str:
    msg = _QUOTED.sub("*", (message or "").lower())
    msg = _NUM.sub("#", msg)
    msg = re.sub(r"\s+", " ", msg).strip()[:160]
    return f"{(error_type or '').lower()}:{msg}"


def signature(store: GraphStore, incident_id: str) -> Signature:
    sig: Signature = {k: set() for k in WEIGHTS}
    head = store.read(
        "MATCH (i:Incident {id: $i}) RETURN i.service, i.endpoint", {"i": incident_id}
    )
    if not head:
        return sig
    service, endpoint = head[0]
    if service:
        sig["service"].add(str(service))
    if endpoint and endpoint != "-":
        sig["endpoint"].add(f"{service}:{endpoint}" if service else str(endpoint))
    for fp, etype, msg in store.read(
        "MATCH (:Incident {id: $i})-[:evidenced_by]->(e:Error) "
        "RETURN e.fingerprint, e.error_type, e.message",
        {"i": incident_id},
    ):
        sig["fingerprint"].add(str(fp))
        if etype or msg:
            sig["error_family"].add(error_family(etype, msg))
    for (fn,) in store.read(
        "MATCH (:Incident {id: $i})-[:evidenced_by]->(:Error)-[:thrown_in]->(f:Function) "
        "RETURN DISTINCT f.id",
        {"i": incident_id},
    ):
        sig["thrown_in"].add(str(fn))
    culprit = _culprit(store, incident_id)
    if culprit:
        for (fn,) in store.read(
            "MATCH (:Commit {sha: $s})-[:modifies_fn]->(f:Function) RETURN DISTINCT f.id",
            {"s": culprit},
        ):
            sig["changed_fn"].add(str(fn))
        for name, dep_id in store.read(
            "MATCH (:Commit {sha: $s})-[:changes_dep]->(d:Dependency) RETURN d.name, d.id",
            {"s": culprit},
        ):
            sig["dependency"].add(_dep_name(name, dep_id))
    fns = sig["thrown_in"] | sig["changed_fn"]
    if fns:
        for name, dep_id in store.read(
            "MATCH (f:Function)-[:depends_on]->(d:Dependency) WHERE f.id IN $fns "
            "RETURN DISTINCT d.name, d.id",
            {"fns": sorted(fns)},
        ):
            sig["dependency"].add(_dep_name(name, dep_id))
    return sig


def _dep_name(name: object, dep_id: object) -> str:
    if name:
        return str(name)
    raw = str(dep_id)
    return raw.rsplit("@", 1)[0] if raw.rfind("@") > 0 else raw


def _culprit(store: GraphStore, incident_id: str) -> str | None:
    confirmed = store.read(
        "MATCH (:Incident {id: $i})-[:confirmed_cause]->(c:Commit) RETURN c.sha LIMIT 1",
        {"i": incident_id},
    )
    if confirmed:
        return str(confirmed[0][0])
    likely = store.read(
        "MATCH (:Incident {id: $i})-[r:likely_caused_by]->(c:Commit) "
        "WHERE r.rejected IS NULL OR r.rejected = false RETURN c.sha LIMIT 1",
        {"i": incident_id},
    )
    return str(likely[0][0]) if likely else None


def similarity(a: Signature, b: Signature) -> tuple[float, list[str]]:
    num = den = 0.0
    basis: list[str] = []
    for k, w in WEIGHTS.items():
        sa, sb = a.get(k, set()), b.get(k, set())
        union = sa | sb
        if not union:
            continue
        inter = sa & sb
        num += w * len(inter) / len(union)
        den += w
        basis += [f"{k}:{x}" for x in sorted(inter)]
    return (round(num / den, 4) if den else 0.0), basis


def compute(store: GraphStore, incident_id: str) -> list[Match]:
    """Similarity of `incident_id` to every other incident, best first (all, unthresholded)."""
    mine = signature(store, incident_id)
    others = store.read(
        "MATCH (j:Incident) WHERE j.id <> $i RETURN j.id ORDER BY j.id LIMIT $n",
        {"i": incident_id, "n": MAX_INCIDENTS},
    )
    out = []
    for (other,) in others:
        sim, basis = similarity(mine, signature(store, str(other)))
        if sim > 0:
            out.append(Match(str(other), sim, basis))
    out.sort(key=lambda m: (-m.similarity, m.incident_id))
    return out


def write(store: GraphStore, incident_id: str, threshold: float) -> list[Match]:
    """Recompute and replace this incident's `resembles` edges (both directions)."""
    matches = [m for m in compute(store, incident_id) if m.similarity >= threshold]
    store.graph.query(
        "MATCH (:Incident {id: $i})-[r:resembles]-(:Incident) DELETE r", {"i": incident_id}
    )
    edges = []
    for m in matches:
        props = {"similarity": m.similarity, "basis": m.basis}
        edges.append(Edge("Incident", incident_id, "resembles", "Incident", m.incident_id, props))
        edges.append(Edge("Incident", m.incident_id, "resembles", "Incident", incident_id, props))
    store.upsert_edges(edges)
    return matches


async def update_similarity(
    repo: str, incident_id: str, threshold: float | None = None
) -> list[Match]:
    """Investigation-time (and close-time) entry point. Returns the edges written."""
    from causaldeploy.config import get_settings

    t = get_settings().remediation_similarity_threshold if threshold is None else threshold
    return await asyncio.to_thread(write, GraphStore(repo), incident_id, t)
