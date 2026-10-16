import sys
from unittest.mock import AsyncMock

import pytest
from arq import Retry
from sqlalchemy import select

from causaldeploy.db.models import JobRecord
from causaldeploy.db.session import session_scope
from causaldeploy.jobs import handlers
from causaldeploy.jobs.queue import IngestEventPayload, enqueue
from causaldeploy.jobs.worker import run_job

EVENT = {
    "type": "commit",
    "repo": "x/y",
    "sha": "abcdef123",
    "author": "a",
    "ts": "2026-10-09T10:00:00Z",
    "message": "m",
}


async def status(job_id: str) -> tuple[str, int]:
    async with session_scope() as s:
        r = (await s.execute(select(JobRecord).where(JobRecord.id == job_id))).scalar_one()
        return r.status, r.attempts


async def test_duplicate_enqueue_is_one_job(db: None) -> None:
    pool = AsyncMock()
    p = IngestEventPayload(event=EVENT)
    a, created_a = await enqueue("ingest_event", p, "k1", pool)
    b, created_b = await enqueue("ingest_event", p, "k1", pool)
    assert a == b and created_a and not created_b
    pool.enqueue_job.assert_awaited_once()


async def test_failure_retries_then_succeeds_without_duplicate_effect(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def flaky(_: dict) -> None:  # type: ignore[type-arg]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("worker died mid-job")

    monkeypatch.setitem(handlers.HANDLERS, "ingest_event", flaky)
    job_id, _ = await enqueue("ingest_event", IngestEventPayload(event=EVENT), "k2", AsyncMock())
    with pytest.raises(Retry):
        await run_job({}, job_id)
    assert await status(job_id) == ("queued", 1)
    assert await run_job({}, job_id) == "ok"
    assert await status(job_id) == ("succeeded", 2)
    assert await run_job({}, job_id) == "duplicate"  # redelivery after success
    assert calls == 2


async def test_job_goes_dead_after_max_attempts(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    async def always(_: dict) -> None:  # type: ignore[type-arg]
        raise ValueError("nope")

    monkeypatch.setitem(handlers.HANDLERS, "ingest_event", always)
    job_id, _ = await enqueue("ingest_event", IngestEventPayload(event=EVENT), "k3", AsyncMock())
    results = []
    for _ in range(5):
        try:
            results.append(await run_job({}, job_id))
        except Retry:
            results.append("retry")
    assert results == ["retry"] * 4 + ["dead"]
    assert (await status(job_id))[0] == "dead"


async def test_ingest_handler_writes_graph(db: None, store) -> None:  # type: ignore[no-untyped-def]
    from causaldeploy.graph.store import GraphStore

    ev = {**EVENT, "repo": store.repo}
    await handlers.ingest_event({"event": ev})
    assert GraphStore(store.repo).read("MATCH (c:Commit) RETURN c.sha") == [["abcdef123"]]


@pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL is POSIX-only")
async def test_sigkilled_worker_job_is_retried_exactly_once(db: None, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Kill -9 a worker mid-job: another worker finishes it; the effect completes once."""
    import asyncio
    import os
    import signal
    import subprocess
    import sys
    import time

    from arq import create_pool

    from causaldeploy.jobs.queue import redis_settings

    probe = tmp_path / "probe.log"
    env = {**os.environ, "PROBE_FILE": str(probe), "PYTHONPATH": "."}
    cmd = [sys.executable, "-m", "arq", "tests.kill_worker_settings.WorkerSettings"]

    pool = await create_pool(redis_settings())
    await pool.flushdb()
    job_id, _ = await enqueue("ingest_event", IngestEventPayload(event=EVENT), "kill-1", pool)

    first = subprocess.Popen(
        cmd, env={**env, "PROBE_SLEEP": "60"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not (probe.exists() and "start" in probe.read_text()):
            await asyncio.sleep(0.2)
        assert probe.exists(), "first worker never started the job"
        first.send_signal(signal.SIGKILL)  # no graceful shutdown, no cleanup
        first.wait()
    finally:
        if first.poll() is None:
            first.kill()

    second = subprocess.Popen(
        cmd, env={**env, "PROBE_SLEEP": "0"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        deadline = time.time() + 60
        state = ("", 0)
        while time.time() < deadline:
            state = await status(job_id)
            if state[0] == "succeeded":
                break
            await asyncio.sleep(1)
    finally:
        second.terminate()
        second.wait(timeout=15)
        await pool.flushdb()
        await pool.aclose()
    assert state == ("succeeded", 2), state
    assert probe.read_text().split() == [
        "start",
        "start",
        "done",
    ]  # one completion, no duplicate effect
