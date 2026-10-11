#!/usr/bin/env bash
# Runs ON the host from /opt/causaldeploy: ./deploy.sh <image>   e.g. ghcr.io/org/causaldeploy-core:<sha>
set -euo pipefail
cd /opt/causaldeploy
IMAGE="${1:?usage: deploy.sh <image>}"
/usr/local/bin/cd-fetch-env
echo "CD_IMAGE=$IMAGE" > .deploy.env
docker compose --env-file .env --env-file .deploy.env -f docker-compose.prod.yml pull api
docker compose --env-file .env --env-file .deploy.env -f docker-compose.prod.yml up -d --remove-orphans --wait
docker image prune -f >/dev/null
echo "deployed $IMAGE"
