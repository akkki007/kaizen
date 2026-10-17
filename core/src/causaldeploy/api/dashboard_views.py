"""Read-only views behind the dashboard (#93): suspects on the incident list, service health,
deployment timeline, blame hotspots and the latest evaluation result.

Every function here only reads (Postgres, the repository graph, a JSON file); access checks
stay in the routes.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select, text

from causaldeploy.config import Settings, get_settings
from causaldeploy.db.models import IncidentRow, Investigation
from causaldeploy.db.session import session_scope
from causaldeploy.detect import detector
from causaldeploy.graph.store import GraphStore
from causaldeploy.incidents import lifecycle

# -- incident list: the suspect named by the latest investigation -------------------------


class SuspectSummary(BaseModel):
    sha: str  # commit sha; for a config/dependency change, its node key
    kind: str  # commit | config | dependency
    message: str
    confidence: float


def suspect_from_ranking(ranking: dict[str, Any] | None) -> SuspectSummary | None:
    """Top candidate of a stored ranking, or None when the ranking abstained / is empty."""
    if not ranking or ranking.get("verdict") not in ("confident", "ambiguous"):
        return None
    cands = ranking.get("candidates") or []
    if not cands:
        return None
    top = cands[0]
    return SuspectSummary(
        sha=str(top.get("sha", "")),
        kind=str(top.get("kind") or "commit"),
        message=str(top.get("message") or ""),
        confidence=float(top.get("confidence") or 0.0),
    )


async def latest_suspects(incident_ids: list[str]) -> dict[str, SuspectSummary]:
    """incident id -> suspect, from each incident's most recent Investigation row."""
    if not incident_ids:
        return {}
    latest = (
        select(func.max(Investigation.id))
        .where(Investigation.incident_id.in_(incident_ids))
        .group_by(Investigation.incident_id)
    )
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Investigation.incident_id, Investigation.result["ranking"]).where(
                    Investigation.id.in_(latest)
                )
            )
        ).all()
    out = {}
    for inc_id, ranking in rows:
        sus = suspect_from_ranking(ranking if isinstance(ranking, dict) else None)
        if sus is not None:
            out[str(inc_id)] = sus
    return out


def text_filter(q: str) -> Any:
    """Case-insensitive substring match on the incident's id, repo, service, env, endpoint."""
    from sqlalchemy import or_

    pat = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    cols = (IncidentRow.id, IncidentRow.repo, IncidentRow.service, IncidentRow.env)
    return or_(*(c.ilike(pat, escape="\\") for c in (*cols, IncidentRow.endpoint)))


# -- service health ------------------------------------------------------------------------

_SERVICE_WINDOWS = text(
    """
    SELECT date_bin(make_interval(secs => :w), ts, TIMESTAMPTZ '2000-01-01') AS start,
           service, env,
           count(*) AS n,
           count(*) FILTER (WHERE status_code >= 400
                              OR (kind = 'health' AND (body->>'ok') = 'false')) AS errors,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
    FROM telemetry
    WHERE repo = :repo AND ts >= :since AND ts < :until AND kind IN ('request', 'health')
    GROUP BY 1, 2, 3
    ORDER BY 2, 3, 1
    """
)

HealthStatus = Literal["ok", "degraded", "incident"]


class ServiceHealth(BaseModel):
    repo: str
    service: str
    env: str
    requests: int  # in the current (latest complete) windows
    error_rate: float
    p95_ms: float | None  # highest window p95 of the current windows
    baseline_error_rate: float | None  # mean of the windows before the current ones
    baseline_p95_ms: float | None
    status: HealthStatus  # incident: an active incident; degraded: anomalous, no incident yet
    open_incident_id: str | None
    window_seconds: int
    current_windows: int  # how many windows make up "current"


@dataclass(frozen=True)
class _Current:
    window: detector.Window
    baseline: detector.Baseline | None


def _split_current(wins: list[detector.Window], s: Settings) -> _Current:
    k = max(1, s.detect_consecutive)
    cur, before = wins[-k:], wins[:-k]
    p95s = [w.p95 for w in cur if w.p95 is not None]
    agg = detector.Window(
        cur[0].start if cur else datetime.now(UTC),
        sum(w.n for w in cur),
        sum(w.errors for w in cur),
        max(p95s) if p95s else None,
    )
    base = detector._baseline(before, s) or detector._robust_baseline(before, s)
    return _Current(agg, base)


async def services_health(repo: str, now: datetime | None = None) -> list[ServiceHealth]:
    s = get_settings()
    now = now or datetime.now(UTC)
    w = s.detect_window_seconds
    since, until = detector._complete_range(now, s)
    async with session_scope() as session:
        rows = (
            await session.execute(
                _SERVICE_WINDOWS, {"w": float(w), "repo": repo, "since": since, "until": until}
            )
        ).all()
        active = (
            await session.execute(
                select(IncidentRow.service, IncidentRow.env, IncidentRow.id)
                .where(
                    IncidentRow.repo == repo,
                    IncidentRow.status.in_([str(x) for x in lifecycle.ACTIVE]),
                )
                .order_by(IncidentRow.detected_at.asc())
            )
        ).all()
    open_by: dict[tuple[str, str], str] = {}
    for service, env, inc_id in active:  # newest wins
        open_by[(service, env)] = inc_id
    grouped: dict[tuple[str, str], list[detector.Window]] = defaultdict(list)
    for r in rows:
        grouped[(r.service, r.env)].append(
            detector.Window(
                r.start, int(r.n), int(r.errors), float(r.p95) if r.p95 is not None else None
            )
        )
    out: list[ServiceHealth] = []
    for key in sorted(set(grouped) | set(open_by)):
        service, env = key
        ss = s.for_service(service)
        wins = detector._fill_gaps(grouped.get(key, []), w, since, until)
        cur = _split_current(wins, ss)
        status: HealthStatus = "ok"
        if key in open_by:
            status = "incident"
        elif cur.baseline is not None and detector._anomalous(cur.window, cur.baseline, ss):
            status = "degraded"
        out.append(
            ServiceHealth(
                repo=repo,
                service=service,
                env=env,
                requests=cur.window.n,
                error_rate=round(cur.window.rate, 4),
                p95_ms=round(cur.window.p95, 1) if cur.window.p95 is not None else None,
                baseline_error_rate=round(cur.baseline.mean, 4) if cur.baseline else None,
                baseline_p95_ms=round(cur.baseline.p95, 1)
                if cur.baseline and cur.baseline.p95 is not None
                else None,
                status=status,
                open_incident_id=open_by.get(key),
                window_seconds=w,
                current_windows=max(1, ss.detect_consecutive),
            )
        )
    return out


# -- deployment timeline -------------------------------------------------------------------


class DeploymentIncident(BaseModel):
    id: str
    status: str
    severity: str | None
    onset: datetime
    triggered: bool  # the detector linked it (Deployment-[:triggers]->Incident)


class DeploymentView(BaseModel):
    id: str
    sha: str | None
    env: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    commits: int
    incidents: list[DeploymentIncident]


def _ts(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), UTC)
    return None


async def deployments(repo: str, hours: int, now: datetime | None = None) -> list[DeploymentView]:
    """Deployments started in the last `hours`, each with the incidents that followed it: the
    detector's `triggers` edge when there is one, else the incidents whose onset falls after it
    and before the next deployment to the same environment."""
    import asyncio

    now = now or datetime.now(UTC)
    since = now - timedelta(hours=hours)
    store = GraphStore(repo)

    def read() -> tuple[list[list[Any]], list[list[Any]]]:
        deps = store.read(
            "MATCH (d:Deployment) WHERE d.started_at >= $since "
            "OPTIONAL MATCH (d)-[:includes]->(c:Commit) "
            "RETURN d.id, d.env, d.status, d.started_at, d.finished_at, d.sha_to, count(c) "
            "ORDER BY d.started_at",
            {"since": int(since.timestamp())},
        )
        trig = store.read(
            "MATCH (d:Deployment)-[:triggers]->(i:Incident) WHERE d.started_at >= $since "
            "RETURN d.id, i.id",
            {"since": int(since.timestamp())},
        )
        return deps, trig

    deps, trig = await asyncio.to_thread(read)
    async with session_scope() as s:
        incs = (
            (
                await s.execute(
                    select(IncidentRow)
                    .where(IncidentRow.repo == repo, IncidentRow.onset_ts >= since)
                    .order_by(IncidentRow.onset_ts)
                )
            )
            .scalars()
            .all()
        )
    views = [
        DeploymentView(
            id=str(d[0]),
            env=str(d[1] or ""),
            status=str(d[2] or "unknown"),
            started_at=_ts(d[3]) or since,
            finished_at=_ts(d[4]),
            sha=str(d[5]) if d[5] else None,
            commits=int(d[6] or 0),
            incidents=[],
        )
        for d in deps
    ]
    by_id = {v.id: v for v in views}
    triggered = {str(i): str(d) for d, i in trig if str(d) in by_id}
    for inc in incs:
        target = by_id.get(triggered.get(inc.id, ""))
        if target is None:
            target = _preceding(views, inc.env, inc.onset_ts)
        if target is None:
            continue
        target.incidents.append(
            DeploymentIncident(
                id=inc.id,
                status=inc.status,
                severity=inc.severity,
                onset=inc.onset_ts,
                triggered=inc.id in triggered,
            )
        )
    return views


def _preceding(views: list[DeploymentView], env: str, onset: datetime) -> DeploymentView | None:
    """Latest successful deployment to `env` started at or before the onset (any env when
    the repository has no deployment to that one)."""
    ok = [v for v in views if v.status == "success" and v.started_at <= onset]
    same = [v for v in ok if v.env == env]
    pool = same if same or any(v.env == env for v in views) else ok
    return pool[-1] if pool else None


# -- hotspots ------------------------------------------------------------------------------


class FunctionHotspot(BaseModel):
    id: str
    name: str
    service: str
    incidents: int  # distinct incidents blaming a commit that modified it
    confirmed: int  # of those, confirmed by a person as the root cause
    last_incident: str | None
    last_onset: datetime | None
    incident_ids: list[str]  # newest first


class DependencyHotspot(BaseModel):
    name: str
    incidents: int
    incident_ids: list[str]


class Hotspots(BaseModel):
    functions: list[FunctionHotspot]
    dependencies: list[DependencyHotspot]


_NOT_REJECTED = "WHERE r.rejected IS NULL OR r.rejected = false "


def _hotspots_sync(store: GraphStore, limit: int) -> Hotspots:
    likely = store.read(
        "MATCH (i:Incident)-[r:likely_caused_by]->(:Commit)-[:modifies_fn]->(f:Function) "
        + _NOT_REJECTED
        + "RETURN f.id, f.name, f.service, i.id, i.onset_ts"
    )
    confirmed = store.read(
        "MATCH (i:Incident)-[:confirmed_cause]->(:Commit)-[:modifies_fn]->(f:Function) "
        "RETURN f.id, f.name, f.service, i.id, i.onset_ts"
    )
    funcs: dict[str, dict[str, Any]] = {}
    for rows, is_confirmed in ((likely, False), (confirmed, True)):
        for fid, name, service, inc, onset in rows:
            f = funcs.setdefault(
                str(fid),
                {
                    "name": str(name or str(fid).rsplit(":", 1)[-1]),
                    "service": str(service or ""),
                    "incidents": {},
                    "confirmed": set(),
                },
            )
            f["incidents"][str(inc)] = int(onset) if isinstance(onset, (int, float)) else 0
            if is_confirmed:
                f["confirmed"].add(str(inc))
    functions = []
    for fid, f in funcs.items():
        ordered = sorted(f["incidents"].items(), key=lambda kv: (-kv[1], kv[0]))
        last_id, last_ts = ordered[0]
        functions.append(
            FunctionHotspot(
                id=fid,
                name=f["name"],
                service=f["service"],
                incidents=len(ordered),
                confirmed=len(f["confirmed"]),
                last_incident=last_id,
                last_onset=_ts(last_ts) if last_ts else None,
                incident_ids=[i for i, _ in ordered],
            )
        )
    functions.sort(key=lambda x: (-x.incidents, -x.confirmed, x.id))

    dep_rows = store.read(
        "MATCH (i:Incident)-[r:likely_caused_by]->(:Commit)-[:changes_dep]->(d:Dependency) "
        + _NOT_REJECTED
        + "RETURN d.name, d.id, i.id"
    )
    dep_rows += store.read(
        "MATCH (i:Incident)-[:confirmed_cause]->(:Commit)-[:changes_dep]->(d:Dependency) "
        "RETURN d.name, d.id, i.id"
    )
    dep_rows += store.read(
        "MATCH (i:Incident)-[r:candidate]->(x:DependencyChange) "
        "WHERE r.rank = 1 AND r.verdict IN ['confident', 'ambiguous'] "
        "RETURN x.name, x.id, i.id"
    )
    deps: dict[str, set[str]] = defaultdict(set)
    for name, key, inc in dep_rows:
        deps[str(name or str(key).split("@")[0].split(":")[0])].add(str(inc))
    dependencies = sorted(
        (
            DependencyHotspot(name=n, incidents=len(ids), incident_ids=sorted(ids))
            for n, ids in deps.items()
        ),
        key=lambda x: (-x.incidents, x.name),
    )
    return Hotspots(functions=functions[:limit], dependencies=dependencies[:limit])


async def hotspots(repo: str, limit: int = 20) -> Hotspots:
    import asyncio

    return await asyncio.to_thread(_hotspots_sync, GraphStore(repo), limit)


# -- evaluation ----------------------------------------------------------------------------


class _Open(BaseModel):
    # the harness may add keys; the endpoint passes them through instead of dropping them
    model_config = ConfigDict(extra="allow")


class EvalSummaryRow(_Open):
    method: str
    n: int
    top1: float | None
    top3: float | None
    function_level: float | None
    false_attribution: float | None
    median_ms: float | None
    top1_ci: list[float] | None  # [low, high], 95% bootstrap


class EvalMethodResult(_Open):
    top: str | None
    rank_of_culprit: int | None
    verdict: str
    correct: bool


class EvalScenario(_Open):
    id: str
    variant: int
    title: str
    split: str  # tune | test
    culprit: str
    results: dict[str, EvalMethodResult]  # method -> outcome


class EvalBlastRadius(_Open):
    precision: float | None
    recall: float | None
    n: int


class EvalMemory(_Open):
    variant: int
    confidence_without: float | None
    confidence_with: float | None


class EvalResult(_Open):
    """docs/results/latest.json as written by evalharness.report.to_json (#92)."""

    generated_at: str
    weights_version: str
    methods: list[str]
    splits: dict[str, list[EvalSummaryRow]]  # all | tune | test
    blast_radius: EvalBlastRadius | None
    fix_verification_rate: float | None
    memory: list[EvalMemory]
    runtime_seconds: float
    scenarios: list[EvalScenario]
    failures: list[str]


class EvalMissing(Exception):
    pass


class EvalInvalid(Exception):
    pass


def eval_latest(settings: Settings | None = None) -> EvalResult:
    """The JSON the evaluation harness writes (`make eval` -> docs/results/latest.json),
    returned as is so the dashboard shows exactly the harness's numbers."""
    path = Path((settings or get_settings()).eval_results_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise EvalMissing(
            f"no evaluation result at {path.name}; run `make eval` to produce it"
        ) from e
    try:
        return EvalResult.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as e:
        raise EvalInvalid(f"{path.name} does not have the evaluation result shape: {e}") from e
