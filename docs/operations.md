# Operations runbook

## Environments

| | Dev (everyone) | Prod |
|---|---|---|
| Stack | `docker-compose.yml` | `deploy/docker-compose.prod.yml` on one VM (AWS EC2) behind Caddy |
| URL | `http://localhost:8000` | `https://shoplite-demo.duckdns.org` (Kaizen under `/api/v1`, `/webhooks`, `/auth`; ShopLite everywhere else) |
| Secrets | `.env` (gitignored) | AWS Secrets Manager -> `/opt/causaldeploy/.env` by `cd-fetch-env` at deploy time |
| Infra as code | | `deploy/aws/causaldeploy.yaml` (CloudFormation) |

## New teammate

```bash
git clone https://github.com/akkki007/kaizen && cd kaizen
make bootstrap      # .env, services (healthchecked), uv sync, migrations, npm install
make test           # ~290 tests against the real FalkorDB/Postgres/Redis
```

Needs Docker, `uv`, Node 20+. Ask a teammate for the shared `.env` values (GitHub App id, keys,
webhook secret, OAuth client). Without them everything still runs; GitHub login and clone/backfill are
the only features that need them. Windows specifics (port 5432 clash, `python -m uv`, mock UI):
`README.md` -> "Windows". Architecture: [`architecture.md`](architecture.md).

## Deploy

`main` -> GitHub Actions `deploy` workflow: build image, push to GHCR, copy
`deploy/docker-compose.prod.yml` + `Caddyfile` to the VM, run `cd-fetch-env`, `docker compose pull && up -d`.
Migrations run when the API container starts (`alembic upgrade head`).

Manual: `ssh ubuntu@<host> 'cd /opt/causaldeploy && ./deploy.sh <image tag>'`.

## Rollback

Every deploy is an image tag. `ssh ubuntu@<host> 'cd /opt/causaldeploy && ./deploy.sh <previous tag>'`.
Schema migrations are additive; if one must be undone: `docker compose exec api alembic downgrade -1`
(only the latest revision is tested for downgrade).

## Rotate secrets

| Secret | How |
|---|---|
| Session secret / webhook secret | new value in Secrets Manager (`aws secretsmanager put-secret-value`), redeploy; webhook secret must also be changed in the GitHub App settings. Rotating the session secret signs everyone out. |
| GitHub App private key | generate a new key in the App settings, `put-secret-value`, redeploy, delete the old key |
| Ingest API keys | `POST /api/v1/repos/{owner}/{name}/api-keys` (new) then `DELETE .../{id}` (old); both work during the overlap |

## Backups and restore

```bash
make backup                                  # backups/<UTC ts>/{postgres.dump,falkordb.rdb,SHA256SUMS}
COMPOSE_FILE=deploy/docker-compose.prod.yml BACKUP_S3_URI=s3://bucket/cd ./scripts/backup.sh   # prod
make restore DIR=backups/<ts>                # verifies checksums, restores both stores, restarts
make drill                                   # automated write -> backup -> destroy -> restore -> verify
```

FalkorDB runs with AOF (`appendfsync everysec`) plus RDB snapshots, memory capped by
`FALKORDB_MAXMEMORY` (prod 768mb, `noeviction`: writes fail loudly rather than dropping graph data),
query timeout 5 s. A restart keeps the graph (volume `falkordb-data`). Postgres uses the `postgres-data` volume.

Restore notes: an AOF-enabled FalkorDB ignores `dump.rdb` unless AOF is absent, so `restore.sh` boots
once with AOF off, switches it on, and stops; do the same by hand if you restore manually.

## Replay events

Three kinds of replay, all offline and idempotent:

| What | Command | Notes |
|---|---|---|
| Stored GitHub webhook deliveries (`raw_events`) | `cd core && uv run python -m causaldeploy.cli replay [--limit N]` | re-runs normalisation and enqueues jobs with `replay:<delivery>` keys; graph writes are MERGE, so a replay changes nothing that was already applied. Use after a bug fix in `github/webhooks.py` or to rebuild a dropped graph (after `make backfill`). Needs a worker running |
| Producer events | `POST /api/v1/events` with the recorded batch | duplicates are no-ops: the job key is the event's SHA-256 |
| LLM replies (agent) | `CD_LLM_REPLAY_MODE=replay` (+ `CD_LLM_REPLAY_DIR`, default `tests/fixtures/llm` relative to `core/`) | serves stored replies keyed on sha256(model, prompt version, redacted messages, tools); zero network, no API key; a miss raises `ReplayMissError` naming the key. `record` calls the model and stores each reply; `off` (default) calls the model |
| Evaluation runs | `cd core && uv run python -m causaldeploy.evalharness --record ../eval/runs` then `--replay ../eval/runs` | `--record` writes each variant's graph writes to `<dir>/<scenario>-v<n>.jsonl`; `--replay` evaluates the recordings instead of rendering scenarios (no LLM, no network; FalkorDB only). `make eval` regenerates `docs/results/latest.{md,json,html}` |

Production replay of webhooks: `docker compose -f deploy/docker-compose.prod.yml exec worker
python -m causaldeploy.cli replay --limit 100` on the VM (through SSM, as in Deploy).

## Re-record golden LLM fixtures

The golden agent tests (`core/tests/test_llm_golden.py`, S1-S5 rubric: cites and names the
culprit, every claim cited, within token/cost/latency budget) run in replay mode in CI. Fixtures
must be re-recorded when the prompt (`agent/prompts/investigator.v1.md`, its version is stem +
content hash), the tool schemas or tool outputs change, because the replay key changes. Symptom:
`ReplayMissError ... re-record with CD_LLM_REPLAY_MODE=record`.

```bash
cd core
# CI fixtures (tests/fixtures/llm/golden) from the deterministic scripted investigator (no key, no cost)
uv run python -m causaldeploy.evalharness.llm_golden --mode record-scripted
# real model, costs money; writes eval/llm-live and prints the rubric (from the repo root:
#   CD_OPENAI_API_KEY=... CD_OPENAI_MODEL=gpt-4o-mini make eval-live)
uv run python -m causaldeploy.evalharness.llm_golden --mode live --dir ../eval/llm-live
# check
uv run pytest -q tests/test_llm_golden.py tests/test_agent_harness.py
```

Review the fixture diff before committing (fixtures hold redacted messages only, never keys).
To adopt a good live run as the CI fixtures, record with `--mode live --dir tests/fixtures/llm/golden`
and keep `--model` equal to the model name the tests use.

## Demo reset

`make demo-reset [REPO=owner/name] [YES=1]` (Windows: `.\scripts\demo-reset.ps1 -Repo owner/name -Yes`)
deletes one repository's incidents, transitions, investigations, remediation runs, LLM calls,
telemetry and jobs, removes the runtime/memory nodes from its graph (code and history stay;
`--drop-graph` drops it all), migrates, and seeds the resolved demo history
(`cli demo-history`). It refuses `CD_ENV=prod` and repositories outside `CD_ALLOWED_REPOS`, and
asks you to type the repository name unless `--yes`. API keys, installations, raw deliveries and
the audit log are kept. Script: [`demo.md`](demo.md).

## Capacity assumptions

One repository graph of ShopLite size (~1k nodes) is a few MB. 50 repositories of 10k functions each
is roughly 1-2 GB of graph memory; raise `FALKORDB_MAXMEMORY` and the instance size together.
Raw telemetry lives in Postgres (`telemetry`, index `(repo, service, ts)`), kept
`CD_TELEMETRY_RETENTION_DAYS` days.

## Slow queries and metrics

* Graph reads over 500 ms are logged (`slow graph query`) and counted in `cd_graph_slow_queries_total`.
* Server-side: `redis-cli GRAPH.SLOWLOG <graph>`.
* `GET /metrics` (not exposed through Caddy): request latency, job outcomes/latency per pipeline stage,
  queue depth, ingest counts. Set `CD_OTLP_ENDPOINT` to export traces: one investigation is one
  trace from API request through the job to graph reads. Logs carry `request_id`, `job_id`, `incident_id`.
* LLM cost per investigation: `GET /api/v1/incidents/{id}/llm-cost` (table `llm_calls`).

## Index plan (telemetry window queries)

`ix_telemetry_window (repo, service, ts)` serves "service X in [t0, t1]" scans. Detection windows are
minutes wide, so one index range scan per window. If volume grows past ~50M rows, partition `telemetry`
by month on `ts` and drop partitions for retention instead of deleting rows.

## PR Guardian

On `pull_request` opened/synchronize/reopened (not Kaizen's own `kaizen/*` fix branches; allowlisted repos
only) the `guard_pr` job fetches the PR files, maps hunks to functions via the graph's line
ranges, and upserts one comment (marker `<!-- kaizen-pr-guardian -->`) with blast radius
(callers, endpoints, services) and past incidents per changed function. Code: `core/src/causaldeploy/guardian/`.

Requirements on the GitHub App: **Pull requests: Read & write** (comment) and the `pull_request`
webhook event. Without them the job fails and retries; nothing else is affected.
Limitation: line ranges come from the graph (default branch), so a PR based on a stale default
branch can map a hunk to the wrong function; unmapped files are listed, never guessed.

## Connect flow (sign in, choose a repository, start)

1. `/login` -> GitHub sign-in (`/auth/login`, App user-to-server OAuth). The session carries the
   repositories where the App is installed and the user's role on each.
2. `/connect` (default landing after sign-in):
   - no installed repository: "Install on GitHub" (GitHub's own repository picker), then
     "I installed it" re-runs sign-in, which refreshes the session's repository list;
   - repositories listed with their state (`GET /api/v1/onboarding`): `not_started`, `importing`,
     `ready`, `failed`, `unavailable` (outside `CD_ALLOWED_REPOS`). Only an approver (push access)
     can start one; the user ticks the repositories and presses **Start monitoring**
     (`POST /api/v1/repos/{owner}/{name}/connect`, CSRF-protected, idempotent);
   - progress shows live counts from the graph (commits read, functions mapped, services,
     endpoints), then "Kaizen is watching" and a link to the dashboard.
3. Returning users with a monitored repository skip straight to `/overview`; `/connect?manage=1`
   reopens the flow (the Repositories page links to it).

Installing the App only *records* repositories; nothing is cloned until the user starts it. Pushes
keep an already-imported repository current but never start a first import.

Owner step (recommended): in the GitHub App settings set **Setup URL** to
`https://shoplite-demo.duckdns.org/connect` and tick **Redirect on update**, so GitHub sends users
back to the flow right after installing. The flow works without it (the user returns manually).
To monitor repositories beyond the allowlist, widen `CD_ALLOWED_REPOS` (empty = any installed repo).

## Alerts and the live demo

**Alerts.** When an investigation names a suspect (confident or ambiguous), Kaizen posts to Slack:
service and endpoint, the commit and author, the function that changed and the function that
threw, the confidence, and a link to the evidence. It sends once per incident and never fails the
investigation if Slack is down.
- Create a free Slack workspace -> a Slack app with **Incoming Webhooks** -> copy the webhook URL.
- Put it in the host's secrets as `CD_SLACK_WEBHOOK_URL` (never commit it). Check with
  `POST /api/v1/repos/{owner}/{name}/alerts/test` (approver) or by injecting a problem.
- `CD_ALERT_GITHUB_ISSUES=true` also opens a GitHub issue per incident (needs the App to have
  Issues: write).

**Live graph and demo** (`/graph`). The page draws the repository's functions, services and recent
commits from FalkorDB: green while healthy, red on the function that failed, amber on the
suspect commit, with the Cypher behind the view and the engine (Postgres or FalkorDB) of every
step. `CD_DEMO_INJECT_ENABLED` adds **Inject a problem**: it records a deployment containing the
latest change to a function that serves an endpoint, then sends the telemetry a failing service
would send (healthy windows, then 70% 5xx with a stack throwing in that function) through the
normal ingest path. Detection, the graph queries, ranking and the alert are the production
code; only the traffic is synthetic, and the API says `simulated: true`. **Reset** removes the
incident state again. `CD_DEMO_LOGIN_ENABLED` adds **Try the demo** on the login page: a
viewer-only session on `CD_DEMO_REPOS` (it can inject and reset, and cannot propose or approve
fixes, so nothing touches the real repository).
