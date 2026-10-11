from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from causaldeploy.config import get_settings
from causaldeploy.db.models import Base


def test_downgrade_and_upgrade_latest_revision(migrated_db: None) -> None:
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")


async def test_models_match_migrations(migrated_db: None) -> None:
    eng = create_async_engine(get_settings().sqlalchemy_url)
    async with eng.connect() as c:
        diff = await c.run_sync(
            lambda sc: compare_metadata(MigrationContext.configure(sc), Base.metadata)
        )
    await eng.dispose()
    assert diff == []
