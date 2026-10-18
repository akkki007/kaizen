#!/usr/bin/env bash
# One command for local development: data services in Docker, then API, worker and UI on the host.
# Ctrl-C stops the API, worker and UI; the data containers keep running (`make down` stops them).
#
# Host ports already taken by something else (a system Postgres, another project's Redis) are
# skipped: the next free port is used and the API/worker are pointed at it through CD_* variables,
# which take precedence over .env.
set -euo pipefail
cd "$(dirname "$0")/.."

# Terminals inside AppImage apps leak their bundled Python's PYTHONHOME/PYTHONPATH, which breaks
# uv's interpreter ("No module named 'encodings'").
unset PYTHONHOME PYTHONPATH

busy() { ss -ltnH "sport = :$1" 2>/dev/null | grep -q .; }

# Sets $1 to the host port for a compose service: the one it already publishes, else the first
# port from $3 that is free and not already handed to another service in this run.
claimed=" "
claim() {
  local var=$1 service=$2 port=$3 internal=$4 published
  published=$(docker compose port "$service" "$internal" 2>/dev/null | awk -F: 'NF{print $NF}' || true)
  if [[ -n $published ]]; then
    port=$published
  else
    while busy "$port" || [[ $claimed == *" $port "* ]]; do port=$((port + 1)); done
  fi
  claimed+="$port "
  export "$var=$port"
}

for port in 3000 8000; do
  if busy "$port"; then
    echo "port $port is in use; stop whatever is listening there first:" >&2
    ss -ltnpH "sport = :$port" >&2
    exit 1
  fi
done

[[ -f .env ]] || cp .env.example .env

claim CD_FALKORDB_PORT falkordb 6380 6379
claim CD_FALKORDB_BROWSER_PORT falkordb 3001 3000
claim CD_POSTGRES_PORT postgres 5432 5432
claim CD_REDIS_PORT redis 6379 6379
export CD_POSTGRES_DSN="postgresql://causaldeploy:causaldeploy@localhost:${CD_POSTGRES_PORT}/causaldeploy"
export CD_REDIS_URL="redis://localhost:${CD_REDIS_PORT}/0"
export CD_FALKORDB_HOST=localhost

echo "postgres :$CD_POSTGRES_PORT  redis :$CD_REDIS_PORT  falkordb :$CD_FALKORDB_PORT (browser :$CD_FALKORDB_BROWSER_PORT)"
docker compose up -d --wait falkordb postgres redis

[[ -d core/.venv ]] || (cd core && uv sync)
[[ -d ui/node_modules ]] || (cd ui && npm install)
(cd core && uv run alembic upgrade head)

# Each process's output is prefixed with its name; stopping the script stops all three.
trap 'trap - EXIT; kill 0' INT TERM EXIT
run() {
  local name=$1; shift
  ("$@" 2>&1 | sed -u "s/^/[$name] /") &
}
run api    bash -c 'cd core && exec uv run uvicorn causaldeploy.api.main:app --reload --port 8000'
run worker bash -c 'cd core && exec uv run arq causaldeploy.jobs.worker.WorkerSettings'
# The UI uses its built-in demo data by default: signing in needs the GitHub App to accept
# http://localhost:3000/auth/callback. `make dev MOCK=0` talks to the local API instead.
if [[ ${MOCK:-1} == 1 ]]; then
  run ui bash -c 'cd ui && exec npm run dev:mock'
  ui_note="demo data; MOCK=0 for the real API"
else
  run ui bash -c 'cd ui && API_URL=http://localhost:8000 exec npm run dev'
  ui_note="real API, sign in at /login"
fi

echo "UI http://localhost:3000 ($ui_note)   API http://localhost:8000   (Ctrl-C to stop)"
wait
