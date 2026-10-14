"""Persist telemetry and link error signatures to code in the graph (thrown_in / observed_on)."""

from datetime import datetime
from typing import Any

from sqlalchemy import insert

from causaldeploy.contracts import HealthEvent, TelemetryEvent
from causaldeploy.db.models import Telemetry
from causaldeploy.db.session import session_scope
from causaldeploy.detect.fingerprint import app_frames, fingerprint
from causaldeploy.graph.store import Edge, GraphStore

MAX_FRAMES = 5


def event_fingerprint(e: TelemetryEvent) -> str | None:
    if e.kind != "error":
        return None
    return fingerprint(e.error_type, list(e.stack), e.message)


async def store_telemetry(event: TelemetryEvent | HealthEvent) -> str | None:
    """Write one raw telemetry/health row (Postgres is the source for detection windows)."""
    if isinstance(event, HealthEvent):
        row: dict[str, Any] = {
            "repo": event.repo,
            "service": event.service,
            "env": event.env,
            "ts": event.ts,
            "kind": "health",
            "endpoint": "health",
            "status_code": event.status_code,
            "latency_ms": event.latency_ms,
            "fingerprint": None,
            "body": {"ok": event.ok},
        }
        fp = None
    else:
        fp = event_fingerprint(event)
        row = {
            "repo": event.repo,
            "service": event.service,
            "env": event.env,
            "ts": event.ts,
            "kind": event.kind,
            "endpoint": event.endpoint,
            "status_code": event.status_code,
            "latency_ms": event.latency_ms,
            "fingerprint": fp,
            "body": event.model_dump(
                mode="json", include={"request_id", "message", "error_type", "stack"}
            ),
        }
    async with session_scope() as s:
        await s.execute(insert(Telemetry).values(**row))
    return fp


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def link_error(store: GraphStore, event: TelemetryEvent, fp: str) -> list[str]:
    """Upsert the signature and map its application frames to Function nodes.

    The frame -> function match is a graph query (file path suffix + line inside the function's
    range, innermost wins), so it uses the code graph exactly as it was backfilled.
    Returns the function ids the error was thrown in, innermost first.
    """
    store.ensure_schema()
    ts = _epoch(event.ts)
    store.graph.query(
        "MERGE (e:Error {fingerprint: $fp}) "
        "SET e.error_type = $type, e.message = $msg, e.service = $svc, "
        "e.first_seen = CASE WHEN e.first_seen IS NULL OR e.first_seen > $ts "
        "THEN $ts ELSE e.first_seen END, "
        "e.last_seen = CASE WHEN e.last_seen IS NULL OR e.last_seen < $ts "
        "THEN $ts ELSE e.last_seen END",
        {
            "fp": fp,
            "type": event.error_type or "Error",
            "msg": (event.message or "")[:300],
            "svc": event.service,
            "ts": ts,
        },
    )
    thrown: list[str] = []
    for frame in app_frames(list(event.stack))[:MAX_FRAMES]:
        rows = store.read(
            "MATCH (f:Function) WHERE $path ENDS WITH f.path AND f.service = $svc "
            "AND f.start_line <= $line AND f.end_line >= $line "
            "RETURN f.id ORDER BY f.end_line - f.start_line LIMIT 1",
            {"path": frame.path, "line": frame.line, "svc": event.service},
        )
        if not rows:
            rows = store.read(  # service of a frame may differ (shared packages): retry without it
                "MATCH (f:Function) WHERE $path ENDS WITH f.path "
                "AND f.start_line <= $line AND f.end_line >= $line "
                "RETURN f.id ORDER BY f.end_line - f.start_line LIMIT 1",
                {"path": frame.path, "line": frame.line},
            )
        if rows:
            thrown.append(rows[0][0])
    edges = [
        Edge("Error", fp, "thrown_in", "Function", fid, {"frame_depth": i})
        for i, fid in enumerate(thrown)
    ]
    if event.endpoint:
        ep = f"{event.service}:{event.endpoint}"
        if store.read("MATCH (x:Endpoint {id: $id}) RETURN x.id", {"id": ep}):
            edges.append(Edge("Error", fp, "observed_on", "Endpoint", ep))
    store.upsert_edges(edges)
    return thrown
