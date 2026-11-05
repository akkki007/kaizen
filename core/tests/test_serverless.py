import pytest
from sqlalchemy import select

from causaldeploy.config import Settings, get_settings
from causaldeploy.db.models import JobRecord
from causaldeploy.db.session import session_scope
from causaldeploy.jobs import handlers
from causaldeploy.jobs.queue import IngestEventPayload, enqueue


def test_managed_postgres_options_are_translated_for_asyncpg() -> None:
    s = Settings(
        postgres_dsn="postgresql://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require"
    )
    assert s.sqlalchemy_url == "postgresql+asyncpg://u:p@ep-x.neon.tech/db?ssl=require"


def test_falkordb_password_is_a_secret() -> None:
    s = Settings(falkordb_password="hunter2-secret")  # type: ignore[arg-type]
    assert "hunter2-secret" in s.secret_values() and "hunter2-secret" not in repr(s)


async def test_inline_mode_runs_the_job_in_the_request_without_a_queue(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, object]] = []

    async def fake(payload: dict[str, object]) -> None:
        seen.append(payload)

    monkeypatch.setitem(handlers.HANDLERS, "ingest_event", fake)
    monkeypatch.setenv("CD_JOBS_INLINE", "1")
    get_settings.cache_clear()
    try:
        # no Redis pool is passed or created: inline mode never touches the queue
        job_id, created = await enqueue(
            "ingest_event", IngestEventPayload(event={"a": 1}), "k-inline"
        )
        assert created and seen and seen[0]["event"] == {"a": 1}
        async with session_scope() as s:
            rec = (await s.execute(select(JobRecord).where(JobRecord.id == job_id))).scalar_one()
        assert rec.status == "succeeded"
    finally:
        get_settings.cache_clear()
