# ADR 0003: Postgres for state and raw telemetry, FalkorDB for reasoning

Status: accepted (PLAN.md §2 gap 6, §5)

## Context

Kaizen needs (a) transactional application state with migrations, audit and job bookkeeping,
(b) high-volume, time-windowed telemetry scans for detection, and (c) multi-hop traversals over
code, commits, deployments, runtime evidence and past incidents for ranking and explanation.
Raw samples in a graph do not scale or query well; call-path and similarity questions are
awkward and slow as recursive SQL.

## Decision

* **Postgres 16** (SQLAlchemy async + Alembic) holds incidents and their transitions,
  investigations, remediation runs, jobs, raw webhook deliveries, API keys, LLM calls, the audit
  log and raw telemetry (`ix_telemetry_window (repo, service, ts)`, purged after
  `CD_TELEMETRY_RETENTION_DAYS`).
* **FalkorDB 6.0.2** (openCypher) holds one graph per repository: code structure, history,
  deployments, `Error` signatures, `Metric` window summaries, incidents, candidates, fixes and
  the agent trace. Writes go only through `GraphStore` (MERGE on unique keys, batched); queries
  are parameterised with a server-side timeout.
* Postgres is the system of record. The graph is derived and can be rebuilt from Git (backfill)
  and stored events (`cli replay`); incident status is written in Postgres first and mirrored to
  the graph by the lifecycle module.

## Consequences

* Two stores to operate: both are in `make backup` / `make restore` / `make drill` (CI job),
  FalkorDB with AOF + RDB and a memory cap with `noeviction`.
* Cross-store writes are not atomic. Jobs are retried and graph writes are idempotent, so a
  failure between the two converges on retry; one known non-idempotent case (error telemetry rows
  re-inserted when the graph link fails) is documented in `docs/performance.md`.
* Per-repository graphs give tenant isolation for free (`test_graph_store.py::test_graphs_are_isolated_per_repo`).
