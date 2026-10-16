"""Per-feature evidence contract (#89): every feature returns {value, evidence_ids, explanation}
and every evidence id names a node that exists in the graph."""

import math

import pytest

from causaldeploy.graph.builder import GraphBuilder, Scenario
from causaldeploy.graph.schema import NODE_KEYS
from causaldeploy.graph.store import Edge, GraphStore
from causaldeploy.investigate import features as F
from causaldeploy.investigate.features import FeatureResult
from causaldeploy.investigate.scorer import TAU_SECONDS, rank


def resolves(store: GraphStore, node_id: str) -> bool:
    label, _, key = node_id.partition(":")
    if label not in NODE_KEYS or not key:
        return False
    k = NODE_KEYS[label]
    rows = store.read(f"MATCH (n:{label} {{{k}: $k}}) RETURN count(n)", {"k": key})
    return bool(rows and rows[0][0] == 1)


def feature(
    store: GraphStore, sc: Scenario, key: str, name: str, kind: F.CandidateKind = "commit"
) -> FeatureResult:
    inc = F.load_incident(store, sc.incident_id)
    assert inc is not None
    r = F.feature_results(store, inc, key, TAU_SECONDS, kind)[name]
    assert r.explanation, name
    unresolved = [i for i in r.evidence_ids if not resolves(store, i)]
    assert not unresolved, (name, unresolved)
    return r


def test_temporal_decays_from_deploy_to_onset(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    r = feature(builder.store, sc, sc.culprit_commit, "temporal")
    assert r.value == pytest.approx(math.exp(-60 / TAU_SECONDS), abs=1e-4)
    assert r.evidence_ids == ["Deployment:dep-842", "Incident:inc-1"]
    assert "60s before onset" in r.explanation


def test_path_cites_the_call_path_s2(builder: GraphBuilder) -> None:
    sc = builder.s2_two_functions_one_file()
    r = feature(builder.store, sc, sc.culprit_commit, "path")
    assert r.value == 1.0
    assert f"Function:{sc.culprit_functions[0]}" in r.evidence_ids
    assert "Error:fp-s2" in r.evidence_ids and f"Commit:{sc.culprit_commit}" in r.evidence_ids
    decoy = feature(builder.store, sc, "d3c0y001", "path")
    assert decoy.value == 0.5 and "1 call hop" in decoy.explanation
    assert "Function:payments:src/routes.js:handlePayment" in decoy.evidence_ids


def test_endpoint_cites_handler_and_endpoint_s1(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    r = feature(builder.store, sc, sc.culprit_commit, "endpoint")
    assert r.value == 1.0
    assert "Endpoint:payments:POST /payments" in r.evidence_ids
    assert "Function:payments:src/routes.js:handlePayment" in r.evidence_ids
    assert feature(builder.store, sc, "n0000001", "endpoint").evidence_ids == []


def test_service_follows_service_calls_s4(builder: GraphBuilder) -> None:
    sc = builder.s4_cross_service()
    assert feature(builder.store, sc, sc.culprit_commit, "service").value == 0.0
    builder.store.upsert_edges([Edge("Service", "orders", "calls", "Service", "payments")])
    r = feature(builder.store, sc, sc.culprit_commit, "service")
    assert r.value == 0.5
    assert {"Service:orders", "Service:payments"} <= set(r.evidence_ids)


def test_centrality_cites_the_changed_function_s3(builder: GraphBuilder) -> None:
    sc = builder.s3_five_commit_deployment()
    r = feature(builder.store, sc, sc.culprit_commit, "centrality")
    assert 0.0 < r.value <= 1.0
    assert f"Function:{sc.culprit_functions[0]}" in r.evidence_ids


def test_dependency_cites_the_changed_dependency_s5(builder: GraphBuilder) -> None:
    sc = builder.s5_dependency_bump()
    r = feature(builder.store, sc, sc.culprit_commit, "dependency")
    assert r.value == 1.0
    assert {"Dependency:money-utils@2.0.0", "Error:fp-s5"} <= set(r.evidence_ids)
    assert f"Function:{sc.culprit_functions[0]}" in r.evidence_ids


def test_msg_sim_lists_shared_words_s1(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    assert feature(builder.store, sc, sc.culprit_commit, "msg_sim").value == 0.0
    builder.store.graph.query(
        "MATCH (e:Error {fingerprint: 'fp-undefined-currency'}) "
        "SET e.message = 'payment validation failed'"
    )
    r = feature(builder.store, sc, sc.culprit_commit, "msg_sim")
    assert r.value == pytest.approx(0.4)  # 2 shared of 4 error tokens, /(4 + 1)
    assert r.evidence_ids == [f"Commit:{sc.culprit_commit}", "Error:fp-undefined-currency"]
    assert "payment" in r.explanation and "validation" in r.explanation


def test_history_cites_the_earlier_incident_s1(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    s = builder.store
    old = builder.commit("0ld00001", 10, modifies_fn=sc.culprit_functions)
    s.upsert_nodes("Incident", [{"id": "old-1", "status": "resolved"}])
    s.upsert_edges(
        [
            Edge("Incident", "old-1", "likely_caused_by", "Commit", old),
            Edge("Incident", sc.incident_id, "resembles", "Incident", "old-1"),
        ]
    )
    r = feature(s, sc, sc.culprit_commit, "history")
    assert r.value == 1.0
    assert r.evidence_ids == [f"Function:{sc.culprit_functions[0]}", "Incident:old-1"]


def test_sequence_cites_the_last_deployment_s3(builder: GraphBuilder) -> None:
    sc = builder.s3_five_commit_deployment()
    r = feature(builder.store, sc, sc.culprit_commit, "sequence")
    assert r.value == 1.0 and r.evidence_ids == ["Deployment:dep-3", "Incident:inc-3"]


def test_triggered_cites_the_linked_deployment_s1(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    r = feature(builder.store, sc, sc.culprit_commit, "triggered")
    assert r.value == 1.0 and r.evidence_ids == ["Deployment:dep-842", "Incident:inc-1"]
    other = builder.commit("b0000001", 250, message="window only")
    builder.deployment("dep-later", "prod", [other], 340)
    assert feature(builder.store, sc, "b0000001", "triggered").value == 0.0


def test_blast_counts_changed_functions_and_callers_s4(builder: GraphBuilder) -> None:
    sc = builder.s4_cross_service()
    r = feature(builder.store, sc, sc.culprit_commit, "blast")
    assert r.value == pytest.approx(2 / 12)  # createOrder + its caller postOrder
    assert set(r.evidence_ids) == {
        "Function:orders:src/orders.js:createOrder",
        "Function:gateway:src/routes.js:postOrder",
    }


def test_config_feature_on_a_config_candidate_s6(builder: GraphBuilder) -> None:
    sc = builder.s6_config_change()
    r = feature(builder.store, sc, sc.culprit_commit, "config", "config")
    assert r.value == 1.0
    assert f"ConfigChange:{sc.culprit_commit}" in r.evidence_ids
    assert "PAYMENTS_URL" in r.explanation
    path = feature(builder.store, sc, sc.culprit_commit, "path", "config")
    assert path.value == 0.0 and "does not apply" in path.explanation


@pytest.mark.parametrize("shape", ["s1", "s2", "s3", "s4", "s5", "s6"])
def test_every_evidence_id_resolves_for_every_candidate(builder: GraphBuilder, shape: str) -> None:
    sc = {
        "s1": builder.s1_payment_validation,
        "s2": builder.s2_two_functions_one_file,
        "s3": builder.s3_five_commit_deployment,
        "s4": builder.s4_cross_service,
        "s5": builder.s5_dependency_bump,
        "s6": builder.s6_config_change,
    }[shape]()
    r = rank(builder.store, sc.incident_id)
    assert r.candidates
    for c in r.candidates:
        assert resolves(builder.store, c.node_id), c.node_id
        assert {f.name for f in c.features} == set(F.FEATURE_QUERIES)
        for f in c.features:
            assert f.explanation
            bad = [i for i in f.evidence_ids if not resolves(builder.store, i)]
            assert not bad, (c.sha, f.name, bad)
            if f.value == 0:
                assert f.evidence_ids == [], (c.sha, f.name)
