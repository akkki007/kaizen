from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from causaldeploy.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    s = get_settings()
    if (
        s.jobs_inline
    ):  # serverless: every invocation may run on a new event loop, so never reuse connections
        # the pooled Neon endpoint is pgbouncer in transaction mode: no prepared-statement cache
        return create_async_engine(
            s.sqlalchemy_url,
            poolclass=NullPool,
            connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
        )
    return create_async_engine(s.sqlalchemy_url, pool_pre_ping=True)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Commit on success, roll back on error."""
    async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
        async with session.begin():
            yield session
