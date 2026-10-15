"""Revert-first remediation (PLAN section 8): branch + PR, staging verification, human merges."""

import asyncio
import uuid
from typing import Any

from sqlalchemy import select

from causaldeploy.config import get_settings
from causaldeploy.db.models import IncidentRow, RemediationRun
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import Edge, GraphStore
from causaldeploy.investigate.scorer import RankResult, rank_graph
from causaldeploy.remediate.ops import GitHubOps, RemediationOps, RevertConflict, make_revert_branch

BRANCH_PREFIX = "kaizen/revert-"
PASS_TOLERANCE = 0.02  # error rate may exceed the baseline by this much and still pass


def pr_body(result: RankResult, incident_id: str, app_url: str) -> str:
    """The PR description: the commit, its score breakdown and a link back to the incident."""
    top = result.candidates[0]
    lines = [
        f"Kaizen reverts `{top.sha[:7]}` ({top.message or 'no message'}) "
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
    lines += [
        "",
        f"Incident page: {app_url}/incidents/{incident_id}",
        "",
        "Verification runs automatically in an isolated staging environment; "
        "nothing is merged without a human. Production is never touched by Kaizen.",
    ]
    return "\n".join(lines)


async def propose_revert(
    repo: str, incident_id: str, installation_id: int, ops: RemediationOps | None = None
) -> RemediationRun | None:
    """Open a revert PR for the top-ranked commit and start verification (once per incident)."""
    s = get_settings()
    if not s.is_allowed(repo):
        raise PermissionError(f"{repo} is not in CD_ALLOWED_REPOS")
    ops = ops or GitHubOps()
    store = GraphStore(repo)
    result = await asyncio.to_thread(rank_graph, store, incident_id)
    if not result.candidates or result.verdict not in ("confident", "ambiguous"):
        return None  # not a code change we can point at: no automatic action
    sha = result.candidates[0].sha
    branch = f"{BRANCH_PREFIX}{sha[:7]}"

    async with session_scope() as session:
        existing = (
            (
                await session.execute(
                    select(RemediationRun).where(
                        RemediationRun.incident_id == incident_id,
                        RemediationRun.status.not_in(("failed", "needs_manual")),
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing:
            return existing
        run = RemediationRun(
            id=f"rem-{uuid.uuid4().hex[:8]}",
            incident_id=incident_id,
            kind="revert",
            status="proposing",
            result={"sha": sha, "branch": branch},
        )
        session.add(run)
    run_id = run.id

    def make_branch() -> tuple[str, str]:
        with ops.clone(repo, installation_id) as clone:
            base = _default_branch(clone)
            head = make_revert_branch(clone, base, sha, branch)
            ops.push(clone, repo, installation_id, branch)
            return base, head

    try:
        base, head = await asyncio.to_thread(make_branch)
    except RevertConflict as e:
        await _set(run_id, "needs_manual", {"sha": sha, "branch": branch, "error": str(e)})
        return await _get(run_id)
    number, url = await ops.open_pr(
        repo,
        installation_id,
        branch,
        base,
        f"Revert {sha[:7]}: incident {incident_id}",
        pr_body(result, incident_id, s.public_base_url.rstrip("/")),
    )
    await ops.dispatch_verify(repo, installation_id, branch)
    await _set(
        run_id,
        "verifying",
        {
            "sha": sha,
            "branch": branch,
            "head": head,
            "pr_number": number,
            "pr_url": url,
            "base": base,
        },
    )
    await asyncio.to_thread(_write_graph, store, incident_id, run_id, sha, url)
    return await _get(run_id)


def _default_branch(clone: Any) -> str:
    import subprocess

    out = subprocess.run(
        ["git", "-C", str(clone), "symbolic-ref", "--short", "HEAD"], capture_output=True, text=True
    )
    return out.stdout.strip() or "main"


def _write_graph(store: GraphStore, incident_id: str, run_id: str, sha: str, pr_url: str) -> None:
    store.ensure_schema()
    fix = f"fix-{run_id}"
    store.upsert_nodes("Fix", [{"id": fix, "kind": "revert", "pr_url": pr_url, "verified": False}])
    store.upsert_edges(
        [
            Edge("Fix", fix, "reverts", "Commit", sha),
            Edge("Incident", incident_id, "fixed_by", "Fix", fix),
        ]
    )


async def _set(run_id: str, status: str, result: dict[str, Any]) -> None:
    async with session_scope() as session:
        row = await session.get(RemediationRun, run_id)
        assert row is not None
        row.status, row.result = status, result


async def _get(run_id: str) -> RemediationRun:
    async with session_scope() as session:
        row = await session.get(RemediationRun, run_id)
        assert row is not None
        session.expunge(row)
        return row


async def record_verification(
    repo: str, branch: str, conclusion: str, run_url: str, report: dict[str, Any] | None
) -> str | None:
    """Store the staging result for the remediation that owns `branch`. Returns its status."""
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
        before = after = baseline = None
        store = GraphStore(repo)
        props = await asyncio.to_thread(
            store.read,
            "MATCH (i:Incident {id: $id}) RETURN i.error_rate, i.baseline_error_rate",
            {"id": inc.id},
        )
        if props:
            before, baseline = props[0]
        if report:
            after = report.get("error_rate")
        passed = (
            conclusion == "success"
            and after is not None
            and after <= (baseline or 0.0) + PASS_TOLERANCE
        )
        run.status = "verified" if passed else "failed"
        run.result = {
            **(run.result or {}),
            "verification": {
                "passed": passed,
                "conclusion": conclusion,
                "run_url": run_url,
                "error_rate_before": before,
                "error_rate_after": after,
                "baseline_error_rate": baseline,
                "p95_ms": (report or {}).get("p95_ms"),
            },
        }
        status, run_id, incident_id = run.status, run.id, inc.id
    await asyncio.to_thread(
        _write_verification, GraphStore(repo), run_id, passed, run_url, report, incident_id
    )
    return status


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


async def finalize_merged(repo: str, branch: str, merged_by: str) -> str | None:
    """A human merged the revert PR: incident resolved, root cause confirmed."""
    from datetime import UTC, datetime

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
        run.status = "merged"
        inc.status, inc.resolved_at = "resolved", datetime.now(UTC)
        sha = (run.result or {}).get("sha")
        incident_id = inc.id
    store = GraphStore(repo)

    def write() -> None:
        store.graph.query(
            "MATCH (i:Incident {id: $i}) SET i.status = 'resolved', i.resolved_at = $t, "
            "i.confirmed_root_cause = $sha",
            {"i": incident_id, "t": int(datetime.now(UTC).timestamp()), "sha": sha},
        )
        store.upsert_nodes("Developer", [{"id": merged_by}])
        store.upsert_edges([Edge("Developer", merged_by, "resolved", "Incident", incident_id)])

    await asyncio.to_thread(write)
    return incident_id
