"""Typed, idempotent write layer. No raw Cypher outside this package."""

import logging
import re
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from falkordb import FalkorDB, Graph
from redis.exceptions import ResponseError

from causaldeploy.config import get_settings
from causaldeploy.graph.schema import (
    NODE_KEYS,
    RANGE_INDEXES,
    RELATIONSHIPS,
    SCHEMA_VERSION,
    UNIQUE_KEYS,
)
from causaldeploy.observability import GRAPH_SLOW_QUERIES, span

QUERY_TIMEOUT_MS = 2000
SLOW_QUERY_MS = 500
_log = logging.getLogger(__name__)
_BATCH = 500


@lru_cache
def get_client() -> FalkorDB:
    s = get_settings()
    return FalkorDB(host=s.falkordb_host, port=s.falkordb_port)


def graph_name(repo: str) -> str:
    return "cd_" + re.sub(r"[^A-Za-z0-9_]", "_", repo)


@dataclass(frozen=True)
class Edge:
    src_label: str
    src_key: str
    rel: str
    dst_label: str
    dst_key: str
    props: dict[str, Any] = field(default_factory=dict)


class GraphStore:
    """One FalkorDB graph per repository (isolation); all writes are MERGE-based."""

    def __init__(self, repo: str, client: FalkorDB | None = None) -> None:
        self.repo = repo
        self._client = client or get_client()
        self.name = graph_name(repo)
        self.graph: Graph = self._client.select_graph(self.name)

    # -- schema -------------------------------------------------------------
    def schema_version(self) -> int:
        rows = self.read("MATCH (m:SchemaMeta) RETURN m.version")
        return int(rows[0][0]) if rows else 0

    def ensure_schema(self) -> None:
        """Apply the versioned schema step (indexes + unique constraints). Idempotent."""
        if self.schema_version() >= SCHEMA_VERSION:
            return
        for label, key in UNIQUE_KEYS:
            self.graph.create_node_range_index(label, key)
        for label, prop in RANGE_INDEXES:
            self.graph.create_node_range_index(label, prop)
        self._wait_indexes()
        for label, key in UNIQUE_KEYS:
            self.graph.create_node_unique_constraint(label, key)
        self.graph.query("MERGE (m:SchemaMeta) SET m.version = $v", {"v": SCHEMA_VERSION})

    def _wait_indexes(self) -> None:
        import time

        for _ in range(50):
            pending = [i for i in self.graph.list_indices().result_set if "OPERATIONAL" not in i]
            if not pending:
                return
            time.sleep(0.1)

    # -- writes -------------------------------------------------------------
    def upsert_nodes(self, label: str, rows: Iterable[dict[str, Any]]) -> int:
        key = NODE_KEYS[label]
        data = [{"key": r[key], "props": {k: v for k, v in r.items() if k != key}} for r in rows]
        for i in range(0, len(data), _BATCH):
            self.graph.query(
                f"UNWIND $rows AS r MERGE (n:{label} {{{key}: r.key}}) SET n += r.props",
                {"rows": data[i : i + _BATCH]},
            )
        return len(data)

    def upsert_edges(self, edges: Iterable[Edge]) -> int:
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        total = 0
        for e in edges:
            if (e.src_label, e.dst_label) not in RELATIONSHIPS.get(e.rel, frozenset()):
                raise ValueError(f"edge not in schema: ({e.src_label})-[:{e.rel}]->({e.dst_label})")
            groups[(e.src_label, e.rel, e.dst_label)].append(
                {"s": e.src_key, "d": e.dst_key, "props": e.props}
            )
            total += 1
        for (src, rel, dst), rows in groups.items():
            sk, dk = NODE_KEYS[src], NODE_KEYS[dst]
            q = (
                f"UNWIND $rows AS r MERGE (a:{src} {{{sk}: r.s}}) MERGE (b:{dst} {{{dk}: r.d}}) "
                f"MERGE (a)-[e:{rel}]->(b) SET e += r.props"
            )
            for i in range(0, len(rows), _BATCH):
                self.graph.query(q, {"rows": rows[i : i + _BATCH]})
        return total

    def delete_edges(self, src_label: str, src_key: str, rel: str) -> None:
        """Remove outgoing edges of one type (used when a candidate set is recomputed)."""
        sk = NODE_KEYS[src_label]
        self.graph.query(
            f"MATCH (a:{src_label} {{{sk}: $k}})-[e:{rel}]->() DELETE e", {"k": src_key}
        )

    # -- reads --------------------------------------------------------------
    def read(self, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        started = time.perf_counter()
        try:
            with span("graph.read", graph=self.name):
                res = self.graph.ro_query(cypher, params or {}, timeout=QUERY_TIMEOUT_MS)
        except ResponseError as e:
            if "empty key" in str(e):  # graph not created yet == empty graph
                return []
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        if elapsed_ms > SLOW_QUERY_MS:
            GRAPH_SLOW_QUERIES.inc()
            _log.warning(
                "slow graph query",
                extra={"ctx": {"graph": self.name, "ms": round(elapsed_ms), "query": cypher[:200]}},
            )
        return res.result_set  # type: ignore[no-any-return]

    def counts(self) -> tuple[int, int]:
        nodes = self.read("MATCH (n) RETURN count(n)")
        edges = self.read("MATCH ()-[e]->() RETURN count(e)")
        return (int(nodes[0][0]) if nodes else 0, int(edges[0][0]) if edges else 0)

    def drop(self) -> None:
        try:
            self.graph.delete()
        except ResponseError as e:
            if "empty key" not in str(e):
                raise
