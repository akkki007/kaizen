"""`demo-history`: seed resolved ShopLite-shaped incidents (with their verified fixes) so the
history feature has something to remember. Deterministic ids and timestamps; every write is an
upsert / insert-if-absent, so running it twice changes nothing. Ends with a similarity pass
over every incident in the repository's graph (computed `resembles` edges, not hand-seeded).
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from causaldeploy.db.models import IncidentRow, RemediationRun, Repository
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import Edge, GraphStore

FN = {
    "pay_post": "payments:services/payments/src/app.js:POST /payments",
    "pay_currency": "payments:services/payments/src/payment/validation.js:validateCurrency",
    "orders_post": "orders:services/orders/src/app.js:POST /orders",
    "orders_line": "orders:services/orders/src/pricing.js:lineCents",
    "gateway_post": "gateway:services/gateway/src/app.js:POST /orders",
}
DEP = "money-utils@1.4.0"


@dataclass(frozen=True)
class Historical:
    slug: str
    service: str
    endpoint: str
    error_type: str
    message: str
    thrown_in: tuple[str, ...]
    changed: tuple[str, ...]
    commit_message: str
    day: int  # days before 2026-10-01

    @property
    def incident_id(self) -> str:
        return f"hist-{self.slug}"

    @property
    def sha(self) -> str:
        return hashlib.sha1(f"kaizen-demo:{self.slug}".encode()).hexdigest()

    @property
    def run_id(self) -> str:
        return f"rem-hist-{self.slug}"


HISTORY = (
    Historical(
        "payments-currency",
        "payments",
        "POST /payments",
        "TypeError",
        "Cannot read properties of undefined (reading 'toUpperCase')",
        (FN["pay_currency"], FN["pay_post"]),
        (FN["pay_currency"],),
        "refactor(payments): normalise currency handling per region",
        30,
    ),
    Historical(
        "orders-rounding",
        "orders",
        "POST /orders",
        "RangeError",
        "amount must be an integer number of cents, got 1999.5",
        (FN["orders_line"], FN["orders_post"]),
        (FN["orders_line"],),
        "perf(orders): compute line totals in floats",
        20,
    ),
    Historical(
        "gateway-timeout",
        "gateway",
        "POST /orders",
        "Error",
        "upstream orders timed out after 3000ms",
        (FN["gateway_post"],),
        (FN["gateway_post"],),
        "feat(gateway): lower the upstream timeout",
        10,
    ),
)
T_BASE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _times(h: Historical) -> dict[str, datetime]:
    onset = T_BASE - timedelta(days=h.day)
    return {
        "onset_ts": onset,
        "detected_at": onset + timedelta(seconds=75),
        "diagnosed_at": onset + timedelta(seconds=150),
        "resolved_at": onset + timedelta(minutes=14),
    }


def _write_graph(store: GraphStore, h: Historical) -> None:
    t = {k: int(v.timestamp()) for k, v in _times(h).items()}
    fp = f"fp-{h.slug}"
    fix = f"fix-{h.run_id}"
    for fid in {*h.thrown_in, *h.changed}:
        service, path, name = fid.split(":", 2)
        store.upsert_nodes("Service", [{"id": service}])
        store.upsert_nodes("File", [{"id": f"{service}:{path}", "path": path, "service": service}])
        store.upsert_nodes(
            "Function", [{"id": fid, "name": name, "path": path, "service": service}]
        )
        store.upsert_edges(
            [
                Edge("File", f"{service}:{path}", "contains", "Function", fid),
                Edge("Function", fid, "belongs_to", "Service", service),
                Edge("Function", fid, "depends_on", "Dependency", DEP),
            ]
        )
    store.upsert_nodes("Dependency", [{"id": DEP, "name": "money-utils", "version": "1.4.0"}])
    store.upsert_nodes(
        "Commit", [{"sha": h.sha, "ts": t["onset_ts"] - 600, "message": h.commit_message}]
    )
    store.upsert_nodes(
        "Error",
        [
            {
                "fingerprint": fp,
                "error_type": h.error_type,
                "message": h.message,
                "service": h.service,
                "first_seen": t["onset_ts"],
                "last_seen": t["resolved_at"],
            }
        ],
    )
    store.upsert_nodes(
        "Incident",
        [
            {
                "id": h.incident_id,
                "status": "resolved",
                "service": h.service,
                "endpoint": h.endpoint,
                "env": "prod",
                "onset_ts": t["onset_ts"],
                "detected_at": t["detected_at"],
                "resolved_at": t["resolved_at"],
                "confirmed_root_cause": h.sha,
                "demo": True,
            }
        ],
    )
    store.upsert_nodes("Fix", [{"id": fix, "kind": "revert", "verified": True, "accepted": True}])
    store.upsert_nodes("Test", [{"id": f"test-{h.run_id}", "name": "staging verification"}])
    store.upsert_nodes("Developer", [{"id": "demo-oncall"}])
    store.upsert_edges(
        [
            *[
                Edge("Error", fp, "thrown_in", "Function", f, {"frame_depth": i})
                for i, f in enumerate(h.thrown_in)
            ],
            *[Edge("Commit", h.sha, "modifies_fn", "Function", f) for f in h.changed],
            Edge("Incident", h.incident_id, "affects", "Service", h.service),
            Edge("Incident", h.incident_id, "evidenced_by", "Error", fp),
            Edge("Incident", h.incident_id, "likely_caused_by", "Commit", h.sha),
            Edge("Incident", h.incident_id, "confirmed_cause", "Commit", h.sha),
            Edge("Incident", h.incident_id, "fixed_by", "Fix", fix),
            Edge("Fix", fix, "reverts", "Commit", h.sha),
            Edge("Fix", fix, "verified_by", "Test", f"test-{h.run_id}"),
            Edge("Developer", "demo-oncall", "resolved", "Incident", h.incident_id),
        ]
    )


def _memory(h: Historical) -> dict[str, Any]:
    t = _times(h)
    return {
        "version": 1,
        "demo": True,
        "candidates": [{"sha": h.sha, "rank": 1, "message": h.commit_message}],
        "root_cause": {"sha": h.sha, "status": "confirmed"},
        "fix": {"remediation_id": h.run_id, "kind": "revert", "sha": h.sha},
        "verification": {"passed": True, "error_rate_after": 0.0},
        "ttd_s": (t["detected_at"] - t["onset_ts"]).total_seconds(),
        "ttdiagnose_s": (t["diagnosed_at"] - t["detected_at"]).total_seconds(),
        "ttr_s": (t["resolved_at"] - t["detected_at"]).total_seconds(),
    }


async def seed_history(repo: str, store: GraphStore | None = None) -> dict[str, Any]:
    import asyncio

    from causaldeploy.config import get_settings
    from causaldeploy.memory.similarity import write

    store = store or GraphStore(repo)
    await asyncio.to_thread(store.ensure_schema)
    async with session_scope() as s:
        await s.execute(insert(Repository).values(full_name=repo).on_conflict_do_nothing())
        for h in HISTORY:
            t = _times(h)
            await s.execute(
                insert(IncidentRow)
                .values(
                    id=h.incident_id,
                    repo=repo,
                    service=h.service,
                    env="prod",
                    endpoint=h.endpoint,
                    status="resolved",
                    severity="sev2",
                    confirmed_root_cause=h.sha,
                    root_cause_status="confirmed",
                    memory=_memory(h),
                    **t,
                )
                .on_conflict_do_nothing()
            )
            await s.execute(
                insert(RemediationRun)
                .values(
                    id=h.run_id,
                    incident_id=h.incident_id,
                    kind="revert",
                    status="merged",
                    approved_by="demo-oncall",
                    result={
                        "sha": h.sha,
                        "branch": f"kaizen/fix-{h.incident_id}",
                        "verification": {"passed": True, "error_rate_after": 0.0},
                        "demo": True,
                    },
                )
                .on_conflict_do_nothing()
            )
    for h in HISTORY:
        await asyncio.to_thread(_write_graph, store, h)
    threshold = get_settings().remediation_similarity_threshold
    ids = [r[0] for r in await asyncio.to_thread(store.read, "MATCH (i:Incident) RETURN i.id")]
    for inc in sorted(ids):
        await asyncio.to_thread(write, store, inc, threshold)
    pairs = await asyncio.to_thread(store.read, "MATCH ()-[r:resembles]->() RETURN count(r)")
    return {
        "repo": repo,
        "historical": [h.incident_id for h in HISTORY],
        "resembles_edges": int(pairs[0][0]) if pairs else 0,
    }
