# GitHub App, webhooks and dev tunnel

## Permissions (least privilege)

| Permission | Level | Why |
|---|---|---|
| Contents | read | blobless clone for backfill and diffs (write only when fix branches ship, issue #91) |
| Pull requests | write | open the remediation PR |
| Deployments | read | map `deployment`/`deployment_status` to graph Deployments |
| Actions | read/write | read workflow runs as Builds; dispatch the verification workflow |
| Checks | write | report verification result on the fix PR |
| Metadata | read | mandatory |

Subscribed events: `push`, `pull_request`, `deployment`, `deployment_status`, `workflow_run`, `check_run`.
Handled today: `push` (incremental backfill job), `deployment`, `deployment_status`, `workflow_run`
(completed). Others are stored raw and ignored.

## Configuration

`CD_GITHUB_APP_ID`, `CD_GITHUB_APP_PRIVATE_KEY` (PEM), `CD_GITHUB_WEBHOOK_SECRET`. All are `SecretStr`
and redacted from logs. Webhook endpoint: `POST /webhooks/github` (HMAC SHA-256, constant-time compare;
deduped on `X-GitHub-Delivery`; raw payloads in `raw_events`).

## Dev tunnel

```bash
make up
smee -u https://smee.io/<channel> -t http://localhost:8000/webhooks/github   # or: ngrok http 8000
```

Set the App's webhook URL to the tunnel URL. Re-run stored deliveries with
`uv run python -m causaldeploy.cli replay` (graph writes are idempotent).
