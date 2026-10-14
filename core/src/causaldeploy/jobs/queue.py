"""Typed job enqueue with idempotency keys and persisted status."""

import uuid
from functools import lru_cache
from typing import Literal

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert

from causaldeploy.config import get_settings
from causaldeploy.db.models import JobRecord
from causaldeploy.db.session import session_scope
from causaldeploy.observability import inject_trace

JobKind = Literal[
    "ingest_event",
    "backfill_repo",
    "investigate_incident",
    "propose_fix",
    "verify_result",
    "remediation_merged",
]
MAX_ATTEMPTS = 5


class IngestEventPayload(BaseModel):
    event: dict[str, object]


class BackfillPayload(BaseModel):
    repo: str
    installation_id: int | None = None


class InvestigatePayload(BaseModel):
    repo: str
    incident_id: str


class ProposeFixPayload(BaseModel):
    repo: str
    incident_id: str


class VerifyResultPayload(BaseModel):
    repo: str
    branch: str
    conclusion: str
    run_id: int
    run_url: str
    installation_id: int | None = None


class MergedPayload(BaseModel):
    repo: str
    branch: str
    merged_by: str


PAYLOADS: dict[str, type[BaseModel]] = {
    "propose_fix": ProposeFixPayload,
    "verify_result": VerifyResultPayload,
    "remediation_merged": MergedPayload,
    "investigate_incident": InvestigatePayload,
    "ingest_event": IngestEventPayload,
    "backfill_repo": BackfillPayload,
}


@lru_cache
def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_pool() -> ArqRedis:
    return await create_pool(redis_settings())


async def enqueue(
    kind: JobKind, payload: BaseModel, idempotency_key: str, pool: ArqRedis | None = None
) -> tuple[str, bool]:
    """Returns (job_id, created). A duplicate idempotency key produces no second effect."""
    job_id = uuid.uuid4().hex
    async with session_scope() as s:
        res = await s.execute(
            insert(JobRecord)
            .values(
                id=job_id,
                kind=kind,
                idempotency_key=idempotency_key,
                status="queued",
                attempts=0,
                payload=payload.model_dump(mode="json") | {"_trace": inject_trace()},
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(JobRecord.id)
        )
        created = res.scalar_one_or_none() is not None
        if not created:
            from sqlalchemy import select

            existing = await s.execute(
                select(JobRecord.id).where(JobRecord.idempotency_key == idempotency_key)
            )
            return existing.scalar_one(), False
    own = pool is None
    pool = pool or await get_pool()
    try:
        await pool.enqueue_job("run_job", job_id, _job_id=job_id)
    finally:
        if own:
            await pool.aclose()
    return job_id, True
