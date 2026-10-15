"""Fetch a PR from GitHub, assess it against the graph, upsert one comment."""

import asyncio

from causaldeploy.github.client import GitHubClient
from causaldeploy.graph.store import GraphStore
from causaldeploy.guardian.review import MARKER, assess, render_comment


async def review_pr(repo: str, number: int, installation_id: int) -> str:
    gh = GitHubClient()
    try:
        files = [
            f async for f in gh.paginate(installation_id, f"/repos/{repo}/pulls/{number}/files")
        ]
        a = await asyncio.to_thread(assess, GraphStore(repo), files)
        body = render_comment(a)
        existing = None
        async for c in gh.paginate(installation_id, f"/repos/{repo}/issues/{number}/comments"):
            if MARKER in (c.get("body") or ""):
                existing = c["id"]
        if existing:
            await gh.request(
                installation_id,
                "PATCH",
                f"/repos/{repo}/issues/comments/{existing}",
                json={"body": body},
            )
        else:
            await gh.request(
                installation_id,
                "POST",
                f"/repos/{repo}/issues/{number}/comments",
                json={"body": body},
            )
        return a.risk
    finally:
        await gh.aclose()
