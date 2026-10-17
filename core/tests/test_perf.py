"""Performance budget (#92, docs/performance.md): detection -> persisted ranking < 30 s,
excluding the LLM. Same flow as scripts/bench_hypothesis.py (which reports the numbers)."""

import importlib.util
import time
from pathlib import Path
from types import ModuleType

import pytest

from causaldeploy.detect.detector import detect_repo
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.investigate.run import investigate
from causaldeploy.jobs import handlers

BUDGET_S = 30.0


def _bench() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "bench_hypothesis.py"
    spec = importlib.util.spec_from_file_location("bench_hypothesis", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_detection_to_ranked_hypothesis_is_within_budget(
    db: None, builder: GraphBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("causaldeploy.investigate.run._run_agent", _no_agent)
    bench = _bench()
    store = builder.store
    sc = builder.s1_payment_validation()
    store.graph.query(
        "MATCH (d:Deployment {id: 'dep-842'}) SET d.started_at = $t",
        {"t": int(bench.T0.timestamp())},
    )
    for e in bench.s1_events(store.repo):
        await handlers.ingest_event({"event": e})

    started = time.perf_counter()
    opened = await detect_repo(store.repo, bench.NOW)
    result = await investigate(store.repo, opened[0])
    elapsed = time.perf_counter() - started

    assert len(opened) == 1 and result.verdict == "confident"
    assert result.candidates[0].sha == sc.culprit_commit
    assert store.read(
        "MATCH (:Incident {id: $id})-[:likely_caused_by]->(c:Commit) RETURN c.sha",
        {"id": opened[0]},
    ) == [[sc.culprit_commit]]
    assert elapsed < BUDGET_S, f"detection -> hypothesis took {elapsed:.1f}s"


async def _no_agent(*_: object) -> None:
    return None
