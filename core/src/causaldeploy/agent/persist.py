"""Persist an agent run: summaries + citations in the graph, full payloads in Postgres."""

from causaldeploy.agent.models import AgentRun
from causaldeploy.graph.schema import NODE_KEYS
from causaldeploy.graph.store import Edge, GraphStore


async def save_llm_calls(run: AgentRun, actor: str = "agent") -> int:
    """One llm_calls row per model round trip (redacted request, response, tokens, cost,
    latency) and one audit_log `llm.send` row per request carrying only its payload hash."""
    from causaldeploy.db.models import AuditLog, LlmCall
    from causaldeploy.db.session import session_scope

    if not run.llm_calls:
        return 0
    async with session_scope() as s:
        for c in run.llm_calls:
            s.add(
                LlmCall(
                    incident_id=run.incident_id,
                    run_id=run.run_id,
                    model=c.model[:64],
                    prompt_version=c.prompt_version[:32],
                    prompt_hash=c.prompt_hash,
                    status=c.status,
                    request=c.request,
                    response=c.response,
                    input_tokens=c.input_tokens,
                    output_tokens=c.output_tokens,
                    cost_usd=c.cost_usd,
                    latency_ms=c.latency_ms,
                )
            )
            s.add(
                AuditLog(
                    actor=actor,
                    action="llm.send",
                    target=run.incident_id,
                    detail={
                        "run_id": run.run_id,
                        "n": c.n,
                        "model": c.model,
                        "prompt_version": c.prompt_version,
                        "payload_sha256": c.payload_sha256,
                        "status": c.status,
                    },
                )
            )
    return len(run.llm_calls)


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
