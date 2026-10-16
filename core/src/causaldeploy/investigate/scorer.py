"""Deterministic, explainable ranking. The LLM never sets a score (PLAN hard rule 3).

Features come from FalkorDB queries (`features.py`); this module only combines them with a
versioned weight set (`weights/<version>.json`), normalizes to a confidence, and decides whether
to abstain.
"""

import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, Field

from causaldeploy.config import get_settings
from causaldeploy.graph.store import Edge, GraphStore
from causaldeploy.investigate import features as F
from causaldeploy.investigate.features import CandidateKind, FeatureResult

Method = Literal["graph", "b0", "b1"]
Verdict = Literal["confident", "ambiguous", "no_code_evidence", "no_candidates"]

DEFAULT_WEIGHTS_VERSION = "v1"
TAU_SECONDS = 600.0
SOFTMAX_T = 0.15
AMBIGUOUS_MARGIN = 0.08
AMBIGUOUS_KEEP = 3  # an ambiguous verdict persists only the top suspects


class WeightSet(BaseModel):
    """One versioned weights file. Extra keys (metrics, date, notes) are kept, not interpreted."""

    model_config = {"extra": "allow"}

    version: str
    fitted_on: str
    weights: dict[str, float]


def _weights_dir() -> Any:
    return resources.files("causaldeploy.investigate").joinpath("weights")


def weight_versions() -> list[str]:
    names = [p.name[: -len(".json")] for p in _weights_dir().iterdir() if p.name.endswith(".json")]
    return sorted(names, key=lambda v: (len(v), v))


@lru_cache
def load_weights(version: str = DEFAULT_WEIGHTS_VERSION) -> WeightSet:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", version):
        raise ValueError(f"bad weights version {version!r}")
    path = _weights_dir().joinpath(f"{version}.json")
    ws = WeightSet.model_validate(json.loads(path.read_text(encoding="utf-8")))
    unknown = set(ws.weights) - set(F.FEATURE_QUERIES)
    if unknown:
        raise ValueError(f"weights {version} name unknown features: {sorted(unknown)}")
    if ws.version != version:
        raise ValueError(f"weights file {version}.json declares version {ws.version}")
    return ws


# Backward compatible module constant: the default weight set.
WEIGHTS: dict[str, float] = {k: load_weights().weights.get(k, 0.0) for k in F.FEATURE_QUERIES}


def lookback_seconds() -> int:
    return get_settings().lookback_hours * 3600


class FeatureValue(BaseModel):
    name: str
    value: float
    weight: float
    contribution: float
    query: str  # stable name, e.g. "feature.path"
    cypher: str
    evidence_ids: list[str] = Field(default_factory=list)  # "<Label>:<key>" nodes behind value
    explanation: str = ""


class EvidencePair(BaseModel):
    changed_function: str
    thrown_function: str
    error: str


class RankedCandidate(BaseModel):
    sha: str  # commit sha; for a config/dependency change, its node key
    message: str
    author: str | None
    deployment: str | None
    rank: int
    score: float
    confidence: float
    features: list[FeatureValue]
    evidence: list[EvidencePair]
    kind: CandidateKind = "commit"
    node_id: str = ""  # stable "<Label>:<key>", e.g. "Commit:abc" or "ConfigChange:orders:X@d"


class RankResult(BaseModel):
    incident_id: str
    method: Method
    verdict: Verdict
    margin: float
    candidates: list[RankedCandidate]
    weights_version: str = ""


@dataclass
class CandidateRow:
    """A candidate with its feature results: everything the pure scoring step needs."""

    cand: dict[str, Any]
    results: dict[str, FeatureResult]

    @property
    def key(self) -> str:
        return str(self.cand["sha"])

    @property
    def kind(self) -> CandidateKind:
        k: CandidateKind = self.cand.get("kind", "commit")
        return k

    @property
    def values(self) -> dict[str, float]:
        return {k: r.value for k, r in self.results.items()}


def _softmax(scores: list[float]) -> list[float]:
    if not scores:
        return []
    hi = max(scores)
    exps = [math.exp((s - hi) / SOFTMAX_T) for s in scores]
    total = sum(exps)
    return [e / total for e in exps]


def score(values: Mapping[str, float], weights: Mapping[str, float]) -> float:
    return sum(weights.get(k, 0.0) * v for k, v in values.items())


def verdict_for(
    top_kind: CandidateKind | None, top: Mapping[str, float] | None, margin: float
) -> Verdict:
    if top is None or top_kind is None:
        return "no_candidates"
    if top_kind != "commit":
        # the leading suspect is not a code change: there is no commit to blame or revert
        return "no_code_evidence"
    if not (top.get("path", 0) > 0 or top.get("endpoint", 0) > 0 or top.get("dependency", 0) > 0):
        return "no_code_evidence"  # nothing ties the change to the failure: say so
    # Confident needs a stack-level link (a thrown function reached from the change, or a changed
    # dependency); an endpoint/service-level match alone is not enough to name a culprit.
    grounded = top.get("path", 0) > 0 or top.get("dependency", 0) > 0
    return "confident" if grounded and margin >= AMBIGUOUS_MARGIN else "ambiguous"


def order_rows(
    rows: Sequence[CandidateRow], weights: Mapping[str, float]
) -> tuple[list[tuple[CandidateRow, float, float]], Verdict, float]:
    """Pure scoring step: (row, score, confidence) best first, the verdict and the margin."""
    scored = sorted(((score(r.values, weights), r) for r in rows), key=lambda t: (-t[0], t[1].key))
    conf = _softmax([s for s, _ in scored])
    ordered = [(r, s, p) for (s, r), p in zip(scored, conf, strict=True)]
    if not ordered:
        return [], "no_candidates", 0.0
    margin = ordered[0][1] - ordered[1][1] if len(ordered) > 1 else ordered[0][1]
    top = ordered[0][0]
    return ordered, verdict_for(top.kind, top.values, margin), margin


def collect_rows(store: GraphStore, incident_id: str) -> list[CandidateRow]:
    """Candidates in scope with every feature computed (the expensive, graph-bound step)."""
    inc = F.load_incident(store, incident_id)
    if inc is None:
        return []
    return [
        CandidateRow(c, F.feature_results(store, inc, c["sha"], TAU_SECONDS, c["kind"]))
        for c in F.candidates(store, inc, lookback_seconds())
    ]


def _resolve(weights: Mapping[str, float] | WeightSet | None) -> tuple[dict[str, float], str]:
    if weights is None:
        ws = load_weights()
        return dict(ws.weights), ws.version
    if isinstance(weights, WeightSet):
        return dict(weights.weights), weights.version
    return dict(weights), "custom"


def _feature_list(
    kind: CandidateKind, results: dict[str, FeatureResult], weights: Mapping[str, float]
) -> list[FeatureValue]:
    queries = F.KIND_QUERIES[kind]
    return [
        FeatureValue(
            name=name,
            value=round(results[name].value, 4),
            weight=weights.get(name, 0.0),
            contribution=round(weights.get(name, 0.0) * results[name].value, 4),
            query=f"feature.{name}",
            cypher=queries.get(name, ""),
            evidence_ids=results[name].evidence_ids,
            explanation=results[name].explanation,
        )
        for name in F.FEATURE_QUERIES
    ]


def rank_graph(
    store: GraphStore,
    incident_id: str,
    weights: Mapping[str, float] | WeightSet | None = None,
    rows: Sequence[CandidateRow] | None = None,
) -> RankResult:
    w, version = _resolve(weights)
    if rows is None:
        rows = collect_rows(store, incident_id)
    ordered, verdict, margin = order_rows(rows, w)
    cands = []
    for i, (row, s, p) in enumerate(ordered, start=1):
        c = row.cand
        pairs = (
            store.read(F.EVIDENCE_PAIRS, {"sha": row.key, "inc": incident_id})
            if row.kind == "commit"
            else []
        )
        cands.append(
            RankedCandidate(
                sha=row.key,
                message=c["message"],
                author=c["author"],
                deployment=c["deployment"],
                rank=i,
                score=round(s, 4),
                confidence=round(p, 4),
                features=_feature_list(row.kind, row.results, w),
                evidence=[
                    EvidencePair(changed_function=r[0], thrown_function=r[1], error=r[2])
                    for r in pairs
                ],
                kind=row.kind,
                node_id=f"{F.KIND_LABEL[row.kind]}:{row.key}",
            )
        )
    return RankResult(
        incident_id=incident_id,
        method="graph",
        verdict=verdict,
        margin=round(margin, 4),
        candidates=cands,
        weights_version=version,
    )


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
            kind=c.get("kind", "commit"),
            node_id=f"{F.KIND_LABEL[c.get('kind', 'commit')]}:{c['sha']}",
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


def _baseline_candidates(store: GraphStore, incident_id: str) -> list[dict[str, Any]]:
    inc = F.load_incident(store, incident_id)
    return F.candidates(store, inc, lookback_seconds()) if inc else []


def rank_b0(store: GraphStore, incident_id: str) -> RankResult:
    """Baseline B0: blame the latest commit in scope (non-code changes rank after commits)."""
    rows = _baseline_candidates(store, incident_id)
    rows.sort(key=lambda c: (c["kind"] != "commit", -(c["ts"] or 0), c["sha"]))
    return _baseline_result(
        incident_id, "b0", rows, [1.0 if i == 0 else 0.0 for i in range(len(rows))]
    )


_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_STOP = frozenset({"the", "and", "for", "with", "from", "into", "src", "lib", "services", "js"})
BM25_K1, BM25_B = 1.2, 0.75


def tokens(text: str) -> list[str]:
    """Words of identifiers, paths and prose: `validatePayment` -> validate, payment."""
    out = []
    for word in _WORD.findall(text):
        for part in _CAMEL.split(word):
            t = part.lower()
            if len(t) >= 3 and t not in _STOP:
                out.append(t)
    return out


def bm25(query: list[str], docs: list[list[str]]) -> list[float]:
    """Okapi BM25 of one query against each doc; IDF is computed over `docs` (the candidates)."""
    n = len(docs)
    if not n:
        return []
    avgdl = sum(len(d) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in set(d))
    terms = list(dict.fromkeys(query))
    out = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for t in terms:
            if not tf[t]:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            norm = tf[t] + BM25_K1 * (1 - BM25_B + BM25_B * len(d) / avgdl)
            s += idf * tf[t] * (BM25_K1 + 1) / norm
        out.append(s)
    return out


def _candidate_text(store: GraphStore, c: dict[str, Any]) -> str:
    if c["kind"] == "commit":
        files = store.read(
            "MATCH (:Commit {sha: $s})-[:modifies]->(f:File) RETURN coalesce(f.path, f.id)",
            {"s": c["sha"]},
        )
        fns = store.read(
            "MATCH (:Commit {sha: $s})-[:modifies_fn]->(f:Function) "
            "RETURN coalesce(f.name, ''), coalesce(f.path, '')",
            {"s": c["sha"]},
        )
        parts = [c["message"], *(r[0] for r in files), *(f"{r[0]} {r[1]}" for r in fns)]
        return " ".join(parts)
    return str(c["message"])


def rank_b1(store: GraphStore, incident_id: str) -> RankResult:
    """Baseline B1: BM25 between the error text and each candidate's message, changed file paths
    and changed function names, with IDF over the candidate set.

    Deliberately uses no graph traversal (no calls, no thrown_in): this is what a log search
    over diffs would do.
    """
    rows = _baseline_candidates(store, incident_id)
    err_rows = store.read(
        "MATCH (:Incident {id: $id})-[:evidenced_by]->(e:Error) "
        "RETURN coalesce(e.message, '') + ' ' + coalesce(e.error_type, '')",
        {"id": incident_id},
    )
    query = tokens(" ".join(r[0] for r in err_rows))
    scores = bm25(query, [tokens(_candidate_text(store, c)) for c in rows])
    scored = sorted(
        zip(scores, rows, strict=True), key=lambda t: (-t[0], -(t[1]["ts"] or 0), t[1]["sha"])
    )
    return _baseline_result(incident_id, "b1", [c for _, c in scored], [s for s, _ in scored])


def rank(store: GraphStore, incident_id: str, method: Method = "graph") -> RankResult:
    return {"graph": rank_graph, "b0": rank_b0, "b1": rank_b1}[method](store, incident_id)


def persist(store: GraphStore, result: RankResult) -> None:
    """Idempotent: replaces the incident's candidate edges and its likely cause.

    Confident: every candidate is kept and the top commit gets `likely_caused_by`.
    Ambiguous: only the top three are kept and `likely_caused_by` is marked `tentative`.
    Abstentions keep the candidates (for the UI) but never name a cause.
    """
    if result.method != "graph":
        return
    store.delete_edges("Incident", result.incident_id, "candidate")
    store.delete_edges("Incident", result.incident_id, "likely_caused_by")
    kept = (
        result.candidates[:AMBIGUOUS_KEEP] if result.verdict == "ambiguous" else result.candidates
    )
    edges = []
    for c in kept:
        props: dict[str, Any] = {
            "rank": c.rank,
            "score": c.score,
            "confidence": c.confidence,
            "kind": c.kind,
            "weights_version": result.weights_version,
            "verdict": result.verdict,
        }
        props.update({f"f_{f.name}": f.value for f in c.features})
        label = F.KIND_LABEL[c.kind]
        edges.append(Edge("Incident", result.incident_id, "candidate", label, c.sha, props))
    top = result.candidates[0] if result.candidates else None
    if top and top.kind == "commit" and result.verdict in ("confident", "ambiguous"):
        edges.append(
            Edge(
                "Incident",
                result.incident_id,
                "likely_caused_by",
                "Commit",
                top.sha,
                {
                    "verdict": result.verdict,
                    "tentative": result.verdict == "ambiguous",
                    "weights_version": result.weights_version,
                },
            )
        )
    store.upsert_edges(edges)
