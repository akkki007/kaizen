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
from causaldeploy.db.models import IncidentRow, Installation, RemediationRun, Repository
from causaldeploy.db.session import session_scope
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.remediate.ops import RevertConflict, make_revert_branch
from causaldeploy.remediate.service import (
    finalize_merged,
    propose_revert,
    record_verification,
)


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
    def __init__(self, bare: Path, tmp: Path) -> None:
        self.bare, self.tmp = bare, tmp
        self.prs: list[dict[str, Any]] = []
        self.dispatched: list[str] = []
        self.pushed: list[str] = []

    @contextmanager
    def clone(self, repo: str, installation_id: int):  # type: ignore[no-untyped-def]
        dest = self.tmp / "clone"
        subprocess.run(["git", "clone", "-q", str(self.bare), str(dest)], check=True)
        yield dest

    def push(self, clone: Path, repo: str, installation_id: int, branch: str) -> None:
        assert branch not in ("main", "master")
        git(clone, "push", "-q", "origin", f"{branch}:{branch}")
        self.pushed.append(branch)

    async def open_pr(self, repo, installation_id, head, base, title, body):  # type: ignore[no-untyped-def]
        self.prs.append({"head": head, "base": base, "title": title, "body": body})
        return 7, f"https://github.com/{repo}/pull/7"

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
                status="investigating",
                onset_ts=now,
                detected_at=now,
            )
        )
    return sc.incident_id


def test_revert_branch_undoes_the_commit_and_never_touches_main(
    remote: tuple[Path, str], tmp_path: Path
) -> None:
    bare, culprit = remote
    clone = tmp_path / "c"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    head = make_revert_branch(clone, "main", culprit, "kaizen/revert-abc1234")
    assert git(clone, "show", f"{head}:a.js") == "function a() {\n  return 1;\n}"
    assert git(clone, "rev-parse", "main") == culprit  # base branch untouched


def test_revert_conflict_is_reported_not_forced(remote: tuple[Path, str], tmp_path: Path) -> None:
    bare, culprit = remote
    clone = tmp_path / "c"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    (clone / "a.js").write_text("function a() {\n  return 2;\n}\n")  # later edit on top
    git(clone, "commit", "-qam", "follow-up")
    with pytest.raises(RevertConflict):
        make_revert_branch(clone, "main", culprit, "kaizen/revert-x")
    assert git(clone, "status", "--porcelain") == ""  # revert was aborted cleanly


async def test_full_remediation_flow(
    db: None, builder: GraphBuilder, remote: tuple[Path, str], tmp_path: Path
) -> None:
    bare, culprit = remote
    inc = await seed(builder, culprit)
    ops = FakeOps(bare, tmp_path)

    run = await propose_revert(builder.store.repo, inc, 1, ops)
    assert run is not None and run.status == "verifying"
    branch = run.result["branch"]  # type: ignore[index]
    assert branch == f"kaizen/revert-{culprit[:7]}"
    assert ops.pushed == [branch] and ops.dispatched == [branch]
    assert ops.prs[0]["base"] == "main" and "temporal" in ops.prs[0]["body"]
    again = await propose_revert(builder.store.repo, inc, 1, ops)  # idempotent per incident
    assert again is not None and again.id == run.id and len(ops.prs) == 1
    fix = builder.store.read(
        "MATCH (:Incident {id: $i})-[:fixed_by]->(f:Fix)-[:reverts]->(c:Commit) RETURN f.id, c.sha",
        {"i": inc},
    )
    assert fix == [[f"fix-{run.id}", culprit]]

    # staging says: healthy again
    builder.store.graph.query(
        "MATCH (i:Incident {id: $i}) SET i.error_rate = 0.7, i.baseline_error_rate = 0.02",
        {"i": inc},
    )
    status = await record_verification(
        builder.store.repo, branch, "success", "https://run/1", {"error_rate": 0.01, "p95_ms": 90}
    )
    assert status == "verified"
    async with session_scope() as s:
        rem = (await s.execute(select(RemediationRun))).scalar_one()
    v = rem.result["verification"]  # type: ignore[index]
    assert v["passed"] is True and v["error_rate_after"] == 0.01 and v["error_rate_before"] == 0.7
    assert builder.store.read(
        "MATCH (f:Fix {id: $f}) RETURN f.verified", {"f": f"fix-{run.id}"}
    ) == [[True]]

    # a human merges
    assert await finalize_merged(builder.store.repo, branch, "yash") == inc
    async with session_scope() as s:
        row = (await s.execute(select(IncidentRow))).scalar_one()
    assert row.status == "resolved"
    assert builder.store.read(
        "MATCH (:Developer {id: 'yash'})-[:resolved]->(i:Incident {id: $i}) RETURN i.confirmed_root_cause",
        {"i": inc},
    ) == [[culprit]]


async def test_verification_failure_is_recorded_and_not_approvable(
    db: None, builder: GraphBuilder, remote: tuple[Path, str], tmp_path: Path
) -> None:
    bare, culprit = remote
    inc = await seed(builder, culprit)
    run = await propose_revert(builder.store.repo, inc, 1, FakeOps(bare, tmp_path))
    assert run is not None
    builder.store.graph.query(
        "MATCH (i:Incident {id: $i}) SET i.error_rate = 0.7, i.baseline_error_rate = 0.02",
        {"i": inc},
    )
    still_broken = await record_verification(
        builder.store.repo,
        run.result["branch"],
        "success",
        "https://run/2",  # type: ignore[index]
        {"error_rate": 0.5},
    )
    assert still_broken == "failed"  # workflow green but errors still above baseline


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

    verify = {
        "action": "completed",
        "repository": {"full_name": "acme/shop"},
        "installation": {"id": 9},
        "workflow_run": {
            "id": 55,
            "name": "verify",
            "display_title": "verify kaizen/revert-abc1234",
            "conclusion": "success",
            "html_url": "https://github.com/acme/shop/actions/runs/55",
        },
    }
    merged = {
        "action": "closed",
        "repository": {"full_name": "acme/shop"},
        "pull_request": {
            "number": 7,
            "merged": True,
            "head": {"ref": "kaizen/revert-abc1234"},
            "merged_by": {"login": "yash"},
        },
    }
    unrelated = {**merged, "pull_request": {**merged["pull_request"], "head": {"ref": "feature/x"}}}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as c:
        for n, (event, payload) in enumerate(
            [("workflow_run", verify), ("pull_request", merged), ("pull_request", unrelated)]
        ):
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
    by_kind = {j.kind: j.payload for j in jobs}
    assert set(by_kind) == {"verify_result", "remediation_merged"}
    assert by_kind["verify_result"]["branch"] == "kaizen/revert-abc1234"
    assert (
        by_kind["verify_result"]["run_id"] == 55
        and by_kind["verify_result"]["installation_id"] == 9
    )
    assert by_kind["remediation_merged"]["merged_by"] == "yash"
