from causaldeploy.evalharness.report import render
from causaldeploy.evalharness.runner import run


def test_graph_ranking_beats_both_baselines_without_false_attribution() -> None:
    """Regression gate for `make eval`: the claim the product makes, measured on the replay set."""
    ev = run()
    assert len(ev.cases) >= 25

    def top1(m: str) -> float:
        xs = [o for o in ev.outcomes if o.method == m]
        return sum(o.top1 for o in xs) / len(xs)

    graph = top1("graph")
    assert graph >= 0.70 and graph > top1("b1 keywords") and graph > top1("b0 latest")
    wrong = [o for o in ev.outcomes if o.method == "graph" and o.false_attribution]
    assert len(wrong) / len(ev.cases) <= 0.10, [
        f"{o.case.scenario}v{o.case.variant}" for o in wrong
    ]
    # the path feature earns its keep (ablation)
    assert graph > top1("graph -path")
    md = render(ev)
    assert "| graph |" in md and "Per scenario" in md and "Blast radius" in md
