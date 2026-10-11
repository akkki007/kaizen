from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from causaldeploy.db.models import Telemetry
from causaldeploy.db.retention import purge_telemetry
from causaldeploy.db.session import session_scope
from causaldeploy.ingest.manifests import diff_manifests, parse_manifest


def row(age_days: int) -> Telemetry:
    return Telemetry(
        repo="a/b",
        service="payments",
        env="prod",
        kind="request",
        ts=datetime.now(UTC) - timedelta(days=age_days),
        body={},
    )


async def test_purge_keeps_only_the_retention_window(db: None) -> None:
    async with session_scope() as s:
        s.add_all([row(30), row(20), row(3), row(0)])
    assert await purge_telemetry(retention_days=14) == 2
    async with session_scope() as s:
        left = (await s.execute(select(func.count()).select_from(Telemetry))).scalar_one()
    assert left == 2


def test_lockfile_resolved_versions_and_diff() -> None:
    lock = (
        '{"packages": {"": {}, "node_modules/express": {"version": "4.21.2"},'
        ' "node_modules/@scope/pkg": {"version": "1.0.0"},'
        ' "node_modules/express/node_modules/qs": {"version": "6.13.0"}}}'
    )
    resolved = parse_manifest("package-lock.json", lock)
    assert resolved == {"express": "4.21.2", "@scope/pkg": "1.0.0", "qs": "6.13.0"}
    newer = parse_manifest("package-lock.json", lock.replace("4.21.2", "4.22.0"))
    assert diff_manifests(resolved, newer) == [("express", "4.21.2", "4.22.0")]
