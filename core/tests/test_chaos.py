"""Chaos checks (#92, docs/performance.md): worker crash, FalkorDB unavailable, duplicate
webhooks. Nothing here restarts a real container: FalkorDB outages are simulated with a client
pointed at a closed port, which raises the same redis ConnectionError a restart produces."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from arq import Retry
from falkordb import FalkorDB
from sqlalchemy import func, select

from causaldeploy.api.main import create_app
from causaldeploy.db.models import JobRecord, RawEvent, Telemetry
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import GraphStore
from causaldeploy.jobs import handlers
from causaldeploy.jobs.queue import IngestEventPayload, enqueue
from causaldeploy.jobs.worker import run_job
from tests.test_api import sign
from tests.test_jobs import EVENT, status


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def falkordb_down_once(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[int]]:
    """The ingest handler's first GraphStore talks to a dead FalkorDB; later ones are healthy."""
    port = _closed_port()
    calls: list[int] = []

    class DeadGraph:
        """Every graph command fails with the error a FalkorDB restart produces."""

        def __getattr__(self, name: str) -> Any:
            def fail(*_: Any, **__: Any) -> None:
                # a real refused connection, not a hand-made exception
                FalkorDB(host="127.0.0.1", port=port)

            return fail

    def store(repo: str) -> GraphStore:
        calls.append(1)
        s = GraphStore(repo)
        if len(calls) == 1:  # the server goes away after the job started
            s.graph = DeadGraph()  # type: ignore[assignment]
        return s

    monkeypatch.setattr(handlers, "GraphStore", store)
    yield calls


async def _run_until_done(job_id: str) -> list[str]:
    outcomes = []
    for _ in range(3):
        try:
            outcomes.append(await run_job({}, job_id))
        except Retry:
            outcomes.append("retry")
        if outcomes[-1] == "ok":
            break
    return outcomes


async def test_falkordb_unavailable_job_is_retried_without_duplicate_graph_effects(
    db: None, store: GraphStore, falkordb_down_once: list[int]
) -> None:
    deployment = {
        "type": "deployment",
        "repo": store.repo,
        "id": "dep-chaos",
        "env": "prod",
        "sha_to": EVENT["sha"],
        "started_at": "2026-10-09T10:00:00Z",
        "finished_at": "2026-10-09T10:02:00Z",
        "status": "success",
    }
    commit = {**EVENT, "repo": store.repo}
    ids = []
    for i, ev in enumerate((commit, deployment)):
        jid, _ = await enqueue(
            "ingest_event", IngestEventPayload(event=ev), f"chaos-fk-{i}", AsyncMock()
        )
        ids.append(jid)

    first = await _run_until_done(ids[0])
    assert first == ["retry", "ok"]  # the outage failed the job; the retry applied it
    async with session_scope() as s:
        rec = (await s.execute(select(JobRecord).where(JobRecord.id == ids[0]))).scalar_one()
    assert rec.status == "succeeded" and rec.attempts == 2
    assert await _run_until_done(ids[1]) == ["ok"]
    assert await run_job({}, ids[0]) == "duplicate"  # arq redelivery after success

    assert store.read("MATCH (c:Commit) RETURN count(c)") == [[1]]
    assert store.read("MATCH (d:Deployment) RETURN count(d)") == [[1]]
    assert store.read("MATCH (:Deployment)-[r:includes]->(:Commit) RETURN count(r)") == [[1]]
    assert store.read("MATCH (:Commit)-[r:authored_by]->() RETURN count(r)") == [[1]]


@pytest.mark.xfail(
    strict=True,
    reason="known gap (docs/performance.md): an error telemetry row is written to Postgres "
    "before the FalkorDB link; a graph outage retries the job and stores the row again",
)
async def test_falkordb_outage_does_not_duplicate_error_telemetry(
    db: None, store: GraphStore, falkordb_down_once: list[int]
) -> None:
    ev = {
        "type": "telemetry",
        "repo": store.repo,
        "service": "payments",
        "env": "prod",
        "ts": "2026-10-09T10:05:00Z",
        "kind": "error",
        "endpoint": "POST /payments",
        "error_type": "TypeError",
        "message": "boom",
        "stack": ["validatePayment (/app/payments/src/payment/validation.js:4:11)"],
    }
    jid, _ = await enqueue("ingest_event", IngestEventPayload(event=ev), "chaos-tel", AsyncMock())
    assert await _run_until_done(jid) == ["retry", "ok"]
    async with session_scope() as s:
        rows = (await s.execute(select(func.count()).select_from(Telemetry))).scalar_one()
    assert rows == 1


async def test_hard_killed_worker_job_is_retried_exactly_once_on_any_os(
    db: None, tmp_path: Any
) -> None:
    """Cross-platform variant of test_jobs' SIGKILL test: Popen.kill() is SIGKILL on POSIX and
    TerminateProcess on Windows; neither lets the worker clean up."""
    from arq import create_pool

    from causaldeploy.jobs.queue import redis_settings

    probe = tmp_path / "probe.log"
    env = {**os.environ, "PROBE_FILE": str(probe), "PYTHONPATH": "."}
    cmd = [sys.executable, "-m", "arq", "tests.kill_worker_settings.WorkerSettings"]

    pool = await create_pool(redis_settings())
    await pool.flushdb()
    job_id, _ = await enqueue("ingest_event", IngestEventPayload(event=EVENT), "kill-x", pool)

    first = subprocess.Popen(
        cmd, env={**env, "PROBE_SLEEP": "60"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline and not (probe.exists() and "start" in probe.read_text()):
            await asyncio.sleep(0.2)
        assert probe.exists(), "first worker never started the job"
        first.kill()
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
        second.kill()
        second.wait(timeout=15)
        await pool.flushdb()
        await pool.aclose()
    assert state == ("succeeded", 2), state
    assert probe.read_text().split() == ["start", "start", "done"]


@pytest_asyncio.fixture
async def client(db: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr("causaldeploy.jobs.queue.get_pool", AsyncMock(return_value=AsyncMock()))
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _counts() -> tuple[int, int]:
    async with session_scope() as s:
        raw = (await s.execute(select(func.count()).select_from(RawEvent))).scalar_one()
        jobs = (await s.execute(select(func.count()).select_from(JobRecord))).scalar_one()
    return raw, jobs


async def test_concurrent_duplicate_webhook_deliveries_are_processed_once(
    client: httpx.AsyncClient,
) -> None:
    payload = {
        "repository": {"full_name": "acme/shoplite"},
        "deployment": {
            "id": 77,
            "sha": "8f31c2a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7",
            "environment": "prod",
            "created_at": "2026-10-09T10:10:00Z",
        },
        "deployment_status": {"state": "success", "created_at": "2026-10-09T10:12:00Z"},
    }
    body = json.dumps(payload).encode()
    h = {
        "x-github-event": "deployment_status",
        "x-github-delivery": "dup-1",
        "x-hub-signature-256": sign(body),
    }
    rs = await asyncio.gather(
        *(client.post("/webhooks/github", content=body, headers=h) for _ in range(6))
    )
    statuses = sorted(r.json()["status"] for r in rs)
    assert statuses == ["accepted"] + ["duplicate"] * 5
    assert await _counts() == (1, 1)


async def test_same_push_under_a_new_delivery_id_queues_no_second_import(
    client: httpx.AsyncClient,
) -> None:
    """A redelivery with a fresh X-GitHub-Delivery passes the delivery dedupe; the effect is
    still keyed on content (push:<repo>:<after>), so the import runs once."""
    payload = {
        "repository": {"full_name": "acme/shoplite"},
        "installation": {"id": 5},
        "after": "8f31c2a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7",
    }
    body = json.dumps(payload).encode()
    for delivery in ("push-a", "push-b"):
        h = {
            "x-github-event": "push",
            "x-github-delivery": delivery,
            "x-hub-signature-256": sign(body),
        }
        assert (await client.post("/webhooks/github", content=body, headers=h)).status_code == 202
    async with session_scope() as s:
        kinds = (await s.execute(select(JobRecord.kind))).scalars().all()
    assert await _counts() == (2, 1) and kinds == ["backfill_repo"]
