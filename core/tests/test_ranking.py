import pytest

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


def test_service_feature_follows_service_calls_across_services(builder: GraphBuilder) -> None:
    from causaldeploy.detect.errors import derive_service_calls

    sc = builder.s4_cross_service()
    # request chain observed in telemetry: gateway -> orders -> payments (callers start first)
    n = derive_service_calls(builder.store, [("payments", 3.0), ("gateway", 1.0), ("orders", 2.0)])
    assert n == 2
    assert (
        derive_service_calls(builder.store, [("payments", 3.0), ("gateway", 1.0), ("orders", 2.0)])
        == 2
    )
    r = rank(builder.store, sc.incident_id)
    # createOrder lives in orders; failing service is payments: 1 service hop
    assert feat(r, sc.culprit_commit, "service") == 0.5
    assert top(r) == sc.culprit_commit


def test_lookback_window_adds_successful_deployments_before_onset(
    builder: GraphBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Candidates = the triggering deployment plus successful deployments to the same env that
    started within `lookback_hours` before onset (#89). The S7 shape (an unrelated later
    deployment) adds a suspect but must not outrank the culprit."""
    from causaldeploy.config import get_settings

    sc = builder.s1_payment_validation()  # onset 360, dep-842 at 300
    later = builder.commit("b0000001", 250, modifies_fn=[], message="unrelated later deploy")
    builder.deployment("dep-later", "prod", [later], 340)
    old = builder.commit("b0000002", 10, message="too old")
    builder.deployment("dep-old", "prod", [old], 360 - 2 * 3600)
    failed = builder.commit("b0000003", 20, message="never went live")
    builder.deployment("dep-failed", "prod", [failed], 350, status="failure")
    staging = builder.commit("b0000004", 30, message="other env")
    builder.deployment("dep-stg", "staging", [staging], 350)
    after = builder.commit("b0000005", 40, message="after onset")
    builder.deployment("dep-after", "prod", [after], 400)

    monkeypatch.setenv("CD_LOOKBACK_HOURS", "1")
    get_settings.cache_clear()
    try:
        r = rank(builder.store, sc.incident_id)
        assert {c.sha for c in r.candidates} == {sc.culprit_commit, "n0000001", "b0000001"}
        assert top(r) == sc.culprit_commit and r.verdict == "confident"
        monkeypatch.setenv("CD_LOOKBACK_HOURS", "3")
        get_settings.cache_clear()
        shas = {c.sha for c in rank(builder.store, sc.incident_id).candidates}
        assert "b0000002" in shas and not shas & {"b0000003", "b0000004", "b0000005"}
    finally:
        monkeypatch.delenv("CD_LOOKBACK_HOURS")
        get_settings.cache_clear()


def test_triggering_deployment_wins_for_a_commit_in_two_deployments(
    builder: GraphBuilder,
) -> None:
    sc = builder.s1_payment_validation()
    builder.deployment("dep-redeploy", "prod", [sc.culprit_commit], 330)
    r = rank(builder.store, sc.incident_id)
    assert len(r.candidates) == 2 and r.candidates[0].deployment == "dep-842"


def test_s3_candidates_are_exactly_the_five_bundled_commits(builder: GraphBuilder) -> None:
    sc = builder.s3_five_commit_deployment()
    r = rank(builder.store, sc.incident_id)
    assert len(r.candidates) == 5
    assert {c.sha for c in r.candidates} == set(sc.commits)
    assert all(c.kind == "commit" and c.node_id == f"Commit:{c.sha}" for c in r.candidates)


def test_s6_config_change_is_the_top_non_code_candidate(builder: GraphBuilder) -> None:
    sc = builder.s6_config_change()
    r = rank(builder.store, sc.incident_id)
    kinds = {c.sha: c.kind for c in r.candidates}
    assert kinds == {sc.culprit_commit: "config", "a0000006": "commit", "c0ffee06": "commit"}
    best = r.candidates[0]
    assert best.sha == sc.culprit_commit and best.node_id == f"ConfigChange:{sc.culprit_commit}"
    # no commit is evidenced: the system says so instead of blaming (or reverting) code
    assert r.verdict == "no_code_evidence"
    assert feat(r, sc.culprit_commit, "config") == 1.0
    persist(builder.store, r)
    s = builder.store
    assert s.read(
        "MATCH (:Incident {id: $i})-[e:candidate]->(x:ConfigChange) RETURN x.id, e.kind",
        {"i": sc.incident_id},
    ) == [[sc.culprit_commit, "config"]]
    assert not s.read(
        "MATCH (:Incident {id: $i})-[:likely_caused_by]->(x) RETURN x", {"i": sc.incident_id}
    )


def test_dependency_change_without_a_code_commit_is_a_candidate(builder: GraphBuilder) -> None:
    """A lockfile/base-image bump ships money-utils 2.0.0 with only a docs commit in the deploy."""
    sc = builder.s5_dependency_bump()
    s = builder.store
    s.delete_edges("Deployment", "dep-5", "includes")
    docs = builder.commit("d0c50001", 190, files=["payments:README.md"], message="docs")
    s.upsert_edges([Edge("Deployment", "dep-5", "includes", "Commit", docs)])
    did = builder.dependency_change("dep-5", "money-utils", "1.9.0", "2.0.0")
    r = rank(s, sc.incident_id)
    assert [c.kind for c in r.candidates] == ["dependency", "commit"]
    best = r.candidates[0]
    assert best.sha == did and feat(r, did, "dependency") == 1.0
    dep = next(f for f in best.features if f.name == "dependency")
    assert "Dependency:money-utils@2.0.0" in dep.evidence_ids
    assert r.verdict == "no_code_evidence"


def test_weights_are_versioned_and_stamped(builder: GraphBuilder) -> None:
    from causaldeploy.investigate.scorer import (
        DEFAULT_WEIGHTS_VERSION,
        WEIGHTS,
        load_weights,
        weight_versions,
    )

    ws = load_weights()
    assert ws.version == DEFAULT_WEIGHTS_VERSION and ws.fitted_on == "tune"
    assert "v1" in weight_versions() and set(ws.weights) <= set(F.FEATURE_QUERIES)
    assert WEIGHTS["path"] == ws.weights["path"]
    sc = builder.s1_payment_validation()
    r = rank(builder.store, sc.incident_id)
    assert r.weights_version == DEFAULT_WEIGHTS_VERSION
    persist(builder.store, r)
    rows = builder.store.read(
        "MATCH (:Incident {id: $i})-[e:candidate|likely_caused_by]->() "
        "RETURN DISTINCT e.weights_version",
        {"i": sc.incident_id},
    )
    assert rows == [[DEFAULT_WEIGHTS_VERSION]]
    with pytest.raises(ValueError):
        load_weights("../v1")


def test_weights_json_ships_inside_the_package() -> None:
    from importlib import resources

    f = resources.files("causaldeploy.investigate").joinpath("weights", "v1.json")
    assert f.is_file() and '"version": "v1"' in f.read_text(encoding="utf-8")


def test_ambiguous_persists_top3_and_a_tentative_cause(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    s = builder.store
    s.graph.query(
        "MATCH (:Error {fingerprint: 'fp-undefined-currency'})-[t:thrown_in]->() DELETE t"
    )
    extra = [builder.commit(f"e000000{i}", 120 + i, message=f"misc {i}") for i in range(3)]
    s.upsert_edges([Edge("Deployment", "dep-842", "includes", "Commit", x) for x in extra])
    r = rank(s, sc.incident_id)
    assert r.verdict == "ambiguous" and len(r.candidates) == 5
    persist(s, r)
    kept = s.read(
        "MATCH (:Incident {id: $i})-[e:candidate]->(c:Commit) RETURN c.sha ORDER BY e.rank",
        {"i": sc.incident_id},
    )
    assert [k[0] for k in kept] == [c.sha for c in r.candidates[:3]]
    assert s.read(
        "MATCH (:Incident {id: $i})-[e:likely_caused_by]->(c) RETURN c.sha, e.tentative",
        {"i": sc.incident_id},
    ) == [[r.candidates[0].sha, True]]
    # a confident result replaces the tentative edge and keeps every candidate
    persist(s, r.model_copy(update={"verdict": "confident"}))
    assert s.read(
        "MATCH (:Incident {id: $i})-[e:likely_caused_by]->() RETURN e.tentative",
        {"i": sc.incident_id},
    ) == [[False]]
    assert (
        len(s.read("MATCH (:Incident {id: $i})-[e:candidate]->() RETURN e", {"i": sc.incident_id}))
        == 5
    )


def test_cli_investigate_prints_verdict_candidates_and_explanations(
    builder: GraphBuilder, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    from causaldeploy import cli

    sc = builder.s1_payment_validation()
    monkeypatch.setattr(
        sys, "argv", ["causaldeploy", "investigate", builder.store.repo, sc.incident_id]
    )
    cli.main()
    out = capsys.readouterr().out
    assert "verdict confident" in out and "weights v1" in out
    assert f"#1 [commit] {sc.culprit_commit}" in out and "#2 [commit] n0000001" in out
    assert "the error is thrown inside the changed function validatePayment" in out
    assert "path: payments:src/payment/validation.js:validatePayment" in out


def test_bm25_idf_is_over_the_candidate_set() -> None:
    from causaldeploy.investigate.scorer import bm25, tokens

    assert tokens("validatePayment src/payment/validation.js") == [
        "validate",
        "payment",
        "payment",
        "validation",
    ]
    docs = [["payment", "currency"], ["payment", "docs"], ["readme"]]
    s = bm25(["payment", "currency"], docs)
    # "payment" is in 2 of 3 docs, "currency" in 1: the rare term carries the score
    assert s[0] > s[1] > s[2] == 0.0
    assert bm25(["x"], []) == []


def test_b1_matches_changed_file_paths_and_function_names(builder: GraphBuilder) -> None:
    sc = builder.s2_two_functions_one_file()
    s = builder.store
    s.graph.query("MATCH (e:Error {fingerprint: 'fp-s2'}) SET e.message = 'receipt format broke'")
    r = rank(s, sc.incident_id, "b1")
    # the decoy changed formatReceipt: its function name matches the error words
    assert top(r) == "d3c0y001" and r.candidates[0].score > r.candidates[1].score
    assert r.verdict == "confident" and r.candidates[0].features == []


async def test_investigation_record_has_inputs_version_and_duration(
    db: None, builder: GraphBuilder
) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from causaldeploy.db.models import IncidentRow, Investigation, Repository
    from causaldeploy.db.session import session_scope
    from causaldeploy.investigate.run import investigate

    sc = builder.s1_payment_validation()
    repo, now = builder.store.repo, datetime.now(UTC)
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
    result = await investigate(repo, sc.incident_id)
    async with session_scope() as s:
        inv = (
            await s.execute(
                select(Investigation).where(Investigation.incident_id == sc.incident_id)
            )
        ).scalar_one()
    assert inv.scorer_version == result.weights_version == "v1"
    assert inv.duration_ms is not None and inv.duration_ms > 0
    assert inv.inputs == {
        "incident_id": sc.incident_id,
        "lookback_hours": 24,
        "candidate_ids": [f"Commit:{sc.culprit_commit}", "Commit:n0000001"],
        "weights_version": "v1",
    }


def test_no_history_is_zero_not_one(builder: GraphBuilder) -> None:
    """Regression: count(*) over an unmatched OPTIONAL MATCH is 1; the feature must be 0."""
    sc = builder.s1_payment_validation()
    r = rank(builder.store, sc.incident_id)
    assert all(feat(r, c.sha, "history") == 0.0 for c in r.candidates)


def test_endpoint_only_evidence_is_never_confident(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    # the failure has no stack: the error is not linked to any function
    builder.store.graph.query(
        "MATCH (:Error {fingerprint: 'fp-undefined-currency'})-[t:thrown_in]->() DELETE t"
    )
    r = rank(builder.store, sc.incident_id)
    assert r.verdict == "ambiguous"  # the culprit may still lead, but with endpoint evidence only


def test_centrality_uses_the_builtin_pagerank_algorithm(builder: GraphBuilder) -> None:
    """The changed function that many others depend on scores higher than a leaf caller."""
    sc = builder.s1_payment_validation()
    s = builder.store
    hub = builder.function("payments", "src/hub.js", "hub")
    callers = [
        builder.function("payments", f"src/c{i}.js", f"caller{i}", calls=[hub]) for i in range(5)
    ]
    assert callers
    leaf = builder.function("payments", "src/leaf.js", "leaf")
    hub_commit = builder.commit("h0000001", 150, modifies_fn=[hub], message="touch hub")
    leaf_commit = builder.commit("l0000001", 151, modifies_fn=[leaf], message="touch leaf")
    s.upsert_edges(
        [
            Edge("Deployment", "dep-842", "includes", "Commit", hub_commit),
            Edge("Deployment", "dep-842", "includes", "Commit", leaf_commit),
        ]
    )
    r = rank(s, sc.incident_id)
    assert feat(r, hub_commit, "centrality") == 1.0  # normalized to the most central function
    assert 0.0 <= feat(r, leaf_commit, "centrality") < feat(r, hub_commit, "centrality")
    assert "algo.pageRank" in F.FEATURE_QUERIES["centrality"]
