import json

import pytest
from mcp import Client

from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.mcp_server.server import build_server


async def call(client: Client, name: str, **args: object) -> dict:  # type: ignore[type-arg]
    r = await client.call_tool(name, args)
    assert not r.is_error, r.content
    return json.loads(r.content[0].text)  # type: ignore[union-attr]


async def test_server_lists_only_read_only_tools(builder: GraphBuilder) -> None:
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        listed = (await c.list_tools()).tools
    names = {t.name for t in listed}
    assert names == {
        "get_incident",
        "get_deployment_commits",
        "shortest_call_path",
        "get_call_path",  # alias
        "blast_radius",
        "dependency_impact",
        "similar_incidents",
        "function_incident_history",
        "rank_candidates",
        "get_candidates",  # alias
        "suggest_responders",
        "get_changed_functions",
        "get_callers",
        "get_diff",
        "get_error_samples",
    }
    for t in listed:
        assert t.annotations and t.annotations.read_only_hint is True
        assert t.annotations.destructive_hint is False
    schemas = {t.name: t.input_schema for t in listed}
    # schemas come from the Pydantic input models: constraints and descriptions included
    assert schemas["get_incident"]["properties"]["incident_id"]["maxLength"] == 64
    assert schemas["get_callers"]["properties"]["depth"]["maximum"] == 3
    assert schemas["get_diff"]["required"] == ["sha"]


async def test_invalid_input_is_rejected_by_the_input_model(builder: GraphBuilder) -> None:
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        r = await c.call_tool("get_error_samples", {"incident_id": "inc-1", "limit": 500})
        assert r.is_error
        r = await c.call_tool("get_incident", {"incident_id": "x' OR 1=1 //"})
        assert r.is_error


async def test_new_tools_and_aliases(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    v = sc.culprit_functions[0]
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        changed = await call(c, "get_changed_functions", sha=sc.culprit_commit)
        assert [f["function_id"] for f in changed["functions"]] == [v]
        assert f"Function:{v}" in changed["node_ids"] and changed["truncated"] is False
        callers = await call(c, "get_callers", function_id=v)
        assert callers["callers"] == [
            {
                "function_id": "payments:src/routes.js:handlePayment",
                "service": "payments",
                "distance": 1,
            }
        ]
        diff = await call(c, "get_diff", sha=sc.culprit_commit, function_id=v)
        assert diff["diff_text_available"] is False and "not diff text" in diff["note"]
        assert diff["hunks"][0]["start_line"] == 1 and diff["hunks"][0]["end_line"] == 200
        docs = await call(c, "get_changed_functions", sha="n0000001")
        assert docs["functions"] == [] and docs["files_without_function_mapping"]
        samples = await call(c, "get_error_samples", incident_id=sc.incident_id, limit=3)
        s0 = samples["samples"][0]
        assert s0["fingerprint"] == "fp-undefined-currency" and "currency" in s0["message"]
        assert s0["stack_functions"] and "Endpoint:payments:POST /payments" in samples["node_ids"]
        a = await call(c, "get_candidates", incident_id=sc.incident_id)
        b = await call(c, "rank_candidates", incident_id=sc.incident_id)
        assert a == b and a["candidates"][0]["deployment"] == "dep-842"
        p1 = await call(c, "get_call_path", sha=sc.culprit_commit, incident_id=sc.incident_id)
        p2 = await call(c, "shortest_call_path", sha=sc.culprit_commit, incident_id=sc.incident_id)
        assert p1 == p2
        unknown = await call(c, "get_diff", sha="ffffffff")
        assert "unknown commit" in unknown["error"]


def test_cap_bounds_items_and_bytes() -> None:
    from causaldeploy.mcp_server.tools import cap

    small = cap({"xs": [1, 2], "node_ids": ["A:1"]})
    assert small == {"xs": [1, 2], "node_ids": ["A:1"], "truncated": False}
    many = cap({"xs": list(range(500)), "nested": [{"ys": list(range(80))}], "node_ids": []})
    assert len(many["xs"]) == 50 and len(many["nested"][0]["ys"]) == 50 and many["truncated"]
    big = cap({"rows": ["x" * 1000] * 40, "node_ids": ["A:1"]}, max_bytes=5_000)
    assert len(json.dumps(big)) <= 5_000 and big["truncated"] and big["node_ids"] == ["A:1"]
    huge_str = cap({"text": "y" * 50_000, "node_ids": []}, max_bytes=2_000)
    assert len(json.dumps(huge_str)) <= 2_000 and huge_str["truncated"]


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
