import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from causaldeploy.config import get_settings
from causaldeploy.contracts import (
    HealthEvent,
    TelemetryEvent,
    VerificationResultEvent,
    parse_event,
)
from causaldeploy.detect.errors import (
    derive_service_calls,
    link_error,
    request_spans,
    store_telemetry,
)
from causaldeploy.graph.store import GraphStore
from causaldeploy.ingest.events import apply_event
from causaldeploy.jobs.queue import (
    BackfillPayload,
    GuardPayload,
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
        rid = getattr(event, "request_id", None)
        if rid and getattr(event, "kind", None) == "request":
            spans = await request_spans(event.repo, rid)
            if len({svc for svc, _ in spans}) > 1:
                await asyncio.to_thread(derive_service_calls, store, spans)
        if fp and isinstance(event, TelemetryEvent):
            await asyncio.to_thread(link_error, store, event, fp)
        return
    if isinstance(event, VerificationResultEvent):
        await _verification_event(event)
        return
    await asyncio.to_thread(apply_event, store, event)


async def _verification_event(event: VerificationResultEvent) -> None:
    """Route a reported verification result to its remediation; post the GitHub check run."""
    from causaldeploy.remediate import service

    installation = await _installation(event.repo)
    ops = service.make_ops() if installation is not None else None
    await service.record_verification_event(event, ops=ops, installation_id=installation)


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
    from causaldeploy.remediate import service
    from causaldeploy.remediate.patch import default_patch_client

    job = ProposeFixPayload.model_validate(payload)
    if not get_settings().is_allowed(job.repo):
        return
    installation = await _installation(job.repo)
    if installation is None:
        raise RuntimeError(f"the GitHub App is not installed on {job.repo}")
    await service.propose_fix(
        job.repo, job.incident_id, installation, service.make_ops(), default_patch_client()
    )


async def verify_result(payload: dict[str, Any]) -> None:
    from causaldeploy.remediate import service

    job = VerifyResultPayload.model_validate(payload)
    if not get_settings().is_allowed(job.repo):
        return
    installation = job.installation_id or await _installation(job.repo)
    report = None
    ops = service.make_ops() if installation is not None else None
    if ops is not None and installation is not None:
        report = await ops.fetch_report(job.repo, installation, job.run_id)
    await service.record_verification(
        job.repo,
        job.branch,
        job.conclusion,
        job.run_url,
        report,
        ops=ops,
        installation_id=installation,
    )


async def remediation_merged(payload: dict[str, Any]) -> None:
    from causaldeploy.remediate.service import finalize_merged

    job = MergedPayload.model_validate(payload)
    if get_settings().is_allowed(job.repo):
        await finalize_merged(job.repo, job.branch, job.merged_by)


async def guard_pr(payload: dict[str, Any]) -> None:
    from causaldeploy.guardian.service import review_pr

    job = GuardPayload.model_validate(payload)
    if not get_settings().is_allowed(job.repo):
        return
    installation = job.installation_id or await _installation(job.repo)
    if installation is None:
        raise RuntimeError(f"the GitHub App is not installed on {job.repo}")
    await review_pr(job.repo, job.number, installation)


HANDLERS: dict[str, Handler] = {
    "guard_pr": guard_pr,
    "propose_fix": propose_fix,
    "verify_result": verify_result,
    "remediation_merged": remediation_merged,
    "ingest_event": ingest_event,
    "backfill_repo": backfill_repo,
    "investigate_incident": investigate_incident,
}
