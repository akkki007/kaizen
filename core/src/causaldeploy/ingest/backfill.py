"""Repository backfill: history -> graph, code analysis at HEAD. Resumable and idempotent."""

import posixpath
from collections.abc import Callable
from pathlib import Path
from typing import Any

from causaldeploy.graph.store import Edge, GraphStore
from causaldeploy.ingest.code_graph import build_code_graph, make_service_of, write_code_graph
from causaldeploy.ingest.events import apply_commit
from causaldeploy.ingest.git import iter_commits, list_tree, read_blob
from causaldeploy.ingest.manifests import diff_manifests, parse_manifest
from causaldeploy.ingest.mapping import map_commit

ANALYZABLE = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
_SKIP = ("node_modules/", "dist/", "build/")


def backfill_repo(
    store: GraphStore,
    repo_dir: Path,
    repo_name: str,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> dict[str, int]:
    """Import every commit (with modifies_fn / changes_dep edges) and the HEAD code graph."""
    store.ensure_schema()
    short = repo_name.rsplit("/", 1)[-1]  # service name for files outside any sub-package
    stats = {"commits": 0, "function_edges": 0}
    for ev in iter_commits(repo_dir):
        tree = list_tree(repo_dir, ev.sha)
        pkg_dirs = {posixpath.dirname(p) for p in tree if posixpath.basename(p) == "package.json"}
        service_of = make_service_of(pkg_dirs, short)
        apply_commit(store, ev.model_copy(update={"repo": repo_name}), service_of)

        parent = ev.parents[0] if ev.parents else None
        code = [
            f
            for f in ev.files
            if f.path.endswith(ANALYZABLE) and not any(s in f.path for s in _SKIP)
        ]
        pre = {
            (f.old_path or f.path): read_blob(repo_dir, parent, f.old_path or f.path)
            if parent
            else None
            for f in code
        }
        post = {
            f.path: read_blob(repo_dir, ev.sha, f.path) if f.status != "removed" else None
            for f in code
        }
        mapping = map_commit(code, pre, post, service_of)
        edges: list[Edge] = []
        for c in mapping.functions:
            props: dict[str, Any] = {"added": c.added, "removed": c.removed}
            if c.moved_from:
                props["moved_from"] = c.moved_from
            edges.append(Edge("Commit", ev.sha, "modifies_fn", "Function", c.function_id, props))
        for fb in mapping.fallbacks:
            fid = f"{service_of(fb.path)}:{fb.path}"
            edges.append(
                Edge(
                    "Commit",
                    ev.sha,
                    "modifies",
                    "File",
                    fid,
                    {"fallback": fb.reason, "added": fb.added, "removed": fb.removed},
                )
            )
        for f in ev.files:
            if posixpath.basename(f.path) != "package.json":
                continue
            before = parse_manifest(
                f.path, (read_blob(repo_dir, parent, f.old_path or f.path) or "") if parent else ""
            )
            after = parse_manifest(f.path, read_blob(repo_dir, ev.sha, f.path) or "")
            for name, _old, new in diff_manifests(before, after):
                if new:
                    store.upsert_nodes(
                        "Dependency", [{"id": f"{name}@{new}", "name": name, "version": new}]
                    )
                    edges.append(
                        Edge("Commit", ev.sha, "changes_dep", "Dependency", f"{name}@{new}")
                    )
        store.upsert_edges(edges)
        stats["commits"] += 1
        stats["function_edges"] += len(mapping.functions)
        if progress:
            progress(stats)

    head = run_head(repo_dir)
    files = {
        p: t
        for p in list_tree(repo_dir, head)
        if (p.endswith(ANALYZABLE) or posixpath.basename(p) == "package.json")
        and (t := read_blob(repo_dir, head, p)) is not None
    }
    write_code_graph(store, build_code_graph(files, short), head)
    return stats


def run_head(repo_dir: Path) -> str:
    from causaldeploy.ingest.git import run_git

    return run_git(repo_dir, "rev-parse", "HEAD").strip()
