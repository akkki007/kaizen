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


async def create_key(repo: str, label: str) -> str:
    from causaldeploy.api.routes import create_api_key

    return await create_api_key(repo, label)


def main() -> None:
    ap = argparse.ArgumentParser(prog="causaldeploy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export-schemas", help="write docs/schemas/*.json and docs/openapi.json")
    r = sub.add_parser("replay", help="replay stored raw webhook deliveries")
    r.add_argument("--limit", type=int)
    k = sub.add_parser("create-api-key", help="print a new ingest API key for a repository")
    k.add_argument("repo")
    k.add_argument("--label", default="")
    a = ap.parse_args()
    if a.cmd == "export-schemas":
        for p in export_schemas():
            print(p)
    elif a.cmd == "replay":
        print(f"replayed {asyncio.run(replay(a.limit))} deliveries")
    else:
        print(asyncio.run(create_key(a.repo, a.label)))


if __name__ == "__main__":
    main()
