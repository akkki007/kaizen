#!/usr/bin/env bash
# One-command demo reset (docs/demo.md): clean one repository's incident state, migrate,
# seed the resolved demo history.
#
#   ./scripts/demo-reset.sh [owner/name] [--yes] [--drop-graph]
#   make demo-reset [REPO=owner/name] [YES=1]
#
# Never production: refuses CD_ENV=prod (environment or .env, checked again in demo_reset.py).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="yashchandnani07/shoplite"
ARGS=()
for a in "$@"; do
  case "$a" in
    --yes|--drop-graph) ARGS+=("$a") ;;
    -*) echo "unknown option $a" >&2; exit 2 ;;
    *) REPO="$a" ;;
  esac
done

if [ "${CD_ENV:-}" = "prod" ]; then
  echo "refusing: CD_ENV=prod. demo-reset never runs against production." >&2
  exit 2
fi

UV=(uv)
command -v uv >/dev/null 2>&1 || UV=(python -m uv)

cd "$ROOT/core"
echo "== migrate"
"${UV[@]}" run alembic upgrade head
echo "== clean $REPO"
"${UV[@]}" run python ../scripts/demo_reset.py "$REPO" ${ARGS[@]+"${ARGS[@]}"}
echo "== seed demo history"
"${UV[@]}" run python -m causaldeploy.cli demo-history "$REPO"
echo "demo state ready for $REPO"
