"""App installation lifecycle: remember installations, repositories and request the first import."""

from typing import Any

from sqlalchemy.dialects.postgresql import insert

from causaldeploy.config import get_settings
from causaldeploy.db.models import Installation, Repository
from causaldeploy.db.session import session_scope

INSTALL_EVENTS = {"installation", "installation_repositories"}


def repositories_in(payload: dict[str, Any]) -> list[str]:
    """Repositories made available by this event (`installation` or `installation_repositories`)."""
    if payload.get("action") not in {"created", "added", "unsuspend", "new_permissions_accepted"}:
        return []
    repos = payload.get("repositories") or payload.get("repositories_added") or []
    allowed = get_settings().is_allowed
    return [r["full_name"] for r in repos if r.get("full_name") and allowed(r["full_name"])]


async def record_installation(payload: dict[str, Any]) -> tuple[int, list[str]]:
    """Upsert the installation and its repositories; returns (installation id, repos to import)."""
    inst = payload["installation"]
    inst_id = int(inst["id"])
    repos = repositories_in(payload)
    async with session_scope() as s:
        await s.execute(
            insert(Installation)
            .values(id=inst_id, account=(inst.get("account") or {}).get("login", ""))
            .on_conflict_do_nothing(index_elements=["id"])
        )
        for full_name in repos:
            await s.execute(
                insert(Repository)
                .values(full_name=full_name, installation_id=inst_id)
                .on_conflict_do_update(
                    index_elements=["full_name"], set_={"installation_id": inst_id}
                )
            )
    return inst_id, repos
