# Kaizen

AI agent that connects production failures to the code changes behind them using a FalkorDB
knowledge graph, ranks root causes with explainable evidence, proposes fixes, and verifies them
before anything ships.

- Repository: <https://github.com/akkki007/kaizen> (branch `main`)
- Plan: [`PLAN.md`](PLAN.md) · Original idea: [`docs/idea.md`](docs/idea.md) ·
  Architecture: [`docs/architecture.md`](docs/architecture.md)
- Work tracking: GitHub issues #84-#93, milestones M1-M4 (deadline 2026-10-18)
- Target app everyone works against: <https://github.com/yashchandnani07/shoplite>
  (deployed at <https://shoplite-demo.duckdns.org>)

## Where things stand

| Issue | Status |
|---|---|
| #84 Platform (API, auth, jobs, Postgres, observability, CI, prod deploy, backups) | built and tested |
| #85 ShopLite (app, seeded history, 28 failure-scenario variants, pipelines) | built and verified, lives in its own repo |
| #86 GitHub App, webhooks, backfill, analyzer, hunk-to-function mapping | built; verified in production |
| #87 FalkorDB schema, core queries, evidence graph, ops | built and tested |
| #88 Telemetry ingest, fingerprinting, detection, incident state machine | built and tested; ShopLite reporter is external |
| #89 Candidate selection, features with evidence, scorer, abstention, blast radius, baselines | built and tested |
| #90 Agent harness: budgets, tracing, replay cache, read-only tools, citations, safety, golden tests | built and tested (replay); live eval is manual |
| #91 Revert/patch remediation, approval gate, verification verdict, memory, similarity | built and tested; live ShopLite verification workflow is external |
| #92 Evaluation harness, security review, performance budget, docs, demo | eval, review, budgets, docs, demo script built; recording and rehearsals pending |
| #93 Web UI | incident list/page, suspects with per-feature Cypher, evidence graph, agent trace, fix + verification; in progress |

Evidence per acceptance item: [`docs/verification.md`](docs/verification.md). Graph vs.
baselines: [`docs/results/latest.md`](docs/results/latest.md) (graph 89% top-1, BM25 39%, latest
commit 32%, 28 scenario variants).

## Run it locally

Needs Docker, [`uv`](https://docs.astral.sh/uv/), Node 20+ and `make` (Windows: see below).

```bash
git clone https://github.com/akkki007/kaizen && cd kaizen

# 1. Put the shared team .env in the repo root (ask the person who owns the project for it).
#    It holds the shared GitHub App identity and CD_ALLOWED_REPOS=yashchandnani07/shoplite.
#    Without it, `make bootstrap` copies .env.example and everything except GitHub login and
#    repository import still works.
cp /path/to/causaldeploy-team.env .env

# 2. Services (FalkorDB, Postgres, Redis), dependencies, migrations, npm install
make bootstrap

# 3. Import the ShopLite repo into your local graph (uses the App; no webhook needed)
make backfill

# 4. Run (three terminals)
make api       # http://localhost:8000   (docs: /docs)
make worker
make ui        # http://localhost:3000   sign in with GitHub, dashboard at /

# 5. Optional: seeded incident history for the demo (docs/demo.md)
make demo-reset YES=1
```

No credentials? You can still develop the UI and run the test-suite:

```bash
cd ui && npm run dev:mock     # fixtures, no backend, no login
make test                     # backend tests against real FalkorDB/Postgres/Redis
```

### Everyday commands

| Command | What |
|---|---|
| `make test` / `make lint` | backend tests (needs services) / ruff + mypy + `tsc` |
| `make eval` | graph vs. baselines -> `docs/results/latest.{md,json,html}` |
| `make perf` | detection -> hypothesis budget + chaos checks ([`docs/performance.md`](docs/performance.md)) |
| `make demo-reset [REPO=..] [YES=1]` | clean one repo's incident state, migrate, seed demo history |
| `make schemas` | regenerate `docs/schemas` and `docs/openapi.json` after a contract/API change; then `cd ui && npm run gen:api` |
| `make backfill REPO=owner/name` | import another repo (must be in `CD_ALLOWED_REPOS`) |
| `make backup` / `make restore DIR=...` / `make drill` | Postgres + FalkorDB backup, restore, automated drill |
| `curl localhost:8000/readyz` | postgres / redis / falkordb health |

### Windows

Tested on Windows 11 with Docker Desktop, PowerShell and Git Bash.

* **Port 5432 already taken** (a local Postgres): start the stack on another port and point
  Kaizen at it, in `.env`:
  ```
  CD_POSTGRES_PORT=5433
  CD_POSTGRES_DSN=postgresql://causaldeploy:causaldeploy@localhost:5433/causaldeploy
  ```
  then `docker compose up -d --wait falkordb postgres redis`. Same idea for `CD_REDIS_PORT` /
  `CD_FALKORDB_PORT` (then also `CD_REDIS_URL`, `CD_FALKORDB_PORT` for the app).
* **`uv` not on PATH** after `pip install uv`: use `python -m uv` everywhere `uv` appears.
* **No `make`**: run the recipe lines from the `Makefile` directly, e.g.
  ```powershell
  cd core; python -m uv sync --python 3.12; python -m uv run alembic upgrade head
  python -m uv run uvicorn causaldeploy.api.main:app --reload --port 8000   # make api
  python -m uv run arq causaldeploy.jobs.worker.WorkerSettings              # make worker
  ```
  Demo reset: `.\scripts\demo-reset.ps1 -Repo yashchandnani07/shoplite -Yes`.
* **Tests** create and drop their own database through an admin DSN; point it at your port and
  give each checkout its own database name:
  ```powershell
  cd core
  $env:CD_TEST_ADMIN_DSN='postgresql://causaldeploy:causaldeploy@localhost:5433/postgres'
  $env:CD_TEST_DB='causaldeploy_test_me'
  python -m uv run pytest -q
  ```
  The POSIX `SIGKILL` worker test is skipped on Windows; `test_chaos.py` covers the same case
  with `TerminateProcess`.
* **Mock UI**: `npm run dev:mock` uses POSIX `VAR=1 cmd` syntax, which `cmd.exe` (npm's default
  shell on Windows) rejects. In PowerShell: `cd ui; $env:NEXT_PUBLIC_API_MOCK='1'; npx next dev`
  (or run `npm run dev:mock` from Git Bash with `npm config set script-shell bash`).

## About the shared `.env`

- It is **secret**: it contains the GitHub App's private key and OAuth client secret. Never
  commit it (`.env`, `.env.*` are git-ignored), never paste it in chat or issues.
- `CD_ALLOWED_REPOS` restricts what is ever imported. Keep it at `yashchandnani07/shoplite`;
  events or jobs for any other repo are ignored.
- `CD_SESSION_SECRET` is generated per file. Production uses a different one on purpose.
- Rotating the App key means updating `causaldeploy/prod/external` in AWS Secrets Manager and
  re-issuing this file.
- `CD_GITHUB_APP_SLUG` enables "Install the App" links (e.g. `causaldeploy-shoplite-demo`).

## One-time owner steps

* "Sign in with GitHub" in the local UI starts at `/auth/login?origin=http://localhost:3000`
  and redirects back to `http://localhost:3000/auth/callback`. Add it under the App's
  **Callback URL** list (GitHub -> Developer settings -> GitHub Apps ->
  `causaldeploy-shoplite-demo`). Until it is added, login fails; everything else works.
* Give the App **Contents: Read and write** (needed to push `kaizen/fix-*` revert branches; see
  [`docs/security.md`](docs/security.md) S-2) and accept the new permission on the ShopLite
  installation. Protect ShopLite's default branch (require a PR review).

## Production

`https://shoplite-demo.duckdns.org` (AWS EC2, us-east-1, Caddy TLS). Deploys happen from `main`
through GitHub Actions (OIDC + SSM, no stored keys). Runbook, rollback, secrets rotation,
backups, replay: [`docs/operations.md`](docs/operations.md). Infra as code: `deploy/aws/*.yaml`.

## Layout

```
core/      Python backend: API, auth, jobs, ingestion, graph, detection, investigation, agent,
           remediation, verification, memory, evaluation harness (causaldeploy package)
ui/        Next.js UI (Tailwind v4, TanStack Query, generated API client)
deploy/    prod compose, Caddyfile, CloudFormation, deploy scripts
scripts/   backup/restore/drill, demo reset, GitHub App manifest helper, env helper
docs/      architecture, ADRs, graph + cookbook, operations, security, performance, demo,
           verification matrix, schemas, OpenAPI, evaluation results
eval/      scenario library S1-S10 (eval/scenarios); `make eval-live` writes live LLM runs here
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `make bootstrap` fails binding 5432 / 6379 / 6380 | a local Postgres/Redis uses the port; stop it or set `CD_POSTGRES_PORT` etc. (see Windows) |
| API refuses to start, names a missing setting | `.env` incomplete; production also requires `CD_ALLOWED_REPOS`, session and OAuth secrets |
| `make backfill`: "App is not installed on ..." | the repo is outside the App installation; only ShopLite is installed |
| Login redirects to an error page | callback URL not registered (see above) |
| UI shows "Something went wrong" with a request id | backend not running; start `make api` or use the mock UI |
| `ReplayMissError` in agent tests | prompt/tools changed; re-record fixtures ([`docs/operations.md`](docs/operations.md)) |

More: [`docs/team-setup.md`](docs/team-setup.md) · [`docs/graph.md`](docs/graph.md) ·
[`docs/github-app.md`](docs/github-app.md) · [`docs/security.md`](docs/security.md) ·
[`docs/adr/`](docs/adr/) · [`ui/README.md`](ui/README.md) · [`CONTRIBUTING.md`](CONTRIBUTING.md)
