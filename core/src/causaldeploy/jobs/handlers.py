import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from causaldeploy.config import get_settings
from causaldeploy.contracts import HealthEvent, TelemetryEvent, parse_event
from causaldeploy.detect.errors import link_error, store_telemetry
from causaldeploy.graph.store import GraphStore
from causaldeploy.ingest.events import apply_event
from causaldeploy.jobs.queue import (
    BackfillPayload,
    IngestEventPayload,
    InvestigatePayload,
    MergedPayload,
    ProposeFixPayload,
    VerifyResultPayload,
)

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


async def _installation(repo: str) -> int | None:
    from sqlalchemy import select

    from causaldeploy.db.models import Repository
    from causaldeploy.db.session import session_scope

    async with session_scope() as s:
        row = (
            await s.execute(select(Repository).where(Repository.full_name == repo))
        ).scalar_one_or_none()
        return row.installation_id if row else None


async def propose_fix(payload: dict[str, Any]) -> None:
    from causaldeploy.remediate.service import propose_revert

    job = ProposeFixPayload.model_validate(payload)
    if not get_settings().is_allowed(job.repo):
        return
    installation = await _installation(job.repo)
    if installation is None:
        raise RuntimeError(f"the GitHub App is not installed on {job.repo}")
    await propose_revert(job.repo, job.incident_id, installation)


async def verify_result(payload: dict[str, Any]) -> None:
    from causaldeploy.remediate.ops import GitHubOps
    from causaldeploy.remediate.service import record_verification

    job = VerifyResultPayload.model_validate(payload)
    if not get_settings().is_allowed(job.repo):
        return
    installation = job.installation_id or await _installation(job.repo)
    report = None
    if installation is not None:
        report = await GitHubOps().fetch_report(job.repo, installation, job.run_id)
    await record_verification(job.repo, job.branch, job.conclusion, job.run_url, report)


async def remediation_merged(payload: dict[str, Any]) -> None:
    from causaldeploy.remediate.service import finalize_merged

    job = MergedPayload.model_validate(payload)
    if get_settings().is_allowed(job.repo):
        await finalize_merged(job.repo, job.branch, job.merged_by)


HANDLERS: dict[str, Handler] = {
    "propose_fix": propose_fix,
    "verify_result": verify_result,
    "remediation_merged": remediation_merged,
    "ingest_event": ingest_event,
    "backfill_repo": backfill_repo,
    "investigate_incident": investigate_incident,
}
