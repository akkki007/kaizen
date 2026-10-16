"""LLM patch remediation (#91, P1): the first step of patch -> revert -> escalate.

Off by default (`CD_REMEDIATION_PATCH_ENABLED=false`). Given the culprit commit's diff, the
source of the functions it changed and the incident's error samples, an injected `PatchClient`
returns a unified diff (+ optionally one test file). Guard rails, all enforced here:
- the patch may only touch files the culprit commit changed; the test file must be a test path;
- at most `remediation_patch_max_files` files and `remediation_patch_max_lines` changed lines;
- it must apply cleanly in a temporary git worktree, and the repository's unit test command
  (`remediation_test_command`, run with the sandboxed environment) must pass there.
Any failure marks the run "rejected" (rejected_by "kaizen", reason logged + AuditLog
"remediation.patch_rejected") and the caller falls back to the revert.
"""

import asyncio
import json
import re
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from causaldeploy.config import get_settings
from causaldeploy.contracts.domain import IncidentStatus
from causaldeploy.db.models import AuditLog, RemediationRun
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import GraphStore
from causaldeploy.incidents import lifecycle
from causaldeploy.investigate.scorer import rank_graph
from causaldeploy.remediate.ops import (
    RemediationOps,
    _git,
    branch_diff,
    configure_identity,
    default_branch,
    run_sandboxed,
)

MAX_SOURCE_CHARS = 8000


class PatchRequest(BaseModel):
    incident_id: str
    culprit_sha: str
    culprit_message: str
    culprit_diff: str
    function_sources: dict[str, str]  # function id -> source at the base branch
    error_samples: list[str]
    allowed_files: list[str]


class PatchProposal(BaseModel):
    diff: str  # unified diff, paths relative to the repository root (a/ b/ prefixes)
    test_path: str | None = None
    test_code: str | None = None
    rationale: str = ""


class PatchClient(Protocol):
    async def propose(self, request: PatchRequest) -> PatchProposal: ...


class PatchRejected(RuntimeError):
    pass


_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|\.(test|spec)\.[a-z]+$|_test\.[a-z]+$")


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def diff_files(diff: str) -> list[str]:
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith(("--- ", "+++ ")):
            p = line[4:].strip().split("\t")[0]
            if p == "/dev/null":
                continue
            p = p[2:] if p.startswith(("a/", "b/")) else p
            if p not in files:
                files.append(p)
    return files


def changed_lines(diff: str) -> int:
    return sum(
        1
        for line in diff.splitlines()
        if line[:1] in "+-" and not line.startswith(("+++ ", "--- "))
    )


def _safe(path: str) -> bool:
    return bool(path) and not path.startswith(("/", "\\")) and ".." not in Path(path).parts


def validate(proposal: PatchProposal, allowed: list[str]) -> list[str]:
    """The files the patch touches. Raises PatchRejected on any guard-rail violation."""
    s = get_settings()
    files = diff_files(proposal.diff)
    if not files:
        raise PatchRejected("the patch changes no files")
    bad = [f for f in files if not _safe(f) or (f not in allowed and not is_test_path(f))]
    if bad:
        raise PatchRejected(f"the patch touches files outside the culprit commit: {bad}")
    if proposal.test_path is not None:
        if not _safe(proposal.test_path) or not is_test_path(proposal.test_path):
            raise PatchRejected(f"not a test path: {proposal.test_path}")
    if len(files) > s.remediation_patch_max_files:
        raise PatchRejected(f"{len(files)} files > {s.remediation_patch_max_files}")
    n = changed_lines(proposal.diff)
    if n > s.remediation_patch_max_lines:
        raise PatchRejected(f"{n} changed lines > {s.remediation_patch_max_lines}")
    return files


def build_request(clone: Path, store: GraphStore, incident_id: str, sha: str) -> PatchRequest:
    message = _git(clone, "log", "-1", "--format=%s", sha)
    diff = _git(clone, "show", "--no-color", "--format=", sha)
    allowed = [f for f in _git(clone, "show", "--name-only", "--format=", sha).splitlines() if f]
    sources: dict[str, str] = {}
    for fid, path, start, end in store.read(
        "MATCH (:Commit {sha: $s})-[:modifies_fn]->(f:Function) "
        "RETURN f.id, f.path, f.start_line, f.end_line",
        {"s": sha},
    ):
        src = clone / str(path)
        if not path or not src.is_file():
            continue
        lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
        lo, hi = max(int(start or 1), 1), int(end or len(lines))
        sources[str(fid)] = "\n".join(lines[lo - 1 : hi])[:MAX_SOURCE_CHARS]
    samples = [
        f"{t or 'Error'}: {m or ''}"
        for t, m in store.read(
            "MATCH (:Incident {id: $i})-[:evidenced_by]->(e:Error) "
            "RETURN e.error_type, e.message LIMIT 5",
            {"i": incident_id},
        )
    ]
    return PatchRequest(
        incident_id=incident_id,
        culprit_sha=sha,
        culprit_message=message,
        culprit_diff=diff[:MAX_SOURCE_CHARS],
        function_sources=sources,
        error_samples=samples,
        allowed_files=allowed,
    )


def _test_cmd() -> list[str]:
    s = get_settings()
    cmd = shlex.split(s.remediation_test_command)
    if not cmd:
        raise PatchRejected("no test command configured")
    exe = shutil.which(cmd[0])  # resolves npm -> npm.cmd on Windows
    return [exe or cmd[0], *cmd[1:]]


def apply_patch(clone: Path, base: str, branch: str, proposal: PatchProposal) -> str:
    """Apply in a temporary worktree, run the unit tests, commit. Returns the head sha."""
    s = get_settings()
    configure_identity(clone)
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "wt"
        _git(clone, "worktree", "add", "-q", "-b", branch, str(wt), base)
        ok = False
        try:
            patch_file = Path(tmp) / "fix.patch"
            patch_file.write_text(
                proposal.diff if proposal.diff.endswith("\n") else proposal.diff + "\n"
            )
            try:
                _git(wt, "apply", "--check", str(patch_file))
                _git(wt, "apply", str(patch_file))
            except RuntimeError as e:
                raise PatchRejected(f"the patch does not apply: {e}") from None
            if proposal.test_path and proposal.test_code is not None:
                target = wt / proposal.test_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(proposal.test_code)
            try:
                res = run_sandboxed(_test_cmd(), wt, timeout=s.remediation_test_timeout_s)
            except (OSError, TimeoutError) as e:
                raise PatchRejected(f"tests could not run: {type(e).__name__}") from None
            except Exception as e:  # subprocess.TimeoutExpired
                raise PatchRejected(f"tests did not finish: {type(e).__name__}") from None
            if res.returncode != 0:
                tail = (res.stdout + res.stderr).strip()[-500:]
                raise PatchRejected(f"tests failed (exit {res.returncode}): {tail}")
            _git(wt, "add", "-A")
            _git(wt, "commit", "-q", "-m", f"Kaizen patch: {proposal.rationale[:60] or 'fix'}")
            head = _git(wt, "rev-parse", "HEAD")
            ok = True
            return head
        finally:
            _git(clone, "worktree", "remove", "--force", str(wt))
            if not ok:
                _git(clone, "branch", "-D", branch)


async def _reject(run_id: str, incident_id: str, result: dict[str, Any], reason: str) -> None:
    async with session_scope() as s:
        row = await s.get(RemediationRun, run_id)
        assert row is not None
        row.status, row.rejected_by, row.reject_reason = "rejected", "kaizen", reason[:2000]
        row.result = {**result, "error": reason[:2000]}
        s.add(
            AuditLog(
                actor="kaizen",
                action="remediation.patch_rejected",
                target=run_id,
                detail={"incident": incident_id, "reason": reason[:1000]},
            )
        )


async def propose_patch(
    repo: str, incident_id: str, installation_id: int, ops: RemediationOps, client: PatchClient
) -> RemediationRun | None:
    """Try an LLM patch. Returns the run (status "verifying", or "rejected" to fall back)."""
    from causaldeploy.remediate import service

    store = GraphStore(repo)
    result = await asyncio.to_thread(rank_graph, store, incident_id)
    if not result.candidates or result.verdict not in ("confident", "ambiguous"):
        return None
    sha = result.candidates[0].sha
    existing, run_id = await service._start_run(incident_id, "patch", sha)
    if existing:
        return existing
    run = await service._get(run_id)
    base_result = dict(run.result or {})
    branch = str(base_result["branch"])
    await lifecycle.advance(
        incident_id,
        IncidentStatus.REMEDIATING,
        only_from=[IncidentStatus.IDENTIFIED, IncidentStatus.EXPLAINED, IncidentStatus.NEEDS_HUMAN],
        reason=f"patch for {sha[:7]} proposed",
    )
    loop = asyncio.get_running_loop()

    def work() -> tuple[str, str, str, PatchProposal]:
        from pathlib import Path as _P

        with ops.clone(repo, installation_id) as c:
            clone = _P(c)
            base = default_branch(clone)
            req = build_request(clone, store, incident_id, sha)
            fut = asyncio.run_coroutine_threadsafe(client.propose(req), loop)
            try:
                proposal = fut.result(timeout=300)
            except (ValidationError, ValueError, KeyError) as e:
                raise PatchRejected(f"unusable patch proposal: {e}") from None
            validate(proposal, req.allowed_files)
            head = apply_patch(clone, base, branch, proposal)
            diff = branch_diff(clone, base, head)
            ops.push(clone, repo, installation_id, branch)
            return base, head, diff, proposal

    try:
        base, head, diff, proposal = await asyncio.to_thread(work)
    except PatchRejected as e:
        await _reject(run_id, incident_id, base_result, str(e))
        return await service._get(run_id)
    labels = service.pr_labels(incident_id)
    prior = await service._similar(repo, store, incident_id)
    number, url = await ops.open_pr(
        repo,
        installation_id,
        branch,
        base,
        f"Patch {sha[:7]}: incident {incident_id}",
        service.pr_body(
            result, incident_id, get_settings().public_base_url.rstrip("/"), prior, "patch"
        )
        + (f"\n\n**Patch rationale:** {proposal.rationale}" if proposal.rationale else ""),
        labels,
    )
    await ops.dispatch_verify(repo, installation_id, branch)
    await lifecycle.advance(
        incident_id,
        IncidentStatus.VERIFYING,
        only_from=[IncidentStatus.REMEDIATING],
        reason="verification dispatched",
    )
    await service._set(
        run_id,
        "verifying",
        {
            **base_result,
            "head": head,
            "base": base,
            "pr_number": number,
            "pr_url": url,
            "labels": labels,
            "diff": diff,
            "rationale": proposal.rationale or service.rationale(result, "patch"),
            "test_path": proposal.test_path,
            "prior_fixes": prior,
        },
    )
    await asyncio.to_thread(service._write_graph, store, incident_id, run_id, sha, url, "patch")
    return await service._get(run_id)


class OpenAIPatchClient:
    """Patch proposals from the configured OpenAI model (JSON output, temperature 0)."""

    def __init__(self) -> None:
        from openai import AsyncOpenAI

        s = get_settings()
        self.model = s.openai_model
        self._client = AsyncOpenAI(api_key=s.openai_api_key.get_secret_value())

    async def propose(self, request: PatchRequest) -> PatchProposal:
        system = (
            "You fix a production regression with the smallest possible change. Reply with JSON"
            ' {"diff": "<unified diff, a/ b/ paths>", "test_path": "<test file path or null>",'
            ' "test_code": "<content or null>", "rationale": "<one sentence>"}. Only edit the'
            " files listed in allowed_files."
        )
        resp = await self._client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": request.model_dump_json()},
            ],
        )
        return PatchProposal.model_validate(json.loads(resp.choices[0].message.content or "{}"))


def default_patch_client() -> PatchClient | None:
    """The production client, or None when patching is off or no model is configured."""
    s = get_settings()
    if not s.remediation_patch_enabled:
        return None
    if not (s.openai_api_key.get_secret_value() and s.openai_model):
        return None
    return OpenAIPatchClient()
