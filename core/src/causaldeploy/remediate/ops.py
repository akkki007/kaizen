"""Everything that touches GitHub or a git working copy, behind one interface.

Production uses `GitHubOps` (installation token, git over HTTPS, REST); tests inject a fake with
a local repository. Nothing here ever pushes to the default branch: revert branches only.
"""

import asyncio
import base64
import io
import json
import subprocess
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from causaldeploy.config import get_settings
from causaldeploy.github.client import GitHubClient


class RemediationOps(Protocol):
    def clone(self, repo: str, installation_id: int) -> Any: ...  # context manager -> Path
    def push(self, clone: Path, repo: str, installation_id: int, branch: str) -> None: ...
    async def open_pr(
        self, repo: str, installation_id: int, head: str, base: str, title: str, body: str
    ) -> tuple[int, str]: ...
    async def dispatch_verify(self, repo: str, installation_id: int, branch: str) -> None: ...
    async def fetch_report(
        self, repo: str, installation_id: int, run_id: int
    ) -> dict[str, Any] | None: ...


def _git(cwd: Path, *args: str, token: str | None = None) -> str:
    cmd = ["git", "-C", str(cwd)]
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd = ["git", "-c", f"http.extraheader=Authorization: basic {basic}", "-C", str(cwd)]
    try:
        return subprocess.run(
            [*cmd, *args], check=True, capture_output=True, text=True
        ).stdout.strip()
    except subprocess.CalledProcessError as e:
        # never include the command (it may carry the token); keep git's own message
        raise RuntimeError(f"git {args[0]} failed: {(e.stderr or '').strip()[:300]}") from None


class RevertConflict(RuntimeError):
    """The commit cannot be reverted cleanly; a human (or a patch) has to resolve it."""


def make_revert_branch(clone: Path, base: str, sha: str, branch: str) -> str:
    """Create `branch` from `base` with `sha` reverted. Returns the new head sha."""
    _git(clone, "config", "user.name", "Kaizen")
    _git(clone, "config", "user.email", "kaizen@users.noreply.github.com")
    _git(clone, "checkout", "-q", "-b", branch, base)
    parents = _git(clone, "rev-list", "--parents", "-n", "1", sha).split()[1:]
    args = ["revert", "--no-edit"] + (["-m", "1"] if len(parents) > 1 else []) + [sha]
    try:
        _git(clone, *args)
    except RuntimeError as e:
        subprocess.run(["git", "-C", str(clone), "revert", "--abort"], capture_output=True)
        raise RevertConflict(str(e)) from None
    return _git(clone, "rev-parse", "HEAD")


class GitHubOps:
    def __init__(self) -> None:
        self._client = GitHubClient()

    @contextmanager
    def clone(self, repo: str, installation_id: int) -> Iterator[Path]:
        token = asyncio.run(self._token(installation_id))
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / repo.rsplit("/", 1)[-1]
            _git(
                Path(tmp),
                "clone",
                "--quiet",
                f"{get_settings().github_web_url}/{repo}.git",
                str(dest),
                token=token,
            )
            yield dest

    async def _token(self, installation_id: int) -> str:
        return await self._client.installation_token(installation_id)

    def push(self, clone: Path, repo: str, installation_id: int, branch: str) -> None:
        if branch in ("main", "master"):  # belt and braces: Kaizen never writes default branches
            raise PermissionError("refusing to push to a default branch")
        token = asyncio.run(self._token(installation_id))
        _git(clone, "push", "--quiet", "origin", f"{branch}:{branch}", token=token)

    async def open_pr(
        self, repo: str, installation_id: int, head: str, base: str, title: str, body: str
    ) -> tuple[int, str]:
        r = await self._client.request(
            installation_id,
            "POST",
            f"/repos/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )
        d = r.json()
        return int(d["number"]), str(d["html_url"])

    async def dispatch_verify(self, repo: str, installation_id: int, branch: str) -> None:
        info = await self._client.request(installation_id, "GET", f"/repos/{repo}")
        await self._client.request(
            installation_id,
            "POST",
            f"/repos/{repo}/actions/workflows/verify.yml/dispatches",
            json={
                "ref": info.json()["default_branch"],
                "inputs": {"ref": branch, "mode": "baseline"},
            },
        )

    async def fetch_report(
        self, repo: str, installation_id: int, run_id: int
    ) -> dict[str, Any] | None:
        arts = await self._client.request(
            installation_id, "GET", f"/repos/{repo}/actions/runs/{run_id}/artifacts"
        )
        art = next(
            (a for a in arts.json().get("artifacts", []) if a["name"] == "verification-report"),
            None,
        )
        if art is None:
            return None
        blob = await self._client.request(installation_id, "GET", art["archive_download_url"])
        with zipfile.ZipFile(io.BytesIO(blob.content)) as z:
            return json.loads(z.read("report.json"))  # type: ignore[no-any-return]
