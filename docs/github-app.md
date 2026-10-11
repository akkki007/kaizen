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

## Registering the App (manifest flow, as done for production)

```bash
python3 scripts/github_app_manifest.py /tmp/ghapp.code   # then open http://localhost:8765 and confirm on GitHub
```

GitHub redirects back with a one-time `code`. Exchange it **on the host**, so the generated private key,
client secret and webhook secret go straight into Secrets Manager and never through a person
(see the SSM command in `docs/operations.md`): `POST /app-manifests/<code>/conversions`, then
`put-secret-value` for `causaldeploy/prod/external` and `causaldeploy/prod/webhook`.
Finally install the App on the repositories to monitor (Settings -> Applications -> Install).

Webhook URL: `https://shoplite-demo.duckdns.org/webhooks/github` (set by the manifest).
