"""Detection -> hypothesis latency (PLAN.md budget: < 30 s, excluding the LLM).

For each run: build an S1-shaped graph (ShopLite payments, culprit commit, deployment) in a
throwaway graph `perf/hyp-<id>`, ingest 14 x 30 s windows of telemetry through the real ingest
handler (the last two windows fail), then time

  detect   find_anomalies + open the incident (Postgres + graph)       `detect_repo`
  rank     similarity pass, candidate ranking, persisted `candidate` /
           `likely_caused_by` edges and the investigations row           `investigate`

with the agent (LLM) disabled. Queue wait is excluded: the detector cron runs every 30 s and
the investigation job starts as soon as a worker polls (<= 0.5 s by default).

    CD_POSTGRES_DSN=postgresql://...@localhost:5433/causaldeploy_perf \
      uv run python scripts/bench_hypothesis.py --runs 5

Use a scratch database: it writes telemetry, incidents and jobs rows. Refuses CD_ENV=prod.
"""

import argparse
import asyncio
import os
import statistics
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 14, 12, 7, 40, tzinfo=UTC)  # windows through 12:07:00 are complete


def s1_events(repo: str) -> list[dict[str, Any]]:
    """20 requests per 30 s window; windows 12-13 return 70% HTTP 500 plus a stack trace."""
    events: list[dict[str, Any]] = []
    for w in range(14):
        failing = w >= 12
        for i in range(20):
            events.append(
                {
                    "type": "telemetry",
                    "repo": repo,
                    "service": "payments",
                    "env": "prod",
                    "ts": (T0 + timedelta(seconds=30 * w + i)).isoformat(),
                    "kind": "request",
                    "endpoint": "POST /payments",
                    "status_code": 500 if failing and i < 14 else 200,
                    "latency_ms": 40.0,
                }
            )
        if failing:
            events.append(
                {
                    "type": "telemetry",
                    "repo": repo,
                    "service": "payments",
                    "env": "prod",
                    "ts": (T0 + timedelta(seconds=30 * w)).isoformat(),
                    "kind": "error",
                    "endpoint": "POST /payments",
                    "error_type": "TypeError",
                    "message": "amount.toFixed is not a function",
                    "stack": ["validatePayment (/app/payments/src/payment/validation.js:1:1)"],
                }
            )
    return events


async def one_run() -> dict[str, float | str]:
    from causaldeploy.detect.detector import detect_repo
    from causaldeploy.graph.builder import GraphBuilder
    from causaldeploy.graph.store import GraphStore
    from causaldeploy.investigate.run import investigate
    from causaldeploy.jobs import handlers

    store = GraphStore(f"perf/hyp-{uuid.uuid4().hex[:8]}")
    try:
        builder = GraphBuilder(store)
        sc = builder.s1_payment_validation()
        store.graph.query(
            "MATCH (d:Deployment {id: 'dep-842'}) SET d.started_at = $t",
            {"t": int(T0.timestamp())},
        )
        events = s1_events(store.repo)
        t = time.perf_counter()
        for e in events:
            await handlers.ingest_event({"event": e})
        ingest_s = time.perf_counter() - t

        t = time.perf_counter()
        opened = await detect_repo(store.repo, NOW)
        detect_s = time.perf_counter() - t
        if len(opened) != 1:
            raise RuntimeError(f"expected one incident, got {opened}")

        t = time.perf_counter()
        result = await investigate(store.repo, opened[0])
        rank_s = time.perf_counter() - t
        top = result.candidates[0].sha if result.candidates else ""
        return {
            "events": len(events),
            "ingest_s": ingest_s,
            "detect_s": detect_s,
            "rank_s": rank_s,
            "total_s": detect_s + rank_s,
            "verdict": result.verdict,
            "culprit_first": "yes" if top == sc.culprit_commit else "no",
        }
    finally:
        store.drop()


def _fmt(xs: list[float]) -> str:
    return f"p50 {statistics.median(xs) * 1000:.0f} ms, max {max(xs) * 1000:.0f} ms"


async def main(runs: int) -> int:
    from causaldeploy.db.session import get_engine

    results = []
    try:
        for i in range(runs):
            r = await one_run()
            results.append(r)
            print(
                f"run {i + 1}: detect {float(r['detect_s']) * 1000:.0f} ms, "
                f"rank+persist {float(r['rank_s']) * 1000:.0f} ms, "
                f"total {float(r['total_s']) * 1000:.0f} ms, verdict {r['verdict']}, "
                f"culprit first: {r['culprit_first']}"
            )
    finally:
        await get_engine().dispose()
    print(f"detect            {_fmt([float(r['detect_s']) for r in results])}")
    print(f"rank + persist    {_fmt([float(r['rank_s']) for r in results])}")
    print(f"detection->ranked {_fmt([float(r['total_s']) for r in results])} (budget 30000 ms)")
    ingest = [float(r["ingest_s"]) / int(r["events"]) for r in results]
    print(f"(ingest handler, for reference: {statistics.median(ingest) * 1000:.1f} ms/event)")
    return 0 if max(float(r["total_s"]) for r in results) < 30 else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--runs", type=int, default=5)
    a = p.parse_args()
    if os.environ.get("CD_ENV") == "prod":
        sys.exit("refusing to run against CD_ENV=prod")
    if not os.environ.get("CD_POSTGRES_DSN"):
        sys.exit("set CD_POSTGRES_DSN to a scratch database")
    # The agent must not run (no key, no replay): the budget excludes the LLM.
    os.environ["CD_OPENAI_API_KEY"] = ""
    os.environ["CD_LLM_REPLAY_MODE"] = "off"
    sys.exit(asyncio.run(main(a.runs)))
