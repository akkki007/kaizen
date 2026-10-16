"""Weight fitting uses the tune split only; held-out cases are rejected before any fitting."""

import pytest

from causaldeploy.evalharness import fit as fitmod
from causaldeploy.evalharness.fit import FitCase, SplitLeakError, collect_tune_cases, fit
from causaldeploy.evalharness.scenarios import Case
from causaldeploy.investigate.features import FeatureResult
from causaldeploy.investigate.scorer import CandidateRow, WeightSet


def row(sha: str, **values: float) -> CandidateRow:
    return CandidateRow(
        {"sha": sha, "kind": "commit", "message": "", "author": None, "deployment": "d"},
        {k: FeatureResult(value=v, evidence_ids=[], explanation="-") for k, v in values.items()},
    )


def case(split: str, culprit: str = "good", scenario: str = "S1") -> Case:
    return Case(
        scenario=scenario,
        variant=0,
        title="synthetic",
        split=split,
        incident_id="inc",
        culprit_sha=culprit,
        culprit_function=None,
        affected_services=[],
        code_bug=True,
        culprit_id=culprit,
    )


def synthetic(split: str = "tune") -> list[FitCase]:
    # the culprit has stack evidence, the decoy is merely more recent and both were deployed
    return [
        FitCase(
            case(split, scenario=f"S{i}"),
            [
                row("good", path=1.0, temporal=0.2, triggered=1.0),
                row("bad", path=0.0, temporal=1.0, triggered=1.0),
            ],
        )
        for i in (1, 3)
    ]


BASE = WeightSet(
    version="base", fitted_on="tune", weights={"path": 0.05, "temporal": 0.30, "triggered": 0.1}
)


def test_a_test_split_case_never_reaches_the_fitter() -> None:
    leaked = synthetic() + [FitCase(case("test", scenario="S4"), [row("good", path=1.0)])]
    with pytest.raises(SplitLeakError, match="S4v0"):
        fit(leaked, BASE)


def test_guard_runs_before_any_scoring(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object, **__: object) -> tuple[float, ...]:
        raise AssertionError("objective evaluated on leaked data")

    monkeypatch.setattr(fitmod, "objective", boom)
    with pytest.raises(SplitLeakError):
        fit(synthetic("test"), BASE)


def test_fit_improves_tune_accuracy_and_freezes_unidentifiable_features() -> None:
    cases = synthetic()
    assert fitmod.objective(cases, BASE.weights)[0] == 0  # base weights blame the decoy
    r = fit(cases, BASE)
    assert r.after[0] == 2 and r.after > r.before
    assert "triggered" in r.frozen and r.weights["triggered"] == 0.1  # constant: not fitted
    assert r.weights["path"] > BASE.weights["path"] or r.weights["temporal"] < 0.30


def test_fit_keeps_base_weights_on_ties() -> None:
    cases = [FitCase(case("tune"), [row("good", path=1.0), row("bad", path=0.0)])]
    w = WeightSet(version="b", fitted_on="tune", weights={"path": 0.40})
    r = fit(cases, w)
    assert r.changed == {} and r.after == r.before


def test_collect_tune_cases_only_builds_tune_scenarios() -> None:
    got = collect_tune_cases({"S1", "S2"})  # S2 is a held-out scenario: filtered out
    assert {c.case.scenario for c in got} == {"S1"} and len(got) == 3
    assert all(c.case.split == "tune" and c.rows for c in got)


def test_write_refuses_to_overwrite_the_base_weights() -> None:
    r = fit(synthetic(), BASE)
    with pytest.raises(ValueError):
        fitmod.write_weights("v1", r, {})
    with pytest.raises(ValueError):
        fitmod.write_weights("base", r, {})
