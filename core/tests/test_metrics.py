import pytest

from causaldeploy.evalharness import metrics as M
from causaldeploy.evalharness.metrics import Simple


def outcomes() -> list[Simple]:
    return [
        Simple(top1=True, top3=True, function_hit=True, millis=10),
        Simple(top1=True, top3=True, function_hit=False, millis=30),
        Simple(top1=False, top3=True, false_attribution=True, millis=20),
        Simple(top1=False, top3=False, millis=40),
    ]


def test_rates_on_synthetic_outcomes() -> None:
    xs = outcomes()
    assert M.top1(xs) == 0.5
    assert M.top3(xs) == 0.75
    assert M.function_level(xs) == 0.25
    assert M.false_attribution(xs) == 0.25
    assert M.time_to_hypothesis_ms(xs) == 25.0  # median of 10, 20, 30, 40


def test_empty_samples_are_not_zero() -> None:
    assert M.top1([]) is None and M.time_to_hypothesis_ms([]) is None
    assert M.bootstrap_ci([]) is None and M.blast_precision_recall([]) is None


def test_blast_precision_recall() -> None:
    p, r = M.blast_precision_recall(  # type: ignore[misc]
        [({"a", "b"}, {"a"}), ({"a"}, {"a", "b"}), (set(), {"c"})]
    )
    assert p == pytest.approx((0.5 + 1.0 + 0.0) / 3)
    assert r == pytest.approx((1.0 + 0.5 + 0.0) / 3)


def test_fix_verification_rate_is_na_without_data() -> None:
    assert M.fix_verification_rate([None, None]) is None
    assert M.fix_verification_rate([True, False, None, True]) == pytest.approx(2 / 3)


def test_bootstrap_ci_is_seeded_and_brackets_the_mean() -> None:
    xs = [True] * 15 + [False] * 5
    a = M.bootstrap_ci(xs, n=1000, seed=7)
    assert a == M.bootstrap_ci(xs, n=1000, seed=7)  # reproducible
    assert a is not None and a[0] < 0.75 < a[1]
    assert 0.5 < a[0] and a[1] <= 1.0
    assert M.bootstrap_ci([True] * 4) == (1.0, 1.0)  # no variance, no width


def test_summary_bundles_everything() -> None:
    s = M.summarize("graph", outcomes())
    assert (s.method, s.n, s.top1, s.top3) == ("graph", 4, 0.5, 0.75)
    assert s.top1_ci is not None and s.top1_ci[0] <= 0.5 <= s.top1_ci[1]
