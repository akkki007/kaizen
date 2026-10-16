"""MCP server: `python -m causaldeploy.mcp_server --repo owner/name` (stdio)."""

import argparse
import asyncio
import inspect
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from causaldeploy.config import get_settings
from causaldeploy.graph.store import GraphStore
from causaldeploy.mcp_server import tools as T

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)


def build_server(repo: str, store: GraphStore | None = None) -> MCPServer:
    """One server per repository: tools cannot reach any other graph. All tools are read-only."""
    if not get_settings().is_allowed(repo):
        raise PermissionError(f"{repo} is not in CD_ALLOWED_REPOS")
    graph = store or GraphStore(repo)
    server = MCPServer("kaizen-graph")

    def tool(spec: T.ToolSpec) -> None:
        async def run(**kwargs: Any) -> dict[str, Any]:
            args = spec.args.model_validate(kwargs)  # the input model is the contract
            out = await asyncio.to_thread(spec.fn, graph, **args.model_dump())
            return T.cap(out)

        # the schema the model sees is generated from the Pydantic input model's fields
        params = [
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty if f.is_required() else f.default,
                annotation=Annotated[f.annotation, f],
            )
            for name, f in spec.args.model_fields.items()
        ]
        run.__name__ = spec.name
        run.__doc__ = spec.description
        run.__signature__ = inspect.Signature(params, return_annotation=dict)  # type: ignore[attr-defined]
        run.__annotations__ = {p.name: p.annotation for p in params} | {"return": dict}
        server.add_tool(run, name=spec.name, description=spec.description, annotations=READ_ONLY)

    for spec in T.TOOLS:
        tool(spec)
    return server


def main() -> None:
    ap = argparse.ArgumentParser(prog="causaldeploy.mcp_server")
    ap.add_argument("--repo", required=True, help="owner/name (must be in CD_ALLOWED_REPOS)")
    args = ap.parse_args()
    build_server(args.repo).run("stdio")


if __name__ == "__main__":
    main()
