import pytest

from causaldeploy.graph.schema import SCHEMA_VERSION
from causaldeploy.graph.store import Edge, GraphStore


def test_schema_applied_once_and_versioned(store: GraphStore) -> None:
    assert store.schema_version() == 0
    store.ensure_schema()
    assert store.schema_version() == SCHEMA_VERSION
    store.ensure_schema()  # idempotent
    assert store.schema_version() == SCHEMA_VERSION


def test_reingest_is_a_noop(store: GraphStore) -> None:
    store.ensure_schema()

    def ingest() -> None:
        store.upsert_nodes("Commit", [{"sha": "abc", "ts": 1}, {"sha": "def", "ts": 2}])
        store.upsert_nodes("Developer", [{"id": "alice"}])
        store.upsert_edges([Edge("Commit", "abc", "authored_by", "Developer", "alice", {"x": 1})])

    ingest()
    first = store.counts()
    ingest()
    assert store.counts() == first == (4, 1)  # 2 commits + developer + SchemaMeta


def test_upsert_updates_properties(store: GraphStore) -> None:
    store.ensure_schema()
    store.upsert_nodes("Incident", [{"id": "i1", "status": "open"}])
    store.upsert_nodes("Incident", [{"id": "i1", "status": "resolved"}])
    assert store.read("MATCH (i:Incident) RETURN i.status") == [["resolved"]]


def test_edge_outside_schema_is_rejected(store: GraphStore) -> None:
    with pytest.raises(ValueError, match="not in schema"):
        store.upsert_edges([Edge("Developer", "a", "modifies_fn", "Function", "f")])


def test_unique_constraint_enforced(store: GraphStore) -> None:
    store.ensure_schema()
    store.graph.query("CREATE (:Commit {sha: 'x'})")
    with pytest.raises(Exception, match="unique constraint"):
        store.graph.query("CREATE (:Commit {sha: 'x'})")


def test_graphs_are_isolated_per_repo() -> None:
    a, b = GraphStore("iso/a"), GraphStore("iso/b")
    try:
        a.ensure_schema()
        a.upsert_nodes("Commit", [{"sha": "abc"}])
        assert b.counts() == (0, 0)
        assert a.counts()[0] == 2
    finally:
        a.drop()
        b.drop()


def test_schema_upgrades_in_place_from_the_previous_version(store: GraphStore) -> None:
    store.ensure_schema()
    store.graph.query("MATCH (m:SchemaMeta) SET m.version = 1")  # a graph created by v1 code
    store.ensure_schema()  # existing indexes/constraints must not raise
    assert store.schema_version() == SCHEMA_VERSION
    store.upsert_nodes("Investigation", [{"id": "inv-1"}])
    with pytest.raises(Exception, match="unique constraint"):
        store.graph.query("CREATE (:Investigation {id: 'inv-1'})")
