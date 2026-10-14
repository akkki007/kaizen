"""Deterministic, explainable ranking. The LLM never sets a score (PLAN hard rule 3).

Features come from FalkorDB queries (`features.py`); this module only combines them with fixed
weights, normalizes to a confidence, and decides whether to abstain.
"""

import math
import re
from typing import Any, Literal

from pydantic import BaseModel

from causaldeploy.graph.store import Edge, GraphStore
from causaldeploy.investigate import features as F

Method = Literal["graph", "b0", "b1"]

# Initial weights; fitted on the tuning split (S1, S3, S5, S8) by the eval harness (#92).
WEIGHTS: dict[str, float] = {
    "temporal": 0.15,
    "path": 0.30,
    "endpoint": 0.15,
    "dependency": 0.10,
    "msg_sim": 0.10,
    "history": 0.10,
    "sequence": 0.10,
    "blast": -0.10,  # mild penalty so the biggest diff is not blamed by default
}
TAU_SECONDS = 600.0
LOOKBACK_SECONDS = 24 * 3600
SOFTMAX_T = 0.15
AMBIGUOUS_MARGIN = 0.08


class FeatureValue(BaseModel):
    name: str
    value: float
    weight: float
    contribution: float
    query: str  # stable name, e.g. "feature.path"
    cypher: str


class EvidencePair(BaseModel):
    changed_function: str
    thrown_function: str
    error: str


class RankedCandidate(BaseModel):
    sha: str
    message: str
    author: str | None
    deployment: str | None
    rank: int
    score: float
    confidence: float
    features: list[FeatureValue]
    evidence: list[EvidencePair]


class RankResult(BaseModel):
    incident_id: str
    method: Method
    verdict: Literal["confident", "ambiguous", "no_code_evidence", "no_candidates"]
    margin: float
    candidates: list[RankedCandidate]


def _softmax(scores: list[float]) -> list[float]:
    if not scores:
        return []
    hi = max(scores)
    exps = [math.exp((s - hi) / SOFTMAX_T) for s in scores]
    total = sum(exps)
    return [e / total for e in exps]


def _feature_list(values: dict[str, float]) -> list[FeatureValue]:
    return [
        FeatureValue(
            name=name,
            value=round(values[name], 4),
            weight=WEIGHTS[name],
            contribution=round(WEIGHTS[name] * values[name], 4),
            query=f"feature.{name}",
            cypher=F.FEATURE_QUERIES[name],
        )
        for name in WEIGHTS
    ]


def _verdict(
    cands: list[RankedCandidate],
) -> tuple[Literal["confident", "ambiguous", "no_code_evidence", "no_candidates"], float]:
    if not cands:
        return "no_candidates", 0.0
    margin = cands[0].score - cands[1].score if len(cands) > 1 else cands[0].score
    top = {f.name: f.value for f in cands[0].features}
    if not (top["path"] > 0 or top["endpoint"] > 0 or top["dependency"] > 0):
        return "no_code_evidence", margin  # nothing ties the change to the failure: say so
    return ("ambiguous" if margin < AMBIGUOUS_MARGIN else "confident"), margin


def rank_graph(store: GraphStore, incident_id: str) -> RankResult:
    inc = F.load_incident(store, incident_id)
    if inc is None:
        return RankResult(
            incident_id=incident_id,
            method="graph",
            verdict="no_candidates",
            margin=0.0,
            candidates=[],
        )
    rows = F.candidates(store, inc, LOOKBACK_SECONDS)
    scored: list[tuple[float, dict[str, Any], dict[str, float]]] = []
    for c in rows:
        vals = F.feature_values(store, inc, c["sha"], TAU_SECONDS)
        scored.append((sum(WEIGHTS[k] * v for k, v in vals.items()), c, vals))
    scored.sort(key=lambda t: (-t[0], t[1]["sha"]))  # deterministic tie-break
    conf = _softmax([s for s, _, _ in scored])
    cands = []
    for i, ((score, c, vals), p) in enumerate(zip(scored, conf, strict=True), start=1):
        pairs = store.read(F.EVIDENCE_PAIRS, {"sha": c["sha"], "inc": inc.id})
        cands.append(
            RankedCandidate(
                sha=c["sha"],
                message=c["message"],
                author=c["author"],
                deployment=c["deployment"],
                rank=i,
                score=round(score, 4),
                confidence=round(p, 4),
                features=_feature_list(vals),
                evidence=[
                    EvidencePair(changed_function=r[0], thrown_function=r[1], error=r[2])
                    for r in pairs
                ],
            )
        )
    verdict, margin = _verdict(cands)
    return RankResult(
        incident_id=incident_id,
        method="graph",
        verdict=verdict,
        margin=round(margin, 4),
        candidates=cands,
    )


_TOKEN = re.compile(r"[a-z0-9]{4,}")


def _baseline_result(
    incident_id: str, method: Method, ordered: list[dict[str, Any]], scores: list[float]
) -> RankResult:
    cands = [
        RankedCandidate(
            sha=c["sha"],
            message=c["message"],
            author=c["author"],
            deployment=c["deployment"],
            rank=i,
            score=round(s, 4),
            confidence=0.0,
            features=[],
            evidence=[],
        )
        for i, (c, s) in enumerate(zip(ordered, scores, strict=True), start=1)
    ]
    return RankResult(
        incident_id=incident_id,
        method=method,
        verdict="confident" if cands else "no_candidates",
        margin=0.0,
        candidates=cands,
    )


def rank_b0(store: GraphStore, incident_id: str) -> RankResult:
    """Baseline B0: blame the latest commit in the deployment."""
    inc = F.load_incident(store, incident_id)
    rows = F.candidates(store, inc, LOOKBACK_SECONDS) if inc else []
    rows.sort(key=lambda c: (-(c["ts"] or 0), c["sha"]))
    return _baseline_result(
        incident_id, "b0", rows, [1.0 if i == 0 else 0.0 for i in range(len(rows))]
    )


def rank_b1(store: GraphStore, incident_id: str) -> RankResult:
    """Baseline B1: time window + keyword overlap between error text and commit message/files.

    Deliberately uses no graph traversal (no calls, no thrown_in): this is what a log search
    over diffs would do.
    """
    inc = F.load_incident(store, incident_id)
    rows = F.candidates(store, inc, LOOKBACK_SECONDS) if inc else []
    err_rows = store.read(
        "MATCH (:Incident {id: $id})-[:evidenced_by]->(e:Error) "
        "RETURN coalesce(e.message, '') + ' ' + coalesce(e.error_type, '')",
        {"id": incident_id},
    )
    etoks = set(_TOKEN.findall(" ".join(r[0] for r in err_rows).lower()))
    scored = []
    for c in rows:
        files = store.read(
            "MATCH (:Commit {sha: $s})-[:modifies]->(f:File) RETURN f.path", {"s": c["sha"]}
        )
        ctoks = set(_TOKEN.findall((c["message"] + " " + " ".join(r[0] for r in files)).lower()))
        scored.append((len(etoks & ctoks) / (len(etoks) + 1), c))
    scored.sort(key=lambda t: (-t[0], -(t[1]["ts"] or 0), t[1]["sha"]))
    return _baseline_result(incident_id, "b1", [c for _, c in scored], [s for s, _ in scored])


def rank(store: GraphStore, incident_id: str, method: Method = "graph") -> RankResult:
    return {"graph": rank_graph, "b0": rank_b0, "b1": rank_b1}[method](store, incident_id)


def persist(store: GraphStore, result: RankResult) -> None:
    """Idempotent: replaces the incident's candidate edges; top-1 also gets likely_caused_by."""
    if result.method != "graph":
        return
    store.delete_edges("Incident", result.incident_id, "candidate")
    store.delete_edges("Incident", result.incident_id, "likely_caused_by")
    edges = []
    for c in result.candidates:
        props: dict[str, Any] = {"rank": c.rank, "score": c.score, "confidence": c.confidence}
        props.update({f"f_{f.name}": f.value for f in c.features})
        edges.append(Edge("Incident", result.incident_id, "candidate", "Commit", c.sha, props))
    if result.candidates and result.verdict in ("confident", "ambiguous"):
        edges.append(
            Edge(
                "Incident",
                result.incident_id,
                "likely_caused_by",
                "Commit",
                result.candidates[0].sha,
                {"verdict": result.verdict},
            )
        )
    store.upsert_edges(edges)
