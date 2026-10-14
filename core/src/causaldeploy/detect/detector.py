"""Incident detection: windowed error rate / p95 vs. a rolling baseline (PLAN section 4)."""

import hashlib
import logging
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from causaldeploy.config import Settings, get_settings
from causaldeploy.db.models import IncidentRow, Repository
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import Edge, GraphStore

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


def _baseline(windows: list[Window], s: Settings) -> tuple[float, float, float | None] | None:
    usable = [w for w in windows if w.n >= s.detect_min_requests]
    if len(usable) < 3:
        return None
    rates = [w.rate for w in usable]
    p95s = [w.p95 for w in usable if w.p95 is not None]
    threshold = statistics.fmean(rates) + max(
        s.detect_sigma * statistics.pstdev(rates), s.detect_min_error_delta
    )
    return statistics.fmean(rates), threshold, (statistics.fmean(p95s) if p95s else None)


def _anomalous(w: Window, threshold: float, base_p95: float | None, s: Settings) -> bool:
    if w.n < s.detect_min_requests:
        return False
    slow = bool(base_p95 and w.p95 and w.p95 > s.detect_p95_factor * base_p95 and w.p95 > 500)
    return w.rate > threshold or slow


def evaluate(
    windows: list[Window], s: Settings
) -> tuple[datetime, Window, float, float | None] | None:
    """Detection rule over consecutive windows (oldest first; quiet windows have n == 0).

    Anomalous = error rate above `baseline mean + max(sigma * std, min_delta)`, or p95 above
    `factor * baseline p95`. The latest `detect_consecutive` windows must all be anomalous;
    onset is the first window of the unbroken anomalous run, and the baseline is whatever
    came before that run. Returns (onset, latest window, baseline rate, baseline p95).
    """
    k = s.detect_consecutive
    if len(windows) < k + 3:
        return None
    first = _baseline(windows[:-k], s)
    if first is None:
        return None
    _, threshold, base_p95 = first
    if not all(_anomalous(w, threshold, base_p95, s) for w in windows[-k:]):
        return None
    i = len(windows) - k
    while i > 0 and _anomalous(windows[i - 1], threshold, base_p95, s):
        i -= 1
    base = _baseline(windows[:i], s) or first
    return windows[i].start, windows[-1], base[0], base[2]


async def find_anomalies(repo: str, now: datetime, s: Settings | None = None) -> list[Anomaly]:
    s = s or get_settings()
    w = s.detect_window_seconds
    until = now - timedelta(seconds=w)  # only complete windows
    until = datetime.fromtimestamp((until.timestamp() // w) * w, UTC)
    since = until - timedelta(minutes=s.detect_baseline_minutes)
    async with session_scope() as session:
        rows = (
            await session.execute(
                _WINDOWS, {"w": float(w), "repo": repo, "since": since, "until": until}
            )
        ).all()
    grouped: dict[tuple[str, str, str], list[Window]] = {}
    for r in rows:
        grouped.setdefault((r.service, r.env, r.endpoint or "-"), []).append(
            Window(r.start, int(r.n), int(r.errors), float(r.p95) if r.p95 is not None else None)
        )
    found = []
    for (service, env, endpoint), wins in grouped.items():
        wins = _fill_gaps(wins, w, since, until)
        hit = evaluate(wins, s)
        if hit and wins[-1].start == hit[1].start:  # still anomalous in the latest window
            onset, last, base_rate, base_p95 = hit
            found.append(
                Anomaly(
                    service, env, endpoint, onset, last.rate, base_rate, last.p95, base_p95, last.n
                )
            )
    return found


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


async def open_incident(
    repo: str, a: Anomaly, detected_at: datetime, store: GraphStore | None = None
) -> str | None:
    """Create the incident unless one is already open for service+env+endpoint. Returns its id."""
    async with session_scope() as session:
        open_row = (
            await session.execute(
                select(IncidentRow.id).where(
                    IncidentRow.repo == repo,
                    IncidentRow.service == a.service,
                    IncidentRow.env == a.env,
                    IncidentRow.endpoint == a.endpoint,
                    IncidentRow.status != "resolved",
                )
            )
        ).first()
        if open_row:
            return None
        inc_id = incident_id(repo, a)
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
            )
        )
    await _write_graph(store or GraphStore(repo), inc_id, a, detected_at)
    log.info(
        "incident opened",
        extra={
            "ctx": {
                "incident": inc_id,
                "service": a.service,
                "endpoint": a.endpoint,
                "rate": round(a.error_rate, 3),
            }
        },
    )
    return inc_id


async def _write_graph(store: GraphStore, inc_id: str, a: Anomaly, detected_at: datetime) -> None:
    import asyncio

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


async def detect_repo(repo: str, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    opened = []
    for a in await find_anomalies(repo, now):
        inc = await open_incident(repo, a, now)
        if inc:
            opened.append(inc)
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
