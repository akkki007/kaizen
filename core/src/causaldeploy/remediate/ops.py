"""Everything that touches GitHub or a git working copy, behind one interface.

Production uses `GitHubOps` (installation token, git over HTTPS, REST); tests inject a fake with
a local repository. Nothing here ever pushes to the default branch: fix branches only.

Sandbox: every git / test subprocess runs with `sandbox_env()`, an allowlist of the variables
a toolchain needs (PATH, HOME, locale, temp dirs, Windows system paths). Kaizen's own
configuration and any cloud or GitHub credential (CD_*, GITHUB_*, OPENAI_*, AWS_*, ...) is
never inherited, so a test suite or git hook in the target repository cannot read them. The
installation token reaches git only as a one-shot `http.extraheader` argument for clone/push.

Limits (documented, not enforced here): the subprocesses still share the host network and
filesystem. Network isolation of untrusted test code needs the worker to run in a container or
VM with an egress policy; verification proper runs in the repository's own CI (staging), not on
the Kaizen worker.
"""

import asyncio
import base64
import io
import json
import os
import subprocess
import tempfile
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from causaldeploy.config import get_settings
from causaldeploy.github.client import GitHubClient

# Variables a git / node / python toolchain needs; nothing else is passed through.
ENV_ALLOWLIST = frozenset(
    {
        "PATH",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TMP",
        "TEMP",
        "TMPDIR",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
    }
)
# Belt and braces: even if one of these were allowlisted by name, it is dropped.
ENV_DENY_PREFIXES = ("CD_", "GITHUB_", "GH_", "OPENAI_", "AWS_", "ANTHROPIC_", "AZURE_", "GOOGLE_")


def sandbox_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for git and test subprocesses (see module docstring)."""
    src = os.environ if source is None else source
    env = {
        k: v
        for k, v in src.items()
        if k.upper() in ENV_ALLOWLIST and not k.upper().startswith(ENV_DENY_PREFIXES)
    }
    env["GIT_TERMINAL_PROMPT"] = "0"  # never block on a credential prompt
    env["CI"] = "true"
    return env


def run_sandboxed(
    cmd: list[str], cwd: Path, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a test/build command in `cwd` with the scrubbed environment (no check)."""
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, env=sandbox_env(), timeout=timeout
    )


class RemediationOps(Protocol):
    def clone(self, repo: str, installation_id: int) -> Any: ...  # context manager -> Path
    def push(self, clone: Path, repo: str, installation_id: int, branch: str) -> None: ...
    async def open_pr(
        self,
        repo: str,
        installation_id: int,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: list[str],
    ) -> tuple[int, str]: ...
    async def close_pr(
        self, repo: str, installation_id: int, number: int, comment: str
    ) -> None: ...
    async def merge_pr(
        self, repo: str, installation_id: int, number: int, sha: str | None
    ) -> None: ...
    async def post_check(
        self,
        repo: str,
        installation_id: int,
        head_sha: str,
        passed: bool,
        details_url: str,
        summary: str,
    ) -> None: ...
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
            [*cmd, *args], check=True, capture_output=True, text=True, env=sandbox_env()
        ).stdout.strip()
    except subprocess.CalledProcessError as e:
        # never include the command (it may carry the token); keep git's own message
        raise RuntimeError(f"git {args[0]} failed: {(e.stderr or '').strip()[:300]}") from None


class RevertConflict(RuntimeError):
    """The commit cannot be reverted cleanly; a human (or a patch) has to resolve it."""


def configure_identity(clone: Path) -> None:
    _git(clone, "config", "user.name", "Kaizen")
    _git(clone, "config", "user.email", "kaizen@users.noreply.github.com")


def make_revert_branch(clone: Path, base: str, sha: str, branch: str) -> str:
    """Create `branch` from `base` with `sha` reverted. Returns the new head sha.

    A merge commit is reverted against its first parent (`-m 1`, the mainline); squash and
    ordinary commits revert directly."""
    configure_identity(clone)
    _git(clone, "checkout", "-q", "-b", branch, base)
    parents = _git(clone, "rev-list", "--parents", "-n", "1", sha).split()[1:]
    args = ["revert", "--no-edit"] + (["-m", "1"] if len(parents) > 1 else []) + [sha]
    try:
        _git(clone, *args)
    except RuntimeError as e:
        subprocess.run(
            ["git", "-C", str(clone), "revert", "--abort"], capture_output=True, env=sandbox_env()
        )
        raise RevertConflict(str(e)) from None
    return _git(clone, "rev-parse", "HEAD")


MAX_DIFF_BYTES = 200_000


def branch_diff(clone: Path, base: str, head: str) -> str:
    """Unified diff of the fix branch against its base (what the PR will show)."""
    out = _git(clone, "diff", "--no-color", f"{base}...{head}")
    return out if len(out) <= MAX_DIFF_BYTES else out[:MAX_DIFF_BYTES] + "\n... (truncated)\n"


def default_branch(clone: Path) -> str:
    try:
        return _git(clone, "symbolic-ref", "--short", "HEAD") or "main"
    except RuntimeError:
        return "main"


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
        self,
        repo: str,
        installation_id: int,
        head: str,
        base: str,
        title: str,
        body: str,
        labels: list[str],
    ) -> tuple[int, str]:
        r = await self._client.request(
            installation_id,
            "POST",
            f"/repos/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )
        d = r.json()
        number = int(d["number"])
        if labels:  # PRs are issues for labels; missing labels are created by GitHub
            await self._client.request(
                installation_id,
                "POST",
                f"/repos/{repo}/issues/{number}/labels",
                json={"labels": labels},
            )
        return number, str(d["html_url"])

    async def close_pr(self, repo: str, installation_id: int, number: int, comment: str) -> None:
        if comment:
            await self._client.request(
                installation_id,
                "POST",
                f"/repos/{repo}/issues/{number}/comments",
                json={"body": comment},
            )
        await self._client.request(
            installation_id, "PATCH", f"/repos/{repo}/pulls/{number}", json={"state": "closed"}
        )

    async def merge_pr(self, repo: str, installation_id: int, number: int, sha: str | None) -> None:
        body: dict[str, Any] = {"merge_method": get_settings().remediation_merge_method}
        if sha:
            body["sha"] = sha  # GitHub refuses if the head moved since verification
        await self._client.request(
            installation_id, "PUT", f"/repos/{repo}/pulls/{number}/merge", json=body
        )

    async def post_check(
        self,
        repo: str,
        installation_id: int,
        head_sha: str,
        passed: bool,
        details_url: str,
        summary: str,
    ) -> None:
        payload: dict[str, Any] = {
            "name": "Kaizen verification",
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "success" if passed else "failure",
            "output": {
                "title": "Fix verified" if passed else "Fix did not verify",
                "summary": summary[:60000],
            },
        }
        if details_url:
            payload["details_url"] = details_url
        await self._client.request(
            installation_id, "POST", f"/repos/{repo}/check-runs", json=payload
        )

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
