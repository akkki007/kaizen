"""Remediation (PLAN section 8, #91): fix branch + PR, staging verification, approval, merge.

Fallback chain: patch (only when enabled and a patch client is injected) -> revert -> escalate.
A revert that conflicts, or any fix whose verification fails with nothing left to try, moves the
incident to NEEDS_HUMAN with an AuditLog "remediation.escalated" row.

Approval invariant: the only code paths that end in a merge are (a) a person merging on GitHub,
which resolves the incident only if the run was approved (`finalize_merged`), and (b) the "app"
merge policy, which calls the merge API only for a run whose status is "approved" with an
`approved_by` (`merge_approved`). Nothing else merges.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from causaldeploy.config import get_settings
from causaldeploy.contracts import VerificationResultEvent
from causaldeploy.contracts.domain import IncidentStatus
from causaldeploy.db.models import AuditLog, IncidentRow, IncidentTransition, RemediationRun
from causaldeploy.db.models import Repository as RepositoryRow
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import Edge, GraphStore
from causaldeploy.incidents import lifecycle
from causaldeploy.investigate.scorer import RankResult, rank_graph
from causaldeploy.remediate.ops import (
    GitHubOps,
    RemediationOps,
    RevertConflict,
    branch_diff,
    default_branch,
    make_revert_branch,
)
from causaldeploy.verify.verdict import Metrics, VerdictConfig, verdict

log = logging.getLogger(__name__)

BRANCH_PREFIX = "kaizen/fix-"
LEGACY_PREFIXES = ("kaizen/revert-",)  # branches opened before the rename; still matched
KAIZEN_PREFIXES = (BRANCH_PREFIX, *LEGACY_PREFIXES)
SYSTEM = "kaizen"
# a run in one of these states does not block a new attempt for the incident
SETTLED = ("failed", "needs_manual", "rejected")
REJECTABLE = ("proposing", "verifying", "verified", "approved", "failed", "needs_manual")

# Production GitHub access; tests replace it (monkeypatch) with a fake
make_ops: Callable[[], RemediationOps] = GitHubOps


def is_kaizen_branch(ref: str) -> bool:
    return ref.startswith(KAIZEN_PREFIXES)


def fix_branch(incident_id: str, attempt: int = 1) -> str:
    """`kaizen/fix-<incident>`; later attempts (fallback, re-proposal) get `-<n>`."""
    return f"{BRANCH_PREFIX}{incident_id}" + (f"-{attempt}" if attempt > 1 else "")


def pr_labels(incident_id: str) -> list[str]:
    return ["kaizen", f"incident:{incident_id}"]


class RemediationStateError(RuntimeError):
    """The remediation is not in a state that allows the requested action."""


def prior_fixes(store: GraphStore, incident_id: str) -> list[dict[str, Any]]:
    """Resolved incidents that resemble this one, with the fix that worked (best first)."""
    rows = store.read(
        "MATCH (:Incident {id: $i})-[r:resembles]->(j:Incident {status: 'resolved'}) "
        "OPTIONAL MATCH (j)-[:fixed_by]->(f:Fix) "
        "OPTIONAL MATCH (f)-[:reverts|patches]->(c:Commit) "
        "RETURN j.id, r.similarity, r.basis, f.kind, f.pr_url, c.sha "
        "ORDER BY r.similarity DESC, j.id LIMIT 3",
        {"i": incident_id},
    )
    return [
        {
            "incident_id": j,
            "similarity": sim,
            "basis": list(basis or []),
            "fix_kind": kind,
            "fix_pr_url": url,
            "fix_sha": sha,
        }
        for j, sim, basis, kind, url, sha in rows
    ]


def rationale(result: RankResult, kind: str = "revert") -> str:
    top = result.candidates[0]
    text = (
        f"{'Revert' if kind == 'revert' else 'Patch'} of {top.sha[:7]} "
        f"({top.message or 'no message'}): ranked #1 by the deterministic graph score with "
        f"confidence {top.confidence:.0%} and margin {result.margin:.2f} ({result.verdict})."
    )
    if top.evidence:
        e = top.evidence[0]
        text += f" It changed `{e.changed_function}`, on the path to `{e.thrown_function}`."
    return text


def pr_body(
    result: RankResult,
    incident_id: str,
    app_url: str,
    prior: list[dict[str, Any]] | None = None,
    kind: str = "revert",
) -> str:
    """The PR description: the commit, its score breakdown and a link back to the incident."""
    top = result.candidates[0]
    verb = "reverts" if kind == "revert" else "patches"
    lines = [
        f"Kaizen {verb} `{top.sha[:7]}` ({top.message or 'no message'}) "
        f"for incident `{incident_id}`.",
        "",
        "**Why this commit** (deterministic graph score, "
        f"confidence {top.confidence:.0%}, margin {result.margin:.2f}):",
        "",
        "| feature | value | contribution |",
        "|---|---|---|",
        *[f"| {f.name} | {f.value:.2f} | {f.contribution:+.3f} |" for f in top.features],
    ]
    if top.evidence:
        e = top.evidence[0]
        lines += ["", f"Path: `{e.changed_function}` -> `{e.thrown_function}`"]
    for p in prior or []:
        if not p.get("fix_kind"):
            continue
        what = f"{p['fix_kind']} of `{(p.get('fix_sha') or '?')[:7]}`"
        link = f" ({p['fix_pr_url']})" if p.get("fix_pr_url") else ""
        lines += [
            "",
            f"**Seen before:** incident `{p['incident_id']}` (similarity "
            f"{float(p['similarity'] or 0):.2f}) was resolved by a {what}{link}.",
        ]
        if p.get("basis"):
            lines.append("Shared: " + ", ".join(f"`{b}`" for b in p["basis"][:5]))
    lines += [
        "",
        f"Incident page: {app_url}/incidents/{incident_id}",
        "",
        "Verification runs automatically in an isolated staging environment; "
        "nothing is merged without a recorded approval. Production is never touched by Kaizen.",
    ]
    return "\n".join(lines)


async def propose_fix(
    repo: str,
    incident_id: str,
    installation_id: int,
    ops: RemediationOps | None = None,
    patch_client: Any = None,
) -> RemediationRun | None:
    """Entry point of the fallback chain: patch (if enabled) -> revert -> escalate."""
    ops = ops or make_ops()
    if get_settings().remediation_patch_enabled and patch_client is not None:
        from causaldeploy.remediate.patch import propose_patch

        run = await propose_patch(repo, incident_id, installation_id, ops, patch_client)
        if run is not None and run.status not in SETTLED:
            return run
    return await propose_revert(repo, incident_id, installation_id, ops)


async def _start_run(incident_id: str, kind: str, sha: str) -> tuple[RemediationRun | None, str]:
    """A new run row (or the live one). Returns (existing live run | None, new run id)."""
    async with session_scope() as session:
        existing = (
            (
                await session.execute(
                    select(RemediationRun).where(
                        RemediationRun.incident_id == incident_id,
                        RemediationRun.status.not_in(SETTLED),
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing:
            session.expunge(existing)
            return existing, existing.id
        attempts = (
            await session.execute(
                select(func.count())
                .select_from(RemediationRun)
                .where(RemediationRun.incident_id == incident_id)
            )
        ).scalar_one()
        branch = fix_branch(incident_id, attempts + 1)
        run = RemediationRun(
            id=f"rem-{uuid.uuid4().hex[:8]}",
            incident_id=incident_id,
            kind=kind,
            status="proposing",
            result={"sha": sha, "branch": branch, "attempt": attempts + 1},
        )
        session.add(run)
        return None, run.id


async def _similar(repo: str, store: GraphStore, incident_id: str) -> list[dict[str, Any]]:
    try:
        from causaldeploy.memory.similarity import update_similarity

        await update_similarity(repo, incident_id)
        return await asyncio.to_thread(prior_fixes, store, incident_id)
    except Exception:  # history is advisory; never block a fix on it
        log.warning("similarity lookup failed", extra={"ctx": {"incident": incident_id}})
        return []


async def propose_revert(
    repo: str, incident_id: str, installation_id: int, ops: RemediationOps | None = None
) -> RemediationRun | None:
    """Open a revert PR for the top-ranked commit and start verification (once per incident)."""
    s = get_settings()
    if not s.is_allowed(repo):
        raise PermissionError(f"{repo} is not in CD_ALLOWED_REPOS")
    ops = ops or make_ops()
    store = GraphStore(repo)
    result = await asyncio.to_thread(rank_graph, store, incident_id)
    if not result.candidates or result.verdict not in ("confident", "ambiguous"):
        return None  # not a code change we can point at: no automatic action
    sha = result.candidates[0].sha
    existing, run_id = await _start_run(incident_id, "revert", sha)
    if existing:
        return existing
    run = await _get(run_id)
    branch = str((run.result or {})["branch"])
    attempt = int((run.result or {}).get("attempt", 1))
    await lifecycle.advance(
        incident_id,
        IncidentStatus.REMEDIATING,
        only_from=[
            IncidentStatus.IDENTIFIED,
            IncidentStatus.EXPLAINED,
            IncidentStatus.NEEDS_HUMAN,
            IncidentStatus.VERIFYING,  # fallback after a failed patch
        ],
        reason=f"revert of {sha[:7]} proposed",
    )
    prior = await _similar(repo, store, incident_id)

    def make_branch() -> tuple[str, str, str]:
        with ops.clone(repo, installation_id) as clone:
            clone = Path(clone)
            base = default_branch(clone)
            head = make_revert_branch(clone, base, sha, branch)
            diff = branch_diff(clone, base, head)
            ops.push(clone, repo, installation_id, branch)
            return base, head, diff

    base_result = {"sha": sha, "branch": branch, "attempt": attempt}
    try:
        base, head, diff = await asyncio.to_thread(make_branch)
    except RevertConflict as e:
        await _set(run_id, "needs_manual", {**base_result, "error": str(e)})
        await escalate(incident_id, run_id, f"revert of {sha[:7]} conflicts: {e}")
        return await _get(run_id)
    labels = pr_labels(incident_id)
    number, url = await ops.open_pr(
        repo,
        installation_id,
        branch,
        base,
        f"Revert {sha[:7]}: incident {incident_id}",
        pr_body(result, incident_id, s.public_base_url.rstrip("/"), prior),
        labels,
    )
    await ops.dispatch_verify(repo, installation_id, branch)
    await lifecycle.advance(
        incident_id,
        IncidentStatus.VERIFYING,
        only_from=[IncidentStatus.REMEDIATING],
        reason="verification dispatched",
    )
    await _set(
        run_id,
        "verifying",
        {
            **base_result,
            "head": head,
            "pr_number": number,
            "pr_url": url,
            "base": base,
            "labels": labels,
            "diff": diff,
            "rationale": rationale(result),
            "prior_fixes": prior,
        },
    )
    await asyncio.to_thread(_write_graph, store, incident_id, run_id, sha, url, "revert")
    return await _get(run_id)


async def escalate(incident_id: str, run_id: str | None, reason: str) -> None:
    """Automatic remediation is exhausted: a person takes over."""
    await lifecycle.advance(
        incident_id,
        IncidentStatus.NEEDS_HUMAN,
        only_from=[IncidentStatus.REMEDIATING, IncidentStatus.VERIFYING],
        reason=reason,
    )
    async with session_scope() as s:
        s.add(
            AuditLog(
                actor=SYSTEM,
                action="remediation.escalated",
                target=incident_id,
                detail={"remediation": run_id, "reason": reason[:500]},
            )
        )


def _write_graph(
    store: GraphStore, incident_id: str, run_id: str, sha: str, pr_url: str, kind: str
) -> None:
    store.ensure_schema()
    fix = f"fix-{run_id}"
    store.upsert_nodes("Fix", [{"id": fix, "kind": kind, "pr_url": pr_url, "verified": False}])
    store.upsert_edges(
        [
            Edge("Fix", fix, "reverts" if kind == "revert" else "patches", "Commit", sha),
            Edge("Incident", incident_id, "fixed_by", "Fix", fix),
        ]
    )


async def _set(run_id: str, status: str, result: dict[str, Any]) -> None:
    async with session_scope() as session:
        row = await session.get(RemediationRun, run_id)
        assert row is not None
        row.status, row.result = status, result


async def _merge_result(run_id: str, extra: dict[str, Any]) -> None:
    async with session_scope() as session:
        row = await session.get(RemediationRun, run_id, with_for_update=True)
        assert row is not None
        row.result = {**(row.result or {}), **extra}


async def _get(run_id: str) -> RemediationRun:
    async with session_scope() as session:
        row = await session.get(RemediationRun, run_id)
        assert row is not None
        session.expunge(row)
        return row


async def installation_of(repo: str) -> int | None:
    async with session_scope() as s:
        row = await s.get(RepositoryRow, repo)
        return row.installation_id if row else None


# -- verification -----------------------------------------------------------------------------


def _report_metrics(
    report: dict[str, Any] | None, graph_before: float | None, graph_baseline: float | None
) -> tuple[Metrics, Metrics, Metrics]:
    """failing / fixed / baseline metrics from the verification report, with the incident's
    detected rates (graph) as the fallback for failing and baseline."""
    r = report or {}
    fixed = Metrics.parse(r.get("fixed")) or Metrics.parse(r) or Metrics()
    failing = Metrics.parse(r.get("failing")) or Metrics(error_rate=graph_before)
    baseline = Metrics.parse(r.get("baseline")) or Metrics(error_rate=graph_baseline)
    if baseline.error_rate is None and graph_baseline is not None:
        baseline = baseline.model_copy(update={"error_rate": graph_baseline})
    if failing.error_rate is None and graph_before is not None:
        failing = failing.model_copy(update={"error_rate": graph_before})
    return failing, fixed, baseline


async def record_verification(
    repo: str,
    branch: str,
    conclusion: str,
    run_url: str,
    report: dict[str, Any] | None,
    *,
    ops: RemediationOps | None = None,
    installation_id: int | None = None,
) -> str | None:
    """Store the staging result (from the verify workflow) for the run that owns `branch`."""
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(RemediationRun).where(
                        RemediationRun.status.in_(("verifying", "verified", "failed"))
                    )
                )
            )
            .scalars()
            .all()
        )
        run = next((r for r in rows if (r.result or {}).get("branch") == branch), None)
        if run is None:
            return None
        inc = (
            await session.execute(select(IncidentRow).where(IncidentRow.id == run.incident_id))
        ).scalar_one()
        if inc.repo != repo:
            return None
        run_id, incident_id = run.id, inc.id
    props = await asyncio.to_thread(
        GraphStore(repo).read,
        "MATCH (i:Incident {id: $id}) RETURN i.error_rate, i.baseline_error_rate",
        {"id": incident_id},
    )
    before, base = props[0] if props else (None, None)
    failing, fixed, baseline = _report_metrics(report, before, base)
    r = report or {}
    tests_passed = r.get("tests_passed")
    return await _apply_verification(
        repo,
        run_id,
        conclusion=conclusion,
        run_url=run_url,
        failing=failing,
        fixed=fixed,
        baseline=baseline,
        workflow_ok=conclusion == "success",
        tests_passed=tests_passed if isinstance(tests_passed, bool) else None,
        tests=list(r.get("tests") or []),
        ops=ops,
        installation_id=installation_id,
    )


async def record_verification_event(
    event: VerificationResultEvent,
    *,
    ops: RemediationOps | None = None,
    installation_id: int | None = None,
) -> str | None:
    """A `verification_result` event (POST /api/v1/events), keyed by remediation id."""
    async with session_scope() as session:
        run = await session.get(RemediationRun, event.remediation_id)
        if run is None or run.status not in ("verifying", "verified", "failed"):
            return None
        inc = await session.get(IncidentRow, run.incident_id)
        if inc is None or inc.repo != event.repo or inc.id != event.incident_id:
            return None  # an API key for one repo cannot verify another repo's fix
    return await _apply_verification(
        event.repo,
        event.remediation_id,
        conclusion="success" if event.passed else "failure",
        run_url=event.details_url or "",
        failing=Metrics(error_rate=event.error_rate_before),
        fixed=Metrics(error_rate=event.error_rate_after),
        baseline=Metrics(error_rate=event.baseline_error_rate),
        workflow_ok=event.passed,
        tests_passed=event.tests_passed,
        tests=[],
        ops=ops,
        installation_id=installation_id,
    )


async def _apply_verification(
    repo: str,
    run_id: str,
    *,
    conclusion: str,
    run_url: str,
    failing: Metrics,
    fixed: Metrics,
    baseline: Metrics,
    workflow_ok: bool,
    tests_passed: bool | None,
    tests: list[Any],
    ops: RemediationOps | None,
    installation_id: int | None,
) -> str | None:
    cfg = VerdictConfig.from_settings()
    v = verdict(failing, fixed, baseline, cfg, workflow_ok=workflow_ok, tests_passed=tests_passed)
    async with session_scope() as session:
        run = await session.get(RemediationRun, run_id, with_for_update=True)
        if run is None or run.status not in ("verifying", "verified", "failed"):
            return None
        was = run.status
        run.status = "verified" if v.passed else "failed"
        run.result = {
            **(run.result or {}),
            "verification": {
                "passed": v.passed,
                "conclusion": conclusion,
                "run_url": run_url,
                "error_rate_before": failing.error_rate,
                "error_rate_after": fixed.error_rate,
                "baseline_error_rate": baseline.error_rate,
                "p95_ms": fixed.p95_ms,
                "metrics": {
                    "failing": failing.model_dump(),
                    "fixed": fixed.model_dump(),
                    "baseline": baseline.model_dump(),
                },
                "inputs": {
                    "failing": failing.model_dump(),
                    "fixed": fixed.model_dump(),
                    "baseline": baseline.model_dump(),
                    "workflow_ok": workflow_ok,
                    "tests_passed": tests_passed,
                },
                "config": cfg.model_dump(),
                "verdict": v.model_dump(),
                "tests": tests,
            },
        }
        status, incident_id, kind = run.status, run.incident_id, run.kind
        head = (run.result or {}).get("head")
        pr_number = (run.result or {}).get("pr_number")
    report = {"error_rate": fixed.error_rate, "p95_ms": fixed.p95_ms}
    await asyncio.to_thread(
        _write_verification, GraphStore(repo), run_id, v.passed, run_url, report, incident_id
    )
    if ops is not None and installation_id is not None and head:
        summary = (
            f"Error rate {fixed.error_rate} vs. baseline {baseline.error_rate} "
            f"(failing build: {failing.error_rate}). "
            + ("All checks passed." if v.passed else "; ".join(v.reasons))
        )
        check: dict[str, Any] = {"conclusion": "success" if v.passed else "failure"}
        try:
            await ops.post_check(repo, installation_id, str(head), v.passed, run_url, summary)
            check["posted"] = True
        except Exception as e:  # the verdict stands even if GitHub is unavailable
            check.update(posted=False, error=type(e).__name__)
            log.warning("check run post failed", extra={"ctx": {"remediation": run_id}})
        async with session_scope() as session:
            row = await session.get(RemediationRun, run_id, with_for_update=True)
            assert row is not None
            res = dict(row.result or {})
            res["verification"] = {**res.get("verification", {}), "check": check}
            row.result = res
    if not v.passed and was == "verifying":
        # a failed fix never resolves: try the next step of the chain, else escalate
        reason = f"verification failed ({conclusion}): " + "; ".join(v.reasons)
        if kind == "patch" and ops is not None and installation_id is not None:
            if pr_number:
                await _close_pr_quietly(ops, repo, installation_id, int(pr_number), reason)
            nxt = await propose_revert(repo, incident_id, installation_id, ops)
            if nxt is None:
                await escalate(incident_id, run_id, reason)
        else:
            await escalate(incident_id, run_id, reason)
    return status


async def _close_pr_quietly(
    ops: RemediationOps, repo: str, installation_id: int, number: int, comment: str
) -> str | None:
    try:
        await ops.close_pr(repo, installation_id, number, comment)
        return None
    except Exception as e:
        log.warning("closing PR failed", extra={"ctx": {"repo": repo, "pr": number}})
        return type(e).__name__


def _write_verification(
    store: GraphStore,
    run_id: str,
    passed: bool,
    url: str,
    report: dict[str, Any] | None,
    incident_id: str,
) -> None:
    ver = f"ver-{run_id}"
    store.upsert_nodes(
        "VerificationRun",
        [
            {
                "id": ver,
                "passed": passed,
                "run_url": url,
                "error_rate": (report or {}).get("error_rate"),
                "p95_ms": (report or {}).get("p95_ms"),
            }
        ],
    )
    store.graph.query(
        "MATCH (f:Fix {id: $f}) SET f.verified = $v", {"f": f"fix-{run_id}", "v": passed}
    )
    store.upsert_edges([Edge("Fix", f"fix-{run_id}", "verified_by", "VerificationRun", ver)])


# -- approval, rejection, merge ---------------------------------------------------------------


async def merge_approved(run_id: str, ops: RemediationOps | None = None) -> dict[str, Any]:
    """ "app" merge policy: merge the PR through the API. Refuses anything not approved."""
    async with session_scope() as s:
        run = await s.get(RemediationRun, run_id)
        if run is None:
            raise RemediationStateError("remediation not found")
        if run.status != "approved" or not run.approved_by:
            raise RemediationStateError(f"remediation is {run.status}; merging needs an approval")
        inc = await s.get(IncidentRow, run.incident_id)
        assert inc is not None
        repo, r = inc.repo, dict(run.result or {})
    number = r.get("pr_number")
    installation = await installation_of(repo)
    if not number or installation is None:
        out = {"status": "failed", "error": "no pull request or installation"}
    else:
        try:
            await (ops or make_ops()).merge_pr(repo, installation, int(number), r.get("head"))
            out = {"status": "requested"}
        except Exception as e:
            log.warning("app merge failed", extra={"ctx": {"remediation": run_id}})
            out = {"status": "failed", "error": type(e).__name__}
    out["at"] = datetime.now(UTC).isoformat()
    await _merge_result(run_id, {"merge": {"policy": "app", **out}})
    async with session_scope() as s:
        s.add(AuditLog(actor=SYSTEM, action="remediation.app_merge", target=run_id, detail=out))
    return out


async def _status_before_remediation(incident_id: str) -> IncidentStatus:
    """Where an incident goes back to when a person rejects a fix still being prepared."""
    async with session_scope() as s:
        prev = (
            await s.execute(
                select(IncidentTransition.from_status)
                .where(
                    IncidentTransition.incident_id == incident_id,
                    IncidentTransition.to_status == IncidentStatus.REMEDIATING,
                )
                .order_by(IncidentTransition.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if prev in (IncidentStatus.IDENTIFIED, IncidentStatus.EXPLAINED):
        return IncidentStatus(prev)
    return IncidentStatus.NEEDS_HUMAN


async def reject_remediation(
    run_id: str, actor: str, reason: str, ops: RemediationOps | None = None
) -> RemediationRun:
    """A person rejects the fix. Lifecycle:
    - incident REMEDIATING (PR still being prepared) -> back to the diagnosed state it came
      from (IDENTIFIED / EXPLAINED) so another fix can be proposed;
    - incident VERIFYING (PR open / verified / approved) -> NEEDS_HUMAN;
    - any other incident state is left alone (e.g. already escalated or closed).
    The PR is closed with the reason as a comment; the Fix node gets accepted=false."""
    async with session_scope() as s:
        run = await s.get(RemediationRun, run_id, with_for_update=True)
        if run is None:
            raise RemediationStateError("remediation not found")
        if run.status not in REJECTABLE:
            raise RemediationStateError(f"remediation is {run.status}; it cannot be rejected")
        inc = await s.get(IncidentRow, run.incident_id)
        assert inc is not None
        run.status, run.rejected_by, run.reject_reason = "rejected", actor, reason
        s.add(
            AuditLog(
                actor=actor,
                action="remediation.reject",
                target=run_id,
                detail={"repo": inc.repo, "incident": inc.id, "reason": reason[:1000]},
            )
        )
        repo, incident_id, status = inc.repo, inc.id, IncidentStatus(inc.status)
        number = (run.result or {}).get("pr_number")

    def write() -> None:
        GraphStore(repo).graph.query(
            "MATCH (f:Fix {id: $f}) SET f.accepted = false, f.rejected_by = $a",
            {"f": f"fix-{run_id}", "a": actor},
        )

    await asyncio.to_thread(write)
    if number:
        installation = await installation_of(repo)
        err = "no installation"
        if installation is not None:
            err = (
                await _close_pr_quietly(
                    ops or make_ops(),
                    repo,
                    installation,
                    int(number),
                    f"Kaizen fix rejected by @{actor}: {reason}",
                )
                or ""
            )
        await _merge_result(run_id, {"pr_closed": not err, **({"close_error": err} if err else {})})
    msg = f"fix {run_id} rejected by {actor}: {reason}"[:500]
    if status == IncidentStatus.REMEDIATING:
        back = await _status_before_remediation(incident_id)
        await lifecycle.advance(
            incident_id, back, only_from=[IncidentStatus.REMEDIATING], actor=actor, reason=msg
        )
    elif status == IncidentStatus.VERIFYING:
        await lifecycle.advance(
            incident_id,
            IncidentStatus.NEEDS_HUMAN,
            only_from=[IncidentStatus.VERIFYING],
            actor=actor,
            reason=msg,
        )
    return await _get(run_id)


async def finalize_merged(repo: str, branch: str, merged_by: str) -> str | None:
    """The approved fix PR was merged: incident resolved, root cause recorded, memory written."""
    async with session_scope() as session:
        rows = (await session.execute(select(RemediationRun))).scalars().all()
        run = next((r for r in rows if (r.result or {}).get("branch") == branch), None)
        if run is None:
            return None
        inc = (
            await session.execute(select(IncidentRow).where(IncidentRow.id == run.incident_id))
        ).scalar_one()
        if inc.repo != repo:
            return None
        if run.status != "approved" or not run.approved_by:
            # A merge without a recorded approval of a verified fix never resolves the incident
            session.add(
                AuditLog(
                    actor=merged_by,
                    action="remediation.merged_without_approval",
                    target=run.id,
                    detail={"branch": branch, "status": run.status},
                )
            )
            return None
        run.status = "merged"
        await lifecycle.apply(
            session, inc, IncidentStatus.RESOLVED, actor=merged_by, reason=f"fix merged ({branch})"
        )
        inc.resolved_at = datetime.now(UTC)
        sha = (run.result or {}).get("sha")
        if sha and inc.root_cause_status != "rejected":
            # a verified, approved and merged revert confirms its commit unless a person said no
            inc.confirmed_root_cause = inc.confirmed_root_cause or sha
            inc.root_cause_status = inc.root_cause_status or "confirmed"
        incident_id, run_id = inc.id, run.id
        confirmed = inc.confirmed_root_cause
    store = GraphStore(repo)

    def write() -> None:
        store.graph.query(
            "MATCH (i:Incident {id: $i}) SET i.status = 'resolved', i.resolved_at = $t, "
            "i.confirmed_root_cause = $sha",
            {"i": incident_id, "t": int(datetime.now(UTC).timestamp()), "sha": confirmed or sha},
        )
        store.graph.query("MATCH (f:Fix {id: $f}) SET f.accepted = true", {"f": f"fix-{run_id}"})
        edges = [Edge("Developer", merged_by, "resolved", "Incident", incident_id)]
        if confirmed:
            edges.append(Edge("Incident", incident_id, "confirmed_cause", "Commit", confirmed))
        store.upsert_nodes("Developer", [{"id": merged_by}])
        store.upsert_edges(edges)

    await asyncio.to_thread(write)
    from causaldeploy.memory.writer import write_memory

    await write_memory(repo, incident_id, run_id, merged_by)
    return incident_id
