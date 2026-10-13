# Kaizen

AI agent that connects production failures to the code changes behind them using a FalkorDB
knowledge graph, ranks root causes with explainable evidence, proposes fixes, and verifies them
before anything ships.

- Plan: [`PLAN.md`](PLAN.md) · Original idea: [`docs/idea.md`](docs/idea.md)
- Work tracking: GitHub issues #84-#93, milestones M1-M4 (deadline 2026-10-18)
- Target app everyone works against: <https://github.com/yashchandnani07/shoplite>
  (deployed at <https://shoplite-demo.duckdns.org>)

## Where things stand

| Area | Status |
|---|---|
| #84 Platform (API, auth, jobs, Postgres, observability, CI, prod deploy, backups) | built and tested |
| #85 ShopLite (app, seeded history, 28 failure-scenario variants, pipelines) | built and verified, lives in its own repo |
| #86 GitHub App, webhooks, backfill, analyzer, hunk-to-function mapping | built; verified in production |
| #87 FalkorDB schema, 6 core queries, evidence graph, ops | built and tested |
| #93 Web UI | scaffold + analytics dashboard on incident data; incident detail and evidence graph **not built** |
| #88/#89 incident detection and ranking, #90 agent, #91 remediation, #92 eval | **not started**: no incident is opened automatically yet |

Full evidence per acceptance item: [`docs/verification.md`](docs/verification.md).

### Branches (read this first)

| Branch | What it is |
|---|---|
| `main` | scaffold only |
| `feat/m1-foundations` | everything for #84-#87. Pull request #94, ready for review |
| `feat/ui-scaffold` | **the branch to work from**: contains all of the above plus the UI and the team setup |

Merge #94 first (or together); `feat/ui-scaffold` builds on it.

## Run it locally

Needs Docker, [`uv`](https://docs.astral.sh/uv/) and Node 20+.

```bash
git clone https://github.com/akkki007/deploy-agent && cd deploy-agent
git checkout feat/ui-scaffold

# 1. Put the shared team .env in the repo root (ask the person who owns the project for it).
#    It holds the shared GitHub App identity and CD_ALLOWED_REPOS=yashchandnani07/shoplite.
cp /path/to/causaldeploy-team.env .env

# 2. Services (FalkorDB, Postgres, Redis), dependencies, migrations, npm install
make bootstrap

# 3. Import the ShopLite repo into your local graph (uses the App; no webhook needed)
make backfill

# 4. Run (three terminals)
make api       # http://localhost:8000   (docs: /docs)
make worker
make ui        # http://localhost:3000   sign in with GitHub, dashboard at /
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
| `make schemas` | regenerate `docs/schemas` and `docs/openapi.json` after a contract/API change; then `cd ui && npm run gen:api` |
| `make backfill REPO=owner/name` | import another repo (must be in `CD_ALLOWED_REPOS`) |
| `make backup` / `make restore DIR=...` / `make drill` | Postgres + FalkorDB backup, restore, automated drill |
| `curl localhost:8000/readyz` | postgres / redis / falkordb health |

## About the shared `.env`

- It is **secret**: it contains the GitHub App's private key and OAuth client secret. Never
  commit it (`.env`, `.env.*` are git-ignored), never paste it in chat or issues.
- `CD_ALLOWED_REPOS` restricts what is ever imported. Keep it at `yashchandnani07/shoplite`;
  events or jobs for any other repo are ignored.
- `CD_SESSION_SECRET` is generated per file. Production uses a different one on purpose.
- Rotating the App key means updating `causaldeploy/prod/external` in AWS Secrets Manager and
  re-issuing this file.
- `CD_GITHUB_APP_SLUG` enables "Install the App" links (e.g. `causaldeploy-shoplite-demo`).

## One-time owner step

"Sign in with GitHub" in the local UI starts at `/auth/login?origin=http://localhost:3000`
and redirects back to `http://localhost:3000/auth/callback`. Add it under the App's
**Callback URL** list (GitHub -> Developer settings -> GitHub Apps ->
`causaldeploy-shoplite-demo`). Until it is added, login fails; everything else works.

## Production

`https://shoplite-demo.duckdns.org` (AWS EC2, us-east-1, Caddy TLS). Deploys happen from `main`
through GitHub Actions (OIDC + SSM, no stored keys). Runbook, rollback, secrets rotation,
backups: [`docs/operations.md`](docs/operations.md). Infra as code: `deploy/aws/*.yaml`.

## Layout

```
core/      Python backend: API, auth, jobs, ingestion, graph, GitHub integration
ui/        Next.js UI (Tailwind v4, TanStack Query, generated API client, analytics dashboard)
deploy/    prod compose, Caddyfile, CloudFormation, deploy scripts
scripts/   backup/restore/drill, GitHub App manifest helper, env helper
docs/      verification matrix, operations, graph model, GitHub App, schemas, OpenAPI
eval/      evaluation harness (placeholder, issue #92)
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `make bootstrap` fails binding 5432 / 6379 / 6380 | a local Postgres/Redis uses the port; stop it |
| API refuses to start, names a missing setting | `.env` incomplete; production also requires `CD_ALLOWED_REPOS`, session and OAuth secrets |
| `make backfill`: "App is not installed on ..." | the repo is outside the App installation; only ShopLite is installed |
| Login redirects to an error page | callback URL not registered (see above) |
| UI shows "Something went wrong" with a request id | backend not running; start `make api` or use `npm run dev:mock` |

More: [`docs/team-setup.md`](docs/team-setup.md) · [`docs/graph.md`](docs/graph.md) ·
[`docs/github-app.md`](docs/github-app.md) · [`ui/README.md`](ui/README.md) ·
[`CONTRIBUTING.md`](CONTRIBUTING.md)
