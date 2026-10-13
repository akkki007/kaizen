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
git clone <repo> && cd deploy-agent
make bootstrap      # .env, services (healthchecked), uv sync, migrations, npm install
make test           # 70+ tests against the real FalkorDB/Postgres/Redis
```

Needs Docker, `uv`, Node 20+. Ask a teammate for the shared `.env` values (GitHub App id, keys,
webhook secret, OAuth client). Without them everything still runs; GitHub login and clone/backfill are
the only features that need them.

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
