"""Connect flow: which repositories a user can start monitoring, and the action that starts it.

Sign-in gives the session the repositories where the App is installed. Installing only records
them; nothing is cloned until an approver presses "Start monitoring" (`connect`), which enqueues
the first import. `GET /onboarding` reports where each repository is in that journey.
"""

import asyncio
import secrets
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from causaldeploy.api.auth import CurrentUser, csrf_protect, require_role
from causaldeploy.config import get_settings
from causaldeploy.db.models import (
    ApiKey,
    AuditLog,
    IncidentRow,
    Installation,
    JobRecord,
    Repository,
)
from causaldeploy.db.session import session_scope
from causaldeploy.github.client import GitHubClient
from causaldeploy.graph.store import GraphStore
from causaldeploy.jobs.queue import BackfillPayload, enqueue

router = APIRouter(prefix="/api/v1")

State = Literal["not_started", "importing", "ready", "failed", "unavailable"]


class GraphStats(BaseModel):
    commits: int = 0
    functions: int = 0
    services: int = 0
    endpoints: int = 0


class RepoOnboarding(BaseModel):
    repo: str
    role: Literal["viewer", "approver"]
    state: State
    detail: str | None = None  # why it failed / is unavailable
    backfilled_at: datetime | None = None
    stats: GraphStats = GraphStats()
    incidents: int = 0
    has_api_key: bool = False


_STATS = {
    "commits": "MATCH (n:Commit) RETURN count(n)",
    "functions": "MATCH (n:Function) RETURN count(n)",
    "services": "MATCH (n:Service) RETURN count(n)",
    "endpoints": "MATCH (n:Endpoint) RETURN count(n)",
}


def _graph_stats(repo: str) -> GraphStats:
    store = GraphStore(repo)
    out = {k: int((store.read(q) or [[0]])[0][0]) for k, q in _STATS.items()}
    return GraphStats(**out)


async def _last_backfill(repo: str) -> JobRecord | None:
    async with session_scope() as s:
        return (
            await s.execute(
                select(JobRecord)
                .where(
                    JobRecord.kind == "backfill_repo", JobRecord.payload["repo"].as_string() == repo
                )
                .order_by(JobRecord.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


async def _describe(repo: str, role: Literal["viewer", "approver"]) -> RepoOnboarding:
    if not get_settings().is_allowed(repo):
        return RepoOnboarding(
            repo=repo,
            role=role,
            state="unavailable",
            detail="This deployment is limited to specific repositories (CD_ALLOWED_REPOS).",
        )
    async with session_scope() as s:
        rep = (
            await s.execute(select(Repository).where(Repository.full_name == repo))
        ).scalar_one_or_none()
        incidents = (
            await s.execute(
                select(func.count()).select_from(IncidentRow).where(IncidentRow.repo == repo)
            )
        ).scalar_one()
        keys = (
            await s.execute(
                select(func.count())
                .select_from(ApiKey)
                .where(ApiKey.repo == repo, ApiKey.revoked_at.is_(None))
            )
        ).scalar_one()
    job = await _last_backfill(repo)
    backfilled = rep.backfilled_at if rep else None
    state: State
    detail = None
    if job is not None and job.status in ("queued", "running"):
        state = "importing"
    elif backfilled is not None:
        state = "ready"
    elif job is not None and job.status in ("failed", "dead"):
        state, detail = "failed", job.error or "the import failed"
    else:
        state = "not_started"
    stats = (
        await asyncio.to_thread(_graph_stats, repo)
        if state in ("importing", "ready")
        else GraphStats()
    )
    return RepoOnboarding(
        repo=repo,
        role=role,
        state=state,
        detail=detail,
        backfilled_at=backfilled,
        stats=stats,
        incidents=incidents,
        has_api_key=keys > 0,
    )


@router.get("/onboarding", response_model=list[RepoOnboarding])
async def onboarding(user: CurrentUser) -> list[RepoOnboarding]:
    rows = [await _describe(repo, role) for repo, role in sorted(user.repos.items())]
    order = {"importing": 0, "ready": 1, "not_started": 2, "failed": 3, "unavailable": 4}
    return sorted(rows, key=lambda r: (order[r.state], r.repo))


class ConnectResult(BaseModel):
    repo: str
    state: State
    job_id: str | None = None


@router.post(
    "/repos/{owner}/{name}/connect",
    response_model=ConnectResult,
    status_code=202,
    dependencies=[Depends(csrf_protect)],
)
async def connect_repo(owner: str, name: str, user: CurrentUser) -> ConnectResult:
    """Start monitoring: record the repository and enqueue its first import. Idempotent."""
    repo = f"{owner}/{name}"
    require_role(user, repo, "approver")
    if not get_settings().is_allowed(repo):
        raise HTTPException(409, "this deployment does not monitor that repository")
    current = await _describe(repo, user.repos[repo])
    if current.state in ("importing", "ready"):
        return ConnectResult(repo=repo, state=current.state)
    async with session_scope() as s:
        rep = (
            await s.execute(select(Repository).where(Repository.full_name == repo))
        ).scalar_one_or_none()
        installation_id = rep.installation_id if rep else None
    if installation_id is None:  # the installation webhook was missed: ask GitHub
        client = GitHubClient()
        try:
            installation_id = await client.repo_installation_id(repo)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from None
        finally:
            await client.aclose()
        async with session_scope() as s:
            await s.execute(
                insert(Installation)
                .values(id=installation_id, account=owner)
                .on_conflict_do_nothing(index_elements=["id"])
            )
            await s.execute(
                insert(Repository)
                .values(full_name=repo, installation_id=installation_id)
                .on_conflict_do_update(
                    index_elements=["full_name"], set_={"installation_id": installation_id}
                )
            )
    jid, _ = await enqueue(
        "backfill_repo",
        BackfillPayload(repo=repo, installation_id=installation_id),
        f"connect:{repo}:{secrets.token_hex(8)}",
    )
    async with session_scope() as s:
        s.add(AuditLog(actor=user.login, action="repo.connect", target=repo, detail={"job": jid}))
    return ConnectResult(repo=repo, state="importing", job_id=jid)
