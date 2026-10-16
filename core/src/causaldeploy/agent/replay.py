"""Record/replay cache for LLM calls: deterministic agent runs with zero network.

Key = sha256 of the canonical JSON of (model, prompt_version, messages, tools). Messages are the
redacted ones the agent sends, so fixtures never hold secrets. One file per key:
`<dir>/<key>.json`.

  off     pass through to the wrapped client
  record  call the wrapped client and store its reply (overwrites)
  replay  serve stored replies only; a miss raises ReplayMissError, nothing is called
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from causaldeploy.agent import prompts
from causaldeploy.agent.llm import LlmClient, LlmReply, reply_from_dict, reply_to_dict
from causaldeploy.agent.safety import canonical_json
from causaldeploy.config import get_settings

Mode = Literal["off", "record", "replay"]


class ReplayMissError(LookupError):
    """Replay mode found no recorded reply for this exact request."""


def cache_key(
    model: str, prompt_version: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> str:
    blob = canonical_json(
        {"model": model, "prompt_version": prompt_version, "messages": messages, "tools": tools}
    )
    return hashlib.sha256(blob.encode()).hexdigest()


class ReplayClient:
    def __init__(
        self,
        inner: LlmClient | None,
        mode: Mode | None = None,
        directory: str | Path | None = None,
        *,
        model: str | None = None,
        prompt_version: str | None = None,
    ) -> None:
        s = get_settings()
        self.inner = inner
        self.mode: Mode = mode or s.llm_replay_mode
        self.dir = Path(directory or s.llm_replay_dir)
        self.model: str = model or str(getattr(inner, "model", "") or s.openai_model or "unknown")
        self.prompt_version = prompt_version or prompts.load(prompts.INVESTIGATOR).version
        if self.mode != "replay" and inner is None:
            raise ValueError(f"mode {self.mode!r} needs a wrapped client")

    def key(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
        return cache_key(self.model, self.prompt_version, messages, tools)

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmReply:
        if self.mode == "off":
            assert self.inner is not None
            return await self.inner.chat(messages, tools)
        key = self.key(messages, tools)
        path = self.dir / f"{key}.json"
        if self.mode == "replay":
            if not path.is_file():
                last = messages[-1].get("content") if messages else None
                raise ReplayMissError(
                    f"no recorded LLM reply for key {key} in {self.dir} "
                    f"(model={self.model}, prompt_version={self.prompt_version}, "
                    f"{len(messages)} message(s), last={str(last)[:80]!r}). The prompt, tools or "
                    "tool results changed since recording; re-record with "
                    "CD_LLM_REPLAY_MODE=record."
                )
            stored = json.loads(path.read_text(encoding="utf-8"))
            return reply_from_dict(stored["reply"], source="replayed")
        assert self.inner is not None
        reply = await self.inner.chat(messages, tools)
        self.dir.mkdir(parents=True, exist_ok=True)
        record = {
            "key": key,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "messages": len(messages),
            "reply": reply_to_dict(reply),
        }
        # key order is kept: a replayed reply must equal the recorded one byte for byte
        path.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        return reply
