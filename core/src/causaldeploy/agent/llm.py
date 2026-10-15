"""LLM access for the agent. The model is configuration (`CD_OPENAI_MODEL`), never hard-coded."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from causaldeploy.config import get_settings


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


class LlmClient(Protocol):
    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LlmReply: ...


class OpenAIClient:
    """Chat Completions with tool calling, temperature 0."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        from openai import AsyncOpenAI

        s = get_settings()
        key = api_key or s.openai_api_key.get_secret_value()
        self.model = model or s.openai_model
        if not key or not self.model:
            raise RuntimeError("CD_OPENAI_API_KEY and CD_OPENAI_MODEL must be set to run the agent")
        self._client = AsyncOpenAI(api_key=key)

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmReply:
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        if tools:
            kwargs["tools"] = tools
        resp = await self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        import json

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
        return LlmReply(msg.content, calls, raw, usage)
