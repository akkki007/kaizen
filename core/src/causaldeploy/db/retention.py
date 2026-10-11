"""Retention for raw telemetry (the graph keeps only summaries)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete

from causaldeploy.config import get_settings
from causaldeploy.db.models import Telemetry
from causaldeploy.db.session import session_scope


async def purge_telemetry(retention_days: int | None = None, now: datetime | None = None) -> int:
    """Delete telemetry older than the retention window; returns the number of rows removed."""
    days = retention_days if retention_days is not None else get_settings().telemetry_retention_days
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    async with session_scope() as s:
        res = await s.execute(delete(Telemetry).where(Telemetry.ts < cutoff))
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


async def purge_job(ctx: dict[Any, Any], *args: Any, **kwargs: Any) -> int:
    """ARQ cron entry point."""
    return await purge_telemetry()
