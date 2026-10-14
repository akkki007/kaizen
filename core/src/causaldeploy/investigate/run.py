"""Investigation job: rank candidates, persist them to the graph and Postgres."""

import asyncio
from typing import Any

from sqlalchemy import select

from causaldeploy.db.models import IncidentRow, Investigation
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import GraphStore
from causaldeploy.investigate.scorer import RankResult, persist, rank


async def investigate(repo: str, incident_id: str) -> RankResult:
    store = GraphStore(repo)
    async with session_scope() as s:
        row = (
            await s.execute(select(IncidentRow).where(IncidentRow.id == incident_id))
        ).scalar_one()
        if row.status == "open":
            row.status = "investigating"
    result = await asyncio.to_thread(_rank_and_persist, store, incident_id)
    async with session_scope() as s:
        s.add(Investigation(incident_id=incident_id, result=result.model_dump(mode="json")))
    return result


def _rank_and_persist(store: GraphStore, incident_id: str) -> RankResult:
    result = rank(store, incident_id, "graph")
    persist(store, result)
    return result


def summary(result: RankResult) -> dict[str, Any]:
    top = result.candidates[0] if result.candidates else None
    return {"verdict": result.verdict, "top": top.sha if top else None, "margin": result.margin}
