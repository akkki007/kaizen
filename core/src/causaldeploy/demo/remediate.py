"""Demo fix flow for simulated incidents: the real remediation state machine, no GitHub writes.

A simulated incident has no real outage behind it, so reverting its commit on the real repository
would be wrong. This runs the same steps and state transitions (propose -> verifying -> verified ->
approve -> resolved, with the real verdict thresholds, lifecycle, graph writes and memory) but:
  * no branch is pushed and no pull request is opened (`simulated: true` on the run),
  * the staging verification result is synthetic (failing build 70% errors, fixed build 0%),
  * the revert diff is the real commit's patch, inverted, fetched read-only from GitHub when the
    App can reach it.
"""

import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from causaldeploy.contracts import VerificationResultEvent
from causaldeploy.contracts.domain import IncidentStatus
from causaldeploy.db.models import AuditLog, RemediationRun, Repository
from causaldeploy.db.session import session_scope
from causaldeploy.github.client import GitHubClient
from causaldeploy.graph.store import GraphStore
from causaldeploy.incidents import lifecycle
from causaldeploy.investigate.scorer import rank_graph
from causaldeploy.remediate import service as svc

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task[None]] = set()
VERIFY_DELAY_S = 4.0
_HUNK = re.compile(r"^@@ -(\S+) \+(\S+) @@(.*)$")


async def is_simulated(incident_id: str) -> bool:
    async with session_scope() as s:
        row = (
            await s.execute(
                select(AuditLog.id).where(
                    AuditLog.action == "demo.inject",
                    AuditLog.detail["incident_id"].as_string() == incident_id,
                )
            )
        ).first()
    return row is not None


def invert_patch(filename: str, patch: str) -> str:
    """The patch that undoes `patch` (swap +/- lines and the two ranges of every hunk)."""
    out = [f"diff --git a/{filename} b/{filename}", f"--- a/{filename}", f"+++ b/{filename}"]
    for line in patch.splitlines():
        m = _HUNK.match(line)
        if m:
            out.append(f"@@ -{m[2]} +{m[1]} @@{m[3]}")
        elif line.startswith("+"):
            out.append("-" + line[1:])
        elif line.startswith("-"):
            out.append("+" + line[1:])
        else:
            out.append(line)
    return "\n".join(out)


async def revert_diff(repo: str, sha: str) -> str | None:
    """Real commit patch, inverted; None when GitHub is unreachable (the demo still runs)."""
    try:
        async with session_scope() as s:
            rep = (
                await s.execute(select(Repository).where(Repository.full_name == repo))
            ).scalar_one_or_none()
        if rep is None or rep.installation_id is None:
            return None
        gh = GitHubClient()
        try:
            r = await gh.request(rep.installation_id, "GET", f"/repos/{repo}/commits/{sha}")
        finally:
            await gh.aclose()
        files = r.json().get("files") or []
        parts = [invert_patch(f["filename"], f["patch"]) for f in files if f.get("patch")]
        return "\n".join(parts) or None
    except Exception:
        log.warning("could not fetch the commit patch for the demo revert diff")
        return None


async def propose(repo: str, incident_id: str) -> str | None:
    """Start a simulated revert; returns the remediation id (None when nothing to revert)."""
    store = GraphStore(repo)
    result = await asyncio.to_thread(rank_graph, store, incident_id)
    if not result.candidates or result.verdict not in ("confident", "ambiguous"):
        return None
    sha = result.candidates[0].sha
    existing, run_id = await svc._start_run(incident_id, "revert", sha)
    if existing:
        return existing.id
    run = await svc._get(run_id)
    branch = str((run.result or {})["branch"])
    attempt = int((run.result or {}).get("attempt", 1))
    await lifecycle.advance(
        incident_id,
        IncidentStatus.REMEDIATING,
        only_from=[IncidentStatus.IDENTIFIED, IncidentStatus.EXPLAINED, IncidentStatus.NEEDS_HUMAN],
        reason=f"revert of {sha[:7]} proposed (simulated)",
    )
    diff = await revert_diff(repo, sha)
    labels = svc.pr_labels(incident_id)
    await lifecycle.advance(
        incident_id,
        IncidentStatus.VERIFYING,
        only_from=[IncidentStatus.REMEDIATING],
        reason="staging verification started (simulated)",
    )
    await svc._set(
        run_id,
        "verifying",
        {
            "sha": sha,
            "branch": branch,
            "attempt": attempt,
            "base": "main",
            "labels": labels,
            "diff": diff,
            "rationale": svc.rationale(result),
            "prior_fixes": await svc._similar(repo, store, incident_id),
            "simulated": True,
        },
    )
    await asyncio.to_thread(svc._write_graph, store, incident_id, run_id, sha, "", "revert")
    task = asyncio.create_task(_finish_verification(repo, incident_id, run_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return run_id


async def _finish_verification(repo: str, incident_id: str, run_id: str) -> None:
    await asyncio.sleep(VERIFY_DELAY_S)  # long enough for the UI to show the verifying step
    try:
        await svc.record_verification_event(
            VerificationResultEvent(
                repo=repo,
                remediation_id=run_id,
                incident_id=incident_id,
                passed=True,
                tests_passed=True,
                error_rate_before=0.7,
                error_rate_after=0.0,
                baseline_error_rate=0.0,
                ts=datetime.now(UTC),
            )
        )
    except Exception:
        log.exception("simulated verification failed for %s", run_id)


async def complete(repo: str, run_id: str, approver: str) -> str | None:
    """Simulated merge of an approved demo fix: resolves the incident and writes the memory."""
    async with session_scope() as s:
        run = await s.get(RemediationRun, run_id)
        branch = str((run.result or {}).get("branch")) if run else ""
    return await svc.finalize_merged(repo, branch, approver)


async def simulated_run(run_id: str) -> dict[str, Any] | None:
    async with session_scope() as s:
        run = await s.get(RemediationRun, run_id)
        return dict(run.result or {}) if run and (run.result or {}).get("simulated") else None
