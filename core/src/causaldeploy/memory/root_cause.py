"""Root cause confirmation (#91): a person confirms or rejects a suspect commit.

confirmed -> incidents.confirmed_root_cause = sha, root_cause_status = "confirmed";
             graph: Incident-[:confirmed_cause]->Commit (replacing any earlier confirmation)
             and Incident.confirmed_root_cause.
rejected  -> root_cause_status = "rejected" (confirmed_root_cause cleared if it was this sha);
             graph: the likely_caused_by edge to that commit gets rejected=true, and a
             confirmed_cause edge to it is removed.
The ranking's history feature reads `confirmed_cause`; nothing here re-ranks.
"""

import asyncio

from sqlalchemy import select

from causaldeploy.db.models import AuditLog, IncidentRow
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import Edge, GraphStore


class UnknownCommit(LookupError):
    pass


def _write(store: GraphStore, incident_id: str, sha: str, confirmed: bool, actor: str) -> None:
    if confirmed:
        store.delete_edges("Incident", incident_id, "confirmed_cause")
        store.upsert_edges(
            [Edge("Incident", incident_id, "confirmed_cause", "Commit", sha, {"by": actor})]
        )
        store.graph.query(
            "MATCH (i:Incident {id: $i}) SET i.confirmed_root_cause = $s",
            {"i": incident_id, "s": sha},
        )
        store.graph.query(
            "MATCH (:Incident {id: $i})-[r:likely_caused_by]->(:Commit {sha: $s}) "
            "SET r.rejected = false",
            {"i": incident_id, "s": sha},
        )
    else:
        store.graph.query(
            "MATCH (:Incident {id: $i})-[r:likely_caused_by]->(:Commit {sha: $s}) "
            "SET r.rejected = true, r.rejected_by = $a",
            {"i": incident_id, "s": sha, "a": actor},
        )
        store.graph.query(
            "MATCH (:Incident {id: $i})-[r:confirmed_cause]->(:Commit {sha: $s}) DELETE r",
            {"i": incident_id, "s": sha},
        )
        store.graph.query(
            "MATCH (i:Incident {id: $i}) WHERE i.confirmed_root_cause = $s "
            "SET i.confirmed_root_cause = null",
            {"i": incident_id, "s": sha},
        )


async def set_root_cause(
    incident_id: str, sha: str, confirmed: bool, actor: str, reason: str | None = None
) -> IncidentRow:
    """Record the decision (Postgres + audit + graph). Raises UnknownCommit."""
    async with session_scope() as s:
        repo = (
            await s.execute(select(IncidentRow.repo).where(IncidentRow.id == incident_id))
        ).scalar_one()
    store = GraphStore(repo)
    known = await asyncio.to_thread(
        store.read, "MATCH (c:Commit {sha: $s}) RETURN c.sha", {"s": sha}
    )
    if not known:
        raise UnknownCommit(sha)
    async with session_scope() as s:
        row = (
            await s.execute(
                select(IncidentRow).where(IncidentRow.id == incident_id).with_for_update()
            )
        ).scalar_one()
        if confirmed:
            row.confirmed_root_cause, row.root_cause_status = sha, "confirmed"
        else:
            if row.confirmed_root_cause == sha:
                row.confirmed_root_cause = None
            row.root_cause_status = "rejected"
        s.add(
            AuditLog(
                actor=actor,
                action="incident.root_cause." + ("confirm" if confirmed else "reject"),
                target=incident_id,
                detail={"sha": sha, "reason": reason or "", "repo": repo},
            )
        )
        await s.flush()
        s.expunge(row)
    await asyncio.to_thread(_write, store, incident_id, sha, confirmed, actor)
    return row
