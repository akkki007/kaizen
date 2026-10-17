"""Worker drain throughput: how fast queued ingest jobs reach `succeeded`.

Run after (or while) `bench_ingest.py` posts events. Polls the `jobs` table of the database in
CD_POSTGRES_DSN until nothing created since --since is queued/running, then prints the
end-to-end processing rate (first job created -> last job finished).

    uv run python scripts/bench_drain.py --timeout 600   # all ingest jobs in the database

Use a dedicated database: the default counts every ingest job in it. Timestamps are the
database server's clock (with Docker Desktop it can drift from the host's after sleep).

Read-only: it never writes to the database.
"""

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime

import asyncpg

QUERY = """
SELECT count(*) FILTER (WHERE status = 'succeeded'),
       count(*) FILTER (WHERE status IN ('queued', 'running')),
       count(*) FILTER (WHERE status IN ('failed', 'dead')),
       min(created_at), max(updated_at) FILTER (WHERE status = 'succeeded')
FROM jobs WHERE kind = 'ingest_event' AND created_at >= $1
"""


async def run(dsn: str, since: datetime, wait_s: float) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        started = time.monotonic()
        deadline = started + wait_s
        done0 = (await conn.fetchrow(QUERY, since))[0]
        while True:
            done, pending, failed, first, last = await conn.fetchrow(QUERY, since)
            if pending == 0 or time.monotonic() > deadline:
                break
            print(f"  {done} done, {pending} pending", flush=True)
            await asyncio.sleep(2)
    finally:
        await conn.close()
    if not done or first is None or last is None:
        print("no succeeded ingest jobs in the window")
        return 1
    window = time.monotonic() - started
    print(f"observed {done - done0} completions in {window:.1f}s = "
          f"{(done - done0) / max(window, 1e-6):.0f} jobs/s while polling")  # fmt: skip
    span = max((last - first).total_seconds(), 1e-6)
    print(f"processed {done} jobs in {span:.1f}s = {done / span:.0f} jobs/s "
          f"(pending {pending}, failed/dead {failed})")  # fmt: skip
    return 0 if pending == 0 and failed == 0 else 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dsn", default="", help="default: CD_POSTGRES_DSN / .env (get_settings)")
    p.add_argument(
        "--since",
        default="1970-01-01T00:00:00+00:00",
        help="ISO timestamp in the database clock; jobs created at/after it count (default: all)",
    )
    p.add_argument("--timeout", type=float, default=600.0)
    a = p.parse_args()
    if not a.dsn:
        from causaldeploy.config import get_settings

        a.dsn = get_settings().postgres_dsn
    since = datetime.fromisoformat(a.since)
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    sys.exit(asyncio.run(run(a.dsn, since, a.timeout)))


if __name__ == "__main__":
    main()
