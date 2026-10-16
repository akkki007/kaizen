"""#88 detection rules: baseline hygiene, severity, overrides, cooldown, health, metrics, noise."""

import random
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select, update

from causaldeploy.config import Settings
from causaldeploy.contracts import HealthEvent, TelemetryEvent
from causaldeploy.db.models import IncidentRow, IncidentTransition, Telemetry
from causaldeploy.db.session import session_scope
from causaldeploy.detect.detector import (
    Anomaly,
    Window,
    detect_repo,
    evaluate_windows,
    open_incident,
    severity,
)
from causaldeploy.detect.errors import link_error, store_telemetry_many
from causaldeploy.graph.store import GraphStore
from causaldeploy.incidents import lifecycle
from causaldeploy.jobs import handlers

S = Settings()
T0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 14, 12, 7, 40, tzinfo=UTC)  # windows through 12:07:00 are complete


def win(i: int, n: int, errors: int, p95: float = 40.0) -> Window:
    return Window(T0 + timedelta(seconds=30 * i), n, errors, p95)


# -- pure rules ---------------------------------------------------------------------------


def test_baseline_comes_only_from_pre_onset_windows() -> None:
    """A long failing run must not poison its own baseline (it did with a mean/std first pass:
    6 failing windows in the 'baseline' lifted the threshold above 0.7 and nothing fired)."""
    wins = [*(win(i, 20, 0) for i in range(12)), *(win(i, 20, 14) for i in range(12, 20))]
    hit = evaluate_windows(wins, S)
    assert hit is not None
    assert hit.onset_index == 12 and hit.onset == win(12, 0, 0).start
    assert hit.baseline.mean == 0.0 and hit.baseline.std == 0.0  # pre-onset windows only


def test_severity_rule() -> None:
    assert severity(0.7, 20, None, None, S) == "sev1"
    assert severity(0.3, 12, None, None, S) == "sev1"
    assert severity(0.3, 4, None, None, S) == "sev2"
    assert severity(0.15, 6, None, None, S) == "sev2"
    assert severity(0.0, 0, 2600.0, 40.0, S) == "sev2"  # >= 2 x factor x baseline p95
    assert severity(0.15, 3, None, None, S) == "sev3"


def test_per_service_overrides_are_merged() -> None:
    s = Settings(detect_overrides={"payments": {"min_requests": 50, "detect_sigma": 4}})
    p = s.for_service("payments")
    assert (p.detect_min_requests, p.detect_sigma) == (50, 4.0)
    assert isinstance(p.detect_min_requests, int)
    assert s.for_service("orders") is s
    wins = [*(win(i, 20, 0) for i in range(12)), win(12, 20, 14), win(13, 20, 14)]
    assert evaluate_windows(wins, s) is not None  # default thresholds
    assert evaluate_windows(wins, p) is None  # 20 requests < the payments minimum of 50
    with pytest.raises(ValidationError, match="unknown threshold"):
        Settings(detect_overrides={"payments": {"window_seconds": 5}})


# -- end to end (Postgres + FalkorDB) ------------------------------------------------------


def request(
    repo: str, ts: datetime, status: int, endpoint: str = "POST /payments"
) -> TelemetryEvent:
    return TelemetryEvent(
        repo=repo,
        service="payments",
        ts=ts,
        kind="request",
        endpoint=endpoint,
        status_code=status,
        latency_ms=40.0,
    )


async def failing_traffic(repo: str, failing_from: int = 12, windows: int = 14) -> None:
    events = [
        request(
            repo, T0 + timedelta(seconds=30 * w + i), 500 if w >= failing_from and i < 14 else 200
        )
        for w in range(windows)
        for i in range(20)
    ]
    await store_telemetry_many(events)


async def test_bulk_insert_and_sha_are_persisted(db: None, store: GraphStore) -> None:
    await store_telemetry_many(
        [request(store.repo, T0 + timedelta(seconds=i), 200) for i in range(50)]
    )
    await handlers.ingest_event(
        {
            "event": {
                "type": "telemetry",
                "repo": store.repo,
                "service": "payments",
                "ts": T0.isoformat(),
                "kind": "request",
                "endpoint": "GET /x",
                "status_code": 200,
                "sha": "abc1234",
            }
        }
    )
    await handlers.ingest_event(
        {
            "event": {
                "type": "health",
                "repo": store.repo,
                "service": "payments",
                "env": "prod",
                "ts": T0.isoformat(),
                "ok": True,
                "sha": "def5678",
            }
        }
    )
    async with session_scope() as s:
        rows = (await s.execute(select(Telemetry.kind, Telemetry.sha))).all()
    assert len(rows) == 52
    assert {r.sha for r in rows if r.sha} == {"abc1234", "def5678"}


async def test_error_count_service_edge_and_sha_preference(store: GraphStore) -> None:
    store.ensure_schema()
    common = {"path": "payments/src/v.js", "service": "payments", "start_line": 1}
    store.upsert_nodes(
        "Function",
        [
            {"id": "f-old", **common, "end_line": 10, "sha_seen": "old"},
            {"id": "f-new", **common, "end_line": 30, "sha_seen": "new"},
        ],
    )

    def err(i: int, sha: str | None) -> TelemetryEvent:
        return TelemetryEvent(
            repo=store.repo,
            service="payments",
            ts=T0 + timedelta(seconds=i),
            kind="error",
            error_type="TypeError",
            message="boom",
            stack=["fn (/app/payments/src/v.js:5:1)"],
            sha=sha,
        )

    assert link_error(store, err(0, None), "fp1") == ["f-old"]  # innermost without version info
    assert link_error(store, err(1, "new"), "fp1") == ["f-new"]  # the version that ran wins
    link_error(store, err(2, "zzz"), "fp1")
    assert store.read("MATCH (e:Error {fingerprint: 'fp1'}) RETURN e.count") == [[3]]
    assert store.read(
        "MATCH (:Error {fingerprint: 'fp1'})-[r:raised_by]->(s:Service) RETURN s.id, count(r)"
    ) == [["payments", 1]]


async def test_severity_metrics_and_idempotent_detection(db: None, store: GraphStore) -> None:
    await failing_traffic(store.repo)
    opened = await detect_repo(store.repo, NOW)
    assert len(opened) == 1
    async with session_scope() as s:
        inc = (await s.execute(select(IncidentRow))).scalar_one()
    assert inc.severity == "sev1"  # 70% errors
    assert store.read("MATCH (i:Incident) RETURN i.severity") == [["sev1"]]

    counts = store.counts()
    metrics = store.read("MATCH (m:Metric) RETURN count(m)")[0][0]
    onset = store.read(
        "MATCH (:Incident {id: $i})-[:correlated_with]->(m:Metric) "
        "RETURN m.window_start, m.n, m.error_rate ORDER BY m.window_start",
        {"i": inc.id},
    )
    assert metrics == 14  # one summary per non-empty evaluated window
    assert [r[0] for r in onset] == [
        int((T0 + timedelta(seconds=30 * w)).timestamp()) for w in (12, 13)
    ]
    assert all(r[1] == 20 and r[2] == 0.7 for r in onset)

    assert await detect_repo(store.repo, NOW) == []  # same incident stays the only one
    assert store.counts() == counts  # MERGE-only: re-running adds no nodes or edges


async def test_cooldown_suppresses_flapping(db: None, store: GraphStore) -> None:
    await failing_traffic(store.repo)
    [inc_id] = await detect_repo(store.repo, NOW)
    await lifecycle.transition(inc_id, "closed", actor="ann", reason="false alarm")

    def flap(minutes: int) -> Anomaly:  # the same target fails again with a new onset
        return Anomaly(
            "payments", "prod", "POST /payments", NOW + timedelta(minutes=minutes),
            0.7, 0.0, None, None, 20,
        )  # fmt: skip

    assert await open_incident(store.repo, flap(1), NOW + timedelta(minutes=2), store) is None
    # once the closure is older than detect_cooldown_minutes a new incident opens
    async with session_scope() as s:
        await s.execute(
            update(IncidentTransition)
            .where(IncidentTransition.incident_id == inc_id)
            .values(created_at=NOW - timedelta(hours=1))
        )
    new = await open_incident(store.repo, flap(1), NOW + timedelta(minutes=2), store)
    assert new is not None and new != inc_id
    zero = Settings(detect_cooldown_minutes=0)
    await lifecycle.transition(new, "closed", actor="ann")
    third = await open_incident(store.repo, flap(5), NOW + timedelta(minutes=6), store, zero)
    assert third is not None


async def test_consecutive_failed_health_checks_open_an_incident(
    db: None, store: GraphStore
) -> None:
    def check(minute: int, ok: bool) -> HealthEvent:
        return HealthEvent(
            repo=store.repo,
            service="payments",
            env="prod",
            ts=T0 + timedelta(minutes=minute),
            ok=ok,
        )

    # one check a minute: far below detect_min_requests per window
    await store_telemetry_many(
        [check(m, True) for m in range(4)] + [check(4, False), check(5, False)]
    )
    assert await detect_repo(store.repo, T0 + timedelta(minutes=5, seconds=10)) == []  # 2 < 3
    await store_telemetry_many([check(6, False)])
    opened = await detect_repo(store.repo, T0 + timedelta(minutes=6, seconds=10))
    assert len(opened) == 1
    async with session_scope() as s:
        inc = (await s.execute(select(IncidentRow))).scalar_one()
    assert (inc.endpoint, inc.severity, inc.onset_ts) == (
        "health",
        "sev1",
        T0 + timedelta(minutes=4),
    )
    assert await detect_repo(store.repo, T0 + timedelta(minutes=6, seconds=20)) == []


async def test_s10_noisy_but_healthy_traffic_opens_nothing(db: None, store: GraphStore) -> None:
    rng = random.Random(10)
    events = []
    for w in range(40):  # 20 minutes of 30 s windows
        n = rng.randint(8, 30)
        for i in range(n):
            if w in (15, 39):  # single-window spikes, including the very latest window
                failed = rng.random() < 0.5
            else:  # sparse background errors well under min_error_delta
                failed = rng.random() < 0.03
            events.append(
                request(store.repo, T0 + timedelta(seconds=30 * w + i), 500 if failed else 200)
            )
        if rng.random() < 0.2:  # an endpoint that is almost idle
            events.append(request(store.repo, T0 + timedelta(seconds=30 * w), 500, "GET /rare"))
    await store_telemetry_many(events)
    opened = []
    for step in range(12, 41):  # evaluate the way the cron does, as time passes
        opened += await detect_repo(store.repo, T0 + timedelta(seconds=30 * step + 40))
    assert opened == []
    async with session_scope() as s:
        assert (await s.execute(select(IncidentRow))).first() is None
