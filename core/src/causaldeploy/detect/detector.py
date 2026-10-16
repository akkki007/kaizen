"""Incident detection: windowed error rate / p95 vs. a rolling baseline (PLAN section 4).

Rules (#88):
- windowed: per service+env+endpoint, the latest `detect_consecutive` complete windows exceed
  the baseline computed from the windows *before* the anomalous run (no self-poisoning);
- health: `detect_health_failures` consecutive failed health checks, regardless of volume;
- flapping guard: no new incident for the same repo/service/env/endpoint within
  `detect_cooldown_minutes` of one being resolved or closed;
- per-service thresholds via `detect_overrides`;
- severity sev1..sev3 from error rate / z-score (see `severity`).
Window summaries of anomalous groups are kept as `Metric` nodes; the incident is
`correlated_with` its onset windows. All graph writes are MERGEs, so re-running is idempotent.
"""

import asyncio
import hashlib
import logging
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text

from causaldeploy.config import Settings, get_settings
from causaldeploy.db.models import IncidentRow, IncidentTransition, Repository
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import Edge, GraphStore
from causaldeploy.incidents import lifecycle

log = logging.getLogger(__name__)

_WINDOWS = text(
    """
    SELECT date_bin(make_interval(secs => :w), ts, TIMESTAMPTZ '2000-01-01') AS start,
           service, env, endpoint,
           count(*) AS n,
           count(*) FILTER (WHERE status_code >= 400
                              OR (kind = 'health' AND (body->>'ok') = 'false')) AS errors,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
    FROM telemetry
    WHERE repo = :repo AND ts >= :since AND ts < :until AND kind IN ('request', 'health')
    GROUP BY 1, 2, 3, 4
    ORDER BY 2, 3, 4, 1
    """
)

_HEALTH = text(
    """
    SELECT service, env, ts, (body->>'ok') = 'false' AS failed
    FROM telemetry
    WHERE repo = :repo AND kind = 'health' AND ts >= :since AND ts <= :now
    ORDER BY service, env, ts
    """
)


@dataclass(frozen=True)
class Window:
    start: datetime
    n: int
    errors: int
    p95: float | None

    @property
    def rate(self) -> float:
        return self.errors / self.n if self.n else 0.0


@dataclass(frozen=True)
class Baseline:
    mean: float
    std: float
    threshold: float  # error rate above which a window is anomalous
    p95: float | None


@dataclass(frozen=True)
class Hit:
    onset_index: int  # index of the first window of the anomalous run
    onset: datetime
    last: Window
    baseline: Baseline  # computed from windows before the onset only


@dataclass(frozen=True)
class Anomaly:
    service: str
    env: str
    endpoint: str
    onset: datetime  # start of the first anomalous window, not the detection time
    error_rate: float
    baseline_rate: float
    p95: float | None
    baseline_p95: float | None
    requests: int
    baseline_std: float = 0.0
    z: float = 0.0
    severity: str = "sev3"
    source: str = "window"  # window | health
    windows: tuple[Window, ...] = ()  # evaluated window summaries, oldest first
    onset_index: int = 0  # windows[onset_index:] are the anomalous run


def _usable(windows: list[Window], s: Settings) -> list[Window]:
    return [w for w in windows if w.n >= s.detect_min_requests]


def _make(mean: float, std: float, p95: float | None, s: Settings) -> Baseline:
    return Baseline(mean, std, mean + max(s.detect_sigma * std, s.detect_min_error_delta), p95)


def _baseline(windows: list[Window], s: Settings) -> Baseline | None:
    usable = _usable(windows, s)
    if len(usable) < 3:
        return None
    rates = [w.rate for w in usable]
    p95s = [w.p95 for w in usable if w.p95 is not None]
    return _make(
        statistics.fmean(rates),
        statistics.pstdev(rates),
        statistics.fmean(p95s) if p95s else None,
        s,
    )


def _robust_baseline(windows: list[Window], s: Settings) -> Baseline | None:
    """Median / MAD: a long anomalous run inside the range cannot drag the threshold up, so
    the pass that *locates* the run is not poisoned by the run itself."""
    usable = _usable(windows, s)
    if len(usable) < 3:
        return None
    rates = [w.rate for w in usable]
    med = statistics.median(rates)
    mad = statistics.median(abs(r - med) for r in rates)
    p95s = [w.p95 for w in usable if w.p95 is not None]
    return _make(med, 1.4826 * mad, statistics.median(p95s) if p95s else None, s)


def _anomalous(w: Window, b: Baseline, s: Settings) -> bool:
    if w.n < s.detect_min_requests:
        return False
    slow = bool(b.p95 and w.p95 and w.p95 > s.detect_p95_factor * b.p95 and w.p95 > 500)
    return w.rate > b.threshold or slow


def evaluate_windows(windows: list[Window], s: Settings) -> Hit | None:
    """Detection rule over consecutive windows (oldest first; quiet windows have n == 0).

    Anomalous = error rate above `baseline mean + max(sigma * std, min_delta)`, or p95 above
    `factor * baseline p95`. The latest `detect_consecutive` windows must all be anomalous;
    onset is the first window of the unbroken anomalous run. A robust (median/MAD) pass over
    everything before the latest k windows locates the run; the baseline is then recomputed
    from the windows strictly before the onset, and the latest k windows must still be
    anomalous against it.
    """
    k = s.detect_consecutive
    if len(windows) < k + 3:
        return None
    rough = _robust_baseline(windows[:-k], s)
    if rough is None:
        return None
    if not all(_anomalous(w, rough, s) for w in windows[-k:]):
        return None
    i = len(windows) - k
    while i > 0 and _anomalous(windows[i - 1], rough, s):
        i -= 1
    base = _baseline(windows[:i], s) or rough
    if not all(_anomalous(w, base, s) for w in windows[-k:]):
        return None
    return Hit(i, windows[i].start, windows[-1], base)


def evaluate(
    windows: list[Window], s: Settings
) -> tuple[datetime, Window, float, float | None] | None:
    """`evaluate_windows` as (onset, latest window, baseline rate, baseline p95)."""
    hit = evaluate_windows(windows, s)
    if hit is None:
        return None
    return hit.onset, hit.last, hit.baseline.mean, hit.baseline.p95


def zscore(rate: float, b: Baseline, s: Settings) -> float:
    """Deviation of an error rate from the baseline in baseline standard deviations. The std
    is floored at `min_error_delta / sigma` so a perfectly flat baseline does not make every
    blip infinitely significant."""
    return (rate - b.mean) / max(b.std, s.detect_min_error_delta / s.detect_sigma)


def severity(
    error_rate: float, z: float, p95: float | None, base_p95: float | None, s: Settings
) -> str:
    """Severity of a windowed anomaly (health-check failures are always sev1):

    sev1  error rate >= 50%, or (z >= 10 and error rate >= 25%)
    sev2  error rate >= 20%, or z >= 5, or p95 >= 2 x detect_p95_factor x baseline p95
    sev3  anything else that crossed the detection threshold (e.g. a moderate p95 rise)
    """
    if error_rate >= 0.5 or (z >= 10 and error_rate >= 0.25):
        return "sev1"
    much_slower = bool(p95 and base_p95 and p95 >= 2 * s.detect_p95_factor * base_p95)
    if error_rate >= 0.2 or z >= 5 or much_slower:
        return "sev2"
    return "sev3"


def _complete_range(now: datetime, s: Settings) -> tuple[datetime, datetime]:
    w = s.detect_window_seconds
    until = now - timedelta(seconds=w)  # only complete windows
    until = datetime.fromtimestamp((until.timestamp() // w) * w, UTC)
    return until - timedelta(minutes=s.detect_baseline_minutes), until


async def find_anomalies(repo: str, now: datetime, s: Settings | None = None) -> list[Anomaly]:
    s = s or get_settings()
    w = s.detect_window_seconds
    since, until = _complete_range(now, s)
    async with session_scope() as session:
        rows = (
            await session.execute(
                _WINDOWS, {"w": float(w), "repo": repo, "since": since, "until": until}
            )
        ).all()
        health = (await session.execute(_HEALTH, {"repo": repo, "since": since, "now": now})).all()
    found = _health_anomalies(health, s)
    down = {(a.service, a.env) for a in found}
    grouped: dict[tuple[str, str, str], list[Window]] = {}
    for r in rows:
        grouped.setdefault((r.service, r.env, r.endpoint or "-"), []).append(
            Window(r.start, int(r.n), int(r.errors), float(r.p95) if r.p95 is not None else None)
        )
    for (service, env, endpoint), wins in grouped.items():
        if endpoint == "health" and (service, env) in down:
            continue  # already reported by the consecutive-failure rule
        ss = s.for_service(service)
        wins = _fill_gaps(wins, w, since, until)
        hit = evaluate_windows(wins, ss)
        if hit and wins[-1].start == hit.last.start:  # still anomalous in the latest window
            b, last = hit.baseline, hit.last
            z = zscore(last.rate, b, ss)
            found.append(
                Anomaly(
                    service,
                    env,
                    endpoint,
                    hit.onset,
                    last.rate,
                    b.mean,
                    last.p95,
                    b.p95,
                    last.n,
                    baseline_std=b.std,
                    z=z,
                    severity=severity(last.rate, z, last.p95, b.p95, ss),
                    windows=tuple(wins),
                    onset_index=hit.onset_index,
                )
            )
    return found


def _health_anomalies(rows: Sequence[Any], s: Settings) -> list[Anomaly]:
    """N consecutive failed health checks (newest last) open an incident even though health
    checks are far too sparse for the windowed rule's `detect_min_requests`."""
    by_target: dict[tuple[str, str], list[tuple[datetime, bool]]] = {}
    for r in rows:
        by_target.setdefault((r.service, r.env), []).append((r.ts, bool(r.failed)))
    out = []
    for (service, env), checks in by_target.items():
        need = s.for_service(service).detect_health_failures
        run = 0
        while run < len(checks) and checks[len(checks) - 1 - run][1]:
            run += 1
        if run < need:
            continue
        before = checks[: len(checks) - run]
        base = sum(1 for _, failed in before if failed) / len(before) if before else 0.0
        out.append(
            Anomaly(
                service,
                env,
                "health",
                checks[len(checks) - run][0],  # first failure of the unbroken run
                1.0,
                base,
                None,
                None,
                run,
                severity="sev1",  # failing its health checks: the service is down
                source="health",
            )
        )
    return out


def _fill_gaps(wins: list[Window], w: int, since: datetime, until: datetime) -> list[Window]:
    """Quiet windows count as zero traffic so a run must be *consecutive* in time."""
    by_start = {x.start: x for x in wins}
    out, t = [], datetime.fromtimestamp((since.timestamp() // w) * w, UTC)
    while t < until:
        out.append(by_start.get(t, Window(t, 0, 0, None)))
        t += timedelta(seconds=w)
    return out


def incident_id(repo: str, a: Anomaly) -> str:
    key = f"{repo}|{a.service}|{a.env}|{a.endpoint}|{int(a.onset.timestamp())}"
    return "inc-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def metric_id(service: str, env: str, endpoint: str, window_start: datetime) -> str:
    return f"{service}|{env}|{endpoint}|{int(window_start.timestamp())}"


async def _last_terminal_at(session: Any, repo: str, a: Anomaly) -> datetime | None:
    """When an incident for the same target was last resolved or closed (None if never)."""
    same = (
        IncidentRow.repo == repo,
        IncidentRow.service == a.service,
        IncidentRow.env == a.env,
        IncidentRow.endpoint == a.endpoint,
    )
    terminal = [str(st) for st in lifecycle.TERMINAL]
    by_transition = (
        await session.execute(
            select(func.max(IncidentTransition.created_at))
            .join(IncidentRow, IncidentRow.id == IncidentTransition.incident_id)
            .where(*same, IncidentTransition.to_status.in_(terminal))
        )
    ).scalar()
    by_row = (
        await session.execute(
            select(func.max(IncidentRow.resolved_at)).where(*same, IncidentRow.status.in_(terminal))
        )
    ).scalar()
    stamps = [t for t in (by_transition, by_row) if t is not None]
    return max(stamps) if stamps else None


async def _open_or_existing(
    repo: str, a: Anomaly, detected_at: datetime, s: Settings
) -> tuple[str | None, bool]:
    """(incident id, created). The id is the already-active incident for the target when
    there is one; None when the cooldown suppresses a new incident."""
    async with session_scope() as session:
        active = (
            await session.execute(
                select(IncidentRow.id).where(
                    IncidentRow.repo == repo,
                    IncidentRow.service == a.service,
                    IncidentRow.env == a.env,
                    IncidentRow.endpoint == a.endpoint,
                    IncidentRow.status.in_([str(st) for st in lifecycle.ACTIVE]),
                )
            )
        ).first()
        if active:
            return str(active[0]), False
        last = await _last_terminal_at(session, repo, a)
        cooldown = timedelta(minutes=s.detect_cooldown_minutes)
        # a closure stamped after the detection time also counts as recent
        if last is not None and cooldown and last >= detected_at - cooldown:
            log.info(
                "incident suppressed by cooldown",
                extra={"ctx": {"service": a.service, "endpoint": a.endpoint}},
            )
            return None, False
        inc_id = incident_id(repo, a)
        if (await session.get(IncidentRow, inc_id)) is not None:
            return None, False  # same onset already handled (and terminal): do not resurrect
        if (await session.get(Repository, repo)) is None:
            session.add(Repository(full_name=repo))
            await session.flush()
        session.add(
            IncidentRow(
                id=inc_id,
                repo=repo,
                service=a.service,
                env=a.env,
                endpoint=a.endpoint,
                status="open",
                onset_ts=a.onset,
                detected_at=detected_at,
                severity=a.severity,
            )
        )
        await session.flush()
        await lifecycle.record_opened(session, inc_id)
    return inc_id, True


async def open_incident(
    repo: str,
    a: Anomaly,
    detected_at: datetime,
    store: GraphStore | None = None,
    s: Settings | None = None,
) -> str | None:
    """Create the incident unless one is already active for service+env+endpoint or the
    target is in its post-resolution cooldown. Returns the new incident's id."""
    inc_id, created = await _open_or_existing(repo, a, detected_at, s or get_settings())
    if not created or inc_id is None:
        return None
    await _announce(store or GraphStore(repo), inc_id, a, detected_at)
    return inc_id


async def _announce(store: GraphStore, inc_id: str, a: Anomaly, detected_at: datetime) -> None:
    """Side effects of a newly opened incident: graph node + edges, investigation job, log."""
    await _write_graph(store, inc_id, a, detected_at)
    from causaldeploy.jobs.queue import InvestigatePayload, enqueue

    await enqueue(
        "investigate_incident",
        InvestigatePayload(repo=store.repo, incident_id=inc_id),
        f"investigate:{inc_id}",
    )
    log.info(
        "incident opened",
        extra={
            "ctx": {
                "incident": inc_id,
                "service": a.service,
                "endpoint": a.endpoint,
                "rate": round(a.error_rate, 3),
                "severity": a.severity,
            }
        },
    )


async def _write_graph(store: GraphStore, inc_id: str, a: Anomaly, detected_at: datetime) -> None:
    await asyncio.to_thread(_write_graph_sync, store, inc_id, a, detected_at)


def _write_graph_sync(store: GraphStore, inc_id: str, a: Anomaly, detected_at: datetime) -> None:
    store.ensure_schema()
    onset = int(a.onset.timestamp())
    store.upsert_nodes(
        "Incident",
        [
            {
                "id": inc_id,
                "status": "open",
                "service": a.service,
                "env": a.env,
                "endpoint": a.endpoint,
                "onset_ts": onset,
                "detected_at": int(detected_at.timestamp()),
                "error_rate": round(a.error_rate, 4),
                "baseline_error_rate": round(a.baseline_rate, 4),
                "severity": a.severity,
            }
        ],
    )
    edges = [Edge("Incident", inc_id, "affects", "Service", a.service)]
    # errors this service threw since the baseline ended, scoped to the endpoint when known
    errs = store.read(
        "MATCH (e:Error) WHERE e.service = $svc AND e.last_seen >= $since "
        "OPTIONAL MATCH (e)-[:observed_on]->(x:Endpoint) "
        "WITH e, x WHERE x IS NULL OR x.id = $ep "
        "RETURN e.fingerprint",
        {"svc": a.service, "since": onset - 60, "ep": f"{a.service}:{a.endpoint}"},
    )
    edges += [Edge("Incident", inc_id, "evidenced_by", "Error", r[0]) for r in errs]
    dep = store.read(
        "MATCH (d:Deployment {env: $env}) WHERE d.status = 'success' AND d.started_at <= $onset "
        "RETURN d.id ORDER BY d.started_at DESC LIMIT 1",
        {"env": a.env, "onset": onset},
    )
    if dep:
        edges.append(Edge("Deployment", dep[0][0], "triggers", "Incident", inc_id))
    store.upsert_edges(edges)


def metric_rows(a: Anomaly, s: Settings) -> list[dict[str, Any]]:
    """Summaries (never raw rows) of the evaluated, non-empty windows of an anomaly."""
    ss = s.for_service(a.service)
    base = _make(a.baseline_rate, a.baseline_std, a.baseline_p95, ss)
    return [
        {
            "id": metric_id(a.service, a.env, a.endpoint, w.start),
            "service": a.service,
            "env": a.env,
            "endpoint": a.endpoint,
            "window_start": int(w.start.timestamp()),
            "window_seconds": s.detect_window_seconds,
            "n": w.n,
            "errors": w.errors,
            "error_rate": round(w.rate, 4),
            "p95": w.p95,
            "z": round(zscore(w.rate, base, ss), 3),
            "anomalous": i >= a.onset_index,
        }
        for i, w in enumerate(a.windows)
        if w.n > 0
    ]


def write_metrics(store: GraphStore, inc_id: str, a: Anomaly, s: Settings) -> int:
    """Upsert the window summaries and link the incident to its onset windows (idempotent)."""
    rows = metric_rows(a, s)
    if not rows:
        return 0
    store.ensure_schema()
    store.upsert_nodes("Metric", rows)
    store.upsert_edges(
        Edge("Incident", inc_id, "correlated_with", "Metric", r["id"])
        for r in rows
        if r["anomalous"]
    )
    return len(rows)


async def detect_repo(repo: str, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    s = get_settings()
    opened = []
    for a in await find_anomalies(repo, now, s):
        inc, created = await _open_or_existing(repo, a, now, s)
        if inc is None:
            continue
        store = GraphStore(repo)
        if created:
            await _announce(store, inc, a, now)
            opened.append(inc)
        await asyncio.to_thread(write_metrics, store, inc, a, s)
    return opened


async def detect_all(now: datetime | None = None) -> list[str]:
    """ARQ cron entry point: every allowed repository that has recent telemetry."""
    s = get_settings()
    since = (now or datetime.now(UTC)) - timedelta(minutes=s.detect_baseline_minutes + 1)
    async with session_scope() as session:
        repos = (
            (
                await session.execute(
                    text("SELECT DISTINCT repo FROM telemetry WHERE ts >= :t"), {"t": since}
                )
            )
            .scalars()
            .all()
        )
    opened: list[str] = []
    for repo in repos:
        if s.is_allowed(repo):
            opened += await detect_repo(repo, now)
    return opened
