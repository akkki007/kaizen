"""The investigation agent (PLAN section 7.2).

The agent lists tools from the MCP graph server, chooses which to call, and finally returns a
structured explanation whose claims cite node ids. Ranking stays deterministic: the agent
explains it or abstains, and a validator rejects any citation of a node no tool returned.

Harness guarantees (#90):
* every message is redacted before it leaves (`safety.redact_payload`), and tool output is fenced
  as untrusted data the prompt forbids taking instructions from;
* only the read-only graph tools the server lists can be called; anything else is refused;
* a Budget (tool calls, tokens, USD) bounds the run: once spent the run stops with
  `budget_exhausted` and an `abort_reason`;
* two invalid answers fall back to a deterministic template explanation (`fallback`);
* every model round trip is kept as an `LlmCallRecord` (persisted to llm_calls by the caller).
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp import Client
from pydantic import ValidationError

from causaldeploy.agent import prompts, template
from causaldeploy.agent.llm import LlmClient, LlmReply, reply_to_dict
from causaldeploy.agent.models import AgentRun, Explanation, LlmCallRecord, RunStatus, ToolStep
from causaldeploy.agent.replay import ReplayMissError, cache_key
from causaldeploy.agent.safety import payload_hash, redact_payload, wrap_untrusted
from causaldeploy.agent.validator import validate
from causaldeploy.config import get_settings
from causaldeploy.graph.store import GraphStore
from causaldeploy.mcp_server.tools import READ_ONLY_TOOL_NAMES

log = logging.getLogger(__name__)

MAX_TOOL_CALLS = 8
SYSTEM_PROMPT = prompts.load(prompts.INVESTIGATOR).text  # kept for importers; see prompts/


@dataclass(frozen=True)
class Budget:
    max_steps: int  # tool calls
    max_tokens: int  # input + output, all calls
    max_cost_usd: float

    @classmethod
    def from_settings(cls) -> "Budget":
        s = get_settings()
        return cls(s.llm_max_steps, s.llm_max_tokens, s.llm_max_cost_usd)


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
        parsed = Explanation.model_validate(json.loads(text))
        return parsed.model_copy(update={"source": "model"}), None
    except (json.JSONDecodeError, ValidationError) as e:
        return None, f"final answer is not valid JSON for the schema: {str(e)[:300]}"


class _Run:
    """State of one agent run (messages, steps, usage, call records)."""

    def __init__(
        self,
        client: Client,
        store: GraphStore,
        incident_id: str,
        llm: LlmClient,
        budget: Budget,
        run_id: str,
        clock: Callable[[], float],
    ) -> None:
        self.client, self.store, self.incident_id, self.llm = client, store, incident_id, llm
        self.budget, self.run_id, self.clock = budget, run_id, clock
        self.prompt = prompts.load(prompts.INVESTIGATOR)
        self.model = str(getattr(llm, "model", "") or get_settings().openai_model or "unknown")
        self.steps: list[ToolStep] = []
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.cost = 0.0
        self.calls: list[LlmCallRecord] = []
        self.messages: list[dict[str, Any]] = []

    # -- model calls ------------------------------------------------------------------------
    async def chat(self, tools: list[dict[str, Any]]) -> LlmReply:
        safe: list[dict[str, Any]] = redact_payload(self.messages)  # nothing unredacted leaves
        key = cache_key(self.model, self.prompt.version, safe, tools)
        request = {"messages": safe, "tools": [t["function"]["name"] for t in tools]}
        started = self.clock()
        try:
            reply = await self.llm.chat(safe, tools)
        except Exception as e:
            self._record(len(self.calls) + 1, key, request, tools, None, started, error=e)
            raise
        self._record(len(self.calls) + 1, key, request, tools, reply, started)
        return reply

    def _record(
        self,
        n: int,
        key: str,
        request: dict[str, Any],
        tools: list[dict[str, Any]],
        reply: LlmReply | None,
        started: float,
        error: Exception | None = None,
    ) -> None:
        latency = round((self.clock() - started) * 1000, 1)
        tin = int(reply.usage.get("input_tokens", 0)) if reply else 0
        tout = int(reply.usage.get("output_tokens", 0)) if reply else 0
        cost = get_settings().llm_cost_usd(self.model, tin, tout)
        self.usage["input_tokens"] += tin
        self.usage["output_tokens"] += tout
        self.cost = round(self.cost + cost, 6)
        self.calls.append(
            LlmCallRecord(
                n=n,
                model=self.model,
                prompt_version=self.prompt.version,
                prompt_hash=key,
                payload_sha256=payload_hash({"messages": request["messages"], "tools": tools}),
                status="error"
                if reply is None
                else ("replayed" if reply.source == "replayed" else "ok"),
                request=request,
                response=reply_to_dict(reply) if reply else {"error": type(error).__name__},
                input_tokens=tin,
                output_tokens=tout,
                cost_usd=cost,
                latency_ms=latency,
                attempts=reply.attempts if reply else 1,
            )
        )

    def over_budget(self) -> str | None:
        b, total = self.budget, self.usage["input_tokens"] + self.usage["output_tokens"]
        if total > b.max_tokens:
            return f"token budget exceeded: {total} > {b.max_tokens} tokens"
        if self.cost > b.max_cost_usd:
            return f"cost budget exceeded: ${self.cost:.4f} > ${b.max_cost_usd:.4f}"
        return None

    # -- tools ------------------------------------------------------------------------------
    async def call_tool(self, name: str, args: dict[str, Any], allowed: set[str]) -> ToolStep:
        started = self.clock()
        if name not in allowed:
            payload: dict[str, Any] = {
                "error": f"tool '{name}' is not available: only the read-only graph tools are",
                "node_ids": [],
            }
        else:
            res = await self.client.call_tool(name, args)
            text = res.content[0].text if res.content else "{}"  # type: ignore[union-attr]
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = {"error": text, "node_ids": []}
            if res.is_error or not isinstance(payload, dict):
                payload = {"error": text, "node_ids": []}
        step = ToolStep(
            n=len(self.steps) + 1,
            name=name,
            args=args,
            summary=_summarize(name, payload),
            latency_ms=round((self.clock() - started) * 1000, 1),
            node_ids=list(payload.get("node_ids", [])),
            result=payload,
        )
        self.steps.append(step)
        return step

    # -- endings ----------------------------------------------------------------------------
    def finish(
        self,
        status: RunStatus,
        explanation: Explanation | None,
        errors: list[str] | None = None,
        abort_reason: str | None = None,
    ) -> AgentRun:
        return AgentRun(
            incident_id=self.incident_id,
            status=status,
            explanation=explanation,
            steps=self.steps,
            validation_errors=errors or [],
            usage=self.usage,
            run_id=self.run_id,
            prompt_version=self.prompt.version,
            model=self.model,
            cost_usd=self.cost,
            abort_reason=abort_reason,
            llm_calls=self.calls,
        )

    async def templated(self) -> tuple[Explanation | None, list[str]]:
        """The deterministic explanation, validated like a model answer."""
        try:
            expl, returned = await asyncio.to_thread(template.build, self.store, self.incident_id)
        except Exception as e:  # the template must never take the run down with it
            log.exception("template explanation failed")
            return None, [f"template explanation failed: {type(e).__name__}"]
        errors = await asyncio.to_thread(validate, expl, self.steps, self.store, returned)
        return (None if errors else expl), errors

    async def abort(self, status: RunStatus, reason: str) -> AgentRun:
        log.warning(
            f"agent run {status}: {reason}",
            extra={
                "ctx": {
                    "run_id": self.run_id,
                    "incident_id": self.incident_id,
                    "status": status,
                    "abort_reason": reason,
                    "tool_calls": len(self.steps),
                    "tokens": self.usage["input_tokens"] + self.usage["output_tokens"],
                    "cost_usd": self.cost,
                }
            },
        )
        expl, _ = await self.templated()
        return self.finish(status, expl, abort_reason=reason)


async def run_agent(
    client: Client,
    store: GraphStore,
    incident_id: str,
    llm: LlmClient,
    max_tool_calls: int | None = None,
    *,
    budget: Budget | None = None,
    question: str | None = None,
    run_id: str | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> AgentRun:
    """Investigate one incident. `question` adds a follow-up ("what else could be affected?")."""
    b = budget or Budget.from_settings()
    if max_tool_calls is not None:
        b = Budget(max_tool_calls, b.max_tokens, b.max_cost_usd)
    run = _Run(client, store, incident_id, llm, b, run_id or uuid.uuid4().hex, clock)

    listed = await client.list_tools()
    tools = [t for t in openai_tools(listed) if t["function"]["name"] in READ_ONLY_TOOL_NAMES]
    allowed = {t["function"]["name"] for t in tools}
    ask = f"Investigate incident {incident_id}."
    if question:
        ask += f"\nThe responder also asks: {question}"
    run.messages = [
        {"role": "system", "content": run.prompt.text},
        {"role": "user", "content": redact_payload(ask)},
    ]
    retried = False

    while True:
        if reason := run.over_budget():
            return await run.abort("budget_exhausted", reason)
        out_of_steps = len(run.steps) >= b.max_steps
        try:
            reply = await run.chat([] if out_of_steps else tools)
        except ReplayMissError:
            raise
        except Exception as e:
            return await run.abort("error", f"LLM call failed: {type(e).__name__}: {e}"[:500])
        run.messages.append(
            redact_payload(reply.raw or {"role": "assistant", "content": reply.content})
        )

        if reply.tool_calls:
            if out_of_steps:
                return await run.abort(
                    "budget_exhausted",
                    f"step budget exhausted: {b.max_steps} tool call(s) used and the model "
                    "asked for more instead of answering",
                )
            for call in reply.tool_calls:
                if len(run.steps) >= b.max_steps:
                    run.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": '{"error": "tool budget exhausted"}',
                        }
                    )
                    continue
                step = await run.call_tool(call.name, call.arguments, allowed)
                run.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": wrap_untrusted(
                            f"tool:{call.name}", json.dumps(redact_payload(step.result))
                        ),
                    }
                )
            continue

        explanation, parse_error = _parse(reply.content)
        errors = (
            [parse_error]
            if parse_error
            else (
                await asyncio.to_thread(validate, explanation, run.steps, store)
                if explanation
                else []
            )
        )
        if explanation and not errors:
            return run.finish("explained", explanation)
        if not retried:
            retried = True
            run.messages.append(
                {
                    "role": "user",
                    "content": "Your answer was rejected:\n- "
                    + "\n- ".join(errors)
                    + "\nFix these and reply with the JSON only.",
                }
            )
            continue
        fallback, template_errors = await run.templated()
        if fallback is not None:
            return run.finish("fallback", fallback, errors)
        return run.finish("explanation_invalid", None, errors + template_errors)


def _summarize(name: str, payload: dict[str, Any]) -> str:
    if payload.get("error"):
        return f"error: {str(payload['error'])[:120]}"
    keys = [k for k in payload if k not in ("node_ids", "truncated")]
    parts = []
    for k in keys[:3]:
        v = payload[k]
        parts.append(f"{k}={len(v)} item(s)" if isinstance(v, list) else f"{k}={str(v)[:40]}")
    return f"{name}: " + ", ".join(parts)
