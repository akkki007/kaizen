"""Investigation job: rank candidates, persist them to the graph and Postgres."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from causaldeploy.agent.models import AgentRun
from causaldeploy.config import get_settings
from causaldeploy.contracts.domain import IncidentStatus as S
from causaldeploy.db.models import IncidentRow, Investigation
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import GraphStore
from causaldeploy.incidents import lifecycle
from causaldeploy.investigate.scorer import RankResult, persist, rank

log = logging.getLogger(__name__)

async def investigate(repo: str, incident_id: str) -> RankResult:
    store = GraphStore(repo)
    await lifecycle.advance(
        incident_id, S.INVESTIGATING, only_from=[S.OPEN], reason="investigation started"
    )
    await _update_similarity(repo, incident_id)  # before ranking: feeds the history feature
    try:
        started = time.perf_counter()
        result = await asyncio.to_thread(_rank_and_persist, store, incident_id)
        rank_ms = (time.perf_counter() - started) * 1000
        run = await _run_agent(repo, store, incident_id)
    except Exception:
        # never stay "investigating"
        await lifecycle.advance(
            incident_id, S.OPEN, only_from=[S.INVESTIGATING], reason="investigation failed"
        )
        raise
    async with session_scope() as s:
        inv = Investigation(
            incident_id=incident_id,
            result={
                "ranking": result.model_dump(mode="json"),
                "agent": run.model_dump(mode="json") if run else None,
            },
            **investigation_record(result, rank_ms),
        )
        s.add(inv)
        await s.flush()
        inv_id = inv.id
    # a named suspect is "identified"; abstaining (no code evidence) goes back to "open" with the
    # abstention recorded, instead of claiming an investigation is still running
    named = result.verdict in ("confident", "ambiguous")
    if named:
        async with session_scope() as s:
            row = (
                await s.execute(select(IncidentRow).where(IncidentRow.id == incident_id))
            ).scalar_one()
            row.diagnosed_at = row.diagnosed_at or datetime.now(UTC)
        explained = run is not None and run.status == "explained"
        await lifecycle.advance(
            incident_id,
            S.EXPLAINED if explained else S.IDENTIFIED,
            only_from=[S.INVESTIGATING],
            reason=f"ranking {result.verdict}",
        )
    else:
        await lifecycle.advance(
            incident_id, S.OPEN, only_from=[S.INVESTIGATING], reason=f"ranking {result.verdict}"
        )
    if run:
        from causaldeploy.agent.persist import write_trace

        await asyncio.to_thread(write_trace, store, run, f"inv-{inv_id}")
    return result


async def _update_similarity(repo: str, incident_id: str) -> None:
    """Computed `resembles {similarity, basis}` edges to past incidents (#91). Best effort: a
    failure here must not block ranking."""
    from causaldeploy.memory.similarity import update_similarity

    try:
        await update_similarity(repo, incident_id)
    except Exception:
        log.warning("similarity pass failed", extra={"ctx": {"incident": incident_id}})


async def _run_agent(repo: str, store: GraphStore, incident_id: str) -> AgentRun | None:
    """Agent explanation on top of the deterministic ranking; skipped without an OpenAI key
    (unless CD_LLM_REPLAY_MODE=replay). Every model call is recorded in llm_calls."""
    from mcp import Client

    from causaldeploy.agent import llm as agent_llm
    from causaldeploy.agent.loop import run_agent
    from causaldeploy.agent.persist import save_llm_calls
    from causaldeploy.mcp_server.server import build_server

    llm = agent_llm.build_llm_client()
    if llm is None:
        return None
    async with Client(build_server(repo, store)) as client:
        run = await run_agent(client, store, incident_id, llm)
    await save_llm_calls(run)
    return run


def _rank_and_persist(store: GraphStore, incident_id: str) -> RankResult:
    result = rank(store, incident_id, "graph")
    persist(store, result)
    return result


def investigation_record(result: RankResult, rank_ms: float) -> dict[str, Any]:
    """What the ranking was computed from (#89): reproducible inputs, weights version, time."""
    return {
        "inputs": {
            "incident_id": result.incident_id,
            "lookback_hours": get_settings().lookback_hours,
            "candidate_ids": [c.node_id for c in result.candidates],
            "weights_version": result.weights_version,
        },
        "scorer_version": result.weights_version,
        "duration_ms": round(rank_ms, 3),
    }


def summary(result: RankResult) -> dict[str, Any]:
    top = result.candidates[0] if result.candidates else None
    return {"verdict": result.verdict, "top": top.sha if top else None, "margin": result.margin}
