import subprocess
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from causaldeploy.api.auth import User, make_session
from causaldeploy.api.main import create_app
from causaldeploy.db.models import AuditLog, IncidentRow, Installation, RemediationRun, Repository
from causaldeploy.db.session import session_scope
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.remediate import ops as ops_mod
from causaldeploy.remediate.ops import RevertConflict, make_revert_branch
from causaldeploy.remediate.service import (
    finalize_merged,
    propose_revert,
    record_verification,
)
from causaldeploy.verify.verdict import recompute


def git(d: Path, *a: str) -> str:
    return subprocess.run(
        ["git", "-C", str(d), "-c", "user.name=t", "-c", "user.email=t@t.t", *a],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> tuple[Path, str]:
    """A bare 'GitHub' remote with a history whose HEAD~0 is the culprit."""
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    (work / "a.js").write_text("function a() {\n  return 1;\n}\n")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "init")
    (work / "a.js").write_text("function a() {\n  return null.x;\n}\n")
    git(work, "commit", "-qam", "break a")
    culprit = git(work, "rev-parse", "HEAD")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return bare, culprit


class FakeOps:
    """Records every GitHub side effect; git operations go to a local bare remote."""

    def __init__(self, bare: Path, tmp: Path) -> None:
        self.bare, self.tmp = bare, tmp
        self.prs: list[dict[str, Any]] = []
        self.dispatched: list[str] = []
        self.pushed: list[str] = []
        self.closed: list[dict[str, Any]] = []
        self.merged: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self._n = 0

    @contextmanager
    def clone(self, repo: str, installation_id: int):  # type: ignore[no-untyped-def]
        self._n += 1
        dest = self.tmp / f"clone{self._n}"
        subprocess.run(["git", "clone", "-q", str(self.bare), str(dest)], check=True)
        yield dest

    def push(self, clone: Path, repo: str, installation_id: int, branch: str) -> None:
        assert branch not in ("main", "master")
        git(clone, "push", "-q", "origin", f"{branch}:{branch}")
        self.pushed.append(branch)

    async def open_pr(self, repo, installation_id, head, base, title, body, labels):  # type: ignore[no-untyped-def]
        self.prs.append(
            {"head": head, "base": base, "title": title, "body": body, "labels": labels}
        )
        return 7 + len(self.prs) - 1, f"https://github.com/{repo}/pull/{7 + len(self.prs) - 1}"

    async def close_pr(self, repo, installation_id, number, comment):  # type: ignore[no-untyped-def]
        self.closed.append({"number": number, "comment": comment})

    async def merge_pr(self, repo, installation_id, number, sha):  # type: ignore[no-untyped-def]
        self.merged.append({"number": number, "sha": sha})

    async def post_check(self, repo, installation_id, head_sha, passed, details_url, summary):  # type: ignore[no-untyped-def]
        self.checks.append(
            {"head": head_sha, "passed": passed, "url": details_url, "summary": summary}
        )

    async def dispatch_verify(self, repo, installation_id, branch):  # type: ignore[no-untyped-def]
        self.dispatched.append(branch)

    async def fetch_report(self, repo, installation_id, run_id):  # type: ignore[no-untyped-def]
        return None


async def seed(builder: GraphBuilder, culprit: str) -> str:
    sc = builder.s1_payment_validation()
    # the culprit commit of the graph fixture is the real commit in the remote
    s = builder.store
    s.graph.query(
        "MATCH (c:Commit {sha: $o}) SET c.sha = $n", {"o": sc.culprit_commit, "n": culprit}
    )
    now = datetime.now(UTC)
    async with session_scope() as session:
        session.add(Installation(id=1, account="acme"))
        await session.flush()
        session.add(Repository(full_name=s.repo, installation_id=1))
        await session.flush()
        session.add(
            IncidentRow(
                id=sc.incident_id,
                repo=s.repo,
                service="payments",
                env="prod",
                endpoint="POST /payments",
                status="identified",  # ranked: a suspect is named, so a fix can be proposed
                onset_ts=now,
                detected_at=now,
            )
        )
    return sc.incident_id


async def incident_status(inc: str) -> str:
    async with session_scope() as s:
        row = await s.get(IncidentRow, inc)
        assert row is not None
        return row.status


async def audit_actions() -> list[str]:
    async with session_scope() as s:
        return [a.action for a in (await s.execute(select(AuditLog))).scalars().all()]


def test_revert_branch_undoes_the_commit_and_never_touches_main(
    remote: tuple[Path, str], tmp_path: Path
) -> None:
    bare, culprit = remote
    clone = tmp_path / "c"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    head = make_revert_branch(clone, "main", culprit, "kaizen/fix-inc-1")
    assert git(clone, "show", f"{head}:a.js") == "function a() {\n  return 1;\n}"
    assert git(clone, "rev-parse", "main") == culprit  # base branch untouched


def test_revert_conflict_is_reported_not_forced(remote: tuple[Path, str], tmp_path: Path) -> None:
    bare, culprit = remote
    clone = tmp_path / "c"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    (clone / "a.js").write_text("function a() {\n  return 2;\n}\n")  # later edit on top
    git(clone, "commit", "-qam", "follow-up")
    with pytest.raises(RevertConflict):
        make_revert_branch(clone, "main", culprit, "kaizen/fix-x")
    assert git(clone, "status", "--porcelain") == ""  # revert was aborted cleanly


def _spy_git(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    real = ops_mod._git

    def spy(cwd: Path, *args: str, token: str | None = None) -> str:
        calls.append(args)
        return real(cwd, *args, token=token)

    monkeypatch.setattr(ops_mod, "_git", spy)
    return calls


def test_merge_commit_is_reverted_against_its_mainline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "m"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.js").write_text("ok\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.js").write_text("broken\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "feature work")
    git(repo, "checkout", "-q", "main")
    (repo / "c.js").write_text("unrelated\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "main moves on")
    git(repo, "merge", "-q", "--no-ff", "feature", "-m", "Merge pull request #5")
    merge = git(repo, "rev-parse", "HEAD")
    assert len(git(repo, "rev-list", "--parents", "-n", "1", merge).split()) == 3

    calls = _spy_git(monkeypatch)
    head = make_revert_branch(repo, "main", merge, "kaizen/fix-inc-m")
    revert_call = next(c for c in calls if c[0] == "revert")
    assert revert_call == ("revert", "--no-edit", "-m", "1", merge)
    files = git(repo, "ls-tree", "--name-only", head).split()
    assert "b.js" not in files and "c.js" in files  # the merged branch's change is undone only


def test_squash_merge_is_reverted_as_a_single_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "s"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.js").write_text("ok\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    git(repo, "checkout", "-q", "-b", "feature")
    for i in range(3):
        (repo / "a.js").write_text(f"step {i}\n")
        git(repo, "commit", "-qam", f"wip {i}")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--squash", "feature")
    git(repo, "commit", "-qm", "Feature (#6)")
    squash = git(repo, "rev-parse", "HEAD")

    calls = _spy_git(monkeypatch)
    head = make_revert_branch(repo, "main", squash, "kaizen/fix-inc-s")
    revert_call = next(c for c in calls if c[0] == "revert")
    assert revert_call == ("revert", "--no-edit", squash)  # no -m for a single-parent commit
    assert git(repo, "show", f"{head}:a.js") == "ok"


async def test_full_remediation_flow(
    db: None, builder: GraphBuilder, remote: tuple[Path, str], tmp_path: Path
) -> None:
    bare, culprit = remote
    inc = await seed(builder, culprit)
    ops = FakeOps(bare, tmp_path)

    run = await propose_revert(builder.store.repo, inc, 1, ops)
    assert run is not None and run.status == "verifying"
    branch = run.result["branch"]  # type: ignore[index]
    assert branch == f"kaizen/fix-{inc}"
    assert ops.pushed == [branch] and ops.dispatched == [branch]
    assert ops.prs[0]["base"] == "main" and "temporal" in ops.prs[0]["body"]
    assert ops.prs[0]["labels"] == ["kaizen", f"incident:{inc}"]
    r = run.result or {}
    assert "-  return null.x;" in r["diff"] and "+  return 1;" in r["diff"]
    assert r["labels"] == ["kaizen", f"incident:{inc}"] and culprit[:7] in r["rationale"]
    assert await incident_status(inc) == "verifying"
    again = await propose_revert(builder.store.repo, inc, 1, ops)  # idempotent per incident
    assert again is not None and again.id == run.id and len(ops.prs) == 1
    fix = builder.store.read(
        "MATCH (:Incident {id: $i})-[:fixed_by]->(f:Fix)-[:reverts]->(c:Commit) RETURN f.id, c.sha",
        {"i": inc},
    )
    assert fix == [[f"fix-{run.id}", culprit]]

    # staging says: healthy again; the check run is posted on the fix head
    builder.store.graph.query(
        "MATCH (i:Incident {id: $i}) SET i.error_rate = 0.7, i.baseline_error_rate = 0.02",
        {"i": inc},
    )
    report = {
        "error_rate": 0.01,
        "p95_ms": 90,
        "requests": 200,
        "baseline": {"error_rate": 0.02, "p95_ms": 80},
        "tests": [{"name": "payments e2e", "passed": True}, {"name": "unit", "passed": True}],
    }
    status = await record_verification(
        builder.store.repo, branch, "success", "https://run/1", report, ops=ops, installation_id=1
    )
    assert status == "verified"
    assert ops.checks == [
        {
            "head": r["head"],
            "passed": True,
            "url": "https://run/1",
            "summary": ops.checks[0]["summary"],
        }
    ]
    async with session_scope() as s:
        rem = (await s.execute(select(RemediationRun))).scalar_one()
    v = rem.result["verification"]  # type: ignore[index]
    assert v["passed"] is True and v["error_rate_after"] == 0.01 and v["error_rate_before"] == 0.7
    assert v["metrics"]["baseline"]["p95_ms"] == 80 and v["check"]["conclusion"] == "success"
    assert recompute(v).model_dump() == v["verdict"]  # the stored inputs reproduce the verdict
    assert builder.store.read(
        "MATCH (f:Fix {id: $f}) RETURN f.verified", {"f": f"fix-{run.id}"}
    ) == [[True]]

    # merging before anyone approved never resolves the incident
    assert await finalize_merged(builder.store.repo, branch, "yash") is None
    assert await incident_status(inc) == "verifying"
    assert "remediation.merged_without_approval" in await audit_actions()
    async with session_scope() as s:
        rem = (await s.execute(select(RemediationRun))).scalar_one()
        rem.status, rem.approved_by = "approved", "yash"  # what POST .../approve records

    # a human merges the approved fix
    assert await finalize_merged(builder.store.repo, branch, "yash") == inc
    async with session_scope() as s:
        row = (await s.execute(select(IncidentRow))).scalar_one()
    assert row.status == "resolved" and row.confirmed_root_cause == culprit
    assert builder.store.read(
        "MATCH (:Developer {id: 'yash'})-[:resolved]->(i:Incident {id: $i}) RETURN i.confirmed_root_cause",
        {"i": inc},
    ) == [[culprit]]

    # memory: snapshot with timings, and the Incident -> Commit <- Fix -> Test chain
    mem = row.memory or {}
    assert mem["fix"]["sha"] == culprit and mem["fix"]["approved_by"] == "yash"
    assert mem["candidates"][0]["sha"] == culprit and mem["verification"]["passed"] is True
    assert mem["ttd_s"] == 0 and mem["ttr_s"] is not None and mem["ttr_s"] >= 0
    chain = builder.store.read(
        "MATCH (i:Incident {id: $i})-[:confirmed_cause]->(c:Commit)<-[:reverts]-(f:Fix)"
        "-[:verified_by]->(t:Test), (i)-[:fixed_by]->(f) "
        "RETURN c.sha, f.accepted, t.name ORDER BY t.name",
        {"i": inc},
    )
    assert chain == [[culprit, True, "payments e2e"], [culprit, True, "unit"]]


async def test_failed_verification_never_resolves_and_escalates(
    db: None, builder: GraphBuilder, remote: tuple[Path, str], tmp_path: Path
) -> None:
    bare, culprit = remote
    inc = await seed(builder, culprit)
    ops = FakeOps(bare, tmp_path)
    run = await propose_revert(builder.store.repo, inc, 1, ops)
    assert run is not None
    builder.store.graph.query(
        "MATCH (i:Incident {id: $i}) SET i.error_rate = 0.7, i.baseline_error_rate = 0.02",
        {"i": inc},
    )
    still_broken = await record_verification(
        builder.store.repo,
        run.result["branch"],  # type: ignore[index]
        "success",
        "https://run/2",
        {"error_rate": 0.5},
        ops=ops,
        installation_id=1,
    )
    assert still_broken == "failed"  # workflow green but errors still above baseline
    assert ops.checks[0]["passed"] is False and "error rate" in ops.checks[0]["summary"]
    # nothing left after the revert (no patch support): escalate to a person
    assert await incident_status(inc) == "needs_human"
    assert "remediation.escalated" in await audit_actions()
    # even a forged approval of a failed fix cannot resolve via a merge of anything else
    assert await finalize_merged(builder.store.repo, run.result["branch"], "x") is None  # type: ignore[index]
    assert await incident_status(inc) == "needs_human"


async def test_revert_conflict_escalates(
    db: None, builder: GraphBuilder, remote: tuple[Path, str], tmp_path: Path
) -> None:
    bare, culprit = remote
    work = tmp_path / "w2"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    (work / "a.js").write_text("function a() {\n  return 2;\n}\n")
    git(work, "commit", "-qam", "follow-up on top of the culprit")
    git(work, "push", "-q", "origin", "main")
    inc = await seed(builder, culprit)
    ops = FakeOps(bare, tmp_path)
    run = await propose_revert(builder.store.repo, inc, 1, ops)
    assert run is not None and run.status == "needs_manual" and ops.prs == []
    assert await incident_status(inc) == "needs_human"
    assert "remediation.escalated" in await audit_actions()


async def test_no_pr_when_nothing_ties_the_change_to_the_failure(
    db: None, builder: GraphBuilder, remote: tuple[Path, str], tmp_path: Path
) -> None:
    from causaldeploy.graph.store import Edge

    bare, culprit = remote
    inc = await seed(builder, culprit)
    builder.store.delete_edges("Deployment", "dep-842", "includes")
    builder.store.upsert_edges([Edge("Deployment", "dep-842", "includes", "Commit", "n0000001")])
    ops = FakeOps(bare, tmp_path)
    assert await propose_revert(builder.store.repo, inc, 1, ops) is None
    assert ops.prs == []


async def test_propose_endpoint_is_approver_only_and_csrf_protected(
    db: None, builder: GraphBuilder, remote: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    monkeypatch.setattr("causaldeploy.jobs.queue.get_pool", AsyncMock(return_value=AsyncMock()))
    inc = await seed(builder, remote[1])
    repo = builder.store.repo
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        c.cookies.set("cd_csrf", "t")
        c.cookies.set("cd_session", make_session(User(login="v", repos={repo: "viewer"})))  # type: ignore[arg-type]
        assert (
            await c.post(f"/api/v1/incidents/{inc}/remediations", headers={"x-csrf-token": "t"})
        ).status_code == 403
        c.cookies.set("cd_session", make_session(User(login="a", repos={repo: "approver"})))  # type: ignore[arg-type]
        assert (await c.post(f"/api/v1/incidents/{inc}/remediations")).status_code == 403  # no CSRF
        ok = await c.post(f"/api/v1/incidents/{inc}/remediations", headers={"x-csrf-token": "t"})
        assert ok.status_code == 202 and ok.json()["status"] == "queued"


async def test_workflow_and_merge_webhooks_enqueue_verification_and_finalize(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import hmac
    import json
    from unittest.mock import AsyncMock

    from causaldeploy.db.models import JobRecord

    monkeypatch.setattr("causaldeploy.jobs.queue.get_pool", AsyncMock(return_value=AsyncMock()))

    def sign(body: bytes) -> str:
        return "sha256=" + hmac.new(b"whsec-test", body, hashlib.sha256).hexdigest()

    def verify(run_id: int, branch: str) -> dict[str, Any]:
        return {
            "action": "completed",
            "repository": {"full_name": "acme/shop"},
            "installation": {"id": 9},
            "workflow_run": {
                "id": run_id,
                "name": "verify",
                "display_title": f"verify {branch}",
                "conclusion": "success",
                "html_url": f"https://github.com/acme/shop/actions/runs/{run_id}",
            },
        }

    def merged(number: int, ref: str) -> dict[str, Any]:
        return {
            "action": "closed",
            "repository": {"full_name": "acme/shop"},
            "pull_request": {
                "number": number,
                "merged": True,
                "head": {"ref": ref},
                "merged_by": {"login": "yash"},
            },
        }

    deliveries = [
        ("workflow_run", verify(55, "kaizen/revert-abc1234")),  # legacy branch name
        ("workflow_run", verify(56, "kaizen/fix-inc-1")),
        ("pull_request", merged(7, "kaizen/fix-inc-1")),
        ("pull_request", merged(8, "kaizen/revert-abc1234")),
        ("pull_request", merged(9, "feature/x")),
        ("workflow_run", verify(57, "feature/x")),
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        for n, (event, payload) in enumerate(deliveries):
            body = json.dumps(payload).encode()
            r = await c.post(
                "/webhooks/github",
                content=body,
                headers={
                    "x-github-event": event,
                    "x-github-delivery": f"d{n}",
                    "x-hub-signature-256": sign(body),
                },
            )
            assert r.status_code == 202
    async with session_scope() as s:
        jobs = (
            (await s.execute(select(JobRecord).where(JobRecord.kind != "ingest_event")))
            .scalars()
            .all()
        )
    verify_jobs = sorted(j.payload["branch"] for j in jobs if j.kind == "verify_result")
    merged_jobs = sorted(j.payload["branch"] for j in jobs if j.kind == "remediation_merged")
    assert verify_jobs == ["kaizen/fix-inc-1", "kaizen/revert-abc1234"]
    assert merged_jobs == ["kaizen/fix-inc-1", "kaizen/revert-abc1234"]
    v55 = next(j.payload for j in jobs if j.payload.get("run_id") == 55)
    assert v55["installation_id"] == 9
    assert {j.kind for j in jobs} == {"verify_result", "remediation_merged"}
