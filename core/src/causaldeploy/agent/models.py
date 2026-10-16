from typing import Any, Literal

from pydantic import BaseModel, Field


class Claim(BaseModel):
    text: str
    cited_node_ids: list[str] = Field(min_length=1)


class CausalStep(BaseModel):
    """One link of the story from the change to the failure."""

    step: str
    cited_node_ids: list[str] = Field(min_length=1)


class Alternative(BaseModel):
    """Another suspect the model considered (required when the ranking is ambiguous)."""

    sha: str | None = None
    reason: str
    cited_node_ids: list[str] = []


class Explanation(BaseModel):
    verdict: Literal["confident", "ambiguous", "not_code"]
    culprit_sha: str | None = None
    culprit_function: str | None = None
    claims: list[Claim] = Field(min_length=1)
    causal_chain: list[CausalStep] = []
    confidence_statement: str | None = None
    alternatives: list[Alternative] = []
    suggested_fix: str | None = None
    responders: list[str] = []
    source: Literal["model", "template"] = "model"  # template = deterministic fallback


class ToolStep(BaseModel):
    n: int
    name: str
    args: dict[str, Any]
    summary: str
    latency_ms: float
    node_ids: list[str]
    result: dict[str, Any]  # full payload: kept in Postgres, summarized in the graph
    reason: str | None = None  # the model's stated reason for choosing this tool, if any


class LlmCallRecord(BaseModel):
    """One model round trip, as persisted to llm_calls (request redacted, never raw)."""

    n: int
    model: str
    prompt_version: str
    prompt_hash: str  # replay-cache key of the request
    payload_sha256: str  # hash of the redacted request body, for the audit log
    status: Literal["ok", "error", "replayed"]
    request: dict[str, Any]
    response: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    attempts: int = 1


RunStatus = Literal["explained", "fallback", "explanation_invalid", "budget_exhausted", "error"]


class AgentRun(BaseModel):
    incident_id: str
    status: RunStatus
    explanation: Explanation | None
    steps: list[ToolStep]
    validation_errors: list[str] = []
    usage: dict[str, int] = {}
    run_id: str | None = None
    prompt_version: str | None = None
    model: str | None = None
    cost_usd: float = 0.0
    abort_reason: str | None = None  # why a budget_exhausted / error run stopped
    # per-call records go to llm_calls, not into the investigation JSON
    llm_calls: list[LlmCallRecord] = Field(default_factory=list, exclude=True)
