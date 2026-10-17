# ADR 0004: Incidents follow a guarded state machine with an audit trail

Status: accepted (#88, #91; `core/src/causaldeploy/incidents/lifecycle.py`)

## Context

Detection, investigation, remediation, verification webhooks and people all change incident
status, often concurrently. Before #88 each module wrote `status` directly, which let a job
overwrite a person's decision and left incidents stuck in `investigating` after failures.

## Decision

States: `open`, `investigating`, `identified`, `explained`, `remediating`, `verifying`,
`needs_human`, `resolved`, `closed`.

```
open ─► investigating ─► identified ─► explained ─► remediating ─► verifying ─► resolved
  ▲          │  (abstain/fail)  │            │           │  (conflict)    │ (failed verdict)
  └──────────┘                  └────────────┴─► needs_human ◄───────────┴──────┘
 any non-terminal ─► closed (person) ; resolved/closed ─► open (reopen)
```

* All writes go through `lifecycle.transition` (strict, raises `InvalidTransition`, API returns
  409) or `lifecycle.advance(..., only_from=[...])` (conditional, used by jobs so they never
  override a person: `test_lifecycle.py::test_advance_never_overwrites_a_human_decision`).
* Every change inserts an `incident_transitions` row (from, to, actor, reason, time) in the same
  transaction; `GET /incidents/{id}/history` returns it. The graph `Incident.status` is synced
  after commit.
* `resolved` requires a merged fix with a recorded approval and a passing verification; a failed
  verification goes to `needs_human`, never `resolved`.
* Closed or resolved incidents do not block a new incident for the same service/endpoint; a
  cooldown prevents flapping.

## Consequences

* Full, ordered history per incident for audit and the UI timeline.
* Jobs racing with people lose safely (no-op) instead of corrupting state.
* New states need an entry in `TRANSITIONS` (`test_every_status_has_a_transition_entry`).
