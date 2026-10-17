#!/usr/bin/env bash
# Runs ON the host from /opt/causaldeploy: ./deploy.sh <image>   e.g. ghcr.io/org/causaldeploy-core:<sha>
set -euo pipefail
cd /opt/causaldeploy
IMAGE="${1:?usage: deploy.sh <image> <ui-image>}"
UI_IMAGE="${2:?usage: deploy.sh <image> <ui-image>}"
/usr/local/bin/cd-fetch-env
# Optional settings kept in the same Secrets Manager secret (causaldeploy/prod/external, JSON keys
# slack_webhook_url / openai_model). Read here, on the host, so no value passes through a log or argv.
EXT="$(aws secretsmanager get-secret-value --region "${AWS_REGION:-us-east-1}" \
  --secret-id causaldeploy/prod/external --query SecretString --output text 2>/dev/null || true)"
for pair in slack_webhook_url:CD_SLACK_WEBHOOK_URL openai_model:CD_OPENAI_MODEL; do
  val="$(printf '%s' "$EXT" | jq -r ".${pair%%:*} // empty" 2>/dev/null || true)"
  [ -n "$val" ] && printf '%s=%s\n' "${pair##*:}" "$val" >> .env
done
unset EXT val
printf "CD_IMAGE=%s\nCD_UI_IMAGE=%s\n" "$IMAGE" "$UI_IMAGE" > .deploy.env
docker compose --env-file .env --env-file .deploy.env -f docker-compose.prod.yml pull --quiet
docker compose --env-file .env --env-file .deploy.env -f docker-compose.prod.yml up -d --remove-orphans --wait --quiet-pull
docker image prune -f >/dev/null
docker compose --env-file .env --env-file .deploy.env -f docker-compose.prod.yml ps
echo "deployed $IMAGE"
