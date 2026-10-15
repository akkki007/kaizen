"""PR Guardian: warn on a pull request before it ships.

Changed hunks -> functions (graph line ranges) -> blast radius (calls*, endpoints, services) ->
incident history of those functions. Everything is read from FalkorDB; no LLM, no score invented.
"""

from dataclasses import dataclass, field
from typing import Any

from causaldeploy.graph import queries as q
from causaldeploy.graph.store import GraphStore
from causaldeploy.ingest.git import parse_diff

MARKER = "<!-- kaizen-pr-guardian -->"

FUNCTIONS_IN_FILE = """
MATCH (f:Function {path: $path})
WHERE f.start_line <= $hi AND f.end_line >= $lo
RETURN f.id, f.start_line, f.end_line
""".strip()


@dataclass
class ChangedFunction:
    id: str
    affected_functions: int
    endpoints: list[str]
    services: list[str]
    incidents: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Assessment:
    changed: list[ChangedFunction]
    unmapped_files: list[str]
    risk: str  # high | medium | low | unknown

    @property
    def endpoints(self) -> list[str]:
        return sorted({e for c in self.changed for e in c.endpoints})

    @property
    def services(self) -> list[str]:
        return sorted({s for c in self.changed for s in c.services})


def changed_functions(
    store: GraphStore, files: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """GitHub `pulls/{n}/files` items -> (function ids, files with no function hit)."""
    ids: dict[str, None] = {}
    unmapped: list[str] = []
    for f in files:
        patch = f.get("patch")
        path = f["filename"]
        if not patch:
            unmapped.append(path)
            continue
        parsed = parse_diff(f"diff --git a/{path} b/{path}\n{patch}")
        hit = False
        for fc in parsed:
            for h in fc.hunks:
                lo = h.old_start
                hi = max(h.old_end, h.old_start) if h.removed else h.old_start
                rows = store.read(FUNCTIONS_IN_FILE, {"path": path, "lo": lo, "hi": hi})
                if rows:
                    inner = min(rows, key=lambda r: (r[2] or 0) - (r[1] or 0))
                    ids[inner[0]] = None
                    hit = True
        if not hit:
            unmapped.append(path)
    return list(ids), unmapped


def assess(store: GraphStore, files: list[dict[str, Any]]) -> Assessment:
    fn_ids, unmapped = changed_functions(store, files)
    out: list[ChangedFunction] = []
    for fid in fn_ids:
        b = q.blast_radius(store, fid)
        hist = q.function_incident_history(store, fid)
        out.append(
            ChangedFunction(
                id=fid,
                affected_functions=len(b.affected_functions),
                endpoints=b.affected_endpoints,
                services=b.affected_services,
                incidents=[{"id": h.incident_id, "via": h.via, "status": h.status} for h in hist],
            )
        )
    blamed = any(h["via"] == "blamed" for c in out for h in c.incidents)
    endpoints = {e for c in out for e in c.endpoints}
    if not out:
        risk = "unknown"
    elif blamed or len(endpoints) >= 3:
        risk = "high"
    elif endpoints or any(c.incidents for c in out):
        risk = "medium"
    else:
        risk = "low"
    return Assessment(out, unmapped, risk)


def render_comment(a: Assessment) -> str:
    head = f"{MARKER}\n### Kaizen PR Guardian: risk **{a.risk}**\n"
    if not a.changed:
        return (
            head
            + "\nNo changed function matched the knowledge graph, so no blast radius is known. "
            + "Kaizen does not guess.\n"
        )
    lines = [
        head,
        "| Changed function | Callers affected | Endpoints | Past incidents |",
        "|---|---|---|---|",
    ]
    for c in a.changed:
        inc = ", ".join(f"{i['id']} ({i['via']})" for i in c.incidents) or "none"
        eps = ", ".join(c.endpoints) or "none"
        lines.append(f"| `{c.id}` | {c.affected_functions} | {eps} | {inc} |")
    lines.append("")
    lines.append(f"Services reached: {', '.join(a.services) or 'none'}.")
    if a.unmapped_files:
        lines.append(f"Not mapped to a function: {', '.join(a.unmapped_files)}.")
    lines.append(
        "\nSource: FalkorDB call graph and incident history (`blast_radius`, "
        "`function_incident_history`)."
    )
    return "\n".join(lines) + "\n"
