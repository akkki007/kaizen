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
