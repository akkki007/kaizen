"""Weight fitting (#92): coordinate search over the scorer weights on the tune split ONLY.

    python -m causaldeploy.evalharness.fit                 # fit and print, writes nothing
    python -m causaldeploy.evalharness.fit --write v2      # also write weights/v2.json

Held-out (test) cases never reach the fitter: `fit()` raises `SplitLeakError` if one does. The
held-out evaluation of the fitted weights happens afterwards, through the normal runner.

Features are computed once per tune case (graph queries), then re-scored in Python for every
candidate weight vector. A feature that is constant across the candidates of every tune case
(e.g. `triggered` when each case has one deployment) cannot be identified from the tune split
and is frozen at its base weight instead of drifting.
"""

import argparse
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from causaldeploy.evalharness.runner import ABSTAIN, run, scenario_sources
from causaldeploy.evalharness.scenarios import Case
from causaldeploy.graph.store import GraphStore
from causaldeploy.investigate import features as F
from causaldeploy.investigate.scorer import (
    DEFAULT_WEIGHTS_VERSION,
    CandidateRow,
    WeightSet,
    collect_rows,
    load_weights,
    order_rows,
)

TUNE = "tune"
GRID = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40)
PENALTY_GRID = (-0.20, -0.15, -0.10, -0.05, 0.0)
PASSES = 3


class SplitLeakError(RuntimeError):
    """A non-tune case reached the fitter: fitting on held-out data would void the eval."""


@dataclass
class FitCase:
    case: Case
    rows: list[CandidateRow]


@dataclass
class FitResult:
    base: str
    weights: dict[str, float]
    before: tuple[float, ...]
    after: tuple[float, ...]
    frozen: list[str]
    changed: dict[str, tuple[float, float]] = field(default_factory=dict)
    n_cases: int = 0


def guard_tune_only(cases: Sequence[FitCase]) -> None:
    leaked = sorted({f"{c.case.scenario}v{c.case.variant}" for c in cases if c.case.split != TUNE})
    if leaked:
        raise SplitLeakError(f"non-tune cases reached the weight fitter: {', '.join(leaked)}")


def collect_tune_cases(ids: set[str] | None = None) -> list[FitCase]:
    out = []
    for src in scenario_sources(ids=ids, split=TUNE):
        store = GraphStore(f"fit/{uuid.uuid4().hex[:10]}")
        try:
            case = src.load(store)
            out.append(FitCase(case, collect_rows(store, case.incident_id)))
        finally:
            store.drop()
    guard_tune_only(out)
    return out


def _correct(
    case: Case, ordered: Sequence[tuple[CandidateRow, float, float]], verdict: str
) -> bool:
    top = ordered[0][0] if ordered else None
    if case.code_bug:
        return bool(top and top.key == case.culprit_sha)
    key = case.culprit_id
    return bool(top and key and top.key == key) or (key is None and verdict in ABSTAIN)


def objective(cases: Sequence[FitCase], weights: Mapping[str, float]) -> tuple[float, ...]:
    """(top-1 hits - 2 x confident misses, top-3 hits, mean scale-free margin when right)."""
    hits = misses = top3 = 0
    margins = []
    scale = sum(abs(v) for v in weights.values()) or 1.0
    for fc in cases:
        ordered, verdict, margin = order_rows(fc.rows, weights)
        ok = _correct(fc.case, ordered, verdict)
        hits += ok
        misses += (not ok) and verdict == "confident"
        want = fc.case.culprit_sha if fc.case.code_bug else fc.case.culprit_id
        top3 += any(r.key == want for r, _, _ in ordered[:3])
        if ok:
            margins.append(min(margin / scale, 1.0))
    mean_margin = round(sum(margins) / len(margins), 6) if margins else 0.0
    return (hits - 2 * misses, top3, mean_margin)


def identifiable(cases: Sequence[FitCase], names: Sequence[str]) -> set[str]:
    """Features that differ between candidates in at least one case."""
    out = set()
    for name in names:
        for fc in cases:
            vals = {round(r.values.get(name, 0.0), 6) for r in fc.rows}
            if len(vals) > 1:
                out.add(name)
                break
    return out


def fit(cases: Sequence[FitCase], base: WeightSet | None = None) -> FitResult:
    guard_tune_only(cases)  # first, before anything looks at the data
    base = base or load_weights(DEFAULT_WEIGHTS_VERSION)
    names = list(F.FEATURE_QUERIES)
    w = {k: base.weights.get(k, 0.0) for k in names}
    free = [n for n in names if n in identifiable(cases, names)]
    best = objective(cases, w)
    before = best
    for _ in range(PASSES):
        improved = False
        for name in free:
            grid = PENALTY_GRID if base.weights.get(name, 0.0) < 0 else GRID
            for v in grid:
                if v == w[name]:
                    continue
                trial = {**w, name: v}
                score = objective(cases, trial)
                if score > best:  # strict: ties keep the current (base) weight
                    best, w, improved = score, trial, True
        if not improved:
            break
    changed = {
        k: (base.weights.get(k, 0.0), v) for k, v in w.items() if v != base.weights.get(k, 0.0)
    }
    return FitResult(
        base=base.version,
        weights=w,
        before=before,
        after=best,
        frozen=sorted(set(names) - set(free)),
        changed=changed,
        n_cases=len(cases),
    )


def _split_metrics(ws: WeightSet, split: str) -> dict[str, Any]:
    ev = run(split=split, weights=ws)
    xs = [o for o in ev.outcomes if o.method == "graph"]
    n = len(xs) or 1
    return {
        "n": len(xs),
        "top1": round(sum(o.top1 for o in xs) / n, 4),
        "top3": round(sum(o.top3 for o in xs) / n, 4),
        "false_attribution": round(sum(o.false_attribution for o in xs) / n, 4),
    }


def write_weights(version: str, result: FitResult, metrics: dict[str, Any]) -> Path:
    if version in ("v1", result.base):
        raise ValueError("refusing to overwrite the base weights file")
    path = Path(__file__).resolve().parents[1] / "investigate" / "weights" / f"{version}.json"
    doc = {
        "version": version,
        "fitted_on": TUNE,
        "fitted_at": datetime.now(UTC).date().isoformat(),
        "base": result.base,
        "method": "coordinate search, strict improvement, tune split only",
        "frozen": result.frozen,
        "objective": {"before": list(result.before), "after": list(result.after)},
        "metrics": metrics,
        "weights": result.weights,
    }
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="causaldeploy.evalharness.fit")
    ap.add_argument("--base", default=DEFAULT_WEIGHTS_VERSION)
    ap.add_argument("--write", metavar="VERSION", help="write weights/<VERSION>.json")
    ap.add_argument("--report", type=Path, help="also write the result as markdown")
    a = ap.parse_args(argv)
    base = load_weights(a.base)
    result = fit(collect_tune_cases(), base)
    fitted = WeightSet(version=a.write or "fitted", fitted_on=TUNE, weights=result.weights)
    metrics = {
        "base": {s: _split_metrics(base, s) for s in (TUNE, "test")},
        "fitted": {s: _split_metrics(fitted, s) for s in (TUNE, "test")},
    }
    print(f"tune cases: {result.n_cases}; frozen (not identifiable on tune): {result.frozen}")
    print(f"objective {result.before} -> {result.after}; changed: {result.changed or 'nothing'}")
    print(json.dumps(metrics, indent=2))
    if a.write:
        print(f"wrote {write_weights(a.write, result, metrics)}")
    if a.report:
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(render_fit(result, metrics), encoding="utf-8")
    return 0


def render_fit(result: FitResult, metrics: dict[str, Any]) -> str:
    def pct(x: float) -> str:
        return f"{100 * x:.0f}%"

    rows = [
        f"| {name} | {split} | {m['n']} | {pct(m['top1'])} | {pct(m['top3'])} | "
        f"{pct(m['false_attribution'])} |"
        for name, by_split in metrics.items()
        for split, m in by_split.items()
    ]
    base_test = metrics["base"]["test"]["top1"]
    fit_test = metrics["fitted"]["test"]["top1"]
    keep = fit_test < base_test or (
        metrics["fitted"]["test"]["false_attribution"]
        > metrics["base"]["test"]["false_attribution"]
    )
    decision = (
        f"Held-out results are worse with the fitted weights (top-1 {pct(fit_test)} vs "
        f"{pct(base_test)}), so the default stays `{result.base}` and no new weights file is "
        "written. Where the tune split is already saturated, the margin term is all that is "
        "left to optimize; it moves weight onto the few features the tune scenarios exercise "
        "and away from evidence the held-out scenarios need. The held-out split catches that."
        if keep
        else f"Held-out results are not worse (top-1 {pct(fit_test)} vs {pct(base_test)}); the "
        "fitted weights may be written with `--write` and made the default."
    )
    changed = ", ".join(f"`{k}` {a:+.2f} -> {b:+.2f}" for k, (a, b) in result.changed.items())
    return "\n".join(
        [
            "# Weight fitting (tune split only)",
            "",
            f"`python -m causaldeploy.evalharness.fit` from `{result.base}`: coordinate search "
            f"over {len(GRID)} grid values per feature, {PASSES} passes, strict improvement of "
            "(top-1 hits - 2 x confident misses, top-3 hits, scale-free margin) on "
            f"{result.n_cases} tune variants. Test-split cases are rejected by a guard before "
            "the fitter sees any data (`tests/test_fit.py`).",
            "",
            f"Frozen (constant across candidates in every tune case, so not identifiable): "
            f"{', '.join(f'`{f}`' for f in result.frozen) or 'none'}.",
            "",
            f"Objective {result.before} -> {result.after}. Changed: {changed or 'nothing'}.",
            "",
            "| weights | split | n | top-1 | top-3 | false attribution |",
            "|---|---|---|---|---|---|",
            *rows,
            "",
            f"**Decision.** {decision}",
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
