"""Golden agent runs (S1-S5) in replay mode: rubric must pass with zero model calls.

Fixtures: tests/fixtures/llm/golden (regenerate with
`python -m causaldeploy.evalharness.llm_golden --mode record-scripted`; live: `make eval-live`).
"""

from pathlib import Path
from typing import Any

import httpx
import pytest

from causaldeploy.agent.loop import Budget
from causaldeploy.agent.models import AgentRun
from causaldeploy.evalharness.llm_golden import SCENARIOS, report, rubric, run_golden
from causaldeploy.graph.builder import Scenario

FIXTURES = Path(__file__).parent / "fixtures" / "llm" / "golden"


async def test_golden_runs_pass_the_rubric_in_replay_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*a: Any, **k: Any) -> Any:
        raise AssertionError("network access in replay mode")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    results = await run_golden("replay", FIXTURES)
    assert (
        [r.scenario for r in results]
        == ["S1", "S2", "S3", "S4", "S5"]
        == [f"S{i}" for i in range(1, len(SCENARIOS) + 1)]
    )
    failed = [r for r in results if not r.passed]
    assert not failed, report(results)
    assert {c.name for c in results[0].checks} == {
        "cites_culprit",
        "every_claim_cited",
        "tokens_within_budget",
        "cost_within_budget",
        "latency_within_budget",
    }


def test_rubric_fails_a_wrong_or_over_budget_run() -> None:
    sc = Scenario("S1", "inc-1", "c0ffee01", [], [])
    run = AgentRun.model_validate(
        {
            "incident_id": "inc-1",
            "status": "explained",
            "explanation": {
                "verdict": "confident",
                "culprit_sha": "n0000001",
                "claims": [{"text": "x", "cited_node_ids": ["Commit:n0000001"]}],
            },
            "steps": [],
            "usage": {"input_tokens": 5000, "output_tokens": 10},
            "cost_usd": 2.0,
        }
    )
    checks = {c.name: c.passed for c in rubric(run, sc, Budget(8, 1000, 0.5))}
    assert checks == {
        "cites_culprit": False,
        "every_claim_cited": True,
        "tokens_within_budget": False,
        "cost_within_budget": False,
        "latency_within_budget": True,
    }
