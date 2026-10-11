#!/usr/bin/env bash
# Backup/restore drill on a throwaway compose project: write -> backup -> destroy -> restore -> verify.
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME=cd-restore-drill
export BACKUP_DIR="$(mktemp -d)"
OVERRIDE="$(mktemp --suffix=.yml)"
# no published ports: the drill must not collide with a running dev stack
cat > "$OVERRIDE" <<'YML'
services:
  falkordb: { ports: !reset [] }
  postgres: { ports: !reset [] }
  redis: { ports: !reset [] }
  api: { ports: !reset [] }
YML
export COMPOSE_FILE="docker-compose.yml:$OVERRIDE"
export RESTORE_START=data
C=(docker compose)
trap '"${C[@]}" down -v >/dev/null 2>&1; rm -rf "$BACKUP_DIR" "$OVERRIDE"' EXIT

"${C[@]}" up -d --wait falkordb postgres
"${C[@]}" exec -T postgres psql -U causaldeploy -c "create table drill(v text); insert into drill values ('kept');" >/dev/null
"${C[@]}" exec -T falkordb redis-cli GRAPH.QUERY drill "CREATE (:Commit {sha:'abc'})-[:parent]->(:Commit {sha:'def'})" >/dev/null

OUT=$(./scripts/backup.sh | tail -1)

"${C[@]}" exec -T postgres psql -U causaldeploy -c "drop table drill;" >/dev/null
"${C[@]}" exec -T falkordb redis-cli GRAPH.DELETE drill >/dev/null

./scripts/restore.sh "$OUT" >/dev/null
"${C[@]}" up -d --wait falkordb postgres >/dev/null
pg=$("${C[@]}" exec -T postgres psql -U causaldeploy -tAc "select v from drill")
g=$("${C[@]}" exec -T falkordb redis-cli GRAPH.QUERY drill "MATCH (c:Commit) RETURN count(c)" | head -2 | tail -1 | tr -d '\r')
[ "$pg" = "kept" ] && [ "$g" = "2" ] && echo "restore drill OK (postgres row kept, $g graph nodes)" \
  || { echo "restore drill FAILED: pg=$pg graph=$g" >&2; exit 1; }
