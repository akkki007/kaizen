"""Persist an agent run: summaries + citations in the graph, full payloads in Postgres."""

from causaldeploy.agent.models import AgentRun
from causaldeploy.graph.schema import NODE_KEYS
from causaldeploy.graph.store import Edge, GraphStore


def write_trace(store: GraphStore, run: AgentRun, investigation_id: str) -> None:
    """(Incident)-[:investigated_by]->(Investigation)-[:step {n}]->(ToolCall)-[:cited]->(node)."""
    store.ensure_schema()
    store.upsert_nodes(
        "Investigation",
        [
            {
                "id": investigation_id,
                "status": run.status,
                "tool_calls": len(run.steps),
                "verdict": run.explanation.verdict if run.explanation else "none",
            }
        ],
    )
    edges = [
        Edge("Incident", run.incident_id, "investigated_by", "Investigation", investigation_id)
    ]
    for s in run.steps:
        call_id = f"{investigation_id}:{s.n}"
        store.upsert_nodes(
            "ToolCall",
            [
                {
                    "id": call_id,
                    "name": s.name,
                    "summary": s.summary[:200],
                    "n": s.n,
                    "latency_ms": s.latency_ms,
                }
            ],
        )
        edges.append(
            Edge("Investigation", investigation_id, "step", "ToolCall", call_id, {"n": s.n})
        )
        for nid in s.node_ids:
            label, _, key = nid.partition(":")
            if label in NODE_KEYS and key:
                try:
                    edges.append(Edge("ToolCall", call_id, "cited", label, key))
                except ValueError:
                    continue
    valid = []
    from causaldeploy.graph.schema import RELATIONSHIPS

    for e in edges:
        if (e.src_label, e.dst_label) in RELATIONSHIPS.get(e.rel, frozenset()):
            valid.append(e)
    store.upsert_edges(valid)
