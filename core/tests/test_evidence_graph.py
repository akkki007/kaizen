from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.evidence import evidence_graph


def test_unknown_incident(builder: GraphBuilder) -> None:
    assert evidence_graph(builder.store, "nope") is None


def test_s1_evidence_graph_has_highlighted_path(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    builder.candidates(sc.incident_id, [(sc.culprit_commit, 0.9), ("n0000001", 0.1)])
    g = evidence_graph(builder.store, sc.incident_id)
    assert g is not None
    ids = g.citable_ids()
    for expected in (
        "Incident:inc-1",
        "Deployment:dep-842",
        "Commit:c0ffee01",
        "Error:fp-undefined-currency",
        "Function:payments:src/payment/validation.js:validatePayment",
        "Commit:c0ffee01|modifies_fn|Function:payments:src/payment/validation.js:validatePayment",
    ):
        assert expected in ids, expected
    hl = {n.id for n in g.nodes if n.highlight}
    assert "Commit:c0ffee01" in hl and "Commit:n0000001" not in hl
    ranked = [e for e in g.edges if e.rel == "candidate"]
    assert {e.props["rank"] for e in ranked} == {1, 2}
    assert not any(n.label == "SchemaMeta" for n in g.nodes)
    # every edge endpoint is a node in the payload
    assert all(
        e.source in {n.id for n in g.nodes} and e.target in {n.id for n in g.nodes} for e in g.edges
    )


def test_citation_resolution_and_expansion(builder: GraphBuilder) -> None:
    sc = builder.s4_cross_service()
    builder.candidates(sc.incident_id, [(sc.culprit_commit, 0.8)])
    g = evidence_graph(builder.store, sc.incident_id)
    assert g is not None
    assert g.unresolved(["Commit:c0ffee04", "Commit:ghost"]) == ["Commit:ghost"]
    target = "Function:payments:src/validate.js:validateCharge"
    assert "Function:payments:src/routes.js:handleCharge" not in g.citable_ids()
    expanded = evidence_graph(builder.store, sc.incident_id, expand=target)
    assert expanded is not None
    assert "Function:payments:src/routes.js:handleCharge" in expanded.citable_ids()
