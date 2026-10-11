#!/usr/bin/env bash
# Writes ./.env for local development from the team's GitHub App stored in AWS Secrets Manager.
# Run it yourself with AWS credentials that can read causaldeploy/prod/external (`aws login`).
# Only the GitHub App identity is copied. Production's session secret, database password and
# webhook secret are NOT copied: local sessions must never be valid in production.
set -euo pipefail
cd "$(dirname "$0")/.."
REGION="${AWS_REGION:-us-east-1}"
command -v aws >/dev/null && command -v jq >/dev/null || { echo "needs aws cli and jq" >&2; exit 1; }
EXT=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id causaldeploy/prod/external \
        --query SecretString --output text)
[ -f .env ] && cp .env ".env.bak.$(date +%s)"
umask 077
{
  grep -vE '^(CD_GITHUB_APP_ID|CD_GITHUB_APP_PRIVATE_KEY|CD_GITHUB_OAUTH_CLIENT_ID|CD_GITHUB_OAUTH_CLIENT_SECRET|CD_SESSION_SECRET|CD_ALLOWED_REPOS|CD_PUBLIC_BASE_URL|CD_DUCKDNS_TOKEN)=' .env.example
  echo "CD_GITHUB_APP_ID=$(echo "$EXT" | jq -r .app_id)"
  echo "CD_GITHUB_APP_PRIVATE_KEY=base64:$(echo "$EXT" | jq -r .app_private_key | base64 -w0)"
  echo "CD_GITHUB_OAUTH_CLIENT_ID=$(echo "$EXT" | jq -r .oauth_client_id)"
  echo "CD_GITHUB_OAUTH_CLIENT_SECRET=$(echo "$EXT" | jq -r .oauth_client_secret)"
  echo "CD_SESSION_SECRET=$(openssl rand -base64 48 | tr -d '\n')"
  echo "CD_ALLOWED_REPOS=yashchandnani07/shoplite"
  echo "CD_PUBLIC_BASE_URL=http://localhost:3000"
} > .env
echo ".env written (GitHub App identity + a fresh local session secret). Now: make bootstrap"
