"""Local git access: history, diffs with hunks (`-M` rename detection) and blobs."""

import re
import subprocess
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from causaldeploy.contracts import CommitEvent, FileChange, Hunk

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FS, _RS = "\x1f", "\x1e"


def run_git(repo_dir: Path, *args: str, extra: list[str] | None = None) -> str:
    cmd = ["git", "-c", "core.quotepath=off", *(extra or []), "-C", str(repo_dir), *args]
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def parse_diff(text: str) -> list[FileChange]:
    """Parse `git diff -U0 -M` output into file changes with new/old line ranges."""
    files: list[FileChange] = []
    cur: dict[str, object] | None = None

    def flush() -> None:
        if cur is not None:
            files.append(FileChange(**cur))

    for line in text.splitlines():
        if line.startswith("diff --git "):
            flush()
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            assert m
            cur = {"path": m.group(2), "status": "modified", "old_path": None, "hunks": []}
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur["status"] = "added"
        elif line.startswith("deleted file mode"):
            cur["status"] = "removed"
        elif line.startswith("rename from "):
            cur["status"], cur["old_path"] = "renamed", line[len("rename from ") :]
        elif (m := _HUNK.match(line)) is not None:
            old_start, old_n = int(m[1]), int(m[2]) if m[2] is not None else 1
            new_start, new_n = int(m[3]), int(m[4]) if m[4] is not None else 1
            hunks = cur["hunks"]
            assert isinstance(hunks, list)
            hunks.append(
                Hunk(
                    start=new_start,
                    end=max(0, new_start + new_n - 1),
                    old_start=old_start,
                    old_end=max(0, old_start + old_n - 1),
                    added=new_n,
                    removed=old_n,
                )
            )
    flush()
    return files


def iter_commits(repo_dir: Path, rev: str = "HEAD") -> Iterator[CommitEvent]:
    """Commits oldest first, each with changed files and hunks vs its first parent."""
    fmt = _FS.join(["%H", "%P", "%an", "%aI", "%s"]) + _RS
    log = run_git(repo_dir, "log", "--reverse", "--topo-order", f"--format={fmt}", rev)
    for rec in filter(None, (r.strip("\n") for r in log.split(_RS))):
        sha, parents, author, ts, subject = rec.split(_FS)
        plist = parents.split()
        diff = (
            run_git(
                repo_dir,
                "diff",
                "-U0",
                "-M",
                "--no-color",
                *([plist[0], sha] if plist else ["--root", sha]),
            )
            if plist
            else run_git(repo_dir, "show", "-U0", "-M", "--format=", "--no-color", sha)
        )
        yield CommitEvent(
            repo=repo_dir.name,
            sha=sha,
            parents=plist,
            author=author,
            ts=datetime.fromisoformat(ts),
            message=subject,
            files=parse_diff(diff),
        )


def read_blob(repo_dir: Path, sha: str, path: str) -> str | None:
    try:
        return run_git(repo_dir, "show", f"{sha}:{path}")
    except subprocess.CalledProcessError:
        return None


def list_tree(repo_dir: Path, sha: str) -> list[str]:
    return run_git(repo_dir, "ls-tree", "-r", "--name-only", sha).splitlines()
