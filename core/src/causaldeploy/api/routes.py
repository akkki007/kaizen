import asyncio
import hashlib
import secrets
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from causaldeploy.api import dashboard_views
from causaldeploy.api.auth import CurrentUser, csrf_protect, require_role
from causaldeploy.api.dashboard_views import SuspectSummary
from causaldeploy.api.deps import CallerDep, IngestCaller, charge_events, hash_key
from causaldeploy.api.errors import envelope
from causaldeploy.api.incident_views import (
    IncidentMetrics,
    NodeDetail,
    SimilarIncidentView,
    add_overlay,
    error_samples,
    incident_metrics,
    node_detail,
    similar_incidents,
    status_snapshot,
)
from causaldeploy.config import get_settings
from causaldeploy.contracts import Event, HealthEvent, TelemetryEvent
from causaldeploy.contracts.domain import IncidentStatus
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
from causaldeploy.detect.errors import event_key, store_telemetry_many
from causaldeploy.github.installations import INSTALL_EVENTS, record_installation
from causaldeploy.github.webhooks import normalize, verify_signature
from causaldeploy.graph.evidence import EvidenceGraph, evidence_graph
from causaldeploy.graph.store import GraphStore
from causaldeploy.investigate.scorer import Method, RankResult, persist, rank
from causaldeploy.jobs import queue as jobs_queue
from causaldeploy.jobs.handlers import needs_graph_work
from causaldeploy.jobs.queue import (
    BackfillPayload,
    GuardPayload,
    IngestEventPayload,
    MergedPayload,
    ProposeFixPayload,
    TelemetryBatchPayload,
    VerifyResultPayload,
    enqueue,
)
from causaldeploy.observability import EVENTS_INGESTED
from causaldeploy.remediate.service import KAIZEN_PREFIXES, is_kaizen_branch

v1 = APIRouter(prefix="/api/v1")
webhooks = APIRouter(prefix="/webhooks")
_event: TypeAdapter[Event] = TypeAdapter(Event)


class IncidentSummary(BaseModel):
    id: str
    repo: str
    service: str
    env: str
    endpoint: str
    status: str
    onset_ts: datetime
    detected_at: datetime
    severity: str | None = None  # sev1..sev3, from the detector
    acknowledged_by: str | None = None
    simulated: bool | None = (
        None  # true when created by the demo's "inject a problem"; the traffic was synthetic
    )
    # top candidate of the latest investigation; None before ranking or when it abstained
    suspect: SuspectSummary | None = None


async def simulated_ids(ids: list[str]) -> set[str]:
    """Incidents the demo created with its simulated traffic (recorded in the audit log)."""
    if not ids:
        return set()
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(AuditLog.detail["incident_id"].as_string()).where(
                    AuditLog.action == "demo.inject",
                    AuditLog.detail["incident_id"].as_string().in_(ids),
                )
            )
        ).all()
    return {r[0] for r in rows}


def _summary(
    r: IncidentRow, suspect: SuspectSummary | None = None, simulated: bool = False
) -> IncidentSummary:
    return IncidentSummary(
        id=r.id,
        repo=r.repo,
        service=r.service,
        env=r.env,
        endpoint=r.endpoint,
        status=r.status,
        onset_ts=r.onset_ts,
        detected_at=r.detected_at,
        severity=r.severity,
        acknowledged_by=r.acknowledged_by,
        simulated=simulated or None,
        suspect=suspect,
    )


class IncidentPage(BaseModel):
    """Pagination convention: `limit`/`offset` in, `items` + `total` out."""

    items: list[IncidentSummary]
    total: int
    limit: int
    offset: int


class RejectedEvent(BaseModel):
    index: int  # position in the posted array
    error: str


class AcceptedEvents(BaseModel):
    accepted: int
    # background jobs created: one per non-telemetry event, plus one per batch for telemetry
    # graph work (telemetry rows themselves are stored before the response)
    job_ids: list[str]
    rejected: list[RejectedEvent] = []


def _event_error(e: ValidationError) -> str:
    parts = [
        f"{'.'.join(str(x) for x in err['loc']) or 'event'}: {err['msg']}"
        for err in e.errors(include_url=False, include_context=False)[:5]
    ]
    return "; ".join(parts)[:500]


_ENQUEUE_CONCURRENCY = 8


@v1.post("/events", response_model=AcceptedEvents, status_code=202)
async def post_events(body: list[Any], caller: IngestCaller = CallerDep) -> Any:
    """Batched event ingest (commit/deployment/build/telemetry/health/verification_result).

    Items are validated one by one: valid events are queued, invalid ones come back in
    `rejected` with their index. 413 above `ingest_max_batch` events (or `ingest_max_bytes`,
    enforced by middleware), 429 + Retry-After when the key's events/minute are used up,
    422 when no item is valid, 403 when an event names another repository.
    """
    settings = get_settings()
    if len(body) > settings.ingest_max_batch:
        raise HTTPException(413, f"batch of {len(body)} events exceeds {settings.ingest_max_batch}")
    charge_events(caller, len(body))
    events: list[Event] = []
    rejected: list[RejectedEvent] = []
    for i, item in enumerate(body):
        try:
            events.append(_event.validate_python(item))
        except ValidationError as e:
            rejected.append(RejectedEvent(index=i, error=_event_error(e)))
    if rejected and not events:
        return envelope(422, "no valid events in the batch", [r.model_dump() for r in rejected])
    if any(ev.repo != caller.repo for ev in events):
        raise HTTPException(403, "API key is not scoped to this repository")
    for ev in events:
        EVENTS_INGESTED.labels(ev.type).inc()
    # Telemetry fast path (#92 throughput): the batch is stored with one idempotent INSERT and
    # its graph work becomes ONE job; other event types stay one job per event.
    telemetry = [ev for ev in events if isinstance(ev, TelemetryEvent | HealthEvent)]
    others = [ev for ev in events if not isinstance(ev, TelemetryEvent | HealthEvent)]
    allowed = settings.is_allowed(caller.repo)
    if telemetry and allowed:
        await store_telemetry_many(telemetry)
    graph_work = [ev for ev in telemetry if needs_graph_work(ev)] if allowed else []
    gate = asyncio.Semaphore(_ENQUEUE_CONCURRENCY)
    # one Redis connection for the whole batch
    pool = await jobs_queue.get_pool() if others or graph_work else None

    async def one(ev: Event) -> str:
        digest = hashlib.sha256(ev.model_dump_json().encode()).hexdigest()
        async with gate:
            job_id, _ = await enqueue(
                "ingest_event",
                IngestEventPayload(event=ev.model_dump(mode="json")),
                f"event:{digest}",
                pool=pool,
            )
        return job_id

    async def batch() -> str:
        keys = "".join(event_key(ev) for ev in graph_work)
        job_id, _ = await enqueue(
            "ingest_telemetry_batch",
            TelemetryBatchPayload(
                repo=caller.repo, events=[ev.model_dump(mode="json") for ev in graph_work]
            ),
            f"telemetry-batch:{hashlib.sha256(keys.encode()).hexdigest()}",
            pool=pool,
        )
        return job_id

    try:
        ids = list(await asyncio.gather(*(one(ev) for ev in others)))
        if graph_work:
            ids.append(await batch())
    finally:
        if pool is not None:
            await pool.aclose()
    return AcceptedEvents(accepted=len(events), job_ids=ids, rejected=rejected)


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
    status: Annotated[list[IncidentStatus] | None, Query()] = None,
    severity: Annotated[list[Literal["sev1", "sev2", "sev3"]] | None, Query()] = None,
    service: Annotated[str | None, Query(max_length=128)] = None,
    repo: Annotated[str | None, Query(max_length=255)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> IncidentPage:
    """Newest first. Filters combine with AND; `status`/`severity` repeat for several values
    (e.g. `?status=resolved&status=closed` is the archive); `since`/`until` bound the onset;
    `q` matches id, repo, service, env or endpoint (case-insensitive substring)."""
    from sqlalchemy import func

    conds: list[Any] = [IncidentRow.repo.in_(list(user.repos))]
    if status:
        conds.append(IncidentRow.status.in_([str(x) for x in status]))
    if severity:
        conds.append(IncidentRow.severity.in_(severity))
    if service:
        conds.append(IncidentRow.service == service)
    if repo:
        conds.append(IncidentRow.repo == repo)
    if since:
        conds.append(IncidentRow.onset_ts >= since)
    if until:
        conds.append(IncidentRow.onset_ts <= until)
    if q and q.strip():
        conds.append(dashboard_views.text_filter(q.strip()))
    async with session_scope() as s:
        total = (
            await s.execute(select(func.count()).select_from(IncidentRow).where(*conds))
        ).scalar_one()
        rows = (
            (
                await s.execute(
                    select(IncidentRow)
                    .where(*conds)
                    .order_by(IncidentRow.detected_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
    suspects = await dashboard_views.latest_suspects([r.id for r in rows])
    sim = await simulated_ids([r.id for r in rows])
    items = [_summary(r, suspects.get(r.id), r.id in sim) for r in rows]
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
    incident_id: str,
    user: CurrentUser,
    expand: str | None = None,
    overlay: Literal["blast", "history"] | None = None,
) -> EvidenceGraph:
    """Evidence subgraph. `overlay=blast` adds what the culprit's changed functions can reach
    (callers, endpoints, services); `overlay=history` adds resembling incidents and their fixes.
    Overlay nodes/edges carry `overlay` and are not citable by explanations."""
    row = await _incident_for(user, incident_id)
    store = GraphStore(row.repo)
    graph = await asyncio.to_thread(evidence_graph, store, incident_id, expand)
    if graph is None:
        raise HTTPException(404, "incident has no evidence in the graph yet")
    if overlay is not None:
        graph = await asyncio.to_thread(add_overlay, store, graph, overlay)
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
    kind: str  # revert | patch
    status: str  # proposing|verifying|verified|failed|needs_manual|approved|merged|rejected
    sha: str | None
    branch: str | None
    pr_url: str | None
    approved_by: str | None
    # verification: passed, conclusion, run_url, error_rate_before/after, baseline_error_rate,
    # p95_ms, metrics {failing, fixed, baseline}, inputs, config, verdict {passed, checks,
    # reasons}, tests, check {conclusion, posted}
    verification: dict[str, Any] | None
    pr_number: int | None = None
    diff: str | None = None  # unified diff of the fix branch against its base
    rationale: str | None = None
    labels: list[str] = []
    rejected_by: str | None = None
    reject_reason: str | None = None
    check_status: str | None = None  # conclusion of the GitHub check run Kaizen posted
    merge: dict[str, Any] | None = None  # "app" merge policy outcome
    prior_fixes: list[dict[str, Any]] = []  # similar resolved incidents and their fixes
    simulated: bool | None = None  # demo fix: no pull request is opened, nothing is merged


class IncidentDetail(BaseModel):
    incident: IncidentSummary
    ranking: RankResult  # deterministic, recomputed from the graph so it is always current
    investigation: InvestigationView
    remediation: RemediationView | None
    # people's decisions on the incident (#88/#91), for the incident page
    severity: str | None = None
    acknowledged_by: str | None = None
    confirmed_root_cause: str | None = None  # commit sha
    root_cause_status: str | None = None  # confirmed | rejected


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
    summary = _summary(
        row,
        dashboard_views.suspect_from_ranking((inv.result or {}).get("ranking") if inv else None),
        incident_id in await simulated_ids([incident_id]),
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
            pr_number=r.get("pr_number"),
            diff=r.get("diff"),
            rationale=r.get("rationale"),
            labels=list(r.get("labels") or []),
            rejected_by=rem.rejected_by,
            reject_reason=rem.reject_reason,
            check_status=((r.get("verification") or {}).get("check") or {}).get("conclusion"),
            merge=r.get("merge"),
            prior_fixes=list(r.get("prior_fixes") or []),
            simulated=True if r.get("simulated") else None,
        )
    return IncidentDetail(
        incident=summary,
        ranking=ranking,
        investigation=view,
        remediation=remediation,
        severity=row.severity,
        acknowledged_by=row.acknowledged_by,
        confirmed_root_cause=row.confirmed_root_cause,
        root_cause_status=row.root_cause_status,
    )


@v1.post(
    "/incidents/{incident_id}/remediations", status_code=202, dependencies=[Depends(csrf_protect)]
)
async def propose_remediation(incident_id: str, user: CurrentUser) -> dict[str, str]:
    """Approver-only: open a revert PR for the top-ranked commit and start verification."""
    row = await _incident_for(user, incident_id)
    from causaldeploy.demo import remediate as demo_fix

    if await demo_fix.is_simulated(incident_id) and get_settings().is_demo_repo(row.repo):
        # demo incident: the same flow without touching GitHub, open to the viewer-only demo user
        require_role(user, row.repo, "viewer")
        async with session_scope() as s:
            s.add(
                AuditLog(
                    actor=user.login,
                    action="remediation.propose_simulated",
                    target=incident_id,
                    detail={"repo": row.repo},
                )
            )
        if await demo_fix.propose(row.repo, incident_id) is None:
            raise HTTPException(409, "no suspect to revert for this incident")
        return {"job_id": "", "status": "simulated"}
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


class IncidentActionIn(BaseModel):
    action: Literal["acknowledge", "close", "reopen"]
    reason: str | None = Field(default=None, max_length=2000)


class IncidentActionOut(BaseModel):
    id: str
    status: str
    severity: str | None
    acknowledged_by: str | None


class IncidentTransitionOut(BaseModel):
    from_status: str | None
    to_status: str
    actor: str
    reason: str
    at: datetime


@v1.post(
    "/incidents/{incident_id}/actions",
    response_model=IncidentActionOut,
    dependencies=[Depends(csrf_protect)],
)
async def incident_action(
    incident_id: str, body: IncidentActionIn, user: CurrentUser
) -> IncidentActionOut:
    """acknowledge (viewer+: records who is on it, no status change), close / reopen
    (approver: lifecycle transition to closed / open). Illegal transitions are 409."""
    from causaldeploy.contracts.domain import IncidentStatus
    from causaldeploy.incidents import lifecycle

    row = await _incident_for(user, incident_id)
    require_role(user, row.repo, "viewer" if body.action == "acknowledge" else "approver")
    reason = (body.reason or "").strip()
    target = {"close": IncidentStatus.CLOSED, "reopen": IncidentStatus.OPEN}.get(body.action)
    async with session_scope() as s:
        inc = (
            await s.execute(
                select(IncidentRow).where(IncidentRow.id == incident_id).with_for_update()
            )
        ).scalar_one()
        if target is None:
            if IncidentStatus(inc.status) in lifecycle.TERMINAL:
                raise HTTPException(409, f"incident is {inc.status}; reopen it to acknowledge")
            if inc.acknowledged_by is None:
                inc.acknowledged_by = user.login
        else:
            if inc.status == target:
                raise HTTPException(409, f"incident is already {inc.status}")
            try:
                await lifecycle.apply(s, inc, target, actor=user.login, reason=reason)
            except lifecycle.InvalidTransition as e:
                raise HTTPException(409, str(e)) from e
            if target == IncidentStatus.OPEN:
                inc.acknowledged_by, inc.resolved_at = None, None
        s.add(
            AuditLog(
                actor=user.login,
                action=f"incident.{body.action}",
                target=incident_id,
                detail={"repo": inc.repo, "reason": reason, "status": inc.status},
            )
        )
        out = IncidentActionOut(
            id=inc.id,
            status=inc.status,
            severity=inc.severity,
            acknowledged_by=inc.acknowledged_by,
        )
    if target is not None:
        await lifecycle.sync_graph(row.repo, incident_id, target)
    return out


@v1.get("/incidents/{incident_id}/history", response_model=list[IncidentTransitionOut])
async def incident_history(incident_id: str, user: CurrentUser) -> list[IncidentTransitionOut]:
    """Every status change, oldest first: who made it, why and when."""
    from causaldeploy.incidents import lifecycle

    await _incident_for(user, incident_id)
    return [
        IncidentTransitionOut(
            from_status=t.from_status,
            to_status=t.to_status,
            actor=t.actor,
            reason=t.reason or "",
            at=t.created_at,
        )
        for t in await lifecycle.history(incident_id)
    ]


@v1.post("/remediations/{remediation_id}/approve", dependencies=[Depends(csrf_protect)])
async def approve_remediation(remediation_id: str, user: CurrentUser) -> dict[str, str]:
    """Approver-only, verified fixes only. With CD_REMEDIATION_MERGE_POLICY=app Kaizen then
    merges the PR via the API (the merge webhook resolves the incident); with "manual" (the
    default) a person merges on GitHub."""
    from causaldeploy.remediate import service as remediation

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
        simulated = bool((run.result or {}).get("simulated")) and get_settings().is_demo_repo(
            inc.repo
        )
        require_role(user, inc.repo, "viewer" if simulated else "approver")
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
    out = {
        "id": remediation_id,
        "status": "approved",
        "approved_by": user.login,
        "merge": "manual",
    }
    if simulated:
        from causaldeploy.demo import remediate as demo_fix

        await demo_fix.complete(inc.repo, remediation_id, f"{user.login} (simulated merge)")
        out["merge"] = "simulated"
        return out
    if get_settings().remediation_merge_policy == "app":
        merged = await remediation.merge_approved(remediation_id)
        out["merge"] = str(merged["status"])  # requested | failed
    return out


class RejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class RejectResult(BaseModel):
    id: str
    status: str  # "rejected"
    rejected_by: str
    reject_reason: str
    incident_status: str


async def _remediation_for(user: CurrentUser, remediation_id: str) -> tuple[str, str]:
    """(repo, incident id) of a remediation the user can see; 404 otherwise."""
    async with session_scope() as s:
        run = await s.get(RemediationRun, remediation_id)
        inc = await s.get(IncidentRow, run.incident_id) if run is not None else None
    if run is None or inc is None or inc.repo not in user.repos:
        raise HTTPException(404, "remediation not found")
    return inc.repo, inc.id


@v1.post(
    "/remediations/{remediation_id}/reject",
    response_model=RejectResult,
    dependencies=[Depends(csrf_protect)],
)
async def reject_remediation(
    remediation_id: str, body: RejectBody, user: CurrentUser
) -> RejectResult:
    """Approver-only: reject a fix. Closes its PR, marks the Fix not accepted, and moves the
    incident back (REMEDIATING -> IDENTIFIED/EXPLAINED, VERIFYING -> NEEDS_HUMAN)."""
    from causaldeploy.demo import remediate as demo_fix
    from causaldeploy.remediate import service as remediation

    repo, incident_id = await _remediation_for(user, remediation_id)
    sim = await demo_fix.simulated_run(remediation_id) is not None and get_settings().is_demo_repo(
        repo
    )
    require_role(user, repo, "viewer" if sim else "approver")
    try:
        run = await remediation.reject_remediation(remediation_id, user.login, body.reason)
    except remediation.RemediationStateError as e:
        raise HTTPException(409, str(e)) from e
    async with session_scope() as s:
        inc = await s.get(IncidentRow, incident_id)
        assert inc is not None
        incident_status = inc.status
    return RejectResult(
        id=run.id,
        status=run.status,
        rejected_by=run.rejected_by or user.login,
        reject_reason=run.reject_reason or body.reason,
        incident_status=incident_status,
    )


class RootCauseBody(BaseModel):
    sha: str = Field(min_length=7, max_length=64)
    confirmed: bool
    reason: str | None = Field(None, max_length=2000)


class RootCauseResult(BaseModel):
    incident_id: str
    sha: str
    root_cause_status: str  # confirmed | rejected
    confirmed_root_cause: str | None


@v1.post(
    "/incidents/{incident_id}/root-cause",
    response_model=RootCauseResult,
    dependencies=[Depends(csrf_protect)],
)
async def set_incident_root_cause(
    incident_id: str, body: RootCauseBody, user: CurrentUser
) -> RootCauseResult:
    """Approver-only: confirm (Incident-[:confirmed_cause]->Commit) or reject (the
    likely_caused_by edge gets rejected=true) a suspect commit as the root cause."""
    from causaldeploy.memory.root_cause import UnknownCommit, set_root_cause

    row = await _incident_for(user, incident_id)
    require_role(user, row.repo, "approver")
    try:
        updated = await set_root_cause(
            incident_id, body.sha, body.confirmed, user.login, body.reason
        )
    except UnknownCommit as e:
        raise HTTPException(422, f"commit {body.sha} is not in this repository's graph") from e
    return RootCauseResult(
        incident_id=incident_id,
        sha=body.sha,
        root_cause_status=updated.root_cause_status or "",
        confirmed_root_cause=updated.confirmed_root_cause,
    )


# -- incident page views (#93): read-only -------------------------------------------------

# SSE pacing; module-level so tests can shorten them
STREAM_POLL_S = 2.0
STREAM_HEARTBEAT_S = 15.0
STREAM_MAX_S = 600.0


@v1.get("/incidents/{incident_id}/metrics", response_model=IncidentMetrics)
async def get_incident_metrics(
    incident_id: str,
    user: CurrentUser,
    minutes: Annotated[int, Query(ge=10, le=24 * 60)] = 120,
) -> IncidentMetrics:
    """1-minute request/error/p95 buckets for the incident's service/env/endpoint around the
    onset (2/3 of the window before it), plus the deployments to that env in the window."""
    row = await _incident_for(user, incident_id)
    return await incident_metrics(row, minutes)


@v1.get("/incidents/{incident_id}/similar", response_model=list[SimilarIncidentView])
async def get_similar_incidents(incident_id: str, user: CurrentUser) -> list[SimilarIncidentView]:
    """Incidents this one `resembles` (weighted-Jaccard signature match), best first, with the
    fix that resolved each one and its confirmed root cause."""
    row = await _incident_for(user, incident_id)
    return await similar_incidents(row)


@v1.get(
    "/incidents/{incident_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_incident_status(
    incident_id: str, user: CurrentUser, request: Request
) -> StreamingResponse:
    """Server-Sent Events: `event: status` with {status, updated_at, remediation_status,
    investigation_status} on connect and whenever one changes (polled every ~2 s); a heartbeat
    comment every 15 s; the stream ends after 10 minutes or when the client goes away."""
    await _incident_for(user, incident_id)
    loop = asyncio.get_running_loop()

    async def events() -> AsyncIterator[str]:
        started = last_beat = loop.time()
        last: str | None = None
        while loop.time() - started < STREAM_MAX_S:
            if await request.is_disconnected():
                return
            snap = await status_snapshot(incident_id)
            if snap is None:
                return
            body = snap.model_dump_json()
            if body != last:
                last = body
                last_beat = loop.time()
                yield f"event: status\ndata: {body}\n\n"
            elif loop.time() - last_beat >= STREAM_HEARTBEAT_S:
                last_beat = loop.time()
                yield ": heartbeat\n\n"
            await asyncio.sleep(STREAM_POLL_S)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@v1.get("/incidents/{incident_id}/nodes/{node_id:path}", response_model=NodeDetail)
async def get_incident_node(incident_id: str, node_id: str, user: CurrentUser) -> NodeDetail:
    """Node inspector: capped props and neighbours from the incident's repository graph; a
    Commit adds message/author and the changed functions with their line ranges, a Function its
    location and callers/callees, an Error its most recent samples from telemetry."""
    row = await _incident_for(user, incident_id)
    detail = await asyncio.to_thread(node_detail, GraphStore(row.repo), node_id)
    if detail is None:
        raise HTTPException(404, "node not found in this repository's graph")
    if detail.label == "Error":
        detail.error_samples = await error_samples(row.repo, node_id.partition(":")[2])
    return detail


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


async def _is_imported(repo: str) -> bool:
    """Pushes keep an already-imported repository current; they never start a first import."""
    async with session_scope() as s:
        row = (
            await s.execute(select(Repository).where(Repository.full_name == repo))
        ).scalar_one_or_none()
    return row is not None and row.backfilled_at is not None


async def dispatch_github(delivery: str, event_type: str, payload: dict[str, Any]) -> list[str]:
    """Normalize a stored delivery and enqueue its effects (shared with the replay tool)."""
    ids: list[str] = []
    repo = (payload.get("repository") or {}).get("full_name")
    if repo and not get_settings().is_allowed(repo):
        return ids  # stored raw by the receiver; never acted on
    if event_type in INSTALL_EVENTS and payload.get("installation"):
        # Installing only records the repositories. Importing starts when the user presses
        # "Start monitoring" in the connect flow (api/onboarding.py), so they choose what is read.
        await record_installation(payload)
    if event_type == "push" and repo and await _is_imported(repo):
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
        if run.get("name") == "verify" and title.startswith(
            tuple("verify " + p for p in KAIZEN_PREFIXES)
        ):
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
    if (
        event_type == "pull_request"
        and payload.get("action") in ("opened", "synchronize", "reopened")
        and repo
    ):
        pr = payload.get("pull_request") or {}
        head = str((pr.get("head") or {}).get("ref") or "")
        if not is_kaizen_branch(head):  # our own fix PRs need no warning
            jid, _ = await enqueue(
                "guard_pr",
                GuardPayload(repo=repo, number=int(pr["number"]), installation_id=installation),
                f"guard:{repo}:{pr['number']}:{(pr.get('head') or {}).get('sha')}",
            )
            ids.append(jid)
    if event_type == "pull_request" and payload.get("action") == "closed" and repo:
        pr = payload.get("pull_request") or {}
        head = str((pr.get("head") or {}).get("ref") or "")
        if pr.get("merged") and is_kaizen_branch(head):
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


# -- dashboard (#93): read-only views --------------------------------------------------------


def _visible_repo(user: CurrentUser, repo: str) -> str:
    """404 (not 403) for a repository the user cannot see: do not reveal that it exists."""
    if repo not in user.repos:
        raise HTTPException(404, "repository not found")
    return repo


@v1.get("/services/health", response_model=list[dashboard_views.ServiceHealth])
async def services_health(
    user: CurrentUser, repo: Annotated[str | None, Query(max_length=255)] = None
) -> list[dashboard_views.ServiceHealth]:
    """Per service+env: error rate and p95 of the latest complete detection windows against
    the baseline before them; `incident` while an incident is active, `degraded` when the
    current windows are anomalous but no incident is open yet. Without `repo`: every
    repository the user can see."""
    repos = [_visible_repo(user, repo)] if repo else sorted(user.repos)
    out: list[dashboard_views.ServiceHealth] = []
    for name in repos:
        out += await dashboard_views.services_health(name)
    return out


@v1.get("/repos/{owner}/{name}/deployments", response_model=list[dashboard_views.DeploymentView])
async def repo_deployments(
    owner: str,
    name: str,
    user: CurrentUser,
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 72,
) -> list[dashboard_views.DeploymentView]:
    """Deployments of the last `hours`, oldest first, with the incidents that followed each."""
    repo = _visible_repo(user, f"{owner}/{name}")
    return await dashboard_views.deployments(repo, hours)


@v1.get("/repos/{owner}/{name}/hotspots", response_model=dashboard_views.Hotspots)
async def repo_hotspots(
    owner: str,
    name: str,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dashboard_views.Hotspots:
    """Functions and dependencies most often blamed: incidents whose likely (not rejected) or
    confirmed cause modified the function / changed the dependency, plus top-ranked
    dependency-change suspects."""
    repo = _visible_repo(user, f"{owner}/{name}")
    return await dashboard_views.hotspots(repo, limit)


@v1.get("/eval/latest", response_model=dashboard_views.EvalResult)
async def eval_latest(user: CurrentUser) -> dashboard_views.EvalResult:
    """The latest evaluation result (docs/results/latest.json, written by `make eval`),
    unchanged: generated_at, weights_version, methods, splits {all, tune, test}, blast_radius,
    fix_verification_rate, memory, runtime_seconds, scenarios, failures."""
    try:
        return await asyncio.to_thread(dashboard_views.eval_latest)
    except dashboard_views.EvalMissing as e:
        raise HTTPException(404, str(e)) from e
    except dashboard_views.EvalInvalid as e:
        raise HTTPException(500, str(e)) from e
