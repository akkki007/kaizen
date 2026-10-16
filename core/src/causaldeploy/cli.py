"""Operator CLI: `uv run python -m causaldeploy.cli <command>`."""

import argparse
import asyncio
import json
from pathlib import Path

DOCS = Path(__file__).resolve().parents[3] / "docs"


def schema_documents() -> dict[Path, dict[str, object]]:
    from causaldeploy.api.main import create_app
    from causaldeploy.contracts import SCHEMA_VERSION
    from causaldeploy.contracts.domain import (
        Candidate,
        Hypothesis,
        Incident,
        Remediation,
        VerificationResult,
    )
    from causaldeploy.contracts.events import event_json_schema

    domain = {
        m.__name__: m.model_json_schema()
        for m in (Incident, Candidate, Hypothesis, Remediation, VerificationResult)
    }
    return {
        DOCS / "schemas" / f"events.v{SCHEMA_VERSION.split('.')[0]}.json": event_json_schema(),
        DOCS / "schemas" / f"domain.v{SCHEMA_VERSION.split('.')[0]}.json": {"models": domain},
        DOCS / "openapi.json": create_app().openapi(),
    }


def export_schemas() -> list[Path]:
    out = []
    for path, doc in schema_documents().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        out.append(path)
    return out


def contract_version_problems(base_ref: str) -> list[str]:
    """Schemas that differ from `base_ref` must come with a SCHEMA_VERSION bump."""
    import re
    import subprocess

    root = DOCS.parent

    def git_show(path: str) -> str | None:
        r = subprocess.run(
            ["git", "show", f"{base_ref}:{path}"], cwd=root, capture_output=True, text=True
        )
        return r.stdout if r.returncode == 0 else None

    def version(text: str) -> str:
        m = re.search(r'SCHEMA_VERSION = "([^"]+)"', text)
        return m.group(1) if m else ""

    events_py = "core/src/causaldeploy/contracts/events.py"
    base_version = version(git_show(events_py) or "")
    head_version = version((root / events_py).read_text())
    problems = []
    for path in DOCS.glob("schemas/*.json"):
        rel = path.relative_to(root).as_posix()  # git paths use "/" on every OS
        before = git_show(rel)
        if before is not None and json.loads(before) != json.loads(path.read_text()):
            if base_version == head_version:
                problems.append(f"{rel} changed but SCHEMA_VERSION is still {head_version}")
    return problems


async def replay(limit: int | None) -> int:
    """Re-run stored webhook deliveries through normalization (graph writes are idempotent)."""
    from sqlalchemy import select

    from causaldeploy.api.routes import dispatch_github
    from causaldeploy.db.models import RawEvent
    from causaldeploy.db.session import session_scope

    async with session_scope() as s:
        stmt = select(RawEvent).order_by(RawEvent.received_at)
        rows = (await s.execute(stmt.limit(limit) if limit else stmt)).scalars().all()
    for r in rows:
        await dispatch_github(f"replay:{r.delivery_id}", r.event_type, r.payload)
    return len(rows)


async def backfill(repo: str) -> dict[str, str]:
    """Import `repo` into the local graph directly (no webhook needed): clone with the App's
    installation token, map commits to functions, write the graph."""
    from causaldeploy.config import get_settings
    from causaldeploy.github.backfill_job import run_backfill
    from causaldeploy.github.client import GitHubClient
    from causaldeploy.jobs.queue import BackfillPayload

    if not get_settings().is_allowed(repo):
        raise SystemExit(f"{repo} is not in CD_ALLOWED_REPOS")
    client = GitHubClient()
    try:
        installation = await client.repo_installation_id(repo)
    finally:
        await client.aclose()
    await run_backfill(BackfillPayload(repo=repo, installation_id=installation))
    return {"repo": repo, "installation_id": str(installation)}


async def demo_history(repo: str) -> dict[str, object]:
    """Seed resolved historical incidents + fixes (idempotent) and run the similarity pass."""
    from causaldeploy.config import get_settings
    from causaldeploy.memory.demo import seed_history

    if not get_settings().is_allowed(repo):
        raise SystemExit(f"{repo} is not in CD_ALLOWED_REPOS")
    return await seed_history(repo)


async def create_key(repo: str, label: str) -> str:
    from causaldeploy.api.routes import create_api_key

    return await create_api_key(repo, label)


def main() -> None:
    ap = argparse.ArgumentParser(prog="causaldeploy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export-schemas", help="write docs/schemas/*.json and docs/openapi.json")
    r = sub.add_parser("replay", help="replay stored raw webhook deliveries")
    r.add_argument("--limit", type=int)
    c = sub.add_parser(
        "check-contract-version", help="fail if schemas changed without a version bump"
    )
    c.add_argument("--base", default="origin/main")
    b = sub.add_parser(
        "backfill", help="import a repository into the local graph via the GitHub App"
    )
    b.add_argument("repo", help="owner/name")
    d = sub.add_parser(
        "demo-history", help="seed resolved demo incidents + fixes and compute similarity"
    )
    d.add_argument("repo", help="owner/name")
    k = sub.add_parser("create-api-key", help="print a new ingest API key for a repository")
    k.add_argument("repo")
    k.add_argument("--label", default="")
    a = ap.parse_args()
    if a.cmd == "export-schemas":
        for p in export_schemas():
            print(p)
    elif a.cmd == "check-contract-version":
        problems = contract_version_problems(a.base)
        for problem in problems:
            print(problem)
        raise SystemExit(1 if problems else 0)
    elif a.cmd == "backfill":
        print(json.dumps(asyncio.run(backfill(a.repo))))
    elif a.cmd == "demo-history":
        print(json.dumps(asyncio.run(demo_history(a.repo))))
    elif a.cmd == "replay":
        print(f"replayed {asyncio.run(replay(a.limit))} deliveries")
    else:
        print(asyncio.run(create_key(a.repo, a.label)))


if __name__ == "__main__":
    main()
