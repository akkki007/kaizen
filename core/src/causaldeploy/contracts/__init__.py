"""Versioned event and domain contracts. Every source (GitHub, Actions, SDK, replayer)
produces these; the core never depends on where an event came from (PLAN.md §7).

Compatibility policy: additive optional fields keep SCHEMA_VERSION's minor; any removal,
rename or type change requires a major bump and a regenerated `docs/schemas/`.
"""

from causaldeploy.contracts.domain import (
    Candidate,
    Evidence,
    Hypothesis,
    Incident,
    IncidentStatus,
    Remediation,
    RemediationKind,
    VerificationResult,
)
from causaldeploy.contracts.events import (
    SCHEMA_VERSION,
    BuildEvent,
    CommitEvent,
    DeploymentEvent,
    Event,
    FileChange,
    HealthEvent,
    Hunk,
    TelemetryEvent,
    VerificationResultEvent,
    parse_event,
)

__all__ = [
    "SCHEMA_VERSION",
    "BuildEvent",
    "Candidate",
    "CommitEvent",
    "DeploymentEvent",
    "Event",
    "Evidence",
    "FileChange",
    "HealthEvent",
    "Hunk",
    "Hypothesis",
    "Incident",
    "IncidentStatus",
    "Remediation",
    "RemediationKind",
    "TelemetryEvent",
    "VerificationResult",
    "VerificationResultEvent",
    "parse_event",
]
