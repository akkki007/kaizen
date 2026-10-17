# GitHub App, webhooks and dev tunnel

## Permissions (least privilege)

| Permission | Level | Why |
|---|---|---|
| Contents | write | clone for backfill and diffs; push `kaizen/fix-*` branches; `PUT /pulls/{n}/merge` (only with `CD_REMEDIATION_MERGE_POLICY=app` after a recorded approval) |
| Pull requests | write | open / close the remediation PR, add its labels and comments (the issues label/comment endpoints accept `pull_requests: write` for PRs), PR Guardian comment |
| Deployments | read | map `deployment`/`deployment_status` to graph Deployments |
| Actions | read/write | read workflow runs as Builds and download the verification report artifact; dispatch `verify.yml` |
| Checks | write | post the "Kaizen verification" check run on the fix commit |
| Metadata | read | mandatory |

Not requested on purpose: `issues`, `workflows` (a revert that touches `.github/workflows/*` is
refused by GitHub at push time: the `propose_fix` job fails, is retried and ends `dead`, and a
person makes that fix), `administration`, `secrets`. Every call the code makes is
listed in [`security.md`](security.md#github-app-least-privilege).

Subscribed events: `push`, `pull_request`, `deployment`, `deployment_status`, `workflow_run`, `check_run`.
Handled: `push` (incremental backfill job), `pull_request` (PR Guardian; merged fix PRs finalize the
remediation), `deployment`, `deployment_status`, `workflow_run` (completed: Builds and the `verify`
workflow result), installation events. `check_run` is stored raw and ignored.

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
