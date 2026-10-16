from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

SCHEMA_VERSION = "1.1.0"  # 1.1: optional deployed `sha` on telemetry/health


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCHEMA_VERSION


class Hunk(BaseModel):
    """Changed line range in the *new* file (1-based, inclusive) plus the old range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    old_start: int = Field(0, ge=0)
    old_end: int = Field(0, ge=0)
    added: int = Field(0, ge=0)
    removed: int = Field(0, ge=0)


class FileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    status: Literal["added", "modified", "removed", "renamed"]
    old_path: str | None = None
    hunks: list[Hunk] = []


class CommitEvent(_Base):
    type: Literal["commit"] = "commit"
    repo: str  # owner/name
    sha: str = Field(min_length=7)
    parents: list[str] = []
    author: str
    ts: datetime
    message: str
    branch: str | None = None
    files: list[FileChange] = []


class BuildEvent(_Base):
    type: Literal["build"] = "build"
    repo: str
    id: str
    sha: str
    status: Literal["pending", "success", "failure"]
    started_at: datetime
    finished_at: datetime | None = None


class DeploymentEvent(_Base):
    type: Literal["deployment"] = "deployment"
    repo: str
    id: str
    env: str
    services: list[str] = []
    sha_from: str | None = None  # None for the first deployment in an environment
    sha_to: str
    started_at: datetime
    finished_at: datetime | None = None
    status: Literal["pending", "success", "failure", "rollback"]
    build_id: str | None = None


class TelemetryEvent(_Base):
    type: Literal["telemetry"] = "telemetry"
    repo: str
    service: str
    env: str = "prod"
    ts: datetime
    kind: Literal["log", "error", "request"]
    request_id: str | None = None
    endpoint: str | None = None
    status_code: int | None = None
    latency_ms: float | None = None
    message: str | None = None
    error_type: str | None = None
    stack: list[str] = []  # raw frames, top frame first
    sha: str | None = None  # deployed build version the reporting process runs (optional)


class HealthEvent(_Base):
    type: Literal["health"] = "health"
    repo: str
    service: str
    env: str
    ts: datetime
    ok: bool
    latency_ms: float | None = None
    status_code: int | None = None
    sha: str | None = None  # deployed build version the checked service reports (optional)


class VerificationResultEvent(_Base):
    type: Literal["verification_result"] = "verification_result"
    repo: str
    remediation_id: str
    incident_id: str
    passed: bool
    tests_passed: bool
    error_rate_before: float = Field(ge=0, le=1)
    error_rate_after: float = Field(ge=0, le=1)
    baseline_error_rate: float = Field(ge=0, le=1)
    ts: datetime
    details_url: str | None = None


Event = Annotated[
    CommitEvent
    | BuildEvent
    | DeploymentEvent
    | TelemetryEvent
    | HealthEvent
    | VerificationResultEvent,
    Field(discriminator="type"),
]

_adapter: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(payload: object) -> Event:
    """Validate a raw payload into the matching event model (raises ValidationError)."""
    return _adapter.validate_python(payload)


def event_json_schema() -> dict[str, object]:
    return _adapter.json_schema()
