"""Verification verdict (pure), sandboxed subprocess environment, GitHub ops wire format."""

import json
import sys
from pathlib import Path

import httpx
import pytest
import respx

from causaldeploy.remediate import ops as ops_mod
from causaldeploy.remediate.ops import GitHubOps, run_sandboxed, sandbox_env
from causaldeploy.verify.verdict import Metrics, VerdictConfig, recompute, verdict

CFG = VerdictConfig(error_tolerance=0.02, p95_tolerance_ms=100, min_samples=50)
BASE = Metrics(error_rate=0.01, p95_ms=80)
FAILING = Metrics(error_rate=0.6, p95_ms=900)


def test_healthy_fix_passes() -> None:
    v = verdict(FAILING, Metrics(error_rate=0.02, p95_ms=120, samples=400), BASE, CFG)
    assert v.passed and v.reasons == [] and v.checks["improved"] is True


@pytest.mark.parametrize(
    ("fixed", "kw", "reason"),
    [
        (Metrics(error_rate=0.05, samples=400), {}, "error rate"),
        (Metrics(error_rate=0.0, p95_ms=500, samples=400), {}, "p95"),
        (Metrics(error_rate=0.0, samples=10), {}, "requests measured"),
        (Metrics(), {}, "no error rate"),
        (Metrics(error_rate=0.0, samples=400), {"workflow_ok": False}, "workflow"),
        (Metrics(error_rate=0.0, samples=400), {"tests_passed": False}, "tests failed"),
    ],
)
def test_each_rule_can_fail_the_fix(fixed: Metrics, kw: dict[str, bool], reason: str) -> None:
    v = verdict(FAILING, fixed, BASE, CFG, **kw)
    assert not v.passed and any(reason in r for r in v.reasons)


def test_tolerances_are_configurable() -> None:
    fixed = Metrics(error_rate=0.05, samples=400)
    assert not verdict(FAILING, fixed, BASE, CFG).passed
    loose = VerdictConfig(error_tolerance=0.05, p95_tolerance_ms=100, min_samples=50)
    assert verdict(FAILING, fixed, BASE, loose).passed


def test_unknown_inputs_do_not_gate() -> None:
    # no p95 / sample counts reported: those checks are skipped, not failed
    v = verdict(None, Metrics(error_rate=0.0), Metrics(error_rate=0.0), CFG)
    assert v.passed and v.checks["p95"] is None and v.checks["samples"] is None


def test_recompute_from_a_stored_record() -> None:
    fixed = Metrics(error_rate=0.05, p95_ms=100, samples=400)
    v = verdict(FAILING, fixed, BASE, CFG, tests_passed=True)
    stored = json.loads(
        json.dumps(
            {
                "inputs": {
                    "failing": FAILING.model_dump(),
                    "fixed": fixed.model_dump(),
                    "baseline": BASE.model_dump(),
                    "workflow_ok": True,
                    "tests_passed": True,
                },
                "config": CFG.model_dump(),
                "verdict": v.model_dump(),
            }
        )
    )
    assert recompute(stored).model_dump() == stored["verdict"]


def test_sandbox_env_drops_credentials_and_config() -> None:
    env = sandbox_env(
        {
            "PATH": "/bin",
            "HOME": "/home/k",
            "CD_POSTGRES_DSN": "postgresql://secret",
            "CD_GITHUB_APP_PRIVATE_KEY": "pem",
            "GITHUB_TOKEN": "ghs_x",
            "OPENAI_API_KEY": "sk-x",
            "AWS_SECRET_ACCESS_KEY": "aws",
            "SOME_RANDOM_TOKEN": "x",
        }
    )
    assert env["PATH"] == "/bin" and env["HOME"] == "/home/k"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    leaked = [k for k in env if k.startswith(("CD_", "GITHUB_", "OPENAI_", "AWS_"))]
    assert leaked == [] and "SOME_RANDOM_TOKEN" not in env


def test_subprocesses_never_see_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for k in ("CD_SESSION_SECRET", "GITHUB_TOKEN", "OPENAI_API_KEY", "AWS_ACCESS_KEY_ID"):
        monkeypatch.setenv(k, "leak-me")
    res = run_sandboxed(
        [sys.executable, "-c", "import os, json; print(json.dumps(dict(os.environ)))"], tmp_path
    )
    seen = json.loads(res.stdout)
    assert "leak-me" not in seen.values()
    # git runs with the same scrubbed environment
    captured: dict[str, dict[str, str]] = {}
    real = ops_mod.subprocess.run

    def spy(*a, **kw):  # type: ignore[no-untyped-def]
        captured["env"] = kw.get("env") or {}
        return real(*a, **kw)

    monkeypatch.setattr(ops_mod.subprocess, "run", spy)
    ops_mod._git(tmp_path, "--version")
    assert captured["env"] and "leak-me" not in captured["env"].values()


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> GitHubOps:
    ops = GitHubOps()

    async def token(installation_id: int) -> str:
        return "tok"

    monkeypatch.setattr(ops._client, "installation_token", token)
    return ops


@respx.mock
async def test_github_ops_labels_checks_merge_and_close(gh: GitHubOps) -> None:
    api = "https://api.github.com/repos/acme/shop"
    pr = respx.post(f"{api}/pulls").mock(
        return_value=httpx.Response(201, json={"number": 7, "html_url": "https://gh/pull/7"})
    )
    labels = respx.post(f"{api}/issues/7/labels").mock(return_value=httpx.Response(200, json=[]))
    check = respx.post(f"{api}/check-runs").mock(return_value=httpx.Response(201, json={}))
    merge = respx.put(f"{api}/pulls/7/merge").mock(return_value=httpx.Response(200, json={}))
    comment = respx.post(f"{api}/issues/7/comments").mock(return_value=httpx.Response(201, json={}))
    close = respx.patch(f"{api}/pulls/7").mock(return_value=httpx.Response(200, json={}))

    n, url = await gh.open_pr(
        "acme/shop", 1, "kaizen/fix-inc-1", "main", "t", "b", ["kaizen", "incident:inc-1"]
    )
    assert (n, url) == (7, "https://gh/pull/7")
    assert json.loads(labels.calls[0].request.content) == {"labels": ["kaizen", "incident:inc-1"]}
    assert json.loads(pr.calls[0].request.content)["head"] == "kaizen/fix-inc-1"

    await gh.post_check("acme/shop", 1, "abc123", False, "https://run/1", "error rate too high")
    body = json.loads(check.calls[0].request.content)
    assert body["head_sha"] == "abc123" and body["conclusion"] == "failure"
    assert body["details_url"] == "https://run/1" and body["status"] == "completed"

    await gh.merge_pr("acme/shop", 1, 7, "abc123")
    assert json.loads(merge.calls[0].request.content) == {"merge_method": "squash", "sha": "abc123"}

    await gh.close_pr("acme/shop", 1, 7, "rejected")
    assert comment.called and json.loads(close.calls[0].request.content) == {"state": "closed"}
