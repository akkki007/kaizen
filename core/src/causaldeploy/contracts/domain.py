from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"


class RemediationKind(StrEnum):
    REVERT = "revert"
    PATCH = "patch"
    CONFIG = "config"
    DEPENDENCY = "dependency"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Incident(_Model):
    id: str
    repo: str
    service: str
    env: str
    status: IncidentStatus = IncidentStatus.OPEN
    onset_ts: datetime  # first anomalous window, not detection time
    detected_at: datetime
    resolved_at: datetime | None = None
    confirmed_root_cause: str | None = None  # commit sha
    resolution_type: RemediationKind | None = None


class Evidence(_Model):
    """One citeable fact. `node_id` must resolve to a node/edge in the evidence graph."""

    node_id: str
    kind: str  # commit | function | error | deployment | metric | incident ...
    summary: str


class Candidate(_Model):
    incident_id: str
    commit_sha: str
    rank: int = Field(ge=1)
    score: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    features: dict[str, float] = {}
    evidence: list[Evidence] = []


class Hypothesis(_Model):
    incident_id: str
    top: Candidate
    alternatives: list[Candidate] = []
    ambiguous: bool = False  # top-1 margin below threshold -> abstain
    explanation: str | None = None
    citations: list[str] = []


class Remediation(_Model):
    id: str
    incident_id: str
    kind: RemediationKind
    diff: str | None = None
    target_commit: str | None = None
    approved_by: str | None = None


class VerificationResult(_Model):
    remediation_id: str
    passed: bool
    tests_passed: bool
    error_rate_before: float = Field(ge=0, le=1)
    error_rate_after: float = Field(ge=0, le=1)
    baseline_error_rate: float = Field(ge=0, le=1)
