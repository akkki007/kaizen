from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.graph.store import Edge
from causaldeploy.guardian.review import MARKER, assess, render_comment

PATCH = "@@ -10,2 +10,3 @@ def\n-old\n+new\n+newer\n"


def test_pr_touching_a_function_reports_blast_radius_and_history(builder: GraphBuilder) -> None:
    builder.s1_payment_validation()
    a = assess(
        builder.store,
        [{"filename": "src/payment/validation.js", "patch": PATCH}],
    )
    assert [c.id for c in a.changed] == ["payments:src/payment/validation.js:validatePayment"]
    assert a.endpoints == ["payments:POST /payments"]
    assert a.risk == "medium"
    text = render_comment(a)
    assert MARKER in text and "validatePayment" in text and "POST /payments" in text


def test_unknown_files_are_reported_not_guessed(builder: GraphBuilder) -> None:
    builder.s1_payment_validation()
    a = assess(builder.store, [{"filename": "README.md", "patch": PATCH}])
    assert a.changed == [] and a.unmapped_files == ["README.md"] and a.risk == "unknown"
    assert "does not guess" in render_comment(a)


def test_function_blamed_in_a_past_incident_is_high_risk(builder: GraphBuilder) -> None:
    builder.s1_payment_validation()
    builder.store.upsert_edges(
        [Edge("Incident", "inc-1", "likely_caused_by", "Commit", "c0ffee01")]
    )
    a = assess(builder.store, [{"filename": "src/payment/validation.js", "patch": PATCH}])
    assert a.risk == "high"
    assert a.changed[0].incidents[0]["via"] == "blamed"
