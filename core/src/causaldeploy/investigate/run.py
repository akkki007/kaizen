"""Investigation job: rank candidates, persist them to the graph and Postgres."""

import asyncio
from typing import Any

from sqlalchemy import select

from causaldeploy.agent.models import AgentRun
from causaldeploy.config import get_settings
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
    try:
        result = await asyncio.to_thread(_rank_and_persist, store, incident_id)
        run = await _run_agent(repo, store, incident_id)
    except Exception:
        await _set_status(incident_id, "investigating", "open")  # never stay "investigating"
        raise
    async with session_scope() as s:
        inv = Investigation(
            incident_id=incident_id,
            result={
                "ranking": result.model_dump(mode="json"),
                "agent": run.model_dump(mode="json") if run else None,
            },
        )
        s.add(inv)
        await s.flush()
        inv_id = inv.id
    # a named suspect is "identified"; abstaining (no code evidence) goes back to "open" with the
    # abstention recorded, instead of claiming an investigation is still running
    named = result.verdict in ("confident", "ambiguous")
    await _set_status(incident_id, "investigating", "identified" if named else "open")
    if run:
        from causaldeploy.agent.persist import write_trace

        await asyncio.to_thread(write_trace, store, run, f"inv-{inv_id}")
    return result


async def _set_status(incident_id: str, expected: str, new: str) -> None:
    async with session_scope() as s:
        row = (
            await s.execute(select(IncidentRow).where(IncidentRow.id == incident_id))
        ).scalar_one()
        if row.status == expected:  # never overwrite "resolved"
            row.status = new


async def _run_agent(repo: str, store: GraphStore, incident_id: str) -> AgentRun | None:
    """Agent explanation on top of the deterministic ranking; skipped without an OpenAI key."""
    s = get_settings()
    if not (s.openai_api_key.get_secret_value() and s.openai_model):
        return None
    from mcp import Client

    from causaldeploy.agent.llm import OpenAIClient
    from causaldeploy.agent.loop import run_agent
    from causaldeploy.mcp_server.server import build_server

    async with Client(build_server(repo, store)) as client:
        return await run_agent(client, store, incident_id, OpenAIClient())


def _rank_and_persist(store: GraphStore, incident_id: str) -> RankResult:
    result = rank(store, incident_id, "graph")
    persist(store, result)
    return result


def summary(result: RankResult) -> dict[str, Any]:
    top = result.candidates[0] if result.candidates else None
    return {"verdict": result.verdict, "top": top.sha if top else None, "margin": result.margin}
