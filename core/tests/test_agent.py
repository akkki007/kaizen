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

    model = "gpt-4o-mini"

    def __init__(self, replies: list[LlmReply]) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict[str, Any]]] = []
        self.tools_seen: list[list[str]] = []

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmReply:
        self.seen.append([dict(m) for m in messages])
        self.tools_seen.append([t["function"]["name"] for t in tools])
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


async def test_two_invalid_answers_fall_back_to_the_template(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    bad = {**GOOD, "claims": [{"text": "x", "cited_node_ids": ["Commit:deadbeef"]}]}
    llm = Scripted([call(1, "rank_candidates", incident_id=sc.incident_id), final(bad), final(bad)])
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, sc.incident_id, llm)  # type: ignore[arg-type]
    assert run.status == "fallback" and run.validation_errors  # the model's errors are kept
    expl = run.explanation
    assert expl is not None and expl.source == "template"
    assert expl.verdict == "confident" and expl.culprit_sha == sc.culprit_commit
    assert expl.culprit_function == sc.culprit_functions[0]
    cited = {n for c in expl.claims for n in c.cited_node_ids}
    assert f"Commit:{sc.culprit_commit}" in cited
    assert expl.causal_chain and all(s.cited_node_ids for s in expl.causal_chain)
    assert "Deployment:dep-842" in {n for s in expl.causal_chain for n in s.cited_node_ids}


async def test_template_abstains_when_there_is_no_code_evidence(builder: GraphBuilder) -> None:
    """No candidates at all: the template says not_code instead of inventing a culprit."""
    builder.function("svc", "src/a.js", "a")
    builder.incident("inc-x", service="svc")
    bad = {**GOOD, "claims": [{"text": "x", "cited_node_ids": ["Commit:deadbeef"]}]}
    llm = Scripted([final(bad), final(bad)])
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, "inc-x", llm)  # type: ignore[arg-type]
    assert run.status == "fallback" and run.explanation
    assert run.explanation.verdict == "not_code" and run.explanation.culprit_sha is None
    assert run.explanation.claims[0].cited_node_ids == ["Incident:inc-x"]


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
    assert run.status == "fallback"  # never the model's override
    assert any("contradicts the confident ranking" in e for e in run.validation_errors)
    assert run.explanation and run.explanation.culprit_sha == sc.culprit_commit


async def test_tool_budget_is_enforced(builder: GraphBuilder) -> None:
    sc = builder.s1_payment_validation()
    replies = [call(i, "get_incident", incident_id=sc.incident_id) for i in range(1, 12)]
    llm = Scripted([*replies[:8], final(GOOD)])
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, sc.incident_id, llm, max_tool_calls=3)  # type: ignore[arg-type]
    assert len(run.steps) == 3
    # once the tool budget is spent the model is asked without tools; asking again ends the run
    assert llm.tools_seen[3] == []
    assert run.status == "budget_exhausted" and "step budget" in (run.abort_reason or "")


async def test_explanation_with_causal_chain_alternatives_and_confidence(
    builder: GraphBuilder,
) -> None:
    sc = builder.s1_payment_validation()
    v = sc.culprit_functions[0]
    rich = {
        **GOOD,
        "culprit_function": v,
        "causal_chain": [
            {
                "step": "dep-842 shipped c0ffee01",
                "cited_node_ids": ["Deployment:dep-842", "Commit:c0ffee01"],
            },
            {"step": "c0ffee01 changed validatePayment", "cited_node_ids": [f"Function:{v}"]},
        ],
        "confidence_statement": "The ranking is confident (margin 0.53).",
        "alternatives": [
            {"sha": "n0000001", "reason": "docs only", "cited_node_ids": ["Commit:n0000001"]}
        ],
    }
    llm = Scripted(
        [
            call(1, "get_deployment_commits", incident_id=sc.incident_id),
            call(2, "rank_candidates", incident_id=sc.incident_id),
            final(rich),
        ]
    )
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, sc.incident_id, llm)  # type: ignore[arg-type]
    assert run.status == "explained" and run.explanation
    assert run.explanation.causal_chain[0].cited_node_ids[0] == "Deployment:dep-842"
    assert run.explanation.alternatives[0].sha == "n0000001"
    assert run.explanation.source == "model"
    assert run.prompt_version and run.prompt_version.startswith("investigator.v1-")


async def test_fabricated_citation_in_causal_chain_or_alternatives_is_rejected(
    builder: GraphBuilder,
) -> None:
    sc = builder.s1_payment_validation()
    bad = {
        **GOOD,
        "causal_chain": [{"step": "made up", "cited_node_ids": ["Function:ghost:fn"]}],
        "alternatives": [{"sha": "x", "reason": "made up", "cited_node_ids": ["Commit:feedface"]}],
    }
    llm = Scripted(
        [call(1, "rank_candidates", incident_id=sc.incident_id), final(bad), final(GOOD)]
    )
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, sc.incident_id, llm)  # type: ignore[arg-type]
    assert run.status == "explained"
    retry = llm.seen[-1][-1]["content"]
    assert "causal_chain step 1: 'Function:ghost:fn'" in retry
    assert "alternative 1: 'Commit:feedface'" in retry


def ambiguous_incident(builder: GraphBuilder) -> str:
    """Two commits change the same thrown function in one deployment: a genuine tie."""
    v = builder.function("payments", "src/payment/validation.js", "validatePayment")
    builder.function(
        "payments", "src/routes.js", "handlePayment", calls=[v], endpoint="POST /payments"
    )
    a = builder.commit("aaaa0001", 200, modifies_fn=[v], message="tweak validation")
    b = builder.commit("bbbb0002", 200, modifies_fn=[v], message="tweak validation again")
    builder.deployment("dep-9", "prod", [a, b], 300)
    builder.error("fp-amb", thrown_in=[v], endpoint="payments:POST /payments", service="payments")
    builder.incident(
        "inc-amb",
        service="payments",
        deployment="dep-9",
        errors=["fp-amb"],
        endpoint="POST /payments",
    )
    return "inc-amb"


async def test_ambiguous_ranking_forces_abstention_with_alternatives(
    builder: GraphBuilder,
) -> None:
    inc = ambiguous_incident(builder)
    confident = {
        "verdict": "confident",
        "culprit_sha": "aaaa0001",
        "claims": [{"text": "aaaa0001 did it", "cited_node_ids": ["Commit:aaaa0001"]}],
    }
    abstain = {
        "verdict": "ambiguous",
        "culprit_sha": None,
        "claims": [
            {
                "text": "Two commits changed validatePayment",
                "cited_node_ids": ["Commit:aaaa0001", "Commit:bbbb0002"],
            }
        ],
        "confidence_statement": "Ambiguous: both commits changed the throwing function.",
        "alternatives": [
            {"sha": "aaaa0001", "reason": "tied", "cited_node_ids": ["Commit:aaaa0001"]},
            {"sha": "bbbb0002", "reason": "tied", "cited_node_ids": ["Commit:bbbb0002"]},
        ],
    }
    llm = Scripted([call(1, "rank_candidates", incident_id=inc), final(confident), final(abstain)])
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, inc, llm)  # type: ignore[arg-type]
    assert run.steps[0].result["verdict"] == "ambiguous"
    assert "the ranking is ambiguous" in llm.seen[-1][-1]["content"]
    assert run.status == "explained" and run.explanation
    assert run.explanation.verdict == "ambiguous" and len(run.explanation.alternatives) == 2


async def test_template_abstains_on_an_ambiguous_ranking(builder: GraphBuilder) -> None:
    inc = ambiguous_incident(builder)
    llm = Scripted([final({"verdict": "confident"}), final({"nope": 1})])
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(c, builder.store, inc, llm)  # type: ignore[arg-type]
    expl = run.explanation
    assert run.status == "fallback" and expl is not None
    assert expl.verdict == "ambiguous" and expl.culprit_sha is None
    assert "ambiguous" in (expl.confidence_statement or "")
    assert {a.sha for a in expl.alternatives} == {"aaaa0001", "bbbb0002"}


async def test_real_openai_sdk_client_drives_the_agent_over_http(
    builder: GraphBuilder, monkeypatch: Any
) -> None:
    """The production OpenAIClient (real SDK, real HTTP) against a local chat-completions server.

    Proves request/response serialization and tool-call parsing; it is not a live model.
    """
    import socket
    import threading
    import time

    import uvicorn
    from fastapi import FastAPI, Request

    from causaldeploy.agent.llm import OpenAIClient

    sc = builder.s1_payment_validation()
    script = [
        ("get_incident", {"incident_id": sc.incident_id}),
        ("rank_candidates", {"incident_id": sc.incident_id}),
        None,
    ]
    app = FastAPI()
    seen: list[dict[str, Any]] = []

    @app.post("/v1/chat/completions")
    async def completions(req: Request) -> dict[str, Any]:
        body = await req.json()
        seen.append(body)
        step = script[min(len([m for m in body["messages"] if m["role"] == "tool"]), 2)]
        if step is None:
            msg: dict[str, Any] = {"role": "assistant", "content": json.dumps(GOOD)}
        else:
            msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{len(seen)}",
                        "type": "function",
                        "function": {"name": step[0], "arguments": json.dumps(step[1])},
                    }
                ],
            }
        return {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
    try:
        monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{port}/v1")
        llm = OpenAIClient(api_key="test-key", model="m")
        async with Client(build_server(builder.store.repo, builder.store)) as c:
            run = await run_agent(c, builder.store, sc.incident_id, llm)
    finally:
        server.should_exit = True
        t.join(5)
    assert run.status == "explained"
    assert [s.name for s in run.steps] == ["get_incident", "rank_candidates"]
    assert seen[0]["tools"], "tool schemas are sent to the model"
    # the final answer is requested as structured output; tool calling is unchanged
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert "claims" in seen[0]["response_format"]["json_schema"]["schema"]["properties"]


def resolved_similar_incident(builder: GraphBuilder, incident_id: str) -> str:
    """A resolved earlier incident that resembles `incident_id`, fixed by a revert."""
    from causaldeploy.graph.store import Edge

    builder.incident("inc-0", service="payments", status="resolved", endpoint="POST /payments")
    builder.store.upsert_nodes("Fix", [{"id": "fix-0", "kind": "revert"}])
    builder.store.upsert_edges(
        [
            Edge(
                "Incident",
                incident_id,
                "resembles",
                "Incident",
                "inc-0",
                {"similarity": 0.9, "basis": ["same thrown function"]},
            ),
            Edge("Incident", "inc-0", "fixed_by", "Fix", "fix-0"),
        ]
    )
    return "inc-0"


async def test_what_else_could_be_affected(builder: GraphBuilder) -> None:
    """Follow-up question: blast radius + similar incidents; the answer cites real Endpoint/
    Service nodes the tools returned (validated against the graph)."""
    sc = builder.s4_cross_service()
    prior = resolved_similar_incident(builder, sc.incident_id)
    fn = sc.culprit_functions[0]
    answer = {
        "verdict": "confident",
        "culprit_sha": sc.culprit_commit,
        "culprit_function": fn,
        "claims": [
            {
                "text": "createOrder is called by the gateway's POST /orders handler",
                "cited_node_ids": ["Endpoint:gateway:POST /orders", "Service:gateway"],
            },
            {
                "text": "orders and gateway are affected beyond payments",
                "cited_node_ids": ["Service:orders", "Service:gateway"],
            },
            {
                "text": "a similar incident was resolved by a revert before",
                "cited_node_ids": [f"Incident:{prior}", "Fix:fix-0"],
            },
        ],
    }
    llm = Scripted(
        [
            call(1, "rank_candidates", incident_id=sc.incident_id),
            call(2, "blast_radius", sha=sc.culprit_commit),
            call(3, "similar_incidents", incident_id=sc.incident_id),
            final(answer),
        ]
    )
    async with Client(build_server(builder.store.repo, builder.store)) as c:
        run = await run_agent(
            c,
            builder.store,
            sc.incident_id,
            llm,  # type: ignore[arg-type]
            question="What else could be affected?",
        )
    assert "What else could be affected?" in llm.seen[0][1]["content"]
    assert run.status == "explained", run.validation_errors
    assert run.explanation
    cited = {n for c in run.explanation.claims for n in c.cited_node_ids}
    for nid in ("Endpoint:gateway:POST /orders", "Service:orders", "Service:gateway"):
        assert nid in cited
        label, _, key = nid.partition(":")
        assert builder.store.read(f"MATCH (n:{label} {{id: $k}}) RETURN 1", {"k": key})
