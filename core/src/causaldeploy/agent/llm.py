"""LLM access for the agent. The model is configuration (`CD_OPENAI_MODEL`), never hard-coded.

`OpenAIClient` owns the transport policy: an explicit per-attempt timeout, the SDK's own retries
disabled, and one retry loop (exponential backoff with full jitter, Retry-After honoured) for
429s, timeouts, connection errors and 5xx. The final answer is requested as structured output
(`response_format: json_schema`) where the endpoint supports it; tool calling is unchanged.
"""

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from causaldeploy.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class LlmToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LlmReply:
    content: str | None
    tool_calls: list[LlmToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)  # assistant message to append verbatim
    usage: dict[str, int] = field(default_factory=dict)
    attempts: int = 1  # HTTP attempts it took (retries + 1)
    source: str = "live"  # live | replayed


class LlmClient(Protocol):
    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmReply: ...


class LlmUnavailableError(RuntimeError):
    """The provider kept failing (or failed permanently) after the retry policy ran out."""


def reply_to_dict(r: LlmReply) -> dict[str, Any]:
    return {
        "content": r.content,
        "tool_calls": [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in r.tool_calls
        ],
        "raw": r.raw,
        "usage": r.usage,
    }


def reply_from_dict(d: dict[str, Any], source: str = "live") -> LlmReply:
    return LlmReply(
        content=d.get("content"),
        tool_calls=[LlmToolCall(c["id"], c["name"], c["arguments"]) for c in d["tool_calls"]],
        raw=d.get("raw") or {},
        usage=d.get("usage") or {},
        source=source,
    )


def backoff_delay(
    attempt: int,
    retry_after: float | None,
    base: float,
    cap: float,
    rand: Callable[[float, float], float] = random.uniform,
) -> float:
    """Retry-After when the server sent one, else full jitter: U(0, min(cap, base * 2^attempt))."""
    if retry_after is not None:
        return min(max(retry_after, 0.0), cap)
    return rand(0.0, min(cap, base * (2**attempt)))


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        if ms := headers.get("retry-after-ms"):
            return float(ms) / 1000
        if sec := headers.get("retry-after"):
            return float(sec)
    except ValueError:  # an HTTP date: fall back to jittered backoff
        return None
    return None


class OpenAIClient:
    """Chat Completions with tool calling, temperature 0, bounded retries."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
        structured_output: bool | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        from openai import AsyncOpenAI

        s = get_settings()
        key = api_key or s.openai_api_key.get_secret_value()
        self.model = model or s.openai_model
        if not key or not self.model:
            raise RuntimeError("CD_OPENAI_API_KEY and CD_OPENAI_MODEL must be set to run the agent")
        self.timeout = timeout if timeout is not None else s.llm_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else s.llm_max_retries
        self.structured_output = (
            s.llm_structured_output if structured_output is None else structured_output
        )
        self._base, self._cap = s.llm_backoff_base_seconds, s.llm_backoff_max_seconds
        self._sleep = sleep
        # the SDK would retry on its own (2x by default) on top of ours: disabled
        self._client = AsyncOpenAI(api_key=key, timeout=self.timeout, max_retries=0)

    def _response_format(self) -> dict[str, Any]:
        from causaldeploy.agent.models import Explanation

        schema = Explanation.model_json_schema()
        schema["properties"].pop("source", None)
        return {
            "type": "json_schema",
            "json_schema": {"name": "explanation", "schema": schema, "strict": False},
        }

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmReply:
        import openai

        attempt = 0
        while True:
            kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
            if tools:
                kwargs["tools"] = tools
            if self.structured_output:
                kwargs["response_format"] = self._response_format()
            try:
                resp = await self._client.chat.completions.create(**kwargs)
            except openai.BadRequestError as e:
                if self.structured_output and "response_format" in str(e):
                    # endpoint/model without json_schema support: plain JSON-in-content instead
                    log.warning("structured output unsupported; falling back to plain content")
                    self.structured_output = False
                    continue
                raise LlmUnavailableError(f"OpenAI rejected the request: {e}") from e
            except (
                openai.RateLimitError,
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.InternalServerError,
            ) as e:
                if attempt >= self.max_retries:
                    raise LlmUnavailableError(
                        f"OpenAI unavailable after {attempt + 1} attempt(s): {type(e).__name__}"
                    ) from e
                delay = backoff_delay(attempt, _retry_after(e), self._base, self._cap)
                log.warning(
                    "llm call retry",
                    extra={
                        "ctx": {"attempt": attempt + 1, "error": type(e).__name__, "delay": delay}
                    },
                )
                attempt += 1
                await self._sleep(delay)
                continue
            except openai.APIStatusError as e:
                if e.status_code >= 500 and attempt < self.max_retries:
                    delay = backoff_delay(attempt, _retry_after(e), self._base, self._cap)
                    attempt += 1
                    await self._sleep(delay)
                    continue
                raise LlmUnavailableError(f"OpenAI error {e.status_code}") from e
            return self._to_reply(resp, attempt + 1)

    @staticmethod
    def _to_reply(resp: Any, attempts: int) -> LlmReply:
        msg = resp.choices[0].message
        calls = [
            LlmToolCall(tc.id, tc.function.name, json.loads(tc.function.arguments or "{}"))
            for tc in (msg.tool_calls or [])
            if tc.type == "function"
        ]
        raw: dict[str, Any] = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            raw["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
                if tc.type == "function"
            ]
        usage = {}
        if resp.usage:
            usage = {
                "input_tokens": resp.usage.prompt_tokens,
                "output_tokens": resp.usage.completion_tokens,
            }
        return LlmReply(msg.content, calls, raw, usage, attempts=attempts)


def build_llm_client() -> LlmClient | None:
    """The configured client, wrapped in the replay cache; None when the agent cannot run.

    Replay mode needs no key: it serves recorded replies only (CI, demos, golden tests).
    """
    from causaldeploy.agent.replay import ReplayClient

    s = get_settings()
    if s.llm_replay_mode == "replay":
        return ReplayClient(None, mode="replay", model=s.openai_model or None)
    if not (s.openai_api_key.get_secret_value() and s.openai_model):
        return None
    inner = OpenAIClient()
    if s.llm_replay_mode == "record":
        return ReplayClient(inner, mode="record")
    return inner
