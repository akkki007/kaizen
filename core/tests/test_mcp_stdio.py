"""The MCP server over its real stdio transport (`make mcp`), as an external client would use it."""

import json
import os
import sys

from mcp import ClientSession, StdioServerParameters, stdio_client

from causaldeploy.graph.builder import GraphBuilder


async def test_server_works_over_stdio_in_a_separate_process(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "causaldeploy.mcp_server", "--repo", builder.store.repo],
        env={**os.environ, "CD_ALLOWED_REPOS": ""},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert {"get_incident", "rank_candidates", "shortest_call_path"} <= names
            res = await session.call_tool("rank_candidates", {"incident_id": sc.incident_id})
            payload = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert payload["verdict"] == "confident"
    assert payload["candidates"][0]["sha"] == sc.culprit_commit
    assert "centrality" in payload["candidates"][0]["features"]
