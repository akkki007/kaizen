# Setup, operations and troubleshooting (details)

The short version is in the [README](../README.md). This page keeps the full local setup, Windows notes,
owner steps and troubleshooting.

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
- `CD_GITHUB_APP_SLUG` enables "Install the App" links (`kaizen-graphacks`).
- `CD_SLACK_WEBHOOK_URL` turns on Slack alerts; `CD_OPENAI_API_KEY` + `CD_OPENAI_MODEL` turn on the
  agent's written explanation (the ranking never depends on it). Both are optional.
- `CD_DEMO_LOGIN_ENABLED` / `CD_DEMO_INJECT_ENABLED` / `CD_DEMO_REPOS` control the public demo.
  `CD_ALLOWED_REPOS=*` accepts any repository the App is installed on (production requires an
  explicit value, so "any" is a deliberate choice).
- In production these optional keys live in the `causaldeploy/prod/external` secret
  (`slack_webhook_url`, `openai_api_key`, `openai_model`); see [`docs/operations.md`](docs/operations.md).

## One-time owner steps

* The App must be **public** (App settings -> Advanced -> Make public) for anyone other than its
  owner to sign in and install it. Callback URL `https://kaizen.akkki.tech/auth/callback`, Setup URL
  `https://kaizen.akkki.tech/connect` with "Redirect on update" ticked.
* "Sign in with GitHub" in the local UI starts at `/auth/login?origin=http://localhost:3000`
  and redirects back to `http://localhost:3000/auth/callback`. Add it under the App's
  **Callback URL** list (GitHub -> Developer settings -> GitHub Apps ->
  `causaldeploy-shoplite-demo`). Until it is added, login fails; everything else works.
* Give the App **Contents: Read and write** (needed to push `kaizen/fix-*` revert branches; see
  [`docs/security.md`](docs/security.md) S-2) and accept the new permission on the ShopLite
  installation. Protect ShopLite's default branch (require a PR review).

## Production

Web app: <https://kaizen.akkki.tech> · API, webhooks and ShopLite: `https://shoplite-demo.duckdns.org`
(one AWS EC2 host, us-east-1, Caddy TLS for both names). Deploys happen from `main`
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
