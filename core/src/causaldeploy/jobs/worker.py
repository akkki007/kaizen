"""ARQ worker: `uv run arq causaldeploy.jobs.worker.WorkerSettings`."""

import logging
import time
from typing import Any

from arq import Retry
from sqlalchemy import select

from causaldeploy.config import get_settings
from causaldeploy.db.models import JobRecord
from causaldeploy.db.session import session_scope
from causaldeploy.jobs.handlers import HANDLERS
from causaldeploy.jobs.queue import MAX_ATTEMPTS, redis_settings
from causaldeploy.logging import configure_logging, job_id_var
from causaldeploy.observability import JOB_SECONDS, JOBS_TOTAL, init_tracing, span

log = logging.getLogger(__name__)


async def run_job(ctx: dict[str, Any], job_id: str) -> str:
    token = job_id_var.set(job_id)
    started = time.perf_counter()
    kind = "unknown"
    try:
        async with session_scope() as s:
            rec = (
                await s.execute(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            ).scalar_one()
            if rec.status == "succeeded":  # redelivered after success: no second effect
                return "duplicate"
            rec.status, rec.attempts = "running", rec.attempts + 1
            kind, payload, attempts = rec.kind, rec.payload, rec.attempts
        try:
            with span(f"job.{kind}", parent=payload.get("_trace"), job_id=job_id, attempt=attempts):
                await HANDLERS[kind](payload)
        except Exception as exc:
            dead = attempts >= MAX_ATTEMPTS
            async with session_scope() as s:
                rec = (
                    await s.execute(select(JobRecord).where(JobRecord.id == job_id))
                ).scalar_one()
                rec.status = "dead" if dead else "queued"
                rec.error = f"{type(exc).__name__}: {exc}"[:2000]
            JOBS_TOTAL.labels(kind, "dead" if dead else "retry").inc()
            log.warning("job failed", extra={"ctx": {"kind": kind, "attempts": attempts}})
            if dead:
                return "dead"
            raise Retry(defer=min(2**attempts * 5, 300)) from exc
        async with session_scope() as s:
            rec = (await s.execute(select(JobRecord).where(JobRecord.id == job_id))).scalar_one()
            rec.status, rec.error = "succeeded", None
        JOBS_TOTAL.labels(kind, "ok").inc()
        return "ok"
    finally:
        JOB_SECONDS.labels(kind).observe(time.perf_counter() - started)
        job_id_var.reset(token)


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(get_settings())
    init_tracing(get_settings())


class WorkerSettings:
    functions = [run_job]
    on_startup = startup
    redis_settings = redis_settings()
    max_jobs = 10  # concurrency limit
    max_tries = MAX_ATTEMPTS + 1
    job_timeout = 600
    retry_jobs = True
    handle_signals = True  # SIGTERM: finish in-flight jobs, then exit
