"""Incident state machine (#88): guarded transitions, an audit row per change, graph sync.

Every status write goes through `transition` (strict: raises on an illegal move) or
`advance` (conditional: only from the listed states, used by jobs that race with people).
"""

import asyncio
import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from causaldeploy.contracts.domain import IncidentStatus as S
from causaldeploy.db.models import IncidentRow, IncidentTransition
from causaldeploy.db.session import session_scope

log = logging.getLogger(__name__)

SYSTEM = "system"

TERMINAL: frozenset[S] = frozenset({S.RESOLVED, S.CLOSED})
ACTIVE: frozenset[S] = frozenset(set(S) - TERMINAL)  # an incident that still blocks a new one

TRANSITIONS: dict[S, frozenset[S]] = {
    S.OPEN: frozenset({S.INVESTIGATING, S.NEEDS_HUMAN, S.CLOSED}),
    # back to open when ranking abstains or the job fails: never stuck "investigating"
    S.INVESTIGATING: frozenset({S.OPEN, S.IDENTIFIED, S.EXPLAINED, S.NEEDS_HUMAN, S.CLOSED}),
    S.IDENTIFIED: frozenset({S.EXPLAINED, S.INVESTIGATING, S.REMEDIATING, S.NEEDS_HUMAN, S.CLOSED}),
    S.EXPLAINED: frozenset({S.INVESTIGATING, S.REMEDIATING, S.NEEDS_HUMAN, S.CLOSED}),
    S.REMEDIATING: frozenset({S.VERIFYING, S.IDENTIFIED, S.EXPLAINED, S.NEEDS_HUMAN, S.CLOSED}),
    # verified fixes wait here for approval + merge; a failed fix escalates or tries the next one
    S.VERIFYING: frozenset({S.RESOLVED, S.REMEDIATING, S.NEEDS_HUMAN, S.CLOSED}),
    S.NEEDS_HUMAN: frozenset({S.INVESTIGATING, S.REMEDIATING, S.CLOSED}),
    S.RESOLVED: frozenset({S.OPEN}),  # reopen
    S.CLOSED: frozenset({S.OPEN}),  # reopen
}


class InvalidTransition(ValueError):
    def __init__(self, incident_id: str, current: str, to: str) -> None:
        super().__init__(f"incident {incident_id}: {current} -> {to} is not allowed")
        self.incident_id, self.current, self.to = incident_id, current, to


def allowed(current: str, to: str) -> bool:
    try:
        return S(to) in TRANSITIONS[S(current)]
    except ValueError:
        return False


async def record_opened(session: AsyncSession, incident_id: str, actor: str = SYSTEM) -> None:
    """First history row for a freshly inserted incident (status already "open")."""
    session.add(
        IncidentTransition(
            incident_id=incident_id,
            from_status=None,
            to_status=S.OPEN,
            actor=actor,
            reason="detected",
        )
    )


async def apply(
    session: AsyncSession, row: IncidentRow, to: S | str, *, actor: str = SYSTEM, reason: str = ""
) -> None:
    """Transition inside the caller's transaction. Raises InvalidTransition. No graph sync:
    call `sync_graph` after the commit."""
    to = S(to)
    if row.status == to:
        return
    if not allowed(row.status, to):
        raise InvalidTransition(row.id, row.status, to)
    session.add(
        IncidentTransition(
            incident_id=row.id, from_status=row.status, to_status=to, actor=actor, reason=reason
        )
    )
    row.status = to


async def transition(
    incident_id: str, to: S | str, *, actor: str = SYSTEM, reason: str = ""
) -> str:
    """Strict transition in its own transaction, then graph sync. Returns the repo."""
    async with session_scope() as s:
        row = await _row(s, incident_id)
        await apply(s, row, to, actor=actor, reason=reason)
        repo = row.repo
    await sync_graph(repo, incident_id, S(to))
    return repo


async def advance(
    incident_id: str,
    to: S | str,
    *,
    only_from: Iterable[S | str],
    actor: str = SYSTEM,
    reason: str = "",
) -> bool:
    """Move to `to` only if the current status is in `only_from` (and the move is legal).
    Returns whether it moved. Jobs use this so they never overwrite a person's decision."""
    sources = {S(x) for x in only_from}
    async with session_scope() as s:
        row = await _row(s, incident_id)
        if S(row.status) not in sources or not allowed(row.status, to):
            return False
        await apply(s, row, to, actor=actor, reason=reason)
        repo = row.repo
    await sync_graph(repo, incident_id, S(to))
    return True


async def history(incident_id: str) -> list[IncidentTransition]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(IncidentTransition)
                .where(IncidentTransition.incident_id == incident_id)
                .order_by(IncidentTransition.id)
            )
        ).scalars()
        return list(rows)


async def sync_graph(repo: str, incident_id: str, status: S) -> None:
    """Mirror the status onto the Incident node. Best effort: Postgres is the source of truth."""
    from causaldeploy.graph.store import GraphStore

    def write() -> None:
        GraphStore(repo).graph.query(
            "MATCH (i:Incident {id: $i}) SET i.status = $s", {"i": incident_id, "s": str(status)}
        )

    try:
        await asyncio.to_thread(write)
    except Exception:  # graph down must not undo a committed transition
        log.warning("incident graph sync failed", extra={"ctx": {"incident": incident_id}})


async def _row(s: AsyncSession, incident_id: str) -> IncidentRow:
    return (
        await s.execute(select(IncidentRow).where(IncidentRow.id == incident_id).with_for_update())
    ).scalar_one()
