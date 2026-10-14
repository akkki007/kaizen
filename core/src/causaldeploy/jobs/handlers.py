import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from causaldeploy.config import get_settings
from causaldeploy.contracts import HealthEvent, TelemetryEvent, parse_event
from causaldeploy.detect.errors import link_error, store_telemetry
from causaldeploy.graph.store import GraphStore
from causaldeploy.ingest.events import apply_event
from causaldeploy.jobs.queue import BackfillPayload, IngestEventPayload, InvestigatePayload

Handler = Callable[[dict[str, Any]], Awaitable[None]]


async def ingest_event(payload: dict[str, Any]) -> None:
    p = IngestEventPayload.model_validate(payload)
    event = parse_event(p.event)
    if not get_settings().is_allowed(event.repo):
        return  # queued before the allowlist existed
    store = GraphStore(event.repo)
    if isinstance(event, TelemetryEvent | HealthEvent):
        fp = await store_telemetry(event)  # Postgres first: detection reads from there
        if fp and isinstance(event, TelemetryEvent):
            await asyncio.to_thread(link_error, store, event, fp)
        return
    await asyncio.to_thread(apply_event, store, event)


async def backfill_repo(payload: dict[str, Any]) -> None:
    from causaldeploy.github.backfill_job import run_backfill

    job = BackfillPayload.model_validate(payload)
    if not get_settings().is_allowed(job.repo):
        return  # never clone repositories we were not asked to watch
    await run_backfill(job)


async def investigate_incident(payload: dict[str, Any]) -> None:
    from causaldeploy.investigate.run import investigate

    job = InvestigatePayload.model_validate(payload)
    if not get_settings().is_allowed(job.repo):
        return
    await investigate(job.repo, job.incident_id)


HANDLERS: dict[str, Handler] = {
    "ingest_event": ingest_event,
    "backfill_repo": backfill_repo,
    "investigate_incident": investigate_incident,
}
