from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.store import Edge
from causaldeploy.investigate import features as F
from causaldeploy.investigate.scorer import persist, rank


def top(result) -> str:  # type: ignore[no-untyped-def]
    return result.candidates[0].sha


def feat(result, sha: str, name: str) -> float:  # type: ignore[no-untyped-def]
    c = next(c for c in result.candidates if c.sha == sha)
    return next(f.value for f in c.features if f.name == name)


def test_s1_culprit_is_first_with_every_feature_computed(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    r = rank(builder.store, sc.incident_id)
    assert top(r) == sc.culprit_commit and r.verdict == "confident"
    assert [c.rank for c in r.candidates] == [1, 2]
    best = r.candidates[0]
    assert {f.name for f in best.features} == set(F.FEATURE_QUERIES)
    assert all(f.cypher and f.query == f"feature.{f.name}" for f in best.features)
    assert feat(r, sc.culprit_commit, "path") == 1.0  # changed fn is the thrown fn: d = 0
    assert feat(r, sc.culprit_commit, "endpoint") == 1.0
    assert feat(r, "n0000001", "path") == 0.0
    assert best.confidence > 0.7 and best.evidence[0].changed_function == sc.culprit_functions[0]


def test_ranking_is_deterministic(builder: GraphBuilder) -> None:
    sc = builder.s3_five_commit_deployment()
    a = rank(builder.store, sc.incident_id).model_dump()
    b = rank(builder.store, sc.incident_id).model_dump()
    assert a == b and a["candidates"][0]["sha"] == sc.culprit_commit


def test_s3_picks_the_culprit_out_of_five_where_latest_commit_is_wrong(
    builder: GraphBuilder,
) -> None:
    sc = builder.s3_five_commit_deployment()
    # make the culprit NOT the latest commit so the B0 baseline is demonstrably wrong
    builder.store.graph.query("MATCH (c:Commit {sha: $s}) SET c.ts = 50", {"s": sc.culprit_commit})
    g, b0 = rank(builder.store, sc.incident_id), rank(builder.store, sc.incident_id, "b0")
    assert top(g) == sc.culprit_commit
    assert top(b0) != sc.culprit_commit


def test_s2_function_level_precision_beats_file_level(builder: GraphBuilder) -> None:
    sc = builder.s2_two_functions_one_file()
    r = rank(builder.store, sc.incident_id)
    assert top(r) == sc.culprit_commit
    decoy = "d3c0y001"  # same file, sibling function: farther from the thrown function
    assert feat(r, sc.culprit_commit, "path") > feat(r, decoy, "path") > 0


def test_s4_cross_service_path_is_found(builder: GraphBuilder) -> None:
    sc = builder.s4_cross_service()
    r = rank(builder.store, sc.incident_id)
    assert top(r) == sc.culprit_commit
    assert feat(r, sc.culprit_commit, "path") == 0.5  # createOrder -> validateCharge: 1 hop


def test_s5_dependency_bump_scores_on_the_dependency_feature(builder: GraphBuilder) -> None:
    sc = builder.s5_dependency_bump()
    r = rank(builder.store, sc.incident_id)
    assert top(r) == sc.culprit_commit
    assert feat(r, sc.culprit_commit, "dependency") == 1.0


def test_unrelated_change_yields_no_code_evidence(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    # the only candidate is the docs commit: nothing ties it to the failure
    builder.store.delete_edges("Deployment", "dep-842", "includes")
    builder.store.upsert_edges([Edge("Deployment", "dep-842", "includes", "Commit", "n0000001")])
    r = rank(builder.store, sc.incident_id)
    assert r.verdict == "no_code_evidence"


def test_history_feature_uses_resolved_similar_incidents(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    s = builder.store
    old = builder.commit("0ld00001", 10, modifies_fn=sc.culprit_functions)
    s.upsert_nodes("Incident", [{"id": "old-1", "status": "resolved"}])
    s.upsert_edges(
        [
            Edge("Incident", "old-1", "likely_caused_by", "Commit", old),
            Edge("Incident", sc.incident_id, "resembles", "Incident", "old-1", {"similarity": 0.8}),
        ]
    )
    r = rank(s, sc.incident_id)
    assert feat(r, sc.culprit_commit, "history") == 1.0


def test_persist_replaces_candidate_edges(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    r = rank(builder.store, sc.incident_id)
    persist(builder.store, r)
    persist(builder.store, r)
    rows = builder.store.read(
        "MATCH (:Incident {id: $i})-[c:candidate]->(m:Commit) RETURN m.sha, c.rank, c.f_path ORDER BY c.rank",
        {"i": sc.incident_id},
    )
    assert (
        rows[0][0] == sc.culprit_commit and rows[0][1] == 1 and rows[0][2] == 1.0 and len(rows) == 2
    )
    assert builder.store.read(
        "MATCH (:Incident {id: $i})-[:likely_caused_by]->(m) RETURN m.sha", {"i": sc.incident_id}
    ) == [[sc.culprit_commit]]


def test_b1_keyword_baseline_is_misled_where_graph_is_not(builder: GraphBuilder) -> None:
    """S2: the decoy commit message mentions the error keywords; B1 follows words, graph follows calls."""
    sc = builder.s2_two_functions_one_file()
    s = builder.store
    s.graph.query(
        "MATCH (c:Commit {sha: 'd3c0y001'}) SET c.message = 'tidy payment receipt validation currency'"
    )
    s.graph.query(
        "MATCH (e:Error {fingerprint: 'fp-s2'}) SET e.message = 'payment receipt currency validation failed'"
    )
    assert top(rank(s, sc.incident_id, "b1")) == "d3c0y001"
    assert top(rank(s, sc.incident_id, "graph")) == sc.culprit_commit


async def test_rank_api_returns_features_with_queries_and_methods(
    db: None, builder: GraphBuilder
) -> None:
    from datetime import UTC, datetime

    import httpx

    from causaldeploy.api.auth import User, make_session
    from causaldeploy.api.main import create_app
    from causaldeploy.db.models import IncidentRow, Repository
    from causaldeploy.db.session import session_scope

    sc = builder.s1_payment_validation()
    repo = builder.store.repo
    now = datetime.now(UTC)
    async with session_scope() as s:
        s.add(Repository(full_name=repo))
        await s.flush()
        s.add(
            IncidentRow(
                id=sc.incident_id,
                repo=repo,
                service="payments",
                env="prod",
                endpoint="POST /payments",
                status="open",
                onset_ts=now,
                detected_at=now,
            )
        )
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        c.cookies.set("cd_session", make_session(User(login="al", repos={repo: "viewer"})))  # type: ignore[arg-type]
        g = (await c.post(f"/api/v1/incidents/{sc.incident_id}/rank")).json()
        assert g["method"] == "graph" and g["verdict"] == "confident"
        first = g["candidates"][0]
        assert first["sha"] == sc.culprit_commit
        path = next(f for f in first["features"] if f["name"] == "path")
        assert (
            path["value"] == 1.0
            and path["query"] == "feature.path"
            and "shortestPath" in path["cypher"]
        )
        b0 = (await c.post(f"/api/v1/incidents/{sc.incident_id}/rank?method=b0")).json()
        assert b0["method"] == "b0" and b0["candidates"][0]["features"] == []
        other = httpx.Cookies()
        c.cookies.set("cd_session", make_session(User(login="eve", repos={"x/y": "approver"})))  # type: ignore[arg-type]
        assert (await c.post(f"/api/v1/incidents/{sc.incident_id}/rank")).status_code == 404
        del other


async def test_incident_detail_bundles_ranking_and_trace(db: None, builder: GraphBuilder) -> None:
    from datetime import UTC, datetime

    import httpx

    from causaldeploy.api.auth import User, make_session
    from causaldeploy.api.main import create_app
    from causaldeploy.db.models import IncidentRow, Investigation, Repository
    from causaldeploy.db.session import session_scope

    sc = builder.s1_payment_validation()
    repo, now = builder.store.repo, datetime.now(UTC)
    agent = {
        "status": "explained",
        "validation_errors": [],
        "explanation": {"verdict": "confident", "claims": []},
        "steps": [
            {
                "n": 1,
                "name": "get_incident",
                "args": {"incident_id": "inc-1"},
                "summary": "get_incident: errors=1 item(s)",
                "latency_ms": 12.0,
                "node_ids": ["Incident:inc-1"],
                "result": {},
            }
        ],
    }
    async with session_scope() as s:
        s.add(Repository(full_name=repo))
        await s.flush()
        s.add(
            IncidentRow(
                id=sc.incident_id,
                repo=repo,
                service="payments",
                env="prod",
                endpoint="POST /payments",
                status="investigating",
                onset_ts=now,
                detected_at=now,
            )
        )
        await s.flush()
        s.add(Investigation(incident_id=sc.incident_id, result={"agent": agent}))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        c.cookies.set("cd_session", make_session(User(login="al", repos={repo: "viewer"})))  # type: ignore[arg-type]
        d = (await c.get(f"/api/v1/incidents/{sc.incident_id}")).json()
    assert d["incident"]["endpoint"] == "POST /payments"
    assert d["ranking"]["candidates"][0]["sha"] == sc.culprit_commit
    assert d["investigation"]["status"] == "explained"
    assert d["investigation"]["steps"][0]["name"] == "get_incident"
