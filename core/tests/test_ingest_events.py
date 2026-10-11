import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from causaldeploy.contracts import CommitEvent, DeploymentEvent, parse_event
from causaldeploy.graph.store import GraphStore
from causaldeploy.ingest.events import apply_deployment, apply_event

T0 = datetime(2026, 10, 9, 10, tzinfo=UTC)


def commit(sha: str, parent: str | None, i: int) -> CommitEvent:
    return CommitEvent(
        repo="a/b",
        sha=sha,
        parents=[parent] if parent else [],
        author="al",
        ts=T0 + timedelta(minutes=i),
        message=sha,
    )


def dep(
    i: str, sha_to: str, minutes: int, status: str = "success", sha_from: str | None = None
) -> DeploymentEvent:
    return DeploymentEvent(
        repo="a/b",
        id=i,
        env="prod",
        sha_from=sha_from,
        sha_to=sha_to,
        started_at=T0 + timedelta(minutes=minutes),
        status=status,
        build_id=f"b-{i}",
    )  # type: ignore[arg-type]


def history(store: GraphStore) -> None:
    prev = None
    for i, sha in enumerate(["c1111111", "c2222222", "c3333333", "c4444444", "c5555555"]):
        apply_event(store, commit(sha, prev, i))
        prev = sha


def test_deployment_range_from_previous_successful(store: GraphStore) -> None:
    history(store)
    assert apply_deployment(store, dep("d1", "c2222222", 10)) == ["c1111111", "c2222222"]
    assert apply_deployment(store, dep("d2", "c5555555", 20)) == [
        "c3333333",
        "c4444444",
        "c5555555",
    ]
    rows = store.read("MATCH (d:Deployment {id: 'd2'})-[:includes]->(c) RETURN count(c)")
    assert rows == [[3]]


def test_rollback_is_recorded_as_deployment(store: GraphStore) -> None:
    history(store)
    apply_deployment(store, dep("d1", "c4444444", 10))
    assert apply_deployment(store, dep("d2", "c2222222", 20)) == []
    assert store.read("MATCH (d:Deployment {id: 'd2'}) RETURN d.status, d.sha_from") == [
        ["rollback", "c4444444"]
    ]


def test_failed_deployments_do_not_anchor_the_next_range(store: GraphStore) -> None:
    history(store)
    apply_deployment(store, dep("d1", "c2222222", 10))
    apply_deployment(store, dep("d2", "c4444444", 20, status="failure"))
    assert apply_deployment(store, dep("d3", "c5555555", 30)) == [
        "c3333333",
        "c4444444",
        "c5555555",
    ]


def test_events_replay_is_noop_and_fixtures_apply(store: GraphStore) -> None:
    fx = Path(__file__).parent / "fixtures" / "events"
    events = [
        parse_event(json.loads((fx / f).read_text()))
        for f in ("commit.json", "build.json", "deployment.json")
    ]
    for e in events:
        apply_event(store, e)
    first = store.counts()
    for e in events:
        apply_event(store, e)
    assert store.counts() == first
    assert store.read(
        "MATCH (c:Commit {sha: $s})-[:modifies]->(f:File) RETURN f.path",
        {"s": "8f31c2a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7"},
    ) == [["payments/src/payment/validation.js"]]


def test_backfill_completes_a_deployment_that_arrived_first(store: GraphStore, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import subprocess

    from causaldeploy.ingest.backfill import backfill_repo

    d = tmp_path / "r"
    d.mkdir()

    def git(*a: str) -> str:
        return subprocess.run(
            ["git", "-C", str(d), "-c", "user.name=a", "-c", "user.email=a@b.c", *a],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    (d / "a.js").write_text("function a() {\n  return 1;\n}\n")
    git("add", "-A")
    git("commit", "-qm", "one")
    (d / "a.js").write_text("function a() {\n  return 2;\n}\n")
    git("commit", "-qam", "two")
    head = git("rev-parse", "HEAD")

    # the deployment announcement beats the import: nothing to include yet
    assert apply_deployment(store, dep("early", head, 10)) == []
    backfill_repo(store, d, "acme/r")
    rows = store.read(
        "MATCH (:Deployment {id: 'early'})-[:includes]->(c:Commit) RETURN c.message ORDER BY c.ts"
    )
    assert sorted(r[0] for r in rows) == ["one", "two"]
