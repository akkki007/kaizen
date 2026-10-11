import subprocess
from pathlib import Path

import pytest

from causaldeploy.graph.store import GraphStore
from causaldeploy.ingest.backfill import backfill_repo
from causaldeploy.ingest.git import parse_diff

V = "shop:src/validation.js:validatePayment"


def git(d: Path, *a: str) -> None:
    subprocess.run(
        ["git", "-C", str(d), "-c", "user.name=al", "-c", "user.email=a@b.c", *a],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "shop"
    (d / "src").mkdir(parents=True)
    git(d, "init", "-q", "-b", "main")
    (d / "package.json").write_text(
        '{"name": "payments", "dependencies": {"money-utils": "^1.9.0"}}'
    )
    (d / "src/validation.js").write_text(
        "function validatePayment(p) {\n  return p.currency;\n}\n\nfunction fmt(p) {\n  return 1;\n}\n"
        "module.exports = { validatePayment, fmt };\n"
    )
    (d / "README.md").write_text("hi\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    (d / "README.md").write_text("hi there\n")
    git(d, "commit", "-qam", "docs")
    (d / "src/validation.js").write_text(
        (d / "src/validation.js")
        .read_text()
        .replace("return p.currency;", "return p.currency.code;")
    )
    git(d, "commit", "-qam", "refactor validation")
    (d / "package.json").write_text(
        '{"name": "payments", "dependencies": {"money-utils": "^2.0.0"}}'
    )
    git(d, "commit", "-qam", "bump money-utils")
    return d


def test_parse_diff_rename_and_hunks() -> None:
    diff = (
        "diff --git a/a.js b/b.js\nsimilarity index 90%\nrename from a.js\nrename to b.js\n"
        "--- a/a.js\n+++ b/b.js\n@@ -3,2 +3,3 @@ x\n@@ -10 +11,0 @@\n"
        "diff --git a/n.js b/n.js\nnew file mode 100644\n--- /dev/null\n+++ b/n.js\n@@ -0,0 +1,4 @@\n"
    )
    a, n = parse_diff(diff)
    assert (a.status, a.old_path, a.path) == ("renamed", "a.js", "b.js")
    assert [(h.start, h.end, h.old_start, h.old_end) for h in a.hunks] == [
        (3, 5, 3, 4),
        (11, 10, 10, 10),
    ]
    assert n.status == "added" and (n.hunks[0].start, n.hunks[0].end) == (1, 4)


def test_backfill_builds_graph_and_is_idempotent(store: GraphStore, repo: Path) -> None:
    stats = backfill_repo(store, repo, "acme/shop")
    assert stats["commits"] == 4
    first = store.counts()
    backfill_repo(store, repo, "acme/shop")
    assert store.counts() == first

    blamed = store.read(
        "MATCH (c:Commit)-[:modifies_fn]->(f:Function) WHERE c.message = 'refactor validation' "
        "RETURN f.id"
    )
    assert blamed == [[V]]  # only the edited function, not fmt
    docs = store.read("MATCH (c:Commit {message: 'docs'})-[:modifies_fn]->() RETURN count(*)")
    assert docs == [[0]]
    dep = store.read(
        "MATCH (:Commit {message: 'bump money-utils'})-[:changes_dep]->(d) RETURN d.id"
    )
    assert dep == [["money-utils@2.0.0"]]
    assert store.read("MATCH (:Commit)-[p:parent]->(:Commit) RETURN count(p)") == [[3]]


def test_workspace_version_bump_is_a_dependency_change(store: GraphStore, tmp_path: Path) -> None:
    d = tmp_path / "ws"
    (d / "lib").mkdir(parents=True)
    (d / "app").mkdir()
    git(d, "init", "-q", "-b", "main")
    (d / "lib/package.json").write_text('{"name": "util", "version": "1.0.0"}')
    (d / "lib/index.js").write_text("function f() {\n  return 1;\n}\nmodule.exports = { f };\n")
    (d / "app/package.json").write_text('{"name": "app", "dependencies": {"util": "^1.0.0"}}')
    (d / "app/main.js").write_text(
        'const { f } = require("util");\nfunction run() {\n  return f();\n}\n'
    )
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    (d / "lib/package.json").write_text('{"name": "util", "version": "1.1.0"}')
    git(d, "commit", "-qam", "release util 1.1.0")
    backfill_repo(store, d, "acme/ws")
    assert store.read(
        "MATCH (:Commit {message: 'release util 1.1.0'})-[:changes_dep]->(d) RETURN d.id"
    ) == [["util@1.1.0"]]
    # functions and services depend on the version that is actually in the tree
    assert (
        store.read(
            "MATCH (f:Function)-[:depends_on]->(d:Dependency {id: 'util@1.1.0'}) RETURN f.id"
        )
        != []
    )


def test_merge_commits_do_not_duplicate_change_attribution(
    store: GraphStore, tmp_path: Path
) -> None:
    d = tmp_path / "m"
    (d / "src").mkdir(parents=True)
    git(d, "init", "-q", "-b", "main")
    (d / "package.json").write_text('{"name": "m"}')
    (d / "src/a.js").write_text("function a() {\n  return 1;\n}\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "init")
    git(d, "checkout", "-qb", "feature")
    (d / "src/a.js").write_text("function a() {\n  return 2;\n}\n")
    git(d, "commit", "-qam", "change a")
    git(d, "checkout", "-q", "main")
    git(d, "merge", "--no-ff", "-qm", "merge feature", "feature")
    backfill_repo(store, d, "acme/m")
    rows = store.read("MATCH (c:Commit)-[:modifies_fn]->(f) RETURN c.message")
    assert rows == [["change a"]]
