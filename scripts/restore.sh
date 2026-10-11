#!/usr/bin/env bash
# Restores a directory produced by backup.sh: ./scripts/restore.sh backups/<timestamp>
# Stops the app, replaces the FalkorDB RDB and the Postgres database, then starts everything again.
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${1:?usage: restore.sh <backup dir>}"
COMPOSE=(docker compose)  # honours COMPOSE_FILE / COMPOSE_PROJECT_NAME
( cd "$SRC" && sha256sum -c SHA256SUMS )

"${COMPOSE[@]}" stop api worker 2>/dev/null || true
"${COMPOSE[@]}" up -d postgres falkordb

"${COMPOSE[@]}" exec -T postgres dropdb -U causaldeploy --if-exists causaldeploy
"${COMPOSE[@]}" exec -T postgres createdb -U causaldeploy causaldeploy
"${COMPOSE[@]}" exec -T postgres pg_restore -U causaldeploy -d causaldeploy --no-owner < "$SRC/postgres.dump"

# FalkorDB runs with AOF enabled; an AOF-enabled server ignores dump.rdb when no AOF exists.
# So: place the RDB, boot once with AOF off, switch AOF on (rewrites the log from the loaded
# dataset), then stop. The normal start afterwards loads the AOF.
FK=$("${COMPOSE[@]}" ps -aq falkordb)
IMAGE=$(docker inspect "$FK" -f '{{.Config.Image}}')
VOL=$(docker inspect "$FK" -f '{{range .Mounts}}{{if eq .Destination "/var/lib/falkordb/data"}}{{.Name}}{{end}}{{end}}')
"${COMPOSE[@]}" stop falkordb
SRC_ABS=$(cd "$SRC" && pwd)
docker run --rm -u root --entrypoint sh -v "$VOL:/d" -v "$SRC_ABS:/src:ro" "$IMAGE" \
  -c 'rm -rf /d/appendonlydir /d/dump.rdb && cp /src/falkordb.rdb /d/dump.rdb && chown -R 999:999 /d || cp /src/falkordb.rdb /d/dump.rdb'
TMP=$(docker run -d -e REDIS_ARGS="--appendonly no" -v "$VOL:/var/lib/falkordb/data" "$IMAGE")
for _ in $(seq 1 30); do docker exec "$TMP" redis-cli ping >/dev/null 2>&1 && break; sleep 1; done
docker exec "$TMP" redis-cli CONFIG SET appendonly yes >/dev/null
for _ in $(seq 1 60); do
  info=$(docker exec "$TMP" redis-cli INFO persistence)
  echo "$info" | grep -q 'aof_rewrite_in_progress:0' && echo "$info" | grep -q 'aof_enabled:1' && break
  sleep 1
done
docker stop "$TMP" >/dev/null && docker rm "$TMP" >/dev/null
if [ "${RESTORE_START:-all}" = "data" ]; then
  "${COMPOSE[@]}" up -d falkordb postgres
else
  "${COMPOSE[@]}" up -d
fi
echo "restored from $SRC"
