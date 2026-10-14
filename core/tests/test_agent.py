import json
from typing import Any

from mcp import Client

from causaldeploy.agent.llm import LlmReply, LlmToolCall
from causaldeploy.agent.loop import run_agent
from causaldeploy.agent.persist import write_trace
from causaldeploy.graph.builder import GraphBuilder
from causaldeploy.mcp_server.server import build_server


class Scripted:
    """Plays back a fixed sequence of replies; records what the agent sent."""

    def __init__(self, replies: list[LlmReply]) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmReply:
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


def call(i: int, name: str, **args: Any) -> LlmReply:
    tc = LlmToolCall(f"c{i}", name, args)
    return LlmReply(
        None,
        [tc],
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
        },
    )


def final(obj: dict[str, Any]) -> LlmReply:
    return LlmReply(json.dumps(obj), [], {"role": "assistant", "content": json.dumps(obj)})


GOOD = {
    "verdict": "confident",
    "culprit_sha": "c0ffee01",
    "claims": [
        {
            "text": "validatePayment was changed and is where the error was thrown",
            "cited_node_ids": [
                "Commit:c0ffee01",
                "Function:payments:src/payment/validation.js:validatePayment",
            ],
        }
    ],
    "suggested_fix": "revert c0ffee01",
    "responders": [],
}


async def test_agent_chooses_tools_cites_returned_nodes_and_is_validated(
    builder: GraphBuilder,
) -> None:
    sc = builder.s1_payment_validation()
    llm = Scripted(
        [
            call(1, "get_incident", incident_id=sc.incident_id),
            call(2, "rank_candidates", incident_id=sc.incident_id),
            call(3, "shortest_call_path", sha=sc.culprit_commit, incident_id=sc.incident_id),
            final(GOOD),
        ]
    )
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, sc.incident_id, llm)  # type: ignore[arg-type]
    assert run.status == "explained" and run.explanation
    assert [s.name for s in run.steps] == ["get_incident", "rank_candidates", "shortest_call_path"]
    assert run.steps[2].node_ids and run.explanation.culprit_sha == sc.culprit_commit
    # tool results are fed back to the model
    assert any(m.get("role") == "tool" for m in llm.seen[-1])

    write_trace(builder.store, run, "inv-1")
    rows = builder.store.read(
        "MATCH (:Incident {id: $i})-[:investigated_by]->(:Investigation {id: 'inv-1'})"
        "-[s:step]->(t:ToolCall) RETURN s.n, t.name ORDER BY s.n",
        {"i": sc.incident_id},
    )
    assert rows == [[1, "get_incident"], [2, "rank_candidates"], [3, "shortest_call_path"]]
    cited = builder.store.read(
        "MATCH (:ToolCall {id: 'inv-1:3'})-[:cited]->(n) RETURN labels(n)[0], count(*) ORDER BY 1"
    )
    assert any(r[0] == "Function" for r in cited)


async def test_hallucinated_citation_is_rejected_then_corrected(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    bad = {**GOOD, "claims": [{"text": "x", "cited_node_ids": ["Function:ghost:fn"]}]}
    llm = Scripted(
        [call(1, "rank_candidates", incident_id=sc.incident_id), final(bad), final(GOOD)]
    )
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, sc.incident_id, llm)  # type: ignore[arg-type]
    assert run.status == "explained"
    retry_msg = llm.seen[-1][-1]["content"]
    assert "Function:ghost:fn" in retry_msg and "not returned by any tool" in retry_msg


async def test_two_invalid_answers_fall_back_to_explanation_invalid(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    bad = {**GOOD, "claims": [{"text": "x", "cited_node_ids": ["Commit:deadbeef"]}]}
    llm = Scripted([call(1, "rank_candidates", incident_id=sc.incident_id), final(bad), final(bad)])
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, sc.incident_id, llm)  # type: ignore[arg-type]
    assert run.status == "explanation_invalid" and run.explanation is None and run.validation_errors


async def test_agent_cannot_override_a_confident_ranking(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    wrong = {
        **GOOD,
        "culprit_sha": "n0000001",
        "claims": [{"text": "the docs commit did it", "cited_node_ids": ["Commit:n0000001"]}],
    }
    llm = Scripted(
        [
            call(1, "rank_candidates", incident_id=sc.incident_id),
            call(2, "get_deployment_commits", incident_id=sc.incident_id),
            final(wrong),
            final(wrong),
        ]
    )
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, sc.incident_id, llm)  # type: ignore[arg-type]
    assert run.status == "explanation_invalid"
    assert any("contradicts the confident ranking" in e for e in run.validation_errors)


async def test_tool_budget_is_enforced(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    replies = [call(i, "get_incident", incident_id=sc.incident_id) for i in range(1, 12)]
    llm = Scripted([*replies[:8], final(GOOD)])
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, sc.incident_id, llm, max_tool_calls=3)  # type: ignore[arg-type]
    assert len(run.steps) == 3
