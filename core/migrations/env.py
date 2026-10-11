import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from causaldeploy.config import get_settings
from causaldeploy.db.models import Base

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _main() -> None:
    url = context.config.attributes.get("url") or get_settings().sqlalchemy_url
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


asyncio.run(_main())
