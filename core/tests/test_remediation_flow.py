"""#91 remediation flow: verification events + check runs, approval/merge policy, rejection,
the remediation view, and the patch -> revert -> escalate chain."""

import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from causaldeploy.api.auth import User, make_session
from causaldeploy.api.main import create_app
from causaldeploy.config import get_settings
from causaldeploy.contracts.domain import IncidentStatus
from causaldeploy.db.models import AuditLog, RemediationRun
from causaldeploy.db.session import session_scope
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.incidents import lifecycle
from causaldeploy.jobs import handlers
from causaldeploy.remediate import service
from causaldeploy.remediate.patch import PatchProposal, PatchRequest
from tests.test_remediate import FakeOps, audit_actions, git, incident_status, remote, seed

__all__ = ["remote"]  # fixture re-export


@pytest_asyncio.fixture
async def client(db: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr("causaldeploy.jobs.queue.get_pool", AsyncMock(return_value=AsyncMock()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        yield c


def login(c: httpx.AsyncClient, login: str, repo: str, role: str) -> dict[str, str]:
    c.cookies.set("cd_session", make_session(User(login=login, repos={repo: role})))  # type: ignore[arg-type]
    c.cookies.set("cd_csrf", "t")
    return {"x-csrf-token": "t"}


async def verifying(
    builder: GraphBuilder, remote: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, RemediationRun, FakeOps]:
    bare, culprit = remote
    inc = await seed(builder, culprit)
    ops = FakeOps(bare, tmp_path)
    monkeypatch.setattr(service, "make_ops", lambda: ops)
    run = await service.propose_revert(builder.store.repo, inc, 1, ops)
    assert run is not None and run.status == "verifying"
    return inc, run, ops


def event(repo: str, run_id: str, inc: str, **kw: Any) -> dict[str, Any]:
    return {
        "type": "verification_result",
        "repo": repo,
        "remediation_id": run_id,
        "incident_id": inc,
        "passed": True,
        "tests_passed": True,
        "error_rate_before": 0.7,
        "error_rate_after": 0.0,
        "baseline_error_rate": 0.01,
        "ts": datetime.now(UTC).isoformat(),
        "details_url": "https://ci/run/1",
        **kw,
    }


async def run_status(run_id: str) -> str:
    async with session_scope() as s:
        row = await s.get(RemediationRun, run_id)
        assert row is not None
        return row.status


async def test_verification_result_event_is_routed_and_posts_a_check(
    db: None,
    builder: GraphBuilder,
    remote: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inc, run, ops = await verifying(builder, remote, tmp_path, monkeypatch)
    repo = builder.store.repo
    # another repository's key cannot verify this fix; a mismatched incident is ignored too
    await handlers.ingest_event({"event": event("other/repo", run.id, inc)})
    await handlers.ingest_event({"event": event(repo, run.id, "inc-other")})
    assert await run_status(run.id) == "verifying" and ops.checks == []

    await handlers.ingest_event({"event": event(repo, run.id, inc)})
    assert await run_status(run.id) == "verified"
    head = (run.result or {})["head"]
    assert ops.checks == [
        {
            "head": head,
            "passed": True,
            "url": "https://ci/run/1",
            "summary": ops.checks[0]["summary"],
        }
    ]
    async with session_scope() as s:
        row = await s.get(RemediationRun, run.id)
        assert row is not None and row.result is not None
    v = row.result["verification"]
    assert v["metrics"]["fixed"]["error_rate"] == 0.0 and v["check"]["posted"] is True
    assert await incident_status(inc) == "verifying"  # waits for approval + merge


async def test_failed_verification_event_escalates(
    db: None,
    builder: GraphBuilder,
    remote: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inc, run, ops = await verifying(builder, remote, tmp_path, monkeypatch)
    await handlers.ingest_event(
        {"event": event(builder.store.repo, run.id, inc, passed=False, error_rate_after=0.4)}
    )
    assert await run_status(run.id) == "failed" and ops.checks[0]["passed"] is False
    assert await incident_status(inc) == "needs_human"
    assert "remediation.escalated" in await audit_actions()


async def test_app_merge_policy_merges_only_after_approval(
    client: httpx.AsyncClient,
    builder: GraphBuilder,
    remote: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "remediation_merge_policy", "app")
    inc, run, ops = await verifying(builder, remote, tmp_path, monkeypatch)
    repo = builder.store.repo
    h = login(client, "ann", repo, "approver")

    # not verified yet: no approval, and the merge path refuses without one
    assert (
        await client.post(f"/api/v1/remediations/{run.id}/approve", headers=h)
    ).status_code == 409
    with pytest.raises(service.RemediationStateError):
        await service.merge_approved(run.id, ops)
    assert ops.merged == []

    await handlers.ingest_event({"event": event(repo, run.id, inc)})
    r = await client.post(f"/api/v1/remediations/{run.id}/approve", headers=h)
    assert r.status_code == 200 and r.json() == {
        "id": run.id,
        "status": "approved",
        "approved_by": "ann",
        "merge": "requested",
    }
    head = (run.result or {})["head"]
    assert ops.merged == [{"number": 7, "sha": head}]
    assert {"remediation.approve", "remediation.app_merge"} <= set(await audit_actions())
    # GitHub then sends the merge webhook; the approval on record resolves the incident
    branch = (run.result or {})["branch"]
    assert await service.finalize_merged(repo, branch, "kaizen[bot]") == inc
    assert await incident_status(inc) == "resolved"


async def test_manual_policy_never_merges(
    client: httpx.AsyncClient,
    builder: GraphBuilder,
    remote: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inc, run, ops = await verifying(builder, remote, tmp_path, monkeypatch)
    await handlers.ingest_event({"event": event(builder.store.repo, run.id, inc)})
    h = login(client, "ann", builder.store.repo, "approver")
    r = await client.post(f"/api/v1/remediations/{run.id}/approve", headers=h)
    assert r.status_code == 200 and r.json()["merge"] == "manual" and ops.merged == []


async def test_reject_endpoint_closes_the_pr_and_hands_back_to_a_human(
    client: httpx.AsyncClient,
    builder: GraphBuilder,
    remote: tuple[Path, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inc, run, ops = await verifying(builder, remote, tmp_path, monkeypatch)
    repo = builder.store.repo
    await handlers.ingest_event({"event": event(repo, run.id, inc)})
    url = f"/api/v1/remediations/{run.id}/reject"

    h = login(client, "vic", repo, "viewer")
    assert (await client.post(url, json={"reason": "no"}, headers=h)).status_code == 403
    login(client, "ann", repo, "approver")
    assert (await client.post(url, json={"reason": "no"})).status_code == 403  # CSRF
    h = login(client, "ann", repo, "approver")
    assert (await client.post(url, json={"reason": ""}, headers=h)).status_code == 422
    login(client, "mallory", "other/repo", "approver")
    assert (
        await client.post(url, json={"reason": "x"}, headers={"x-csrf-token": "t"})
    ).status_code == 404

    h = login(client, "ann", repo, "approver")
    r = await client.post(url, json={"reason": "breaks refunds"}, headers=h)
    assert r.status_code == 200
    assert r.json() == {
        "id": run.id,
        "status": "rejected",
        "rejected_by": "ann",
        "reject_reason": "breaks refunds",
        "incident_status": "needs_human",
    }
    assert ops.closed == [{"number": 7, "comment": "Kaizen fix rejected by @ann: breaks refunds"}]
    assert builder.store.read(
        "MATCH (f:Fix {id: $f}) RETURN f.accepted", {"f": f"fix-{run.id}"}
    ) == [[False]]
    async with session_scope() as s:
        log = (
            await s.execute(select(AuditLog).where(AuditLog.action == "remediation.reject"))
        ).scalar_one()
    assert log.actor == "ann" and log.detail["reason"] == "breaks refunds"
    assert (await client.post(url, json={"reason": "again"}, headers=h)).status_code == 409
    # a rejected fix cannot be approved
    assert (
        await client.post(f"/api/v1/remediations/{run.id}/approve", headers=h)
    ).status_code == 409

    # the incident page shows the whole remediation record
    detail = (await client.get(f"/api/v1/incidents/{inc}")).json()["remediation"]
    assert detail["kind"] == "revert" and detail["status"] == "rejected"
    assert detail["rejected_by"] == "ann" and detail["reject_reason"] == "breaks refunds"
    assert detail["labels"] == ["kaizen", f"incident:{inc}"] and detail["pr_number"] == 7
    assert "+  return 1;" in detail["diff"] and detail["rationale"].startswith("Revert")
    assert detail["check_status"] == "success"
    v = detail["verification"]
    assert v["metrics"]["baseline"]["error_rate"] == 0.01 and v["verdict"]["passed"] is True


async def test_rejecting_a_fix_in_preparation_returns_to_the_diagnosis(
    db: None, builder: GraphBuilder, remote: tuple[Path, str]
) -> None:
    inc = await seed(builder, remote[1])
    await lifecycle.transition(inc, IncidentStatus.REMEDIATING, reason="test")
    async with session_scope() as s:
        s.add(RemediationRun(id="rem-p", incident_id=inc, kind="revert", status="proposing"))
    run = await service.reject_remediation("rem-p", "ann", "wrong commit")
    assert run.status == "rejected" and run.rejected_by == "ann"
    assert await incident_status(inc) == "identified"  # can be re-proposed


# -- patch -> revert -> escalate -----------------------------------------------------------


class FakePatchClient:
    def __init__(self, proposal: PatchProposal) -> None:
        self.proposal = proposal
        self.requests: list[PatchRequest] = []

    async def propose(self, request: PatchRequest) -> PatchProposal:
        self.requests.append(request)
        return self.proposal


def _patch(new_line: str, path: str = "a.js") -> str:
    return (
        f"--- a/{path}\n+++ b/{path}\n@@ -1,3 +1,3 @@\n function a() {{\n"
        f"-  return null.x;\n+  {new_line}\n }}\n"
    )


GOOD = PatchProposal(
    diff=_patch("return null?.x ?? 1;"),
    test_path="tests/a.test.js",
    test_code="// regression for incident\n",
    rationale="guard the null access",
)


@pytest.fixture
def patch_repo(
    remote: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str]:
    """The remote gets a unit test command that passes only when a.js no longer throws."""
    bare, culprit = remote
    work = tmp_path / "addcheck"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    (work / "check.py").write_text(
        "import sys\nsrc = open('a.js').read()\nsys.exit(0 if 'null.' not in src else 1)\n"
    )
    git(work, "add", "-A")
    git(work, "commit", "-qm", "add unit test command")
    git(work, "push", "-q", "origin", "main")
    s = get_settings()
    monkeypatch.setattr(s, "remediation_patch_enabled", True)
    monkeypatch.setattr(
        s, "remediation_test_command", f'"{sys.executable.replace(chr(92), "/")}" check.py'
    )
    return bare, culprit


async def test_patch_is_tested_in_a_worktree_and_proposed(
    db: None, builder: GraphBuilder, patch_repo: tuple[Path, str], tmp_path: Path
) -> None:
    bare, culprit = patch_repo
    inc = await seed(builder, culprit)
    ops, llm = FakeOps(bare, tmp_path), FakePatchClient(GOOD)
    run = await service.propose_fix(builder.store.repo, inc, 1, ops, llm)
    assert run is not None and run.kind == "patch" and run.status == "verifying"
    r = run.result or {}
    assert r["branch"] == f"kaizen/fix-{inc}" and ops.prs[0]["title"].startswith("Patch")
    assert "+  return null?.x ?? 1;" in r["diff"] and "tests/a.test.js" in r["diff"]
    assert r["rationale"] == "guard the null access"
    req = llm.requests[0]
    assert req.allowed_files == ["a.js"] and "-  return 1;" in req.culprit_diff
    assert builder.store.read(
        "MATCH (:Incident {id: $i})-[:fixed_by]->(:Fix {kind: 'patch'})-[:patches]->(c:Commit) "
        "RETURN c.sha",
        {"i": inc},
    ) == [[culprit]]


@pytest.mark.parametrize(
    ("proposal", "why"),
    [
        (PatchProposal(diff=_patch("return null.y;")), "tests failed"),
        (PatchProposal(diff=_patch("return 1;", path="b.js")), "outside the culprit"),
        (
            PatchProposal(diff="--- a/a.js\n+++ b/a.js\n@@ -9,1 +9,1 @@\n-nope\n+x\n"),
            "does not apply",
        ),
        (PatchProposal(diff=_patch("return 1;" + "\n+ //" * 200)), "changed lines"),
    ],
)
async def test_a_rejected_patch_falls_back_to_the_revert(
    db: None,
    builder: GraphBuilder,
    patch_repo: tuple[Path, str],
    tmp_path: Path,
    proposal: PatchProposal,
    why: str,
) -> None:
    bare, culprit = patch_repo
    inc = await seed(builder, culprit)
    ops = FakeOps(bare, tmp_path)
    run = await service.propose_fix(builder.store.repo, inc, 1, ops, FakePatchClient(proposal))
    assert run is not None and run.kind == "revert" and run.status == "verifying"
    assert (run.result or {})["branch"] == f"kaizen/fix-{inc}-2"
    assert [p["title"][:6] for p in ops.prs] == ["Revert"]  # the bad patch never reached GitHub
    async with session_scope() as s:
        patch_run = (
            await s.execute(select(RemediationRun).where(RemediationRun.kind == "patch"))
        ).scalar_one()
    assert patch_run.status == "rejected" and patch_run.rejected_by == "kaizen"
    assert why in (patch_run.reject_reason or "")
    assert "remediation.patch_rejected" in await audit_actions()
    branches = git(bare, "branch", "--list")
    assert f"kaizen/fix-{inc}-2" in branches and f"kaizen/fix-{inc}\n" not in branches + "\n"


async def test_patch_failing_verification_falls_back_then_escalates(
    db: None, builder: GraphBuilder, patch_repo: tuple[Path, str], tmp_path: Path
) -> None:
    bare, culprit = patch_repo
    inc = await seed(builder, culprit)
    repo = builder.store.repo
    ops = FakeOps(bare, tmp_path)
    patch_run = await service.propose_fix(repo, inc, 1, ops, FakePatchClient(GOOD))
    assert patch_run is not None and patch_run.kind == "patch"
    bad = {"error_rate": 0.3}
    status = await service.record_verification(
        repo, (patch_run.result or {})["branch"], "success", "u", bad, ops=ops, installation_id=1
    )
    assert status == "failed"
    assert ops.closed[0]["number"] == 7  # the patch PR is closed ...
    async with session_scope() as s:
        revert = (
            await s.execute(select(RemediationRun).where(RemediationRun.kind == "revert"))
        ).scalar_one()
    assert revert.status == "verifying"  # ... and the revert is next in the chain
    assert await incident_status(inc) == "verifying"
    # the revert fails too: nothing left, a person takes over
    await service.record_verification(
        repo, (revert.result or {})["branch"], "success", "u", bad, ops=ops, installation_id=1
    )
    assert await incident_status(inc) == "needs_human"
    assert "remediation.escalated" in await audit_actions()


async def test_patching_is_off_by_default(
    db: None, builder: GraphBuilder, remote: tuple[Path, str], tmp_path: Path
) -> None:
    from causaldeploy.remediate.patch import default_patch_client

    assert get_settings().remediation_patch_enabled is False and default_patch_client() is None
    inc = await seed(builder, remote[1])
    llm = FakePatchClient(GOOD)
    run = await service.propose_fix(builder.store.repo, inc, 1, FakeOps(remote[0], tmp_path), llm)
    assert run is not None and run.kind == "revert" and llm.requests == []
