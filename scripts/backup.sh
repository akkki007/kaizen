#!/usr/bin/env bash
# Backs up Postgres (pg_dump custom format) and FalkorDB (RDB snapshot) into ./backups/<UTC timestamp>/.
# Dev: default compose file. Prod: COMPOSE_FILE=deploy/docker-compose.prod.yml.
# Optional: BACKUP_S3_URI=s3://bucket/prefix uploads the directory with the AWS CLI.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=(docker compose)  # honours COMPOSE_FILE / COMPOSE_PROJECT_NAME
OUT="${BACKUP_DIR:-backups}/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"

"${COMPOSE[@]}" exec -T postgres pg_dump -U causaldeploy -Fc causaldeploy > "$OUT/postgres.dump"

before=$("${COMPOSE[@]}" exec -T falkordb redis-cli LASTSAVE | tr -d '\r')
"${COMPOSE[@]}" exec -T falkordb redis-cli BGSAVE >/dev/null
for _ in $(seq 1 60); do
  now=$("${COMPOSE[@]}" exec -T falkordb redis-cli LASTSAVE | tr -d '\r')
  [ "$now" != "$before" ] && break
  sleep 1
done
[ "$now" != "$before" ] || { echo "FalkorDB BGSAVE did not finish" >&2; exit 1; }
"${COMPOSE[@]}" cp falkordb:/var/lib/falkordb/data/dump.rdb "$OUT/falkordb.rdb"

( cd "$OUT" && sha256sum postgres.dump falkordb.rdb > SHA256SUMS )
[ -z "${BACKUP_S3_URI:-}" ] || aws s3 cp --recursive "$OUT" "$BACKUP_S3_URI/$(basename "$OUT")/"
echo "$OUT"
