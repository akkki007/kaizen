from typing import Any, Literal

from pydantic import BaseModel, Field


class Claim(BaseModel):
    text: str
    cited_node_ids: list[str] = Field(min_length=1)


class Explanation(BaseModel):
    verdict: Literal["confident", "ambiguous", "not_code"]
    culprit_sha: str | None = None
    culprit_function: str | None = None
    claims: list[Claim] = Field(min_length=1)
    suggested_fix: str | None = None
    responders: list[str] = []


class ToolStep(BaseModel):
    n: int
    name: str
    args: dict[str, Any]
    summary: str
    latency_ms: float
    node_ids: list[str]
    result: dict[str, Any]  # full payload: kept in Postgres, summarized in the graph
    reason: str | None = None  # the model's stated reason for choosing this tool, if any


class AgentRun(BaseModel):
    incident_id: str
    status: Literal["explained", "explanation_invalid", "budget_exhausted", "error"]
    explanation: Explanation | None
    steps: list[ToolStep]
    validation_errors: list[str] = []
    usage: dict[str, int] = {}
