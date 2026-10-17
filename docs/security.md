# Security review (#92)

Scope: the Kaizen backend (`core/`), its GitHub App, the production deployment (`deploy/`) and CI
(`.github/workflows/ci.yml`). Reviewed 2026-10-17 against commit `a7cbe1f` plus the fixes listed
below. Every claim cites the code or the test that proves it; "accepted" findings say why.

## Assets

| Asset | Where | Why it matters |
|---|---|---|
| GitHub App private key | AWS Secrets Manager -> `CD_GITHUB_APP_PRIVATE_KEY` (`SecretStr`) | mints installation tokens: read code, push branches, open/merge PRs on installed repos |
| Installation tokens (1 h) | worker memory (`GitHubClient._tokens`) | same as above, short-lived |
| Webhook secret, OAuth client secret, session secret | Secrets Manager -> env, `SecretStr` | forge webhooks / sessions |
| Ingest API keys | `api_keys.key_hash` (SHA-256 only; plaintext shown once) | write telemetry/events for one repo |
| Source code + diffs of monitored repos | temp clones, FalkorDB graph | confidentiality of the customer's code |
| Telemetry (log messages, stack traces) | Postgres `telemetry` | may contain PII from the monitored app |
| LLM prompts/responses | `llm_calls` (redacted), OpenAI | code and log excerpts leave the host |
| Default branch / production of monitored repo | GitHub | Kaizen must never change it without a person |

## Trust boundaries and attackers

```
 Internet ──TLS (Caddy)──► /webhooks/github   (GitHub, HMAC-signed)          A1 forged/replayed webhooks
                       ├─► /api/v1/events     (producers, X-API-Key)          A2 stolen key, flood, oversize
                       ├─► /auth, /api/v1/*   (browsers, session cookie)      A3 CSRF, session forgery, IDOR
 worker ──► GitHub API / git (installation token)                             A4 over-privileged App
 worker ──► OpenAI (redacted, read-only tools)                                A5 prompt injection via repo data
 worker ──► target repo's tests in a temp worktree (LLM patch, off by default) A6 malicious test code
 CI ──► dependencies, images                                                  A7 supply chain, leaked secrets
```

Untrusted inputs: everything in a webhook payload, every ingested event, every commit message,
diff, log line and error message (attacker-controllable through a PR or a crafted request to the
monitored app), and the monitored repository's own test code.

## Findings

Severity is for this deployment (one VM, one allowlisted repository, a small trusted team).

| # | Area | Finding | Severity | Status |
|---|---|---|---|---|
| S-1 | Webhooks | `/webhooks/github` buffered the whole body **before** the HMAC check and had no size cap (only `/api/v1/events` was capped): an unauthenticated sender could make the API hold arbitrarily large bodies in memory | Medium | **Fixed**: 25 MB cap (GitHub's documented maximum) in `api/deps.py` `BodySizeLimit` (declared and streamed sizes); `test_security.py::test_oversized_webhook_is_413_before_it_is_buffered_or_verified` |
| S-2 | GitHub App | Manifest requested `contents: read`, but the code pushes `kaizen/fix-*` branches (`remediate/ops.py` `GitHubOps.push`) and merges approved PRs (`PUT /pulls/{n}/merge`): under-privileged, revert PRs would fail with 403 | Medium | **Fixed** in `docs/github-app-manifest.json` (`contents: write`) and `docs/github-app.md`. **Human step:** the registered App must be updated in GitHub settings (a manifest only applies at registration) and each installation must accept the new permission |
| S-3 | Patch sandbox | LLM patch tests (`remediate/patch.py`) run the target repo's `npm test` on the worker host. The environment is allowlisted (no `CD_*`, `GITHUB_*`, `OPENAI_*`, `AWS_*`; `test_verify.py::test_sandbox_env_drops_credentials_and_config`, `test_subprocesses_never_see_secrets`), but the subprocess shares the network, the filesystem and the user: on Linux it could read `/proc/<worker pid>/environ` | Medium | **Accepted (mitigated)**: patching is off by default (`CD_REMEDIATION_PATCH_ENABLED=false`, `test_remediation_flow.py::test_patching_is_off_by_default`), the production image has no Node/npm so it cannot run there, only allowlisted repos are cloned, and real verification runs in the repo's own CI (staging). **Before enabling patches in prod:** run the test step in a separate container with `--network none`, no secrets and a read-only root |
| S-4 | Container | `core/Dockerfile` runs as root and copies `uv:latest` (unpinned) | Medium | **Accepted** for the deadline: single-tenant VM, no host mounts besides volumes, Trivy scans the image in CI. Follow-up: `USER 10001`, pin the uv image digest |
| S-5 | CSRF | `POST /api/v1/incidents/{id}/rank` persists the recomputed ranking but has no `csrf_protect` dependency (all other state-changing routes do) | Low | **Accepted**: session cookies are `SameSite=Lax` (`test_session_and_csrf_cookie_flags`), so a cross-site POST carries no session; the effect is an idempotent recomputation. Adding the dependency changes the OpenAPI/UI client and is left to the UI merge |
| S-6 | Webhooks | GitHub signatures carry no timestamp: a captured delivery can be re-sent under a new `X-GitHub-Delivery` | Low | **Accepted**: TLS only; deliveries are deduped on the delivery id (`test_api.py::test_webhook_signature_dedupe`, concurrent: `test_chaos.py::test_concurrent_duplicate_webhook_deliveries_are_processed_once`) and every effect is keyed on content (`push:<repo>:<after>`, `merged:<repo>:<pr>`, `verify:<repo>:<run>`, graph MERGE), so a replay does nothing new (`test_same_push_under_a_new_delivery_id_queues_no_second_import`) |
| S-7 | Ingest | Rate limiter is in-process (per API replica, reset on restart) | Low | **Accepted**: one API replica in prod; limit counts events not requests, 429 + `Retry-After` (`test_incident_api.py::test_rate_limit_counts_events_and_sets_retry_after`). Move to Redis if the API is scaled out |
| S-8 | Sessions | HS256 session JWT (8 h TTL) with the user's repo roles snapshotted at login; logout deletes the cookie but there is no server-side revocation, so a role removed on GitHub stays effective until expiry | Low | **Accepted**: short TTL; rotating `CD_SESSION_SECRET` signs everyone out (runbook). Unsigned / foreign-key / tampered / expired tokens are rejected (`test_unsigned_or_foreign_key_session_tokens_are_rejected`, `test_api.py::test_tampered_or_expired_sessions_are_rejected`) |
| S-9 | LLM | Redaction is pattern based (configured secret values, GitHub/AWS/JWT/PEM, `key=value`, emails): an unknown secret format inside a log line could reach the provider | Low | **Accepted (mitigated)**: `test_agent_harness.py::test_redact_payload_scrubs_every_secret_shape`; `audit_log` keeps only the payload hash; `llm_calls` stores the redacted request |
| S-10 | Retention | Telemetry is purged after `CD_TELEMETRY_RETENTION_DAYS` (14, `test_retention.py`); `raw_events`, `llm_calls`, `jobs` payloads and `audit_log` are kept indefinitely | Low | **Accepted**: needed for replay and audit during the project; follow-up: 90-day purge for `raw_events`/`llm_calls`/`jobs` (audit log stays) |
| S-11 | CI | Third-party actions pinned by tag, not commit SHA | Low | **Accepted**: Dependabot updates actions (`.github/dependabot.yml`); the only secret-bearing workflow (`deploy.yml`) uses OIDC, no stored keys |
| S-12 | Ingest keys | No per-IP throttling of failed `X-API-Key` attempts | Low | **Accepted**: keys are `cd_` + 256 random bits (`secrets.token_urlsafe(32)`), stored as SHA-256; guessing is infeasible |
| S-13 | GitHub App | `check_run` is subscribed but never handled; `workflows` permission is not requested, so a revert touching `.github/workflows/*` cannot be pushed | Info | **Accepted**: harmless (stored raw); the failed push leaves that fix to a person |
| S-14 | LLM | Prompt injection through commit messages / logs | Info | **Mitigated, tested**: untrusted data fenced (`agent/safety.py` `wrap_untrusted`), read-only tools only (`test_mcp.py::test_server_lists_only_read_only_tools`), strict output schema + citation validator, ranking decided by the deterministic scorer (`test_agent.py::test_agent_cannot_override_a_confident_ranking`, `test_agent_harness.py::test_prompt_injection_in_repo_data_is_fenced_and_powerless`) |

Totals: 0 critical, 0 high, 4 medium (2 fixed, 2 accepted), 8 low (accepted), 2 info.

## Controls verified (no finding)

**Webhooks.** HMAC-SHA256 over the raw body with a constant-time compare
(`github/webhooks.py` `verify_signature`; `test_github.py::test_signature_verification`); a bad
signature is 401 and stores nothing (`test_api.py::test_webhook_signature_dedupe`); no secret
configured = 503, fail closed (`test_webhook_fails_closed_without_a_configured_secret`); production
refuses to start without the secret (`test_api.py::test_prod_refuses_to_start_without_real_secrets`).
Deliveries for repositories outside `CD_ALLOWED_REPOS` are stored raw and never acted on
(`test_api.py::test_repositories_outside_the_allowlist_are_never_acted_on`). Malformed payloads
yield no events (`test_github.py::test_malformed_webhook_payloads_are_ignored_not_fatal`).

**Ingest API.** Repository-scoped API key (401 without/with a bad or revoked key, 403 for an event
naming another repo: `test_api.py::test_event_ingest_requires_key_and_dedupes`,
`test_api_key_rotation`); body cap `CD_INGEST_MAX_BYTES` (1 MiB) and batch cap
`CD_INGEST_MAX_BATCH` (1000) -> 413, including chunked bodies without Content-Length
(`test_incident_api.py::test_oversized_batches_and_bodies_are_413`); per-item validation: valid
items are queued, invalid ones listed in `rejected` (`test_mixed_batch_accepts_valid_items_and_reports_rejected`);
events/minute rate limit with `Retry-After`. The bench script refuses non-loopback targets.

**Auth, session, CSRF, origin.** GitHub OAuth with a random `state` cookie
(`test_oauth_login_flow_sets_session_and_roles`); session and CSRF cookies `HttpOnly` (CSRF
readable by the UI for double submit), `SameSite=Lax`, `Secure` in prod
(`test_session_and_csrf_cookie_flags`); double-submit CSRF on every state-changing route except
S-5 (`test_api.py::test_only_approvers_can_approve_a_remediation`,
`test_remediate.py::test_propose_endpoint_is_approver_only_and_csrf_protected`); post-login
redirect is root-relative only (`test_post_login_redirect_is_root_relative_only`); the OAuth origin
is an allowlist (`test_api.py::test_safe_origin_is_an_allowlist`); other repositories' incidents
are 404, not 403, so ids do not leak (`test_cross_repo_requests_are_forbidden`). Viewer vs approver
from the user's GitHub permission on the repo.

**LLM.** Investigation tools are read-only and scoped to one repo graph; the MCP server refuses
repositories outside the allowlist (`test_mcp.py::test_server_refuses_repositories_outside_the_allowlist`);
budgets cap steps, tokens and cost (`test_agent_harness.py::test_token_budget_ends_the_run_with_a_reason_and_a_log_line`);
every call is recorded (`test_investigation_records_every_llm_call`) and `audit_log` gets an
`llm.send` row with the payload hash. No agent tool writes to GitHub.

**GitHub App key and tokens.** The key is a `SecretStr`, never logged or printed
(`test_config_logging.py::test_secrets_not_in_repr_or_logs_or_exceptions`, `test_health.py::test_secrets_are_not_printed`);
tokens reach git only as a one-shot `http.extraheader` argument, never in a URL, and git errors
never echo the command (`remediate/ops.py` `_git`). Kaizen never pushes `main`/`master`
(`test_fix_branches_never_target_a_default_branch`) and never merges without a recorded approval
(`test_remediation_flow.py::test_app_merge_policy_merges_only_after_approval`, `test_manual_policy_never_merges`).
Graph names are sanitised per repo (`test_graph_names_cannot_carry_cypher_or_cross_repo_names`)
and all graph queries are parameterised.

### GitHub App least privilege

Every GitHub call the code makes, and the permission it needs:

| Call | Code | Permission |
|---|---|---|
| `POST /app/installations/{id}/access_tokens`, `GET /repos/{r}/installation` | `github/client.py` | App JWT |
| `git clone` (backfill, fix branches) | `github/backfill_job.py`, `remediate/ops.py` | contents: read |
| `git push kaizen/fix-*` | `remediate/ops.py` `push` | **contents: write** |
| `PUT /repos/{r}/pulls/{n}/merge` (merge policy `app`, after approval) | `merge_pr` | **contents: write** |
| `POST /repos/{r}/pulls`, `PATCH .../pulls/{n}` (close) | `open_pr`, `close_pr` | pull_requests: write |
| `POST /repos/{r}/issues/{n}/labels`, `.../comments`, `PATCH .../issues/comments/{id}` (on PRs) | `open_pr`, `close_pr`, `guardian/service.py` | pull_requests: write (accepted for PRs) |
| `GET .../pulls/{n}/files`, `.../issues/{n}/comments` | `guardian/service.py` | pull_requests: read |
| `POST /repos/{r}/check-runs` | `post_check` | checks: write |
| `POST .../actions/workflows/verify.yml/dispatches` | `dispatch_verify` | actions: write |
| `GET .../actions/runs/{id}/artifacts`, artifact download | `fetch_report` | actions: read |
| `GET /repos/{r}` (default branch) | `dispatch_verify` | metadata: read |
| `deployment`, `deployment_status` webhooks | `github/webhooks.py` | deployments: read |

Over-privilege: none beyond `contents: write`, which is inherent to pushing fix branches. It
also allows writing the default branch, which the code refuses; **protect the default branch on
GitHub** (require PR review) so a compromised key cannot bypass that check. Under-privilege: see
S-2 (fixed) and S-13 (`workflows`, accepted).

## CI scanning (`.github/workflows/ci.yml`, `security` + `image` jobs)

| Check | Tool | Fails on |
|---|---|---|
| Secrets in full git history | gitleaks v8.21.2, `.gitleaks.toml` (allowlist: `core/tests/`, the two redactor modules, one old generated report) | any finding |
| Python runtime dependencies | pip-audit 2.7.3 over `uv export --locked --no-dev` | any known vulnerability |
| UI runtime dependencies | `npm audit --omit=dev --audit-level=high` | high/critical |
| Container image | Trivy (`ignore-unfixed`) | fixable HIGH/CRITICAL |
| Updates | Dependabot: uv, npm, GitHub Actions, Dockerfile | n/a |

## Re-running this review

```bash
cd core && uv run pytest -q tests/test_security.py tests/test_api.py tests/test_incident_api.py \
  tests/test_agent_harness.py tests/test_mcp.py tests/test_verify.py tests/test_chaos.py
```
