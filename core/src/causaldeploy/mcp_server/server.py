"""MCP server: `python -m causaldeploy.mcp_server --repo owner/name` (stdio)."""

import argparse
import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from causaldeploy.config import get_settings
from causaldeploy.graph.store import GraphStore
from causaldeploy.mcp_server import tools as T


def build_server(repo: str, store: GraphStore | None = None) -> MCPServer:
    """One server per repository: tools cannot reach any other graph. All tools are read-only."""
    if not get_settings().is_allowed(repo):
        raise PermissionError(f"{repo} is not in CD_ALLOWED_REPOS")
    graph = store or GraphStore(repo)
    server = MCPServer("kaizen-graph")

    def tool(fn: Callable[..., dict[str, Any]], description: str) -> None:
        async def run(**kwargs: Any) -> dict[str, Any]:
            return await asyncio.to_thread(fn, graph, **kwargs)

        params = list(inspect.signature(fn).parameters.values())[1:]  # drop `store`
        run.__name__ = fn.__name__
        run.__doc__ = description
        run.__signature__ = inspect.Signature(params, return_annotation=dict)  # type: ignore[attr-defined]
        run.__annotations__ = {p.name: p.annotation for p in params} | {"return": dict}
        server.add_tool(run, name=fn.__name__, description=description)

    for fn, description in (
        (T.get_incident, "Incident facts: service, endpoint, onset, errors, where thrown."),
        (T.get_deployment_commits, "Deployments before the incident and the commits in each."),
        (T.shortest_call_path, "Shortest call path from a commit's changed to thrown functions."),
        (
            T.blast_radius,
            "Functions, endpoints, services affected by changing a function or commit.",
        ),
        (
            T.dependency_impact,
            "Dependencies a commit changed and the functions/services using them.",
        ),
        (T.similar_incidents, "Resolved incidents resembling this one, with the fix that worked."),
        (T.function_incident_history, "Past incidents that blamed or threw in a function."),
        (T.rank_candidates, "Deterministic ranking of suspect commits with per-feature scores."),
        (T.suggest_responders, "Developers to involve: recent authors, resolvers of similar ones."),
    ):
        tool(fn, description)
    return server


def main() -> None:
    ap = argparse.ArgumentParser(prog="causaldeploy.mcp_server")
    ap.add_argument("--repo", required=True, help="owner/name (must be in CD_ALLOWED_REPOS)")
    args = ap.parse_args()
    build_server(args.repo).run("stdio")


if __name__ == "__main__":
    main()
