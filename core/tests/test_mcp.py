import json

import pytest
from mcp import Client

from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.mcp_server.server import build_server


async def call(client: Client, name: str, **args: object) -> dict:  # type: ignore[type-arg]
    r = await client.call_tool(name, args)
    assert not r.is_error, r.content
    return json.loads(r.content[0].text)  # type: ignore[union-attr]


async def test_server_lists_the_nine_read_only_tools(builder: GraphBuilder) -> None:
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert names == {
        "get_incident",
        "get_deployment_commits",
        "shortest_call_path",
        "blast_radius",
        "dependency_impact",
        "similar_incidents",
        "function_incident_history",
        "rank_candidates",
        "suggest_responders",
    }


async def test_tools_return_citable_node_ids(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        inc = await call(c, "get_incident", incident_id=sc.incident_id)
        assert (
            "Incident:inc-1" in inc["node_ids"] and inc["endpoint_id"] == "payments:POST /payments"
        )
        dep = await call(c, "get_deployment_commits", incident_id=sc.incident_id)
        assert f"Commit:{sc.culprit_commit}" in dep["node_ids"]
        path = await call(
            c, "shortest_call_path", sha=sc.culprit_commit, incident_id=sc.incident_id
        )
        assert path["paths"][0]["distance"] == 0
        ranked = await call(c, "rank_candidates", incident_id=sc.incident_id)
        assert (
            ranked["verdict"] == "confident" and ranked["candidates"][0]["sha"] == sc.culprit_commit
        )
        blast = await call(c, "blast_radius", sha=sc.culprit_commit)
        assert "Service:payments" in blast["node_ids"]


async def test_unknown_incident_is_a_clean_error_payload(builder: GraphBuilder) -> None:
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        out = await call(c, "get_incident", incident_id="nope")
    assert out["node_ids"] == [] and "unknown incident" in out["error"]


def test_server_refuses_repositories_outside_the_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    from causaldeploy.config import get_settings

    monkeypatch.setenv("CD_ALLOWED_REPOS", "acme/shoplite")
    get_settings.cache_clear()
    try:
        with pytest.raises(PermissionError):
            build_server("acme/other")
    finally:
        get_settings.cache_clear()
