"""Diff hunk -> function mapping (produces `Commit -[:modifies_fn]-> Function`).

Behaviour (documented for S9, refactors that rename/move):
  * edited lines are intersected with function ranges at the post-change (added lines)
    and pre-change (removed lines) revision;
  * a renamed *file* keeps function identity by qualified name inside the new path;
  * a function that disappears from one file and reappears with an identical body in
    another changed file is reported as a move (`moved_from`);
  * a function removed with no match is reported against its old id;
  * hunks that hit no function, and moves with several candidates, fall back to the file
    (`fallback` reason on the edge) instead of guessing.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from causaldeploy.contracts import FileChange, Hunk
from causaldeploy.ingest.analyzer.base import FileAnalysis, FunctionDef
from causaldeploy.ingest.analyzer.javascript import JavaScriptAnalyzer
from causaldeploy.ingest.code_graph import fn_id

_analyzer = JavaScriptAnalyzer()


@dataclass
class FnChange:
    function_id: str
    added: int = 0
    removed: int = 0
    moved_from: str | None = None


@dataclass(frozen=True)
class FileFallback:
    path: str
    reason: str  # outside-function | ambiguous-move | unparsed
    added: int
    removed: int


@dataclass
class CommitMapping:
    functions: list[FnChange]
    fallbacks: list[FileFallback]


def _overlap(lo: int, hi: int, f: FunctionDef) -> int:
    return max(0, min(hi, f.end) - max(lo, f.start) + 1)


def _analyze(path: str, text: str | None) -> FileAnalysis | None:
    if text is None or not path.endswith(_analyzer.extensions):
        return None
    return _analyzer.analyze_file(path, text)


def map_commit(
    files: list[FileChange],
    pre: dict[str, str | None],
    post: dict[str, str | None],
    service_of: Callable[[str], str],
) -> CommitMapping:
    """`pre`/`post` map file path -> source (None if absent) at parent / commit."""
    changes: dict[str, FnChange] = {}
    fallbacks: list[FileFallback] = []
    gone: dict[str, tuple[FunctionDef, str]] = {}  # pre id -> (def, old path)
    appeared: list[tuple[FunctionDef, str]] = []  # post defs with no pre counterpart

    def change(fid: str) -> FnChange:
        return changes.setdefault(fid, FnChange(fid))

    for fc in files:
        old_path = fc.old_path or fc.path
        a_post = _analyze(fc.path, post.get(fc.path))
        a_pre = _analyze(old_path, pre.get(old_path))
        if a_post is None and a_pre is None:
            if fc.hunks:
                fallbacks.append(
                    FileFallback(
                        fc.path,
                        "unparsed",
                        sum(h.added for h in fc.hunks),
                        sum(h.removed for h in fc.hunks),
                    )
                )
            continue
        post_svc, pre_svc = service_of(fc.path), service_of(old_path)
        post_defs = {f.qname: f for f in a_post.functions} if a_post else {}
        pre_defs = {f.qname: f for f in a_pre.functions} if a_pre else {}

        uncovered: dict[int, Hunk] = {i: h for i, h in enumerate(fc.hunks)}
        for i, h in enumerate(fc.hunks):
            if h.added:
                for f in post_defs.values():
                    n = _overlap(h.start, h.end, f)
                    if n:
                        change(fn_id(post_svc, fc.path, f.qname)).added += n
                        uncovered.pop(i, None)
            if h.removed:
                for f in pre_defs.values():
                    n = _overlap(h.old_start, h.old_end, f)
                    if n:
                        target = post_defs.get(f.qname)
                        fid = (
                            fn_id(post_svc, fc.path, f.qname)
                            if target
                            else fn_id(pre_svc, old_path, f.qname)
                        )
                        change(fid).removed += n
                        uncovered.pop(i, None)
        for h in uncovered.values():
            fallbacks.append(FileFallback(fc.path, "outside-function", h.added, h.removed))

        for q, f in pre_defs.items():
            if q not in post_defs:
                gone[fn_id(pre_svc, old_path, q)] = (f, old_path)
        for q, f in post_defs.items():
            if q not in pre_defs:
                appeared.append((f, fc.path))

    # moves: identical body hash disappears here and appears there
    by_hash: dict[str, list[str]] = defaultdict(list)
    for pid, (f, _) in gone.items():
        by_hash[f.body_hash].append(pid)
    for f, path in appeared:
        fid = fn_id(service_of(path), path, f.qname)
        cands = by_hash.get(f.body_hash, [])
        if len(cands) == 1:
            change(fid).moved_from = cands[0]
            changes.pop(cands[0], None)
        elif len(cands) > 1:
            fallbacks.append(FileFallback(path, "ambiguous-move", f.end - f.start + 1, 0))
            for pid in cands:
                changes.pop(pid, None)
    return CommitMapping(
        functions=sorted(changes.values(), key=lambda c: c.function_id), fallbacks=fallbacks
    )
