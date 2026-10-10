# CausalDeploy

AI agent that connects production failures to the code changes behind them using a FalkorDB knowledge graph, ranks root causes with explainable evidence, proposes fixes, and verifies them before anything ships.

- Plan: [`PLAN.md`](PLAN.md) · Original idea: [`docs/idea.md`](docs/idea.md)
- Work tracking: GitHub issues #84–#93, milestones M1–M4 (deadline 2026-10-18)

## Layout

```
core/      Python 3.12 backend: API, ingestion, graph, detection, investigation, agent harness, remediation, verification, memory
ui/        Next.js web UI
eval/      Evaluation harness (graph vs. baselines)
docs/      Architecture, schema, runbooks
```

## Quickstart

```bash
cp .env.example .env
make up          # FalkorDB (:6380, browser :3001), Postgres, Redis, API (:8000)
make install     # uv sync + npm install
make test lint
make api         # API with reload   |   make ui   # UI on :3000
```

Health: `curl localhost:8000/healthz` · `curl localhost:8000/readyz`

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for workflow.
