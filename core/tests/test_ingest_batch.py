"""#88/#92 ingest fast path: a telemetry batch is stored with one idempotent INSERT and its
graph work is one job; re-sent batches and retried jobs never duplicate rows."""

import httpx
from sqlalchemy import func, select

from causaldeploy.db.models import JobRecord, Telemetry
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import GraphStore
from causaldeploy.jobs import handlers
from tests.test_api import client  # noqa: F401  (fixture re-export)
from tests.test_incident_api import REPO, key_headers, telemetry


def error_event(i: int) -> dict[str, object]:
    return {
        **telemetry(i),
        "kind": "error",
        "status_code": 500,
        "error_type": "TypeError",
        "message": f"cannot read amount of undefined ({i})",
        "stack": ["at validatePayment (payments/src/payment/validation.js:12:7)"],
    }


async def _counts() -> tuple[int, list[str]]:
    async with session_scope() as s:
        rows = (await s.execute(select(func.count()).select_from(Telemetry))).scalar_one()
        kinds = list((await s.execute(select(JobRecord.kind))).scalars())
    return rows, kinds


async def test_batch_is_stored_inline_once_with_one_graph_job(
    client: httpx.AsyncClient,  # noqa: F811
) -> None:
    h = await key_headers()
    body = [telemetry(i) for i in range(50)] + [error_event(i) for i in range(3)]
    r = await client.post("/api/v1/events", json=body, headers=h)
    assert r.status_code == 202 and r.json()["accepted"] == 53
    rows, kinds = await _counts()
    assert rows == 53  # visible to the detector before any worker runs
    assert kinds == ["ingest_telemetry_batch"]  # only the 3 errors need graph work: one job
    assert len(r.json()["job_ids"]) == 1

    # the reporter re-sends the same batch (e.g. its retry after a timeout): nothing new
    r = await client.post("/api/v1/events", json=body, headers=h)
    assert r.status_code == 202
    assert await _counts() == (53, ["ingest_telemetry_batch"])


async def test_batch_graph_job_links_errors_and_is_safe_to_retry(
    client: httpx.AsyncClient,  # noqa: F811
) -> None:
    payload = {"repo": REPO, "events": [error_event(1), telemetry(2)]}
    store = GraphStore(REPO)
    try:
        await handlers.ingest_telemetry_batch(payload)
        await handlers.ingest_telemetry_batch(payload)  # a retried job
        errors = store.read("MATCH (e:Error) RETURN count(e)")
        assert errors == [[1]]  # one signature for the error, none for plain requests
    finally:
        store.drop()
