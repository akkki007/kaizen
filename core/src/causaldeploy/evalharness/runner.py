"""Runs every method on every scenario variant, each in its own throwaway graph."""

import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from causaldeploy.evalharness.replay import (
    RecordingStore,
    apply_ops,
    read_case,
    recorded_cases,
    write_case,
)
from causaldeploy.evalharness.scenarios import Case, build_case, load_scenarios
from causaldeploy.graph import queries as q
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.store import GraphStore
from causaldeploy.investigate.scorer import (
    RankResult,
    WeightSet,
    load_weights,
    rank_b0,
    rank_b1,
    rank_graph,
)

VARIANTS = (
    "graph",
    "graph -path",
    "graph -history",
    "graph -dependency",
    "b1 bm25",
    "b0 latest",
)
ABLATIONS = {"graph -path": "path", "graph -history": "history", "graph -dependency": "dependency"}
ABSTAIN = ("no_code_evidence", "no_candidates", "ambiguous")


@dataclass
class Outcome:
    case: Case
    method: str
    top_sha: str | None
    verdict: str
    top1: bool
    top3: bool
    function_hit: bool
    false_attribution: bool
    confidence: float
    millis: float
    rank_of_culprit: int | None = None


@dataclass
class CaseRun:
    case: Case
    outcomes: list[Outcome]
    rankings: dict[str, RankResult]
    blast: tuple[str, int, set[str], set[str]] | None = None  # scenario, v, predicted, truth
    memory: tuple[int, float, float] | None = None
    seconds: float = 0.0


@dataclass
class Evaluation:
    outcomes: list[Outcome] = field(default_factory=list)
    blast: list[tuple[str, int, float, float]] = field(default_factory=list)  # scenario, v, P, R
    blast_sets: list[tuple[set[str], set[str]]] = field(default_factory=list)
    # S8: (variant, confidence without the earlier incident, confidence with it)
    memory: list[tuple[int, float, float]] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)
    rankings: dict[tuple[str, int, str], RankResult] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    weights_version: str = ""
    seconds: float = 0.0


def judge(case: Case, method: str, result: RankResult, millis: float) -> Outcome:
    cands = result.candidates
    top = cands[0] if cands else None
    key = case.culprit_id or case.culprit_sha
    rank_of = next((c.rank for c in cands if key and c.sha == key), None)
    if case.code_bug:
        top1 = bool(top and top.sha == case.culprit_sha)
        top3 = any(c.sha == case.culprit_sha for c in cands[:3])
        fn_hit = bool(
            top
            and case.culprit_function
            and any(
                case.culprit_function in (e.changed_function, e.thrown_function)
                for e in top.evidence
            )
        )
        # a wrong confident pick is a false attribution; abstaining is not
        false_attr = bool(top and result.verdict == "confident" and top.sha != case.culprit_sha)
    else:
        # not a code change (S6): right = naming the non-code change, or (when the scenario has
        # none in the graph) abstaining. Blaming a commit with confidence is a false attribution.
        named = bool(top and key and top.sha == key)
        abstained = key is None and result.verdict in ABSTAIN
        top1 = fn_hit = named or abstained
        top3 = abstained or (rank_of is not None and rank_of <= 3)
        false_attr = bool(top and top.kind == "commit" and result.verdict == "confident")
    return Outcome(
        case,
        method,
        top.sha if top else None,
        result.verdict,
        top1,
        top3,
        fn_hit,
        false_attr,
        top.confidence if top else 0.0,
        millis,
        rank_of,
    )


def methods(
    store: GraphStore, incident_id: str, weights: WeightSet
) -> list[tuple[str, Callable[[], RankResult]]]:
    def ablate(feature: str) -> Callable[[], RankResult]:
        w = WeightSet(
            version=f"{weights.version}-{feature}",
            fitted_on=weights.fitted_on,
            weights={**weights.weights, feature: 0.0},
        )
        return lambda: rank_graph(store, incident_id, w)

    out: list[tuple[str, Callable[[], RankResult]]] = [
        ("graph", lambda: rank_graph(store, incident_id, weights))
    ]
    out += [(name, ablate(feat)) for name, feat in ABLATIONS.items()]
    out += [
        ("b1 bm25", lambda: rank_b1(store, incident_id)),
        ("b0 latest", lambda: rank_b0(store, incident_id)),
    ]
    return out


Loader = Callable[[GraphStore], Case]


@dataclass(frozen=True)
class CaseSource:
    scenario: str
    variant: int
    split: str
    load: Loader


def scenario_sources(
    specs: list[dict[str, Any]] | None = None,
    ids: set[str] | None = None,
    split: str = "all",
) -> list[CaseSource]:
    out = []
    for spec in specs if specs is not None else load_scenarios():
        if ids and spec["id"] not in ids:
            continue
        if split != "all" and spec["split"] != split:
            continue
        for v in range(len(spec.get("variants") or [{}])):

            def load(store: GraphStore, spec: dict[str, Any] = spec, v: int = v) -> Case:
                return build_case(GraphBuilder(store), spec, v)

            out.append(CaseSource(spec["id"], v, spec["split"], load))
    return out


def replay_sources(
    directory: Path, ids: set[str] | None = None, split: str = "all"
) -> list[CaseSource]:
    out = []
    for path in recorded_cases(directory):
        case, ops = read_case(path)
        if ids and case.scenario not in ids:
            continue
        if split != "all" and case.split != split:
            continue

        def load(store: GraphStore, case: Case = case, ops: list[Any] = ops) -> Case:
            apply_ops(store, ops)
            return case

        out.append(CaseSource(case.scenario, case.variant, case.split, load))
    return out


def run_case(src: CaseSource, weights: WeightSet, record: Path | None = None) -> CaseRun:
    started = time.perf_counter()
    name = f"eval/{uuid.uuid4().hex[:10]}"
    store: GraphStore = RecordingStore(name) if record else GraphStore(name)
    try:
        case = src.load(store)
        if record and isinstance(store, RecordingStore):
            write_case(record, case, store.ops)
        run = CaseRun(case=case, outcomes=[], rankings={})
        for method, fn in methods(store, case.incident_id, weights):
            t = time.perf_counter()
            res = fn()
            run.outcomes.append(judge(case, method, res, (time.perf_counter() - t) * 1000))
            run.rankings[method] = res
        if case.resembles:
            with_mem = run.rankings["graph"]
            store.graph.query(
                "MATCH (:Incident {id: $i})-[r:resembles]->() DELETE r", {"i": case.incident_id}
            )
            without = rank_graph(store, case.incident_id, weights)
            conf = without.candidates[0].confidence if without.candidates else 0.0
            top = with_mem.candidates[0].confidence if with_mem.candidates else 0.0
            run.memory = (case.variant, conf, top)
        if case.culprit_function and case.affected_services:
            r = q.blast_radius(store, case.culprit_function)
            run.blast = (
                case.scenario,
                case.variant,
                set(r.affected_services),
                set(case.affected_services),
            )
        run.seconds = time.perf_counter() - started
        return run
    finally:
        store.drop()


def run(
    scenarios: list[dict[str, Any]] | None = None,
    *,
    ids: set[str] | None = None,
    split: str = "all",
    jobs: int = 1,
    timeout: float | None = None,
    record: Path | None = None,
    replay: Path | None = None,
    weights: WeightSet | Mapping[str, float] | None = None,
) -> Evaluation:
    """Evaluate all methods. `timeout` (seconds per variant) drops a variant that takes longer
    and lists it under failures; a single graph query is already capped at 2 s by the store."""
    ws = (
        weights
        if isinstance(weights, WeightSet)
        else load_weights()
        if weights is None
        else WeightSet(version="custom", fitted_on="n/a", weights=dict(weights))
    )
    sources = (
        replay_sources(replay, ids, split) if replay else scenario_sources(scenarios, ids, split)
    )
    ev = Evaluation(weights_version=ws.version)
    started = time.perf_counter()

    def one(src: CaseSource) -> CaseRun | str:
        try:
            r = run_case(src, ws, record)
        except Exception as e:  # one broken variant must not hide the others
            return f"{src.scenario} v{src.variant}: {type(e).__name__}: {e}"
        if timeout is not None and r.seconds > timeout:
            return f"{src.scenario} v{src.variant}: took {r.seconds:.1f}s > timeout {timeout}s"
        return r

    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(one, sources))
    else:
        results = [one(s) for s in sources]
    for r in results:  # source order, whatever the completion order
        if isinstance(r, str):
            ev.failures.append(r)
            continue
        ev.cases.append(r.case)
        ev.outcomes.extend(r.outcomes)
        for m, res in r.rankings.items():
            ev.rankings[(r.case.scenario, r.case.variant, m)] = res
        if r.memory:
            ev.memory.append(r.memory)
        if r.blast:
            sid, v, got, want = r.blast
            inter = len(got & want)
            ev.blast.append((sid, v, inter / len(got) if got else 0.0, inter / len(want)))
            ev.blast_sets.append((got, want))
    ev.seconds = time.perf_counter() - started
    return ev
