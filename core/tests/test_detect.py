from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from causaldeploy.config import Settings
from causaldeploy.contracts import TelemetryEvent
from causaldeploy.db.models import IncidentRow, Telemetry
from causaldeploy.db.session import session_scope
from causaldeploy.detect.detector import Window, detect_repo, evaluate
from causaldeploy.detect.errors import link_error
from causaldeploy.detect.fingerprint import app_frames, fingerprint, normalize_message
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.store import GraphStore
from causaldeploy.ingest.code_graph import build_code_graph, write_code_graph
from causaldeploy.jobs import handlers
from tests.shoplite_files import load

S = Settings()
T0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)

STACK = [
    "validatePayment (/app/payments/src/payment/validation.js:4:11)",
    "handlePayment (/app/payments/src/routes.js:7:9)",
    "Layer.handle [as handle_request] (/app/node_modules/express/lib/router/layer.js:95:5)",
    "process.processTicksAndRejections (node:internal/process/task_queues:105:5)",
]


def test_frames_drop_runtime_and_dependency_code() -> None:
    frames = app_frames(STACK)
    assert [(f.function, f.path.rsplit("/", 1)[-1], f.line) for f in frames] == [
        ("validatePayment", "validation.js", 4),
        ("handlePayment", "routes.js", 7),
    ]


def test_fingerprint_is_stable_across_ids_numbers_and_line_drift() -> None:
    a = fingerprint("TypeError", STACK, "Cannot read 'currency' of order ord_1a2b3c at 42")
    b = fingerprint(
        "TypeError",
        [s.replace(":4:11", ":9:3") for s in STACK],
        "Cannot read 'currency' of order ord_ffeedd at 7",
    )
    other = fingerprint("TypeError", STACK, "something else entirely")
    assert a == b != other
    assert normalize_message("Order 12 failed after 3.5s") == "order <n> failed after <n>s"


def win(i: int, n: int, errors: int, p95: float = 40.0) -> Window:
    return Window(T0 + timedelta(seconds=30 * i), n, errors, p95)


def steady(count: int, errors: int = 0) -> list[Window]:
    return [win(i, 20, errors if i % 4 == 0 else 0) for i in range(count)]


def test_detects_after_consecutive_anomalous_windows_and_reports_true_onset() -> None:
    wins = [*steady(12), win(12, 20, 12), win(13, 20, 14)]
    onset, last, base, _ = evaluate(wins, S)  # type: ignore[misc]
    assert onset == win(12, 0, 0).start  # first anomalous window, not the detection time
    assert last.rate == 0.7 and base < 0.05


def test_single_spike_and_quiet_traffic_do_not_open_incidents() -> None:
    assert evaluate([*steady(12), win(12, 20, 12), win(13, 20, 0)], S) is None  # one window only
    assert evaluate([*steady(12), win(12, 2, 2), win(13, 3, 3)], S) is None  # too few requests
    assert evaluate(steady(14), S) is None


def test_slow_but_not_failing_is_detected_by_p95() -> None:
    wins = [*steady(12), win(12, 20, 0, 2400.0), win(13, 20, 0, 2600.0)]
    assert evaluate(wins, S) is not None


def test_error_is_linked_to_the_function_that_threw(store: GraphStore) -> None:
    write_code_graph(store, build_code_graph(load()), "abc")
    ev = TelemetryEvent(
        repo=store.repo,
        service="payments",
        ts=T0,
        kind="error",
        endpoint="POST /payments",
        error_type="TypeError",
        message="boom",
        stack=STACK,
    )
    fp = fingerprint(ev.error_type, list(ev.stack), ev.message)
    thrown = link_error(store, ev, fp)
    assert thrown == [
        "payments:payments/src/payment/validation.js:validatePayment",
        "payments:payments/src/routes.js:handlePayment",
    ]
    again = link_error(store, ev, fp)  # idempotent
    assert again == thrown
    rows = store.read(
        "MATCH (:Error {fingerprint: $fp})-[t:thrown_in]->(f) RETURN f.id, t.frame_depth ORDER BY t.frame_depth",
        {"fp": fp},
    )
    assert [r[1] for r in rows] == [0, 1]
    assert store.read(
        "MATCH (:Error {fingerprint: $fp})-[:observed_on]->(e) RETURN e.id", {"fp": fp}
    ) == [["payments:POST /payments"]]


async def test_end_to_end_ingest_detect_and_open_incident(db: None, builder: GraphBuilder) -> None:
    """telemetry events -> worker handler -> Postgres + graph -> detection -> incident."""
    store = builder.store
    sc = builder.s1_payment_validation()
    now = datetime(2026, 10, 14, 12, 7, 40, tzinfo=UTC)  # windows through 12:07:00 are complete
    # S1 shape: the deployment finished before the failures began
    builder.store.graph.query(
        "MATCH (d:Deployment {id: 'dep-842'}) SET d.started_at = $t", {"t": int(T0.timestamp())}
    )

    events = []
    for w in range(14):  # 30 s windows, T0..T0+7min; failures start in window 12
        failing = w >= 12
        for i in range(20):
            err = failing and i < 14
            events.append(
                {
                    "type": "telemetry",
                    "repo": store.repo,
                    "service": "payments",
                    "env": "prod",
                    "ts": (T0 + timedelta(seconds=30 * w + i)).isoformat(),
                    "kind": "request",
                    "endpoint": "POST /payments",
                    "status_code": 500 if err else 200,
                    "latency_ms": 40.0,
                }
            )
        if failing:
            events.append(
                {
                    "type": "telemetry",
                    "repo": store.repo,
                    "service": "payments",
                    "env": "prod",
                    "ts": (T0 + timedelta(seconds=30 * w)).isoformat(),
                    "kind": "error",
                    "endpoint": "POST /payments",
                    "error_type": "TypeError",
                    "message": "boom",
                    "stack": ["validatePayment (/app/payments/src/payment/validation.js:1:1)"],
                }
            )
    for e in events:
        await handlers.ingest_event({"event": e})

    async with session_scope() as s:
        rows = (await s.execute(select(Telemetry))).scalars().all()
    assert len(rows) == len(events) and any(r.fingerprint for r in rows)

    opened = await detect_repo(store.repo, now)
    assert len(opened) == 1
    assert await detect_repo(store.repo, now) == []  # one open incident per service+endpoint

    async with session_scope() as s:
        inc = (await s.execute(select(IncidentRow))).scalar_one()
    assert (inc.service, inc.endpoint, inc.status) == ("payments", "POST /payments", "open")
    assert inc.onset_ts == T0 + timedelta(seconds=30 * 12)

    g = store.read(
        "MATCH (i:Incident {id: $id}) "
        "OPTIONAL MATCH (i)-[:evidenced_by]->(e:Error) OPTIONAL MATCH (d:Deployment)-[:triggers]->(i) "
        "RETURN i.service, count(DISTINCT e), collect(DISTINCT d.id)",
        {"id": inc.id},
    )
    assert g[0][0] == "payments" and g[0][1] >= 1 and g[0][2] == [sc.incident_id and "dep-842"]
