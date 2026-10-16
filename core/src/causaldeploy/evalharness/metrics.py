"""Evaluation metrics (#92). Pure functions over judged outcomes; no graph, no I/O.

Every rate returns None for an empty sample instead of pretending 0%. The bootstrap is seeded so
a committed report is reproducible bit for bit.
"""

import random
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol


class Judged(Protocol):
    """What a metric needs from one (case, method) outcome."""

    @property
    def top1(self) -> bool: ...
    @property
    def top3(self) -> bool: ...
    @property
    def function_hit(self) -> bool: ...
    @property
    def false_attribution(self) -> bool: ...
    @property
    def millis(self) -> float: ...


@dataclass(frozen=True)
class Simple:
    """A hand-made outcome (tests, ad-hoc analysis)."""

    top1: bool = False
    top3: bool = False
    function_hit: bool = False
    false_attribution: bool = False
    millis: float = 0.0


def _rate(flags: Iterable[bool]) -> float | None:
    xs = list(flags)
    return sum(xs) / len(xs) if xs else None


def top1(xs: Sequence[Judged]) -> float | None:
    return _rate(o.top1 for o in xs)


def top3(xs: Sequence[Judged]) -> float | None:
    return _rate(o.top3 for o in xs)


def function_level(xs: Sequence[Judged]) -> float | None:
    """Top suspect's evidence names the culprit function (finer than the commit)."""
    return _rate(o.function_hit for o in xs)


def false_attribution(xs: Sequence[Judged]) -> float | None:
    """Share of cases where a wrong suspect was named *confidently* (abstaining is not one)."""
    return _rate(o.false_attribution for o in xs)


def time_to_hypothesis_ms(xs: Sequence[Judged]) -> float | None:
    """Median milliseconds from the start of ranking to a ranked result."""
    return statistics.median(o.millis for o in xs) if xs else None


def blast_precision_recall(
    pairs: Sequence[tuple[set[str], set[str]]],
) -> tuple[float, float] | None:
    """Mean precision and recall of predicted vs. true affected services, one pair per case.

    An empty prediction has precision 0 (it predicted nothing useful), not undefined.
    """
    if not pairs:
        return None
    ps, rs = [], []
    for got, want in pairs:
        inter = len(got & want)
        ps.append(inter / len(got) if got else 0.0)
        rs.append(inter / len(want) if want else 1.0)
    return statistics.fmean(ps), statistics.fmean(rs)


def fix_verification_rate(verified: Sequence[bool | None]) -> float | None:
    """Share of proposed fixes that passed verification; None when no case reports it
    (the replay scenarios carry an expected fix kind, not a verification outcome)."""
    known = [v for v in verified if v is not None]
    return _rate(known) if known else None


def bootstrap_ci(
    values: Sequence[float | bool], n: int = 1000, seed: int = 0, alpha: float = 0.05
) -> tuple[float, float] | None:
    """Percentile bootstrap CI of the mean (seeded: same input, same interval)."""
    if not values:
        return None
    xs = [float(v) for v in values]
    rng = random.Random(seed)
    k = len(xs)
    means = sorted(sum(rng.choice(xs) for _ in range(k)) / k for _ in range(n))
    lo = means[int((alpha / 2) * n)]
    hi = means[min(n - 1, int((1 - alpha / 2) * n))]
    return lo, hi


@dataclass(frozen=True)
class Summary:
    method: str
    n: int
    top1: float | None
    top3: float | None
    function_level: float | None
    false_attribution: float | None
    median_ms: float | None
    top1_ci: tuple[float, float] | None


def summarize(method: str, xs: Sequence[Judged], seed: int = 0) -> Summary:
    return Summary(
        method=method,
        n=len(xs),
        top1=top1(xs),
        top3=top3(xs),
        function_level=function_level(xs),
        false_attribution=false_attribution(xs),
        median_ms=time_to_hypothesis_ms(xs),
        top1_ci=bootstrap_ci([o.top1 for o in xs], seed=seed),
    )
