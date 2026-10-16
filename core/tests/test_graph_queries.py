import time

from causaldeploy.graph import queries as q
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.store import Edge


def test_q1_incident_to_deployment_commits(builder: GraphBuilder) -> None:
    sc = builder.s3_five_commit_deployment()
    [d] = q.incident_deployment_commits(builder.store, sc.incident_id)
    assert d.deployment_id == "dep-3" and d.env == "prod"
    assert len(d.commits) == 5 and d.commits[0] == "a0000000" and d.commits[-1] == sc.culprit_commit


def test_q2_same_function_path_s1(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    paths = q.changed_to_thrown_paths(builder.store, sc.culprit_commit, "fp-undefined-currency")
    assert [(p.distance, p.direction) for p in paths] == [(0, "same"), (1, "called_by")]
    assert paths[0].changed_function == sc.culprit_functions[0]
    assert q.changed_to_thrown_paths(builder.store, "n0000001", "fp-undefined-currency") == []


def test_q2_s2_only_failing_function_is_on_path(builder: GraphBuilder) -> None:
    sc = builder.s2_two_functions_one_file()
    on_path = q.changed_to_thrown_paths(builder.store, sc.culprit_commit, "fp-s2")
    decoy = q.changed_to_thrown_paths(builder.store, "d3c0y001", "fp-s2")
    assert on_path[0].distance == 0
    # decoy changed formatReceipt, only reachable through the handler frame: strictly farther
    assert decoy[0].distance == 1 and decoy[0].direction == "called_by"


def test_q2_s4_cross_service_path(builder: GraphBuilder) -> None:
    sc = builder.s4_cross_service()
    [p] = q.changed_to_thrown_paths(builder.store, sc.culprit_commit, "fp-s4")
    assert p.direction == "calls" and p.distance == 1
    assert p.path == ["orders:src/orders.js:createOrder", "payments:src/validate.js:validateCharge"]


def test_q3_blast_radius(builder: GraphBuilder) -> None:
    builder.s4_cross_service()
    r = q.blast_radius(builder.store, "payments:src/validate.js:validateCharge")
    assert r.affected_services == ["gateway", "orders", "payments"]
    assert r.affected_endpoints == ["gateway:POST /orders", "payments:POST /charge"]
    assert "orders:src/orders.js:createOrder" in r.affected_functions
    leaf = q.blast_radius(builder.store, "gateway:src/routes.js:postOrder")
    assert leaf.affected_functions == [] and leaf.affected_services == ["gateway"]


def test_q4_dependency_impact_s5(builder: GraphBuilder) -> None:
    sc = builder.s5_dependency_bump()
    r = q.dependency_impact(builder.store, "money-utils@2.0.0")
    assert r.functions == ["orders:src/total.js:orderTotal", "payments:src/money.js:toCents"]
    assert r.services == sorted(sc.affected_services)
    assert q.dependency_impact(builder.store, "nope@1").functions == []


def test_q5_similar_resolved_incident_fix(builder: GraphBuilder) -> None:
    builder.s1_payment_validation()
    s = builder.store
    s.upsert_nodes(
        "Incident", [{"id": "old-1", "status": "resolved"}, {"id": "old-2", "status": "open"}]
    )
    s.upsert_nodes("Fix", [{"id": "fix-1", "kind": "revert", "verified": True}])
    s.upsert_edges(
        [
            Edge(
                "Incident",
                "inc-1",
                "resembles",
                "Incident",
                "old-1",
                {"similarity": 0.8, "basis": ["thrown_in"]},
            ),
            Edge(
                "Incident",
                "inc-1",
                "resembles",
                "Incident",
                "old-2",
                {"similarity": 0.9, "basis": []},
            ),
            Edge("Incident", "old-1", "fixed_by", "Fix", "fix-1"),
        ]
    )
    [r] = q.similar_resolved_incidents(s, "inc-1")  # unresolved neighbours excluded
    assert (r.incident_id, r.fix_id, r.fix_kind, r.basis) == (
        "old-1",
        "fix-1",
        "revert",
        ["thrown_in"],
    )


def test_q6_function_incident_history(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    builder.candidates("inc-1", [(sc.culprit_commit, 0.9), ("n0000001", 0.1)])
    hist = q.function_incident_history(builder.store, sc.culprit_functions[0])
    assert [(h.incident_id, h.via) for h in hist] == [("inc-1", "blamed"), ("inc-1", "thrown")]


def test_queries_are_fast_on_a_realistic_graph(builder: GraphBuilder) -> None:
    """ShopLite-scale graph (~600 functions, ~50 commits): each query well under 200 ms."""
    sc = builder.s1_payment_validation()
    s = builder.store
    fns = [f"svc{i % 3}:src/f{i // 10}.js:fn{i}" for i in range(600)]
    s.upsert_nodes("Function", [{"id": f} for f in fns])
    s.upsert_edges(
        [Edge("Function", fns[i], "calls", "Function", fns[(i * 7 + 1) % 600]) for i in range(600)]
    )
    s.upsert_edges(
        [Edge("Commit", sc.culprit_commit, "modifies_fn", "Function", f) for f in fns[:20]]
    )
    calls = {
        "q1": lambda: q.incident_deployment_commits(s, "inc-1"),
        "q2": lambda: q.changed_to_thrown_paths(s, sc.culprit_commit, "fp-undefined-currency"),
        "q3": lambda: q.blast_radius(s, fns[5]),
        "q4": lambda: q.dependency_impact(s, "money-utils@2.0.0"),
        "q5": lambda: q.similar_resolved_incidents(s, "inc-1"),
        "q6": lambda: q.function_incident_history(s, sc.culprit_functions[0]),
    }
    for name, fn in calls.items():
        t = time.perf_counter()
        fn()
        assert time.perf_counter() - t < 0.2, name


def test_q7_impact_report_s4_originating_vs_symptomatic(builder: GraphBuilder) -> None:
    sc = builder.s4_cross_service()
    r = q.impact_report(builder.store, sc.incident_id, sc.culprit_commit)
    assert r.originating == "orders" and r.symptomatic == "payments"
    svc = {e.id: e for e in r.services}
    assert svc["payments"].status == "observed" and "callee" in svc["payments"].via
    assert svc["orders"].status == "potential" and "changed" in svc["orders"].via
    assert svc["gateway"].status == "potential" and "caller" in svc["gateway"].via
    eps = {e.id: e.status for e in r.endpoints}
    assert eps == {"payments:POST /charge": "observed", "gateway:POST /orders": "potential"}
    assert r.services[0].status == "observed"  # observed first


def test_q7_impact_report_shared_dependency_fan_out_s5(builder: GraphBuilder) -> None:
    sc = builder.s5_dependency_bump()
    r = q.impact_report(builder.store, sc.incident_id, sc.culprit_commit)
    [dep] = r.shared_dependencies
    assert dep.dependency_id == "money-utils@2.0.0" and dep.services == ["orders", "payments"]
    svc = {e.id: e for e in r.services}
    assert svc["payments"].status == "observed"
    assert svc["orders"].status == "potential" and svc["orders"].via == ["shared_dependency"]
    assert r.symptomatic == "payments" and r.originating is None  # a manifest-only commit
