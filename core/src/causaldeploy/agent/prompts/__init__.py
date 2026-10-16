"""Versioned prompts. A prompt's version is its file stem plus a short hash of its content, so any
edit produces a new version (stored on every AgentRun and llm_calls row, and part of the replay
cache key)."""

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    version: str  # e.g. investigator.v1-3f2a9c1d


@lru_cache
def load(name: str = "investigator.v1") -> Prompt:
    text = resources.files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8").strip()
    digest = hashlib.sha256(text.encode()).hexdigest()[:8]
    return Prompt(name=name, text=text, version=f"{name}-{digest}")


INVESTIGATOR = "investigator.v1"
