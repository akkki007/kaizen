import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from causaldeploy.evalharness.scenarios import Case, build_case, load_scenarios
from causaldeploy.graph import queries as q
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.store import GraphStore
from causaldeploy.investigate.scorer import WEIGHTS, RankResult, rank_b0, rank_b1, rank_graph

VARIANTS = ("graph", "graph -path", "graph -history", "b1 keywords", "b0 latest")


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


@dataclass
class Evaluation:
    outcomes: list[Outcome] = field(default_factory=list)
    blast: list[tuple[str, int, float, float]] = field(default_factory=list)  # scenario, v, P, R
    # S8: (variant, confidence without the earlier incident, confidence with it)
    memory: list[tuple[int, float, float]] = field(default_factory=list)
    cases: list[Case] = field(default_factory=list)


def _judge(case: Case, method: str, result: RankResult, millis: float) -> Outcome:
    cands = result.candidates
    top = cands[0] if cands else None
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
        # not a code change (S6): the right behaviour is to say there is no code evidence
        abstained = result.verdict in ("no_code_evidence", "no_candidates", "ambiguous")
        top1 = top3 = fn_hit = abstained
        false_attr = bool(top and result.verdict == "confident")
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
    )


def _methods(store: GraphStore, incident_id: str) -> list[tuple[str, Callable[[], RankResult]]]:
    return [
        ("graph", lambda: rank_graph(store, incident_id)),
        ("graph -path", lambda: rank_graph(store, incident_id, {**WEIGHTS, "path": 0.0})),
        ("graph -history", lambda: rank_graph(store, incident_id, {**WEIGHTS, "history": 0.0})),
        ("b1 keywords", lambda: rank_b1(store, incident_id)),
        ("b0 latest", lambda: rank_b0(store, incident_id)),
    ]


def run(scenarios: list[dict[str, Any]] | None = None) -> Evaluation:
    ev = Evaluation()
    for spec in scenarios or load_scenarios():
        for v in range(len(spec.get("variants") or [{}])):
            store = GraphStore(f"eval/{uuid.uuid4().hex[:10]}")
            try:
                b = GraphBuilder(store)
                case = build_case(b, spec, v)
                ev.cases.append(case)
                runs = _methods(store, case.incident_id)
                for name, fn in runs:
                    t = time.perf_counter()
                    res = fn()
                    ev.outcomes.append(_judge(case, name, res, (time.perf_counter() - t) * 1000))
                if case.resembles:
                    with_mem = next(o for o in ev.outcomes[-5:] if o.method == "graph")
                    store.graph.query(
                        "MATCH (:Incident {id: $i})-[r:resembles]->() DELETE r",
                        {"i": case.incident_id},
                    )
                    without = rank_graph(store, case.incident_id)
                    conf = without.candidates[0].confidence if without.candidates else 0.0
                    ev.memory.append((v, conf, with_mem.confidence))
                if case.culprit_function and case.affected_services:
                    r = q.blast_radius(store, case.culprit_function)
                    got, want = set(r.affected_services), set(case.affected_services)
                    inter = len(got & want)
                    ev.blast.append(
                        (case.scenario, v, inter / len(got) if got else 0.0, inter / len(want))
                    )
            finally:
                store.drop()
    return ev
