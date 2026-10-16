"""Ingest throughput benchmark against a *local* Kaizen API.

Posts synthetic telemetry at a target rate in batches and reports the achieved accepted rate,
latency per batch and status codes (429s are counted, not retried).

    uv run python scripts/bench_ingest.py --key cd_... --repo acme/shoplite \
        --rate 2000 --seconds 20 --batch 200

Refuses any URL that is not loopback: never point a load generator at a shared environment.
"""

import argparse
import asyncio
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def make_batch(repo: str, n: int, seq: int) -> list[dict[str, object]]:
    now = datetime.now(UTC).isoformat()
    return [
        {
            "type": "telemetry",
            "repo": repo,
            "service": "bench",
            "env": "bench",
            "ts": now,
            "kind": "request",
            "request_id": f"bench-{seq}-{i}",  # distinct events: no idempotency dedupe
            "endpoint": "GET /bench",
            "status_code": 200,
            "latency_ms": 5.0,
        }
        for i in range(n)
    ]


async def run(url: str, key: str, repo: str, rate: int, seconds: float, batch: int) -> int:
    interval = batch / rate  # seconds between batch launches
    statuses: Counter[int] = Counter()
    latencies: list[float] = []
    accepted = 0
    tasks: list[asyncio.Task[None]] = []

    async with httpx.AsyncClient(base_url=url, headers={"x-api-key": key}, timeout=30) as c:

        async def post(seq: int) -> None:
            nonlocal accepted
            t = time.perf_counter()
            try:
                r = await c.post("/api/v1/events", json=make_batch(repo, batch, seq))
            except httpx.HTTPError:
                statuses[0] += 1
                return
            latencies.append(time.perf_counter() - t)
            statuses[r.status_code] += 1
            if r.status_code == 202:
                accepted += int(r.json()["accepted"])

        start = time.perf_counter()
        seq = 0
        while time.perf_counter() - start < seconds:
            tasks.append(asyncio.create_task(post(seq)))
            seq += 1
            await asyncio.sleep(max(0.0, start + seq * interval - time.perf_counter()))
        await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

    print(f"target   {rate} events/s for {seconds:.0f}s in batches of {batch}")
    print(f"achieved {accepted / elapsed:.0f} accepted events/s ({accepted} in {elapsed:.1f}s)")
    if latencies:
        q = statistics.quantiles(latencies, n=20) if len(latencies) > 1 else latencies * 19
        print(f"batch latency p50 {statistics.median(latencies) * 1000:.0f} ms, "
              f"p95 {q[18] * 1000:.0f} ms")  # fmt: skip
    print("statuses " + ", ".join(f"{k or 'error'}: {v}" for k, v in sorted(statuses.items())))
    return 0 if statuses.get(202) else 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--key", required=True, help="ingest API key for --repo")
    p.add_argument("--repo", required=True)
    p.add_argument("--rate", type=int, default=1000, help="target events per second")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--batch", type=int, default=100, help="events per request")
    a = p.parse_args()
    host = urlsplit(a.url).hostname or ""
    if host.strip("[]") not in LOOPBACK:
        sys.exit(f"refusing to benchmark {host!r}: only localhost targets are allowed")
    if a.rate < 1 or a.batch < 1:
        sys.exit("--rate and --batch must be >= 1")
    sys.exit(asyncio.run(run(a.url, a.key, a.repo, a.rate, a.seconds, a.batch)))


if __name__ == "__main__":
    main()
