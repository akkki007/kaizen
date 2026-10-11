# Verification of issues #84-#87

How each acceptance item was checked. "Prod" = https://shoplite-demo.duckdns.org (AWS EC2, us-east-1).

## #84 Platform & Infrastructure

| Item | Evidence |
|---|---|
| Monorepo, `make up/down/test/lint/eval/seed` | `make bootstrap && make test` on a clean checkout; CI green |
| Contracts, fixtures, JSON Schema | `tests/test_contracts.py` (6 golden fixtures, invalid payloads, schema freshness); CI step fails a schema change without a `SCHEMA_VERSION` bump (`cli check-contract-version`, unit-tested) |
| Schemas consumed by the UI client generator | `npm run gen:api` generates `ui/lib/api/schema.d.ts` from `docs/openapi.json` with no manual edits; CI regenerates and diffs |
| Config / secrets | `tests/test_config_logging.py`: invalid config names the field, prod refuses default secrets/empty allowlist, secrets redacted from logs and exceptions |
| FastAPI skeleton | `tests/test_api.py`: readiness 503 when a dependency is down, error envelope, request ids, pagination, rate limit, ingest auth |
| Postgres + Alembic | `tests/test_migrations.py`: fresh DB to head, downgrade latest, model/migration drift = none. Retention: `tests/test_retention.py` |
| Job framework | `tests/test_jobs.py`: idempotent enqueue, retry then success, dead after max attempts, **real SIGKILL of a worker mid-job retried and completed exactly once** |
| Auth | `tests/test_api.py`: OAuth login flow (mocked GitHub), tampered/expired sessions, 401 without session, cross-repo 403/404, CSRF, only approvers approve, API-key rotation |
| Self-observability | `/metrics`, trace context API->job (`test_trace_context_flows_from_enqueue_to_job`), LLM cost per incident, JSON logs with request/job/incident ids |
| Production deployment | Prod: TLS cert, `/readyz` all true, `/metrics` not exposed, bad webhook signature 401, deploys via OIDC+SSM from GitHub Actions |
| Backups / restore | `scripts/test-restore.sh` (write, backup, destroy, restore, verify), also a CI job |
| CI | `.github/workflows/ci.yml`: ruff, mypy, pytest with service containers, restore drill, image build, UI lint/build |

## #85 ShopLite (https://github.com/yashchandnani07/shoplite)

| Item | Evidence |
|---|---|
| App (3 services + money-utils) | 27 tests incl. gateway->orders->payments with one request id in all three logs; `docker compose up` run |
| CI/CD, Deployments | Prod deploy: test, build, GHCR push, SSM deploy, GitHub Deployment `success`, event announced to CausalDeploy and present as a `Deployment` node |
| Health check | `health-check.yml` run on schedule/dispatch, event accepted by prod |
| Seeded history | 75+ commits, 13 merged PRs (merge commits), 3 incidents with ground truth (`docs/history.md`) |
| Load generator | deterministic planner, weighted mix, edge cases, traffic-shift option, record/replay format |
| Scenarios S1-S10 | `scenarios/verify.js`: 28 variants, each checked: CI still passes, base is healthy, failure under load |

## #86 GitHub integration & ingestion

| Item | Evidence |
|---|---|
| App registration, installation auth | App registered (manifest flow), credentials stored by the host; prod obtained real installation tokens and cloned/analysed repos |
| Webhook receiver | signature 401 stores nothing; duplicate delivery ignored; ack < 1 s; replay CLI tested; installation events record repos and request the first import |
| Backfill | real ShopLite history: 77+ commits in ~1.3 s locally, idempotent; prod graph has 80 commits |
| Analyzer | golden tests on the ShopLite-shaped fixture; real repo: 81 functions, 20 endpoints |
| Hunk -> function | prod graph: H1 -> `validateAmount`, H2 -> `toCents`, H3 -> `POST /payments` handler (+ the constructor it really changed); nested handlers blame only the innermost function; merges are not double counted |
| Dependencies | workspace package versions linked; version bump = `changes_dep` |
| Deployment tracking | commit range from the previous successful deployment (prod: 1 commit for the second deploy); rollback and "deployment arrived before its commits" tested |

## #87 Knowledge graph

| Item | Evidence |
|---|---|
| Schema + upsert layer | `tests/test_graph_store.py`: versioned schema, no-op re-ingest, constraints, per-repo isolation |
| Fixtures + golden queries | S1-S5 shapes, six queries with golden tests, each < 200 ms on a ShopLite-sized graph |
| Evidence-path API | `GET /api/v1/incidents/{id}/evidence-graph`: highlighted path, citation resolver, expansion, repo-scoped |
| FalkorDB ops | AOF+RDB, memory cap, slow query log + metric, restore drill |

## Known gaps (not claimed)

* No web UI (issue #93): login page and dashboards do not exist; the API is complete.
* Incident detection and ranking are issues #88/#89; no incident is opened automatically yet.
* Rollback of a prod deploy has not been exercised; backups are not scheduled/shipped to S3 yet.
* Load generator: no 10-minute baseline or staging replay run.
* README quickstart has not been followed by a second person.
* Seeded history authors are invented identities.
* The App was briefly installed on all of the owner's repositories; the allowlist now prevents any
  import outside `CD_ALLOWED_REPOS`, but data imported before that remains until it is deleted.
