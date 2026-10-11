import asyncio
import base64
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import update

from causaldeploy.config import get_settings
from causaldeploy.db.models import Repository
from causaldeploy.db.session import session_scope
from causaldeploy.github.client import GitHubClient
from causaldeploy.graph.store import GraphStore
from causaldeploy.ingest.backfill import backfill_repo
from causaldeploy.jobs.queue import BackfillPayload


def _clone(repo: str, token: str, dest: Path) -> None:
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    url = f"{get_settings().github_web_url}/{repo}.git"
    try:
        subprocess.run(
            [
                "git",
                "-c",
                f"http.extraheader=Authorization: basic {basic}",
                "clone",
                "--quiet",
                "--filter=blob:none",
                url,
                str(dest),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:  # never surface the command: it carries the token
        raise RuntimeError(f"git clone of {repo} failed (exit {e.returncode})") from None


async def run_backfill(p: BackfillPayload) -> None:
    if p.installation_id is None:
        raise ValueError("backfill needs an installation_id")
    client = GitHubClient()
    try:
        token = await client.installation_token(p.installation_id)
    finally:
        await client.aclose()
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / p.repo.rsplit("/", 1)[-1]
        await asyncio.to_thread(_clone, p.repo, token, dest)
        await asyncio.to_thread(backfill_repo, GraphStore(p.repo), dest, p.repo)
    async with session_scope() as s:
        await s.execute(
            update(Repository)
            .where(Repository.full_name == p.repo)
            .values(backfilled_at=datetime.now(UTC))
        )
