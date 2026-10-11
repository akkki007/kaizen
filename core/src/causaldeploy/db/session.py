from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from causaldeploy.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().sqlalchemy_url, pool_pre_ping=True)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Commit on success, roll back on error."""
    async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
        async with session.begin():
            yield session
