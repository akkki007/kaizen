import json
from pathlib import Path

import pytest

from causaldeploy.evalharness.__main__ import main
from causaldeploy.evalharness.report import render, render_html, to_json
from causaldeploy.evalharness.runner import VARIANTS, Evaluation, run

REPLAY_BUDGET_S = 600  # #92: every scenario, replayed offline, in under 10 minutes


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> tuple[Evaluation, Path]:
    d = tmp_path_factory.mktemp("replay")
    return run(record=d, jobs=4), d


def top1(ev: Evaluation, m: str, split: str = "all") -> float:
    xs = [o for o in ev.outcomes if o.method == m and split in ("all", o.case.split)]
    return sum(o.top1 for o in xs) / len(xs)


def test_graph_ranking_beats_both_baselines_without_false_attribution(
    recorded: tuple[Evaluation, Path],
) -> None:
    """Regression gate for `make eval`: the claim the product makes, measured on the replay set."""
    ev, _ = recorded
    assert len(ev.cases) >= 25 and not ev.failures
    graph = top1(ev, "graph")
    assert graph >= 0.70 and graph > top1(ev, "b1 bm25") and graph > top1(ev, "b0 latest")
    # held out: the weights never saw these scenarios
    assert top1(ev, "graph", "test") > max(
        top1(ev, "b1 bm25", "test"), top1(ev, "b0 latest", "test")
    )
    wrong = [o for o in ev.outcomes if o.method == "graph" and o.false_attribution]
    assert len(wrong) / len(ev.cases) <= 0.10, [
        f"{o.case.scenario}v{o.case.variant}" for o in wrong
    ]
    # the path feature earns its keep (ablation)
    assert graph > top1(ev, "graph -path")
    md = render(ev)
    assert "| graph |" in md and "Per scenario" in md and "Blast radius" in md
    assert "graph -dependency" in md and "Known limitations" in md


def test_s6_names_the_config_change_and_baselines_blame_code(
    recorded: tuple[Evaluation, Path],
) -> None:
    ev, _ = recorded
    s6 = [o for o in ev.outcomes if o.case.scenario == "S6"]
    graph = [o for o in s6 if o.method == "graph"]
    assert graph and all(o.top1 and o.rank_of_culprit == 1 for o in graph)
    assert all(o.verdict == "no_code_evidence" for o in graph)
    assert all(not o.top1 for o in s6 if o.method == "b0 latest")


def test_json_report_shape(recorded: tuple[Evaluation, Path]) -> None:
    ev, _ = recorded
    doc = json.loads(json.dumps(to_json(ev)))
    assert set(doc) >= {"generated_at", "weights_version", "methods", "splits", "scenarios"}
    assert doc["weights_version"] == "v1" and doc["methods"] == list(VARIANTS)
    assert set(doc["splits"]) == {"all", "tune", "test"} and doc["failures"] == []
    row = doc["splits"]["all"][0]
    assert set(row) >= {
        "method",
        "top1",
        "top3",
        "function_level",
        "false_attribution",
        "median_ms",
        "top1_ci",
    }
    lo, hi = row["top1_ci"]
    assert lo <= row["top1"] <= hi
    s = doc["scenarios"][0]
    assert set(s) == {"id", "variant", "title", "split", "culprit", "results"}
    assert set(s["results"]["graph"]) == {"top", "rank_of_culprit", "verdict", "correct"}
    html = render_html(ev)
    assert html.startswith("<!doctype html>") and "<svg" in html and "graph -path" in html


def test_replay_reproduces_identical_rankings_offline_in_budget(
    recorded: tuple[Evaluation, Path],
) -> None:
    ev, d = recorded
    files = sorted(d.glob("*.jsonl"))
    assert len(files) == len(ev.cases)
    replayed = run(replay=d, jobs=4)
    assert replayed.seconds < REPLAY_BUDGET_S
    assert not replayed.failures and len(replayed.cases) == len(ev.cases)
    assert replayed.rankings.keys() == ev.rankings.keys()
    for k, r in ev.rankings.items():
        assert replayed.rankings[k].model_dump() == r.model_dump(), k
    assert [(o.case.scenario, o.method, o.top1) for o in replayed.outcomes] == [
        (o.case.scenario, o.method, o.top1) for o in ev.outcomes
    ]


def test_cli_filters_and_writes_md_json_html(tmp_path: Path) -> None:
    out = tmp_path / "res" / "latest.md"
    code = main(["--scenarios", "S1,S2", "--split", "tune", "--out", str(out), "--quiet"])
    assert code == 0
    doc = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert {s["id"] for s in doc["scenarios"]} == {"S1"}  # S2 is test split
    assert out.with_suffix(".html").read_text(encoding="utf-8").count("<svg") >= 1
    assert "S1" in out.read_text(encoding="utf-8")


def test_timeout_turns_a_slow_variant_into_a_listed_failure() -> None:
    ev = run(ids={"S3"}, timeout=0.0)
    assert ev.cases == [] and ev.failures and "timeout" in ev.failures[0]
