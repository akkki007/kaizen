#!/usr/bin/env bash
# Run a shell script on the production host through SSM and stream its output.
#   ssm-run.sh <instance-id> <script-file>      (AWS credentials come from the environment/OIDC)
# Exit status is the remote script's. Nothing is logged except the script's own stdout/stderr.
set -euo pipefail
INSTANCE="${1:?instance id}"
SCRIPT="${2:?script file}"
PARAMS=$(jq -n --rawfile s "$SCRIPT" '{commands: ["bash <<'"'"'EOS'"'"'\n" + $s + "EOS\n"]}')
CID=$(aws ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
  --timeout-seconds 900 --parameters "$PARAMS" --query Command.CommandId --output text)
for _ in $(seq 1 180); do
  STATUS=$(aws ssm get-command-invocation --command-id "$CID" --instance-id "$INSTANCE" \
    --query Status --output text 2>/dev/null || echo Pending)
  case "$STATUS" in Pending|InProgress|Delayed) sleep 5 ;; *) break ;; esac
done
aws ssm get-command-invocation --command-id "$CID" --instance-id "$INSTANCE" \
  --query '[StandardOutputContent, StandardErrorContent]' --output text
[ "$STATUS" = "Success" ] || { echo "remote command finished with status $STATUS" >&2; exit 1; }
