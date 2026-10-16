"""Record / replay of scenario graphs (#92).

`--record DIR` writes, per scenario variant, the normalized graph writes the scenario produced
(the same node/edge upserts ingestion would make) as JSONL: one header line with the case's
ground truth, then one line per write. `--replay DIR` loads them into a fresh graph and runs the
same ranking pipeline, with no scenario rendering, network or LLM involved.
"""

import json
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from causaldeploy.evalharness.scenarios import Case
from causaldeploy.graph.store import Edge, GraphStore

FORMAT = "kaizen-eval-replay/1"


class RecordingStore(GraphStore):
    """A GraphStore that also keeps every node/edge write, in order."""

    def __init__(self, repo: str) -> None:
        super().__init__(repo)
        self.ops: list[dict[str, Any]] = []

    def upsert_nodes(self, label: str, rows: Iterable[dict[str, Any]]) -> int:
        data = [dict(r) for r in rows]
        self.ops.append({"op": "nodes", "label": label, "rows": data})
        return super().upsert_nodes(label, data)

    def upsert_edges(self, edges: Iterable[Edge]) -> int:
        data = list(edges)
        self.ops.append({"op": "edges", "edges": [asdict(e) for e in data]})
        return super().upsert_edges(data)


def case_path(directory: Path, case: Case) -> Path:
    return directory / f"{case.scenario}-v{case.variant}.jsonl"


def write_case(directory: Path, case: Case, ops: list[dict[str, Any]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = case_path(directory, case)
    lines = [json.dumps({"format": FORMAT, "case": asdict(case)}, sort_keys=True)]
    lines += [json.dumps(op, sort_keys=True) for op in ops]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_case(path: Path) -> tuple[Case, list[dict[str, Any]]]:
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not lines or lines[0].get("format") != FORMAT:
        raise ValueError(f"{path} is not a {FORMAT} recording")
    return Case(**lines[0]["case"]), lines[1:]


def apply_ops(store: GraphStore, ops: list[dict[str, Any]]) -> None:
    store.ensure_schema()
    for op in ops:
        if op["op"] == "nodes":
            store.upsert_nodes(op["label"], op["rows"])
        elif op["op"] == "edges":
            store.upsert_edges(Edge(**e) for e in op["edges"])
        else:
            raise ValueError(f"unknown replay op {op['op']!r}")


def recorded_cases(directory: Path) -> list[Path]:
    def key(p: Path) -> tuple[int, int]:
        sid, _, v = p.stem.partition("-v")
        return int(sid[1:]), int(v)

    return sorted(directory.glob("S*-v*.jsonl"), key=key)
