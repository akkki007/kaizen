import asyncio
import hashlib
import secrets
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from causaldeploy.api.auth import CurrentUser, csrf_protect, require_role
from causaldeploy.api.deps import RepoDep, hash_key
from causaldeploy.config import get_settings
from causaldeploy.contracts import Event
from causaldeploy.db.models import (
    ApiKey,
    AuditLog,
    IncidentRow,
    Investigation,
    JobRecord,
    LlmCall,
    RawEvent,
    RemediationRun,
    Repository,
)
from causaldeploy.db.session import session_scope
from causaldeploy.github.installations import INSTALL_EVENTS, record_installation
from causaldeploy.github.webhooks import normalize, verify_signature
from causaldeploy.graph.evidence import EvidenceGraph, evidence_graph
from causaldeploy.graph.store import GraphStore
from causaldeploy.investigate.scorer import Method, RankResult, persist, rank
from causaldeploy.jobs.queue import (
    BackfillPayload,
    IngestEventPayload,
    MergedPayload,
    ProposeFixPayload,
    VerifyResultPayload,
    enqueue,
)
from causaldeploy.observability import EVENTS_INGESTED
from causaldeploy.remediate.service import BRANCH_PREFIX

v1 = APIRouter(prefix="/api/v1")
webhooks = APIRouter(prefix="/webhooks")
_events = TypeAdapter(list[Event])


class IncidentSummary(BaseModel):
    id: str
    repo: str
    service: str
    env: str
    endpoint: str
    status: str
    onset_ts: datetime
    detected_at: datetime


class IncidentPage(BaseModel):
    """Pagination convention: `limit`/`offset` in, `items` + `total` out."""

    items: list[IncidentSummary]
    total: int
    limit: int
    offset: int


class AcceptedEvents(BaseModel):
    accepted: int
    job_ids: list[str]


@v1.post("/events", response_model=AcceptedEvents, status_code=202)
async def post_events(body: list[dict[str, Any]], repo: str = RepoDep) -> AcceptedEvents:
    """Batched event ingest (commit/deployment/build/telemetry/health/verification_result)."""
    try:
        events = _events.validate_python(body)
    except ValidationError as e:
        raise HTTPException(
            422, e.errors(include_url=False, include_context=False).__repr__()
        ) from e
    if any(ev.repo != repo for ev in events):
        raise HTTPException(403, "API key is not scoped to this repository")
    ids = []
    for ev in events:
        EVENTS_INGESTED.labels(ev.type).inc()
        digest = hashlib.sha256(ev.model_dump_json().encode()).hexdigest()
        job_id, _ = await enqueue(
            "ingest_event", IngestEventPayload(event=ev.model_dump(mode="json")), f"event:{digest}"
        )
        ids.append(job_id)
    return AcceptedEvents(accepted=len(events), job_ids=ids)


@v1.get("/jobs/{job_id}")
async def get_job(job_id: str, user: CurrentUser) -> dict[str, Any]:
    async with session_scope() as s:
        rec = (
            await s.execute(select(JobRecord).where(JobRecord.id == job_id))
        ).scalar_one_or_none()
    if rec is None:
        raise HTTPException(404, "job not found")
    payload = rec.payload
    repo = payload.get("repo") or (payload.get("event") or {}).get("repo")
    require_role(user, str(repo), "viewer")
    return {
        "id": rec.id,
        "kind": rec.kind,
        "status": rec.status,
        "attempts": rec.attempts,
        "error": rec.error,
    }


@v1.get("/incidents", response_model=IncidentPage)
async def list_incidents(
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> IncidentPage:
    from sqlalchemy import func

    scope = IncidentRow.repo.in_(list(user.repos))
    async with session_scope() as s:
        total = (
            await s.execute(select(func.count()).select_from(IncidentRow).where(scope))
        ).scalar_one()
        rows = (
            (
                await s.execute(
                    select(IncidentRow)
                    .where(scope)
                    .order_by(IncidentRow.detected_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
    items = [
        IncidentSummary(
            id=r.id,
            repo=r.repo,
            service=r.service,
            env=r.env,
            endpoint=r.endpoint,
            status=r.status,
            onset_ts=r.onset_ts,
            detected_at=r.detected_at,
        )
        for r in rows
    ]
    return IncidentPage(items=items, total=total, limit=limit, offset=offset)


async def _incident_for(user: CurrentUser, incident_id: str) -> IncidentRow:
    async with session_scope() as s:
        row = (
            await s.execute(select(IncidentRow).where(IncidentRow.id == incident_id))
        ).scalar_one_or_none()
    if row is None or row.repo not in user.repos:  # same answer: do not leak other repos' ids
        raise HTTPException(404, "incident not found")
    return row


@v1.get("/incidents/{incident_id}/evidence-graph", response_model=EvidenceGraph)
async def get_evidence_graph(
    incident_id: str, user: CurrentUser, expand: str | None = None
) -> EvidenceGraph:
    row = await _incident_for(user, incident_id)
    graph = await asyncio.to_thread(evidence_graph, GraphStore(row.repo), incident_id, expand)
    if graph is None:
        raise HTTPException(404, "incident has no evidence in the graph yet")
    return graph


class TraceStep(BaseModel):
    n: int
    name: str
    summary: str
    latency_ms: float
    node_ids: list[str]
    args: dict[str, Any]


class InvestigationView(BaseModel):
    id: int | None
    status: str  # explained | explanation_invalid | not_run
    explanation: dict[str, Any] | None
    steps: list[TraceStep]
    validation_errors: list[str]


class RemediationView(BaseModel):
    id: str
    kind: str
    status: str  # proposing|verifying|verified|failed|needs_manual|approved|merged
    sha: str | None
    branch: str | None
    pr_url: str | None
    approved_by: str | None
    verification: dict[str, Any] | None


class IncidentDetail(BaseModel):
    incident: IncidentSummary
    ranking: RankResult  # deterministic, recomputed from the graph so it is always current
    investigation: InvestigationView
    remediation: RemediationView | None


@v1.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def get_incident_detail(incident_id: str, user: CurrentUser) -> IncidentDetail:
    """Everything the incident page needs: suspects with feature queries, agent trace, status."""
    row = await _incident_for(user, incident_id)
    store = GraphStore(row.repo)
    ranking = await asyncio.to_thread(rank, store, incident_id, "graph")
    async with session_scope() as s:
        inv = (
            await s.execute(
                select(Investigation)
                .where(Investigation.incident_id == incident_id)
                .order_by(Investigation.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    agent = (inv.result or {}).get("agent") if inv else None
    steps = [
        TraceStep(
            n=st["n"],
            name=st["name"],
            summary=st["summary"],
            latency_ms=st["latency_ms"],
            node_ids=st["node_ids"],
            args=st["args"],
        )
        for st in (agent or {}).get("steps", [])
    ]
    view = InvestigationView(
        id=inv.id if inv else None,
        status=(agent or {}).get("status", "not_run"),
        explanation=(agent or {}).get("explanation"),
        steps=steps,
        validation_errors=(agent or {}).get("validation_errors", []),
    )
    summary = IncidentSummary(
        id=row.id,
        repo=row.repo,
        service=row.service,
        env=row.env,
        endpoint=row.endpoint,
        status=row.status,
        onset_ts=row.onset_ts,
        detected_at=row.detected_at,
    )
    async with session_scope() as s2:
        rem = (
            await s2.execute(
                select(RemediationRun)
                .where(RemediationRun.incident_id == incident_id)
                .order_by(RemediationRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    remediation = None
    if rem:
        r = rem.result or {}
        remediation = RemediationView(
            id=rem.id,
            kind=rem.kind,
            status=rem.status,
            sha=r.get("sha"),
            branch=r.get("branch"),
            pr_url=r.get("pr_url"),
            approved_by=rem.approved_by,
            verification=r.get("verification"),
        )
    return IncidentDetail(
        incident=summary, ranking=ranking, investigation=view, remediation=remediation
    )


@v1.post(
    "/incidents/{incident_id}/remediations", status_code=202, dependencies=[Depends(csrf_protect)]
)
async def propose_remediation(incident_id: str, user: CurrentUser) -> dict[str, str]:
    """Approver-only: open a revert PR for the top-ranked commit and start verification."""
    row = await _incident_for(user, incident_id)
    require_role(user, row.repo, "approver")
    async with session_scope() as s:
        live = (
            await s.execute(
                select(RemediationRun.id).where(
                    RemediationRun.incident_id == incident_id,
                    RemediationRun.status.in_(("proposing", "verifying", "verified", "approved")),
                )
            )
        ).first()
        if live:
            raise HTTPException(409, "a remediation is already in progress for this incident")
        s.add(
            AuditLog(
                actor=user.login,
                action="remediation.propose",
                target=incident_id,
                detail={"repo": row.repo},
            )
        )
    jid, _ = await enqueue(
        "propose_fix",
        ProposeFixPayload(repo=row.repo, incident_id=incident_id),
        f"propose:{incident_id}:{secrets.token_hex(6)}",
    )
    return {"job_id": jid, "status": "queued"}


@v1.post("/incidents/{incident_id}/rank", response_model=RankResult)
async def rank_incident(
    incident_id: str, user: CurrentUser, method: Method = "graph"
) -> RankResult:
    """Ranked suspect commits with the per-feature breakdown and the Cypher behind each value.

    `method=b0|b1` returns the baselines (no graph traversal) for the "graph off" comparison;
    only the graph ranking is persisted.
    """
    row = await _incident_for(user, incident_id)
    store = GraphStore(row.repo)

    def run() -> RankResult:
        result = rank(store, incident_id, method)
        persist(store, result)
        return result

    return await asyncio.to_thread(run)


class LlmCost(BaseModel):
    incident_id: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms_total: float


@v1.get("/incidents/{incident_id}/llm-cost", response_model=LlmCost)
async def incident_llm_cost(incident_id: str, user: CurrentUser) -> LlmCost:
    from sqlalchemy import func

    await _incident_for(user, incident_id)
    async with session_scope() as s:
        row = (
            await s.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(LlmCall.input_tokens), 0),
                    func.coalesce(func.sum(LlmCall.output_tokens), 0),
                    func.coalesce(func.sum(LlmCall.cost_usd), 0.0),
                    func.coalesce(func.sum(LlmCall.latency_ms), 0.0),
                ).where(LlmCall.incident_id == incident_id)
            )
        ).one()
    return LlmCost(
        incident_id=incident_id,
        calls=row[0],
        input_tokens=int(row[1]),
        output_tokens=int(row[2]),
        cost_usd=float(row[3]),
        latency_ms_total=float(row[4]),
    )


@v1.post("/remediations/{remediation_id}/approve", dependencies=[Depends(csrf_protect)])
async def approve_remediation(remediation_id: str, user: CurrentUser) -> dict[str, str]:
    async with session_scope() as s:
        run = (
            await s.execute(
                select(RemediationRun).where(RemediationRun.id == remediation_id).with_for_update()
            )
        ).scalar_one_or_none()
        inc = None
        if run is not None:
            inc = (
                await s.execute(select(IncidentRow).where(IncidentRow.id == run.incident_id))
            ).scalar_one_or_none()
        if run is None or inc is None or inc.repo not in user.repos:
            raise HTTPException(404, "remediation not found")
        require_role(user, inc.repo, "approver")
        if run.status != "verified":
            raise HTTPException(
                409, f"remediation is {run.status}; only verified fixes can be approved"
            )
        run.status, run.approved_by = "approved", user.login
        s.add(
            AuditLog(
                actor=user.login,
                action="remediation.approve",
                target=remediation_id,
                detail={"repo": inc.repo, "incident": inc.id},
            )
        )
    return {"id": remediation_id, "status": "approved", "approved_by": user.login}


class NewKey(BaseModel):
    id: int
    key: str  # shown once; only its hash is stored


class BackfillJobInfo(BaseModel):
    id: str
    status: str  # queued|running|succeeded|failed|dead
    attempts: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class BackfillStatus(BaseModel):
    """Import state for one repository: the last backfill job and when it last completed."""

    repo: str
    installed: bool  # the App is installed and Kaizen knows the installation id
    backfilled_at: datetime | None
    job: BackfillJobInfo | None


@v1.post(
    "/repos/{owner}/{name}/api-keys",
    response_model=NewKey,
    status_code=201,
    dependencies=[Depends(csrf_protect)],
)
async def create_repo_key(owner: str, name: str, user: CurrentUser, label: str = "") -> NewKey:
    repo = f"{owner}/{name}"
    require_role(user, repo, "approver")
    key = await create_api_key(repo, label)
    async with session_scope() as s:
        key_id = (
            await s.execute(select(ApiKey.id).where(ApiKey.key_hash == hash_key(key)))
        ).scalar_one()
        s.add(
            AuditLog(actor=user.login, action="api_key.create", target=repo, detail={"id": key_id})
        )
    return NewKey(id=key_id, key=key)


@v1.get("/repos/{owner}/{name}/api-keys")
async def list_repo_keys(owner: str, name: str, user: CurrentUser) -> list[dict[str, Any]]:
    repo = f"{owner}/{name}"
    require_role(user, repo, "viewer")
    async with session_scope() as s:
        rows = (
            (await s.execute(select(ApiKey).where(ApiKey.repo == repo).order_by(ApiKey.id)))
            .scalars()
            .all()
        )
    return [
        {"id": r.id, "label": r.label, "created_at": r.created_at, "revoked_at": r.revoked_at}
        for r in rows
    ]


@v1.delete("/repos/{owner}/{name}/api-keys/{key_id}", dependencies=[Depends(csrf_protect)])
async def revoke_repo_key(owner: str, name: str, key_id: int, user: CurrentUser) -> dict[str, str]:
    from datetime import UTC, datetime

    repo = f"{owner}/{name}"
    require_role(user, repo, "approver")
    async with session_scope() as s:
        row = (
            await s.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.repo == repo))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "key not found")
        row.revoked_at = datetime.now(UTC)
        s.add(
            AuditLog(actor=user.login, action="api_key.revoke", target=repo, detail={"id": key_id})
        )
    return {"status": "revoked"}


async def _repo_and_last_backfill(s: Any, repo: str) -> tuple[Repository | None, JobRecord | None]:
    rep = (
        await s.execute(select(Repository).where(Repository.full_name == repo))
    ).scalar_one_or_none()
    job = (
        await s.execute(
            select(JobRecord)
            .where(
                JobRecord.kind == "backfill_repo",
                JobRecord.payload["repo"].as_string() == repo,
            )
            .order_by(JobRecord.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return rep, job


@v1.get("/repos/{owner}/{name}/backfill", response_model=BackfillStatus)
async def get_backfill_status(owner: str, name: str, user: CurrentUser) -> BackfillStatus:
    repo = f"{owner}/{name}"
    require_role(user, repo, "viewer")
    async with session_scope() as s:
        rep, job = await _repo_and_last_backfill(s, repo)
    return BackfillStatus(
        repo=repo,
        installed=rep is not None and rep.installation_id is not None,
        backfilled_at=rep.backfilled_at if rep else None,
        job=BackfillJobInfo(
            id=job.id,
            status=job.status,
            attempts=job.attempts,
            error=job.error,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        if job
        else None,
    )


@v1.post("/repos/{owner}/{name}/backfill", status_code=202, dependencies=[Depends(csrf_protect)])
async def reimport_repo(owner: str, name: str, user: CurrentUser) -> dict[str, str]:
    """Approver-only re-import: clones the repository again and rebuilds its graph."""
    repo = f"{owner}/{name}"
    require_role(user, repo, "approver")
    async with session_scope() as s:
        rep = (
            await s.execute(select(Repository).where(Repository.full_name == repo))
        ).scalar_one_or_none()
        if rep is None or rep.installation_id is None:
            raise HTTPException(409, "the GitHub App is not installed on this repository")
        last = (
            await s.execute(
                select(JobRecord)
                .where(
                    JobRecord.kind == "backfill_repo",
                    JobRecord.payload["repo"].as_string() == repo,
                    JobRecord.status.in_(("queued", "running")),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if last is not None:
            raise HTTPException(409, f"an import is already {last.status} for this repository")
        jid, _ = await enqueue(
            "backfill_repo",
            BackfillPayload(repo=repo, installation_id=rep.installation_id),
            f"reimport:{repo}:{secrets.token_hex(8)}",
        )
        s.add(AuditLog(actor=user.login, action="repo.reimport", target=repo, detail={"job": jid}))
    return {"job_id": jid, "status": "queued"}


@webhooks.post("/github", status_code=202)
async def github_webhook(request: Request) -> dict[str, Any]:
    body = await request.body()
    secret = get_settings().github_webhook_secret.get_secret_value()
    if not secret:
        raise HTTPException(503, "webhook secret not configured")
    if not verify_signature(secret, body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "invalid signature")
    delivery = request.headers.get("x-github-delivery")
    event_type = request.headers.get("x-github-event", "")
    if not delivery:
        raise HTTPException(400, "missing X-GitHub-Delivery")
    try:
        payload = await request.json()
    except ValueError as e:
        raise HTTPException(400, "payload is not JSON") from e
    async with session_scope() as s:
        res = await s.execute(
            insert(RawEvent)
            .values(delivery_id=delivery, source="github", event_type=event_type, payload=payload)
            .on_conflict_do_nothing(index_elements=["delivery_id"])
            .returning(RawEvent.delivery_id)
        )
        fresh = res.scalar_one_or_none() is not None
    if not fresh:
        return {"status": "duplicate"}
    job_ids = await dispatch_github(delivery, event_type, payload)
    return {"status": "accepted", "jobs": job_ids}


async def dispatch_github(delivery: str, event_type: str, payload: dict[str, Any]) -> list[str]:
    """Normalize a stored delivery and enqueue its effects (shared with the replay tool)."""
    ids: list[str] = []
    repo = (payload.get("repository") or {}).get("full_name")
    if repo and not get_settings().is_allowed(repo):
        return ids  # stored raw by the receiver; never acted on
    if event_type in INSTALL_EVENTS and payload.get("installation"):
        inst_id, repos = await record_installation(payload)
        for name in repos:  # initial import when the App is installed
            jid, _ = await enqueue(
                "backfill_repo",
                BackfillPayload(repo=name, installation_id=inst_id),
                f"install:{name}:{inst_id}",
            )
            ids.append(jid)
    if event_type == "push" and repo:
        inst = (payload.get("installation") or {}).get("id")
        jid, _ = await enqueue(
            "backfill_repo",
            BackfillPayload(repo=repo, installation_id=inst),
            f"push:{repo}:{payload.get('after')}",
        )
        ids.append(jid)
    installation = (payload.get("installation") or {}).get("id")
    if event_type == "workflow_run" and payload.get("action") == "completed" and repo:
        run = payload.get("workflow_run") or {}
        title = str(run.get("display_title") or "")
        if run.get("name") == "verify" and title.startswith("verify " + BRANCH_PREFIX):
            jid, _ = await enqueue(
                "verify_result",
                VerifyResultPayload(
                    repo=repo,
                    branch=title.removeprefix("verify ").strip(),
                    conclusion=str(run.get("conclusion") or "failure"),
                    run_id=int(run["id"]),
                    run_url=str(run.get("html_url") or ""),
                    installation_id=installation,
                ),
                f"verify:{repo}:{run['id']}",
            )
            ids.append(jid)
    if event_type == "pull_request" and payload.get("action") == "closed" and repo:
        pr = payload.get("pull_request") or {}
        head = str((pr.get("head") or {}).get("ref") or "")
        if pr.get("merged") and head.startswith(BRANCH_PREFIX):
            jid, _ = await enqueue(
                "remediation_merged",
                MergedPayload(
                    repo=repo,
                    branch=head,
                    merged_by=str((pr.get("merged_by") or {}).get("login") or "unknown"),
                ),
                f"merged:{repo}:{pr.get('number')}",
            )
            ids.append(jid)
    for i, ev in enumerate(normalize(event_type, payload)):
        jid, _ = await enqueue(
            "ingest_event",
            IngestEventPayload(event=ev.model_dump(mode="json")),
            f"github:{delivery}:{i}",
        )
        ids.append(jid)
    return ids


async def create_api_key(repo: str, label: str = "") -> str:
    """Returns the plaintext key once; only its hash is stored."""
    key = "cd_" + secrets.token_urlsafe(32)
    async with session_scope() as s:
        await s.execute(insert(Repository).values(full_name=repo).on_conflict_do_nothing())
        s.add(ApiKey(repo=repo, key_hash=hash_key(key), label=label))
    return key
