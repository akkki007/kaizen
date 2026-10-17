#!/usr/bin/env bash
# Runs ON the host from /opt/causaldeploy: ./deploy.sh <image>   e.g. ghcr.io/org/causaldeploy-core:<sha>
set -euo pipefail
cd /opt/causaldeploy
IMAGE="${1:?usage: deploy.sh <image> <ui-image>}"
UI_IMAGE="${2:?usage: deploy.sh <image> <ui-image>}"
/usr/local/bin/cd-fetch-env
printf "CD_IMAGE=%s\nCD_UI_IMAGE=%s\n" "$IMAGE" "$UI_IMAGE" > .deploy.env
docker compose --env-file .env --env-file .deploy.env -f docker-compose.prod.yml pull --quiet
docker compose --env-file .env --env-file .deploy.env -f docker-compose.prod.yml up -d --remove-orphans --wait --quiet-pull
docker image prune -f >/dev/null
docker compose --env-file .env --env-file .deploy.env -f docker-compose.prod.yml ps
echo "deployed $IMAGE"
