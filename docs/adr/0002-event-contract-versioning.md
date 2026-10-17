# ADR 0002: One source-agnostic, versioned event contract

Status: accepted (PLAN.md §7; implemented in #84, version 1.1.0 in #88)

## Context

Events come from GitHub webhooks, GitHub Actions workflows, the ShopLite reporter and the
evaluation harness. If the core knew each source's payload, every new source would touch
detection and ranking, and recorded runs could not be replayed deterministically.

## Decision

* Every source is normalised to one discriminated union of pydantic models
  (`contracts/events.py`: `commit`, `deployment`, `build`, `telemetry`, `health`,
  `verification_result`), carrying `repo` and a `type`. GitHub payloads are converted in
  `github/webhooks.py normalize`; producers post the contract directly to `POST /api/v1/events`.
* `SCHEMA_VERSION` (semver, now `1.1.0`) is part of the contract. JSON Schema is generated into
  `docs/schemas/events.v1.json` / `domain.v1.json` and OpenAPI into `docs/openapi.json`
  (`make schemas`); `test_contracts.py::test_published_schemas_match_models` fails when they
  are stale.
* CI step `cli check-contract-version --base origin/<base>` fails a PR that changes a published
  schema without bumping `SCHEMA_VERSION` (`test_contract_change_requires_a_version_bump`).
* Additive, optional fields are minor bumps (1.1.0 added the deployed `sha` to telemetry and
  health); removing or retyping a field is a major bump and a new `events.v2.json`.
* Golden fixtures per event type live in `core/tests/fixtures/events/`.

## Consequences

* The evaluation harness and `cli replay` feed recorded events through the same path as
  production, so results are reproducible.
* The UI's API client is generated from `docs/openapi.json` (`npm run gen:api`), and CI checks
  it is current, so contract drift is caught on both sides.
* Producers must validate against the schema; invalid items are rejected per item
  (`rejected` with an index), never silently coerced.
