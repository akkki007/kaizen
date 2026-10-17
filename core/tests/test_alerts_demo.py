import httpx
import pytest
import respx
from sqlalchemy import select

from causaldeploy.alerts.notify import SLACK_ACTION, alert_incident, render
from causaldeploy.config import get_settings
from causaldeploy.db.models import AuditLog, IncidentRow
from causaldeploy.db.session import session_scope
from causaldeploy.demo import inject as demo
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.investigate.run import investigate
from causaldeploy.investigate.scorer import persist, rank


async def _incident(repo: str, inc_id: str) -> IncidentRow:
    async with session_scope() as s:
        return (await s.execute(select(IncidentRow).where(IncidentRow.id == inc_id))).scalar_one()


async def test_inject_runs_the_real_pipeline_and_alerts_the_team(
    db: None, builder: GraphBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CD_SLACK_WEBHOOK_URL", "https://hooks.slack.test/T/B/x")
    get_settings.cache_clear()
    store, repo = builder.store, builder.store.repo
    sc = builder.s1_payment_validation()
    # drop the seeded incident: the demo creates its own through telemetry + detection
    store.graph.query("MATCH (n) WHERE n:Incident OR n:Error DETACH DELETE n")
    try:
        out = await demo.inject(repo)
        assert out["simulated"] and out["commit"] == sc.culprit_commit
        assert out["function"].endswith("validatePayment")
        inc_id = out["incident_id"]

        with respx.mock(assert_all_called=True) as m:
            hook = m.post("https://hooks.slack.test/T/B/x").mock(return_value=httpx.Response(200))
            result = await investigate(repo, inc_id)
            assert result.verdict == "confident"
            assert result.candidates[0].sha == sc.culprit_commit
            text = hook.calls.last.request.read().decode()
        assert "validatePayment" in text and sc.culprit_commit[:8] in text

        # idempotent: a second investigation of the same incident does not re-send
        with respx.mock(assert_all_called=False) as m:
            again = m.post("https://hooks.slack.test/T/B/x").mock(return_value=httpx.Response(200))
            assert await alert_incident(repo, inc_id, rank(store, inc_id)) == []
            assert not again.called
        async with session_scope() as s:
            sent = (
                (await s.execute(select(AuditLog).where(AuditLog.action == SLACK_ACTION)))
                .scalars()
                .all()
            )
        assert len(sent) == 1

        await demo.reset(repo)
        assert not store.read("MATCH (i:Incident) RETURN i.id")
    finally:
        get_settings.cache_clear()


async def test_render_abstains_instead_of_naming_a_culprit(db: None, builder: GraphBuilder) -> None:
    from types import SimpleNamespace

    sc = builder.s1_payment_validation()
    result = rank(builder.store, sc.incident_id)
    result.verdict = "no_code_evidence"
    fake = SimpleNamespace(id="inc-x", service="payments", endpoint="POST /payments", env="prod")
    head, body = render(fake, result, "https://kaizen.example")  # type: ignore[arg-type]
    assert "not naming a culprit" in body and "inc-x" in body and "payments" in head


async def test_code_graph_marks_only_the_failing_function_and_demo_is_gated(
    db: None, builder: GraphBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_api import create_app, login_as

    sc = builder.s1_payment_validation()
    repo = builder.store.repo
    persist(builder.store, rank(builder.store, sc.incident_id))  # records likely_caused_by
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        h = login_as(c, "yash", {repo: "approver"})
        g = (await c.get(f"/api/v1/repos/{repo}/code-graph")).json()
        failing = [n["label"] for n in g["nodes"] if n["state"] == "failing"]
        suspects = [n["label"] for n in g["nodes"] if n["state"] == "suspect"]
        assert "validatePayment" in failing and "handlePayment" in failing
        assert suspects == [sc.culprit_commit[:7]]
        assert all(n["state"] == "ok" for n in g["nodes"] if n["kind"] == "service")
        assert "MATCH (f:Function)" in g["queries"]["functions"] and g["demo_enabled"] is False
        # demo controls are off unless enabled for the repository
        assert (await c.post(f"/api/v1/repos/{repo}/demo/inject", headers=h)).status_code == 404
        monkeypatch.setenv("CD_DEMO_INJECT_ENABLED", "1")
        monkeypatch.setenv("CD_DEMO_REPOS", repo)
        get_settings.cache_clear()
        try:
            viewer = login_as(c, "v", {repo: "viewer"})
            assert (
                await c.post(f"/api/v1/repos/{repo}/demo/reset", headers=viewer)
            ).status_code == 403
        finally:
            get_settings.cache_clear()


async def test_try_the_demo_is_a_viewer_session_that_can_inject_but_not_fix(
    db: None, builder: GraphBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_api import create_app

    builder.s1_payment_validation()
    repo = builder.store.repo
    monkeypatch.setenv("CD_DEMO_REPOS", repo)
    get_settings.cache_clear()
    app = create_app()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            assert (await c.post("/auth/demo")).status_code == 404  # off by default
            monkeypatch.setenv("CD_DEMO_LOGIN_ENABLED", "1")
            monkeypatch.setenv("CD_DEMO_INJECT_ENABLED", "1")
            get_settings.cache_clear()
            r = await c.post("/auth/demo")
            assert r.status_code == 200 and r.json()["repos"] == {repo: "viewer"}
            assert (await c.get("/auth/me")).json()["login"] == "demo"
            h = {"x-csrf-token": c.cookies.get("cd_csrf")}
            assert (await c.get(f"/api/v1/repos/{repo}/code-graph")).status_code == 200
            # may reset the demo, but cannot run approver-only actions
            assert (await c.post(f"/api/v1/repos/{repo}/demo/reset", headers=h)).status_code == 200
            reimport = await c.post(f"/api/v1/repos/{repo}/backfill", headers=h)
            assert reimport.status_code == 403  # approver-only actions stay closed
    finally:
        get_settings.cache_clear()
