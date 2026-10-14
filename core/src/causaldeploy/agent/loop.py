"""The investigation agent (PLAN section 7.2).

The agent lists tools from the MCP graph server, chooses which to call, and finally returns a
structured explanation whose claims cite node ids. Ranking stays deterministic: the agent
explains it or abstains, and a validator rejects any claim citing a node no tool returned.
"""

import json
import time
from typing import Any

from mcp import Client
from pydantic import ValidationError

from causaldeploy.agent.llm import LlmClient
from causaldeploy.agent.models import AgentRun, Explanation, ToolStep
from causaldeploy.agent.validator import validate
from causaldeploy.graph.store import GraphStore

MAX_TOOL_CALLS = 8
SYSTEM_PROMPT = """You are Kaizen's incident investigator. Use the graph tools to find which \
commit and function caused a production incident, then explain it.

Rules:
1. Start with get_incident and rank_candidates.
2. If the ranking is confident, confirm the top candidate with shortest_call_path.
3. If it is ambiguous, use blast_radius, similar_incidents or dependency_impact to explain the tie.
4. NEVER override the ranking: you explain it or you abstain (verdict ambiguous / not_code).
5. Every claim must cite node ids exactly as returned in `node_ids` of tool results (Label:key).
6. Call at most 8 tools. Then reply with ONLY a JSON object:
{"verdict": "confident|ambiguous|not_code", "culprit_sha": str|null, "culprit_function": str|null,
 "claims": [{"text": str, "cited_node_ids": [str, ...]}], "suggested_fix": str|null,
 "responders": [str]}"""


def openai_tools(listed: Any) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.input_schema,
            },
        }
        for t in listed.tools
    ]


def _parse(content: str | None) -> tuple[Explanation | None, str | None]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        return Explanation.model_validate(json.loads(text)), None
    except (json.JSONDecodeError, ValidationError) as e:
        return None, f"final answer is not valid JSON for the schema: {str(e)[:300]}"


async def run_agent(
    client: Client,
    store: GraphStore,
    incident_id: str,
    llm: LlmClient,
    max_tool_calls: int = MAX_TOOL_CALLS,
) -> AgentRun:
    tools = openai_tools(await client.list_tools())
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Investigate incident {incident_id}."},
    ]
    steps: list[ToolStep] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    retried = False

    while True:
        out_of_budget = len(steps) >= max_tool_calls
        reply = await llm.chat(messages, [] if out_of_budget else tools)
        for k, v in reply.usage.items():
            usage[k] = usage.get(k, 0) + v
        messages.append(reply.raw or {"role": "assistant", "content": reply.content})

        if reply.tool_calls and not out_of_budget:
            for call in reply.tool_calls:
                if len(steps) >= max_tool_calls:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": '{"error": "tool budget exhausted"}',
                        }
                    )
                    continue
                started = time.perf_counter()
                res = await client.call_tool(call.name, call.arguments)
                text = res.content[0].text if res.content else "{}"  # type: ignore[union-attr]
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = {"error": text, "node_ids": []}
                if res.is_error:
                    payload = {"error": text, "node_ids": []}
                step = ToolStep(
                    n=len(steps) + 1,
                    name=call.name,
                    args=call.arguments,
                    summary=_summarize(call.name, payload),
                    latency_ms=round((time.perf_counter() - started) * 1000, 1),
                    node_ids=list(payload.get("node_ids", [])),
                    result=payload,
                )
                steps.append(step)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": json.dumps(payload)}
                )
            continue

        explanation, parse_error = _parse(reply.content)
        errors = (
            [parse_error]
            if parse_error
            else (validate(explanation, steps, store) if explanation else [])
        )
        if explanation and not errors:
            return AgentRun(
                incident_id=incident_id,
                status="explained",
                explanation=explanation,
                steps=steps,
                usage=usage,
            )
        if not retried:
            retried = True
            messages.append(
                {
                    "role": "user",
                    "content": "Your answer was rejected:\n- "
                    + "\n- ".join(errors)
                    + "\nFix these and reply with the JSON only.",
                }
            )
            continue
        return AgentRun(
            incident_id=incident_id,
            status="explanation_invalid",
            explanation=None,
            steps=steps,
            validation_errors=errors,
            usage=usage,
        )


def _summarize(name: str, payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return f"error: {str(payload['error'])[:120]}"
    keys = [k for k in payload if k != "node_ids"]
    parts = []
    for k in keys[:3]:
        v = payload[k]
        parts.append(f"{k}={len(v)} item(s)" if isinstance(v, list) else f"{k}={str(v)[:40]}")
    return f"{name}: " + ", ".join(parts)
