import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest
import pytest_asyncio

ADMIN_DSN = os.environ.get(
    "CD_TEST_ADMIN_DSN", "postgresql://causaldeploy:causaldeploy@localhost:5432/postgres"
)
TEST_DB = "causaldeploy_test"
os.environ["CD_ENV"] = "test"
os.environ["CD_POSTGRES_DSN"] = ADMIN_DSN.rsplit("/", 1)[0] + "/" + TEST_DB
os.environ.setdefault("CD_REDIS_URL", "redis://localhost:6379/15")
os.environ["CD_GITHUB_WEBHOOK_SECRET"] = "whsec-test"
os.environ["CD_INGEST_RATE_LIMIT_PER_MIN"] = "1000"
# A developer's real .env (team App credentials, allowlist) must not leak into the tests
for _name in (
    "CD_ALLOWED_REPOS",
    "CD_GITHUB_APP_ID",
    "CD_GITHUB_APP_PRIVATE_KEY",
    "CD_GITHUB_OAUTH_CLIENT_ID",
    "CD_GITHUB_OAUTH_CLIENT_SECRET",
    "CD_PUBLIC_BASE_URL",
):
    os.environ[_name] = ""
os.environ["CD_PUBLIC_BASE_URL"] = "http://localhost:8000"

from causaldeploy.graph.builder import GraphBuilder  # noqa: E402
from causaldeploy.graph.store import GraphStore  # noqa: E402


async def _recreate_db() -> None:
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await conn.execute(f"CREATE DATABASE {TEST_DB}")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_db() -> None:
    """Fresh database migrated to head with the real Alembic migrations."""
    from alembic import command
    from alembic.config import Config

    asyncio.run(_recreate_db())
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture
async def db(migrated_db: None) -> AsyncIterator[None]:
    from sqlalchemy import text

    from causaldeploy.db.models import Base
    from causaldeploy.db.session import get_engine

    async with get_engine().begin() as c:
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield
    await get_engine().dispose()


@pytest.fixture
def store() -> Iterator[GraphStore]:
    """Ephemeral per-test FalkorDB graph (compose FalkorDB on CD_FALKORDB_*)."""
    s = GraphStore(f"test/{uuid.uuid4().hex[:12]}")
    try:
        yield s
    finally:
        s.drop()


@pytest.fixture
def builder(store: GraphStore) -> GraphBuilder:
    return GraphBuilder(store)
