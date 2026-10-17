"""#93 dashboard views: incident list filters + suspect, service health, deployment timeline,
hotspots and the latest evaluation result."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from causaldeploy.api import dashboard_views
from causaldeploy.config import get_settings
from causaldeploy.db.models import IncidentRow, Investigation, Repository, Telemetry
from causaldeploy.db.session import session_scope
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.store import Edge, GraphStore
from tests.test_api import client, login_as  # noqa: F401  (fixture re-export)

REPO = "acme/shop"
OTHER = "evil/corp"
NOW = datetime.now(UTC).replace(microsecond=0)


async def seed_incident(
    inc_id: str,
    *,
    repo: str = REPO,
    status: str = "open",
    severity: str | None = "sev2",
    service: str = "payments",
    env: str = "prod",
    endpoint: str = "POST /payments",
    onset: datetime | None = None,
    ranking: dict[str, object] | None = None,
) -> None:
    onset = onset or NOW
    async with session_scope() as s:
        if (await s.get(Repository, repo)) is None:
            s.add(Repository(full_name=repo))
            await s.flush()
        s.add(
            IncidentRow(
                id=inc_id,
                repo=repo,
                service=service,
                env=env,
                endpoint=endpoint,
                status=status,
                onset_ts=onset,
                detected_at=onset + timedelta(minutes=1),
                severity=severity,
            )
        )
        await s.flush()
        if ranking is not None:
            s.add(Investigation(incident_id=inc_id, result={"ranking": ranking}))


def ranking(sha: str, verdict: str = "confident", kind: str = "commit") -> dict[str, object]:
    return {
        "verdict": verdict,
        "candidates": [
            {"sha": sha, "kind": kind, "message": f"change {sha}", "confidence": 0.81},
            {"sha": "other", "kind": "commit", "message": "noise", "confidence": 0.1},
        ],
    }


# -- incident list ----------------------------------------------------------------------


async def test_incident_list_carries_severity_ack_and_latest_suspect(
    client: httpx.AsyncClient,  # noqa: F811
) -> None:
    login_as(client, "dev", {REPO: "viewer"})
    await seed_incident("inc-a", ranking=ranking("old1"))
    async with session_scope() as s:  # a later investigation replaces the suspect
        s.add(Investigation(incident_id="inc-a", result={"ranking": ranking("abc1234")}))
        row = await s.get(IncidentRow, "inc-a")
        assert row is not None
        row.acknowledged_by = "alice"
    await seed_incident("inc-b", ranking=ranking("zzz", verdict="abstain"))
    await seed_incident("inc-c")
    r = await client.get("/api/v1/incidents")
    assert r.status_code == 200
    by = {i["id"]: i for i in r.json()["items"]}
    assert by["inc-a"]["suspect"] == {
        "sha": "abc1234",
        "kind": "commit",
        "message": "change abc1234",
        "confidence": 0.81,
    }
    assert by["inc-a"]["severity"] == "sev2" and by["inc-a"]["acknowledged_by"] == "alice"
    assert by["inc-b"]["suspect"] is None  # abstained: nobody is named
    assert by["inc-c"]["suspect"] is None  # not ranked yet
    detail = (await client.get("/api/v1/incidents/inc-a")).json()["incident"]
    assert detail["severity"] == "sev2" and detail["suspect"]["sha"] == "abc1234"


async def test_incident_list_filters(client: httpx.AsyncClient) -> None:  # noqa: F811
    login_as(client, "dev", {REPO: "viewer"})
    await seed_incident("inc-open", status="open", severity="sev1", onset=NOW - timedelta(days=3))
    await seed_incident("inc-expl", status="explained", service="orders", endpoint="POST /orders")
    await seed_incident("inc-res", status="resolved", severity="sev3")
    await seed_incident("inc-closed", status="closed", severity=None)
    await seed_incident("inc-hidden", repo=OTHER, status="open")

    async def ids(**params: object) -> set[str]:
        r = await client.get("/api/v1/incidents", params=params)  # type: ignore[arg-type]
        assert r.status_code == 200, r.text
        return {i["id"] for i in r.json()["items"]}

    assert await ids() == {"inc-open", "inc-expl", "inc-res", "inc-closed"}  # never OTHER
    assert await ids(status=["resolved", "closed"]) == {"inc-res", "inc-closed"}  # archive
    assert await ids(status="explained") == {"inc-expl"}
    assert await ids(severity=["sev1", "sev3"]) == {"inc-open", "inc-res"}
    assert await ids(service="orders") == {"inc-expl"}
    assert await ids(q="ORDERS") == {"inc-expl"}  # case-insensitive, endpoint/service
    assert await ids(q="inc-c") == {"inc-closed"}
    assert await ids(q="100%_") == set()  # wildcards are literal
    assert await ids(since=(NOW - timedelta(days=1)).isoformat()) == {
        "inc-expl",
        "inc-res",
        "inc-closed",
    }
    assert await ids(until=(NOW - timedelta(days=1)).isoformat()) == {"inc-open"}
    assert await ids(repo=OTHER) == set()
    bad = await client.get("/api/v1/incidents", params={"status": "nope"})
    assert bad.status_code == 422


# -- service health ---------------------------------------------------------------------


async def add_requests(
    repo: str, service: str, at: datetime, n: int, errors: int, latency: float = 40.0
) -> None:
    async with session_scope() as s:
        for i in range(n):
            s.add(
                Telemetry(
                    repo=repo,
                    service=service,
                    env="prod",
                    ts=at + timedelta(milliseconds=10 * i),
                    kind="request",
                    endpoint="POST /x",
                    status_code=500 if i < errors else 200,
                    latency_ms=latency,
                    body={},
                )
            )


async def test_service_health_ok_degraded_and_incident(db: None) -> None:
    s = get_settings()
    w = s.detect_window_seconds
    now = datetime.fromtimestamp((NOW.timestamp() // w) * w + 7, UTC)
    since, until = dashboard_views.detector._complete_range(now, s)
    k = s.detect_consecutive
    t = since
    while t < until:
        current = t >= until - timedelta(seconds=w * k)
        for svc in ("payments", "orders", "search"):
            bad = current and svc in ("payments", "orders")
            await add_requests(
                REPO, svc, t + timedelta(seconds=2), 10, 8 if bad else 0, 900.0 if bad else 40.0
            )
        t += timedelta(seconds=w)
    await seed_incident("inc-orders", service="orders", status="investigating")
    await seed_incident("inc-old", service="search", status="resolved")
    await seed_incident("inc-quiet", service="billing", status="open")  # no telemetry at all

    by = {h.service: h for h in await dashboard_views.services_health(REPO, now)}
    assert by["search"].status == "ok" and by["search"].error_rate == 0.0
    assert by["search"].open_incident_id is None
    pay = by["payments"]
    assert pay.status == "degraded"  # anomalous, but no incident yet
    assert pay.requests == 10 * k and pay.error_rate == 0.8
    assert pay.baseline_error_rate == 0.0 and pay.baseline_p95_ms == 40.0 and pay.p95_ms == 900.0
    assert by["orders"].status == "incident" and by["orders"].open_incident_id == "inc-orders"
    assert by["billing"].status == "incident" and by["billing"].requests == 0


async def test_service_health_route_scopes_to_visible_repos(
    client: httpx.AsyncClient,  # noqa: F811
) -> None:
    login_as(client, "dev", {REPO: "viewer"})
    await seed_incident("inc-1", status="open")
    await seed_incident("inc-x", repo=OTHER, status="open")
    r = await client.get("/api/v1/services/health")
    assert r.status_code == 200
    assert [(h["repo"], h["service"], h["status"]) for h in r.json()] == [
        (REPO, "payments", "incident")
    ]
    assert (await client.get("/api/v1/services/health", params={"repo": OTHER})).status_code == 404


# -- deployments ------------------------------------------------------------------------


async def test_deployments_with_correlated_incidents(
    client: httpx.AsyncClient,  # noqa: F811
    store: GraphStore,
    builder: GraphBuilder,
) -> None:
    repo = store.repo
    login_as(client, "dev", {repo: "viewer"})
    ts = lambda d: int((NOW - d).timestamp())  # noqa: E731
    builder.commit("c1", ts(timedelta(hours=5)))
    builder.commit("c2", ts(timedelta(hours=4)))
    builder.commit("c3", ts(timedelta(hours=2)))
    builder.deployment("dep-old", "prod", ["c1"], ts(timedelta(days=10)))
    builder.deployment("dep-1", "prod", ["c1", "c2"], ts(timedelta(hours=3)))
    builder.deployment("dep-2", "prod", ["c3"], ts(timedelta(hours=1)))
    # the detector linked inc-trig to dep-1 even though its onset is after dep-2
    builder.incident("inc-trig", service="payments", deployment="dep-1")
    await seed_incident("inc-a", repo=repo, onset=NOW - timedelta(hours=2))
    await seed_incident("inc-b", repo=repo, onset=NOW - timedelta(minutes=30), status="resolved")
    await seed_incident("inc-trig", repo=repo, onset=NOW - timedelta(minutes=20))
    await seed_incident("inc-before", repo=repo, onset=NOW - timedelta(days=20))

    r = await client.get(f"/api/v1/repos/{repo}/deployments", params={"hours": 72})
    assert r.status_code == 200, r.text
    deps = r.json()
    assert [d["id"] for d in deps] == ["dep-1", "dep-2"]  # dep-old is outside the window
    d1, d2 = deps
    assert d1["commits"] == 2 and d1["env"] == "prod" and d1["status"] == "success"
    assert {(i["id"], i["triggered"]) for i in d1["incidents"]} == {
        ("inc-a", False),
        ("inc-trig", True),
    }
    assert [(i["id"], i["status"]) for i in d2["incidents"]] == [("inc-b", "resolved")]
    assert (await client.get(f"/api/v1/repos/{OTHER}/deployments")).status_code == 404


# -- hotspots ---------------------------------------------------------------------------


async def test_hotspots_count_blamed_functions_and_dependencies(
    client: httpx.AsyncClient,  # noqa: F811
    store: GraphStore,
    builder: GraphBuilder,
) -> None:
    repo = store.repo
    login_as(client, "dev", {repo: "viewer"})
    v = builder.function("payments", "src/validate.js", "validatePayment")
    f = builder.function("payments", "src/fmt.js", "formatReceipt")
    builder.commit("bad1", 100, modifies_fn=[v])
    builder.commit("bad2", 200, modifies_fn=[v, f])
    dep = builder.dependency("money-utils", "2.0.0")
    builder.commit("bump", 250, changes_dep=[dep])
    builder.commit("wrong", 300, modifies_fn=[f])
    for i, (inc, onset) in enumerate((("i1", 1000), ("i2", 2000), ("i3", 3000), ("i4", 4000))):
        builder.incident(inc, service="payments", onset_ts=onset, detected_at=onset + i)
    builder.candidates("i1", [("bad1", 0.9)])
    builder.candidates("i2", [("bad2", 0.9)])
    builder.candidates("i3", [("bump", 0.9)])
    builder.candidates("i4", [("wrong", 0.9)])
    # rejected by a person: no longer counts
    store.graph.query("MATCH (:Incident {id: 'i4'})-[r:likely_caused_by]->() SET r.rejected = true")
    store.upsert_edges([Edge("Incident", "i1", "confirmed_cause", "Commit", "bad1")])
    store.upsert_nodes(
        "DependencyChange",
        [{"id": "lodash:4->5@d", "name": "lodash", "from_version": "4", "to_version": "5"}],
    )
    store.upsert_edges(
        [
            Edge(
                "Incident",
                "i2",
                "candidate",
                "DependencyChange",
                "lodash:4->5@d",
                {"rank": 1, "verdict": "confident"},
            )
        ]
    )

    r = await client.get(f"/api/v1/repos/{repo}/hotspots")
    assert r.status_code == 200, r.text
    out = r.json()
    fns = {x["name"]: x for x in out["functions"]}
    assert out["functions"][0]["name"] == "validatePayment"
    assert fns["validatePayment"]["incidents"] == 2 and fns["validatePayment"]["confirmed"] == 1
    assert fns["validatePayment"]["incident_ids"] == ["i2", "i1"]  # newest first
    assert fns["validatePayment"]["last_incident"] == "i2"
    assert fns["formatReceipt"]["incidents"] == 1  # i4's rejected cause is not counted
    assert {(d["name"], d["incidents"]) for d in out["dependencies"]} == {
        ("money-utils", 1),
        ("lodash", 1),
    }
    assert (await client.get(f"/api/v1/repos/{OTHER}/hotspots")).status_code == 404


# -- evaluation -------------------------------------------------------------------------


async def test_eval_latest_serves_the_harness_json_unchanged(
    client: httpx.AsyncClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    login_as(client, "dev", {REPO: "viewer"})
    default = Path(get_settings().eval_results_path)
    assert default.parts[-3:] == ("docs", "results", "latest.json")
    if default.exists():  # the committed result is served byte-for-byte equivalent
        r = await client.get("/api/v1/eval/latest")
        assert r.status_code == 200
        assert r.json() == json.loads(default.read_text(encoding="utf-8"))
    result = {
        "generated_at": "2026-10-16T19:25:50+00:00",
        "weights_version": "v9",
        "methods": ["graph", "b0 latest"],
        "splits": {
            "all": [
                {
                    "method": "graph",
                    "n": 2,
                    "top1": 0.5,
                    "top3": 1.0,
                    "function_level": 0.5,
                    "false_attribution": 0.0,
                    "median_ms": 12.0,
                    "top1_ci": [0.0, 1.0],
                }
            ]
        },
        "blast_radius": None,
        "fix_verification_rate": None,
        "memory": [],
        "runtime_seconds": 0.4,
        "scenarios": [
            {
                "id": "S1",
                "variant": 0,
                "title": "t",
                "split": "tune",
                "culprit": "abc",
                "results": {
                    "graph": {
                        "top": "abc",
                        "rank_of_culprit": 1,
                        "verdict": "confident",
                        "correct": True,
                    }
                },
            }
        ],
        "failures": [],
        "future_key": {"kept": True},  # unknown keys pass through
    }
    fake = tmp_path / "latest.json"
    fake.write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(get_settings(), "eval_results_path", str(fake))
    assert (await client.get("/api/v1/eval/latest")).json() == result
    fake.write_text(json.dumps({"methods": ["graph"]}), encoding="utf-8")
    r = await client.get("/api/v1/eval/latest")
    assert r.status_code == 500 and "shape" in r.json()["error"]["message"]
    monkeypatch.setattr(get_settings(), "eval_results_path", str(tmp_path / "missing.json"))
    r = await client.get("/api/v1/eval/latest")
    assert r.status_code == 404 and "make eval" in r.json()["error"]["message"]
    client.cookies.clear()
    assert (await client.get("/api/v1/eval/latest")).status_code == 401
