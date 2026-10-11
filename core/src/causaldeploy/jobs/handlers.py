import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from causaldeploy.contracts import parse_event
from causaldeploy.graph.store import GraphStore
from causaldeploy.ingest.events import apply_event
from causaldeploy.jobs.queue import BackfillPayload, IngestEventPayload

Handler = Callable[[dict[str, Any]], Awaitable[None]]


async def ingest_event(payload: dict[str, Any]) -> None:
    p = IngestEventPayload.model_validate(payload)
    event = parse_event(p.event)
    store = GraphStore(event.repo)
    await asyncio.to_thread(apply_event, store, event)


async def backfill_repo(payload: dict[str, Any]) -> None:
    from causaldeploy.github.backfill_job import run_backfill

    await run_backfill(BackfillPayload.model_validate(payload))


HANDLERS: dict[str, Handler] = {"ingest_event": ingest_event, "backfill_repo": backfill_repo}
