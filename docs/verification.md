# Verification of issues #84-#93

How each acceptance item was checked. Test names are `core/tests/<file>::<test>`; run them with
`cd core && uv run pytest -q <file>` (Windows: README "Windows"). "Prod" = https://shoplite-demo.duckdns.org (AWS EC2, us-east-1).

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
| CI/CD, Deployments | Prod deploy: test, build, GHCR push, SSM deploy, GitHub Deployment `success`, event announced to Kaizen and present as a `Deployment` node |
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

## #88 Runtime telemetry & incident detection

Status: **done** = implemented and proven by the named test or doc; **partial** = part of the
criterion is proven, the rest named; **external** = lives outside this repo or needs a live run.

| Task / criterion | Status | Evidence |
|---|---|---|
| ShopLite reporter (Express middleware, batching, retry, bounded queue, `service/env/sha`) | external | ShopLite repo; Kaizen side accepts and stores `sha` (contracts 1.1.0): `test_detect_rules.py::test_bulk_insert_and_sha_are_persisted` |
| ✅ Reporter overhead < 2% latency | external | needs a ShopLite load test with/without the middleware |
| ✅ Telemetry arrives with the deployed SHA | partial | Kaizen persists and prefers it (`test_bulk_insert_and_sha_are_persisted`, `test_error_count_service_edge_and_sha_preference`); live arrival from ShopLite not re-checked |
| `POST /api/v1/events` batch endpoint, API-key auth, validation, idempotency | done | `test_api.py::test_event_ingest_requires_key_and_dedupes`, `test_api_key_rotation` |
| Backpressure 429 + Retry-After, size limits | done | `test_incident_api.py::test_rate_limit_counts_events_and_sets_retry_after`, `test_rate_limiter_retry_after_is_when_enough_events_expire`, `test_oversized_batches_and_bodies_are_413` |
| Retention job | done | `test_retention.py::test_purge_keeps_only_the_retention_window` (daily cron `purge_job`) |
| ✅ Sustains 500 events/s locally | done | `docs/performance.md`: telemetry fast path (one idempotent INSERT per batch, one graph job per batch, migration 0004): 496 ev/s at a 500/s target (p50 315 ms per batch) and a ~880 ev/s ceiling on a laptop, every row verified stored; `test_ingest_batch.py` |
| ✅ Invalid/oversized payloads rejected without data loss for valid ones | done | `test_incident_api.py::test_mixed_batch_accepts_valid_items_and_reports_rejected`, `test_oversized_batches_and_bodies_are_413` |
| Stack parsing (app frames only) | done | `test_detect.py::test_frames_drop_runtime_and_dependency_code` |
| Fingerprint = type + top app frame + normalised message | done | `test_detect.py::test_fingerprint_is_stable_across_ids_numbers_and_line_drift` |
| `thrown_in {frame_depth}` mapping at the deployed SHA; Error -> Endpoint, Service; first/last seen, count | done | `test_detect.py::test_error_is_linked_to_the_function_that_threw`, `test_detect_rules.py::test_error_count_service_edge_and_sha_preference` |
| ✅ S1 errors map to `validatePayment()` | done | `test_detect.py::test_error_is_linked_to_the_function_that_threw`, `test_full_loop.py::test_s1_end_to_end` |
| ✅ Same bug at different lines across deploys = one fingerprint | done | `test_detect.py::test_fingerprint_is_stable_across_ids_numbers_and_line_drift` |
| Windows, rolling baseline, error_rate/p95/z, Metric nodes, min sample size | done | `test_detect.py::test_slow_but_not_failing_is_detected_by_p95`, `test_single_spike_and_quiet_traffic_do_not_open_incidents`, `test_detect_rules.py::test_severity_metrics_and_idempotent_detection` |
| ✅ Baseline excludes the incident window | done | `test_detect_rules.py::test_baseline_comes_only_from_pre_onset_windows` |
| ✅ Aggregation job idempotent | done | `test_detect_rules.py::test_severity_metrics_and_idempotent_detection`, `test_detect.py::test_end_to_end_ingest_detect_and_open_incident` (second run opens nothing) |
| Per-service thresholds, consecutive windows, onset, dedupe, cooldown, health checks, `investigate_incident` job | done | `test_detect_rules.py::test_per_service_overrides_are_merged`, `test_cooldown_suppresses_flapping`, `test_consecutive_failed_health_checks_open_an_incident`; job: `test_full_loop.py` |
| ✅ S1 opens exactly one incident, onset within 1 window | done | `test_detect.py::test_end_to_end_ingest_detect_and_open_incident` (onset = exact failing window) |
| ✅ S10 noise opens nothing | done | `test_detect_rules.py::test_s10_noisy_but_healthy_traffic_opens_nothing` |
| State machine, transitions with actor, graph sync, manual acknowledge/close/reopen, root cause confirm/reject | done | `test_lifecycle.py` (4 tests), `test_incident_api.py::test_acknowledge_close_reopen_and_history`, `test_memory.py::test_root_cause_confirm_and_reject`; ADR 0004 |
| ✅ Invalid transitions rejected; full history queryable | done | `test_lifecycle.py::test_invalid_transition_is_rejected_and_not_recorded`, `test_incident_api.py::test_illegal_transitions_are_409_and_other_repos_404`, `GET /incidents/{id}/history` |

## #89 Investigation & root-cause ranking

| Task / criterion | Status | Evidence |
|---|---|---|
| Lookback window, all deployments to the env before onset | done | `test_ranking.py::test_lookback_window_adds_successful_deployments_before_onset`, `test_triggering_deployment_wins_for_a_commit_in_two_deployments` |
| Multi-commit deployments expanded; non-code candidates (config, dependency) | done | `test_ranking.py::test_s3_candidates_are_exactly_the_five_bundled_commits`, `test_s6_config_change_is_the_top_non_code_candidate`, `test_dependency_change_without_a_code_commit_is_a_candidate` |
| ✅ S3 yields exactly the 5 bundled commits; S6 yields a config candidate | done | same two tests |
| Features temporal, path, endpoint, dependency, msg_sim, history, sequence, blast (+ service, centrality, triggered), each `{value, evidence_ids, explanation}` | done | `test_features.py` (one test per feature) |
| ✅ Feature tests on S1-S5; evidence ids resolve in the graph | done | `test_features.py::test_every_evidence_id_resolves_for_every_candidate` (parametrised over the fixtures) |
| Versioned weights, deterministic, confidence, margin abstention, `candidate` + `likely_caused_by` edges, investigation record | done | `test_ranking.py::test_weights_are_versioned_and_stamped`, `test_ranking_is_deterministic`, `test_ambiguous_persists_top3_and_a_tentative_cause`, `test_persist_replaces_candidate_edges`, `test_investigation_record_has_inputs_version_and_duration` |
| ✅ S1-S5 CLI report ranks the true culprit first | done | `test_ranking.py::test_cli_investigate_prints_verdict_candidates_and_explanations` (S1), `test_s1_culprit_is_first_with_every_feature_computed`, `test_s2_function_level_precision_beats_file_level`, `test_s3_picks_the_culprit_out_of_five_where_latest_commit_is_wrong`, `test_s4_cross_service_path_is_found`, `test_s5_dependency_bump_scores_on_the_dependency_feature` on the fixtures. Note: the eval's S4 variants without a stack trace are `ambiguous` (culprit in top 3), see `docs/results/latest.md` |
| ✅ Ambiguous cases abstain | done | `test_ranking.py::test_ambiguous_persists_top3_and_a_tentative_cause`, `test_endpoint_only_evidence_is_never_confident`, `test_unrelated_change_yields_no_code_evidence`; eval: 0% false attribution |
| Blast radius / impact report (observed vs potential, originating vs symptomatic, fan-out) | done | `test_graph_queries.py::test_q7_impact_report_s4_originating_vs_symptomatic`, `test_q7_impact_report_shared_dependency_fan_out_s5`; eval blast P 0.97 / R 0.96 |
| ✅ S4 identifies symptomatic and originating service | done | `test_graph_queries.py::test_q7_impact_report_s4_originating_vs_symptomatic` |
| Baselines B0 and B1 (BM25 over message, paths, function names), same interface | done | `test_ranking.py::test_b1_keyword_baseline_is_misled_where_graph_is_not`, `test_bm25_idf_is_over_the_candidate_set`, `test_b1_matches_changed_file_paths_and_function_names`, `test_rank_api_returns_features_with_queries_and_methods` |
| ✅ Baselines run on the same inputs, comparable lists | done | `test_eval.py::test_graph_ranking_beats_both_baselines_without_false_attribution`; `docs/results/latest.md` |

## #90 Agent harness (OpenAI)

| Task / criterion | Status | Evidence |
|---|---|---|
| Client wrapper: timeouts, retries with jitter, Retry-After, structured output with fallback | done | `test_llm_client.py` (5 tests) |
| Tool loop with step/token/cost budgets; `llm_calls` trace; prompt versioning; fake model | done | `test_agent_harness.py::test_token_budget_ends_the_run_with_a_reason_and_a_log_line`, `test_cost_budget_uses_the_price_table`, `test_investigation_records_every_llm_call`, `test_prompt_is_a_packaged_versioned_file`, `test_run_and_calls_carry_the_prompt_version`; `Scripted` fake in `test_agent.py` |
| ✅ Replay is byte-identical with zero network calls | done | `test_agent_harness.py::test_record_then_replay_is_byte_identical_with_no_network`, `test_replay_mode_builds_a_client_without_an_api_key` |
| ✅ Budget exceeded -> graceful, logged failure | done | `test_agent_harness.py::test_token_budget_ends_the_run_with_a_reason_and_a_log_line`, `test_agent.py::test_tool_budget_is_enforced` |
| Read-only tools, pydantic schemas, repo-scoped, size-capped, citable ids | done | `test_mcp.py::test_server_lists_only_read_only_tools`, `test_invalid_input_is_rejected_by_the_input_model`, `test_cap_bounds_items_and_bytes`, `test_tools_return_citable_node_ids`, `test_server_refuses_repositories_outside_the_allowlist` |
| ✅ "What else could be affected?" answered with tools only | done | `test_agent.py::test_what_else_could_be_affected` |
| Explanation: evidence bundle, causal chain, confidence, alternatives, validator, template fallback, abstention | done | `test_agent.py::test_explanation_with_causal_chain_alternatives_and_confidence`, `test_two_invalid_answers_fall_back_to_the_template`, `test_ambiguous_ranking_forces_abstention_with_alternatives`, `test_template_abstains_on_an_ambiguous_ranking` |
| ✅ Fabricated citation rejected | done | `test_agent.py::test_hallucinated_citation_is_rejected_then_corrected`, `test_fabricated_citation_in_causal_chain_or_alternatives_is_rejected` |
| ✅ S1 explanation mentions function, endpoint, deployment, similar prior incident, all linked | done (scripted model) | `test_full_loop.py::test_s1_end_to_end` (citations validated against the evidence graph); with the real model: `make eval-live` (external, costs money) |
| Safety: delimiting, redaction, schema validation, no GitHub write tool, adversarial tests, audit | done | `test_agent_harness.py::test_redact_payload_scrubs_every_secret_shape`, `test_prompt_injection_in_repo_data_is_fenced_and_powerless`; `docs/security.md` S-9, S-14 |
| ✅ Injection does not alter tool use or output | done | `test_agent_harness.py::test_prompt_injection_in_repo_data_is_fenced_and_powerless`, `test_agent.py::test_agent_cannot_override_a_confident_ranking` |
| ✅ Secrets in logs never in LLM payloads | done | `test_agent_harness.py::test_redact_payload_scrubs_every_secret_shape`, `test_investigation_records_every_llm_call` |
| Golden scenarios, rubric, regression run, cost/latency assertions | done | `test_llm_golden.py::test_golden_runs_pass_the_rubric_in_replay_mode`, `test_rubric_fails_a_wrong_or_over_budget_run` |
| ✅ CI runs replay golden tests; manual command runs live evals | partial | replay in CI: done; `make eval-live` exists, a recorded live run is external (needs a key) |

## #91 Remediation, verification & incident memory

| Task / criterion | Status | Evidence |
|---|---|---|
| Revert incl. merge/squash, `kaizen/fix-<incident>` branch, PR with explanation, labels, conflict report | done | `test_remediate.py::test_revert_branch_undoes_the_commit_and_never_touches_main`, `test_merge_commit_is_reverted_against_its_mainline`, `test_squash_merge_is_reverted_as_a_single_commit`, `test_revert_conflict_is_reported_not_forced`, `test_full_remediation_flow`, `test_verify.py::test_github_ops_labels_checks_merge_and_close` |
| ✅ S1 clean revert PR that passes ShopLite tests | external | Kaizen side proven on a local git remote (`test_full_remediation_flow`); live PR on ShopLite pending (App needs `contents: write`, `docs/security.md` S-2) |
| LLM patch: constraints, worktree, unit tests, diff + rationale | done | `test_remediation_flow.py::test_patch_is_tested_in_a_worktree_and_proposed`; off by default (`test_patching_is_off_by_default`) |
| ✅ Failing patch rejected, logged, falls back to revert | done | `test_remediation_flow.py::test_a_rejected_patch_falls_back_to_the_revert`, `test_patch_failing_verification_falls_back_then_escalates` |
| ✅ S1 patch fixes the bug with a regression test | external | needs a real model run on ShopLite |
| Approval API approve/reject, audit, merge by approver or app with approval, reject feeds memory | done | `test_api.py::test_only_approvers_can_approve_a_remediation`, `test_remediation_flow.py::test_reject_endpoint_closes_the_pr_and_hands_back_to_a_human`, `test_rejecting_a_fix_in_preparation_returns_to_the_diagnosis` |
| ✅ No code path merges/deploys without an approval record | done | `test_remediation_flow.py::test_app_merge_policy_merges_only_after_approval`, `test_manual_policy_never_merges`; merged-without-approval is audited and does not resolve |
| Verification workflow (tests, ephemeral staging, traffic replay, failing SHA baseline), `verification_result` + check | partial | Kaizen side (dispatch, routing, check run): `test_remediation_flow.py::test_verification_result_event_is_routed_and_posts_a_check`, `test_remediate.py::test_workflow_and_merge_webhooks_enqueue_verification_and_finalize`. The workflow itself lives in ShopLite: external |
| ✅ S1 revert passes, unfixed SHA fails | external | ShopLite `verify.yml` live run |
| ✅ Workflow < 10 min | external | measure at rehearsal (`docs/demo.md` checklist) |
| Verdict with tolerances and min samples; `Fix -[:verified_by]-> Test`; fallback chain | done | `test_verify.py` (verdict tests), `test_remediation_flow.py::test_failed_verification_event_escalates` |
| ✅ Verdict reproducible from stored metrics | done | `test_verify.py::test_recompute_from_a_stored_record` |
| ✅ Failed verification never resolves | done | `test_remediate.py::test_failed_verification_never_resolves_and_escalates` |
| Memory writer, human confirm/reject, demo-history backfill | done | `test_memory.py::test_root_cause_confirm_and_reject`, `test_demo_history_is_deterministic_and_idempotent`; `make demo-reset` |
| ✅ Resolved incidents have Incident -> Commit -> Fix -> Test in the graph | done | `test_full_loop.py::test_s1_end_to_end` (`confirmed_cause` / `reverts` / `verified_by` chain + memory timings), `test_remediate.py::test_full_remediation_flow` |
| Weighted-Jaccard `resembles {similarity, basis}`, prior fix in explanation and PR | done | `test_memory.py::test_error_family_masks_values`, `test_pr_body_cites_the_prior_fix` |
| ✅ S8 retrieves the S1-like prior incident and its fix, ranked above unrelated ones | done | `test_memory.py::test_recurrence_ranks_the_prior_incident_with_its_fix_first`; eval S8 confidence 0.65 -> 0.78 |

## #92 Evaluation harness, hardening, docs & demo

| Task / criterion | Status | Evidence |
|---|---|---|
| Scenario runner: selection, parallelism, timeouts, record/replay, per-method outputs | done | `test_eval.py::test_cli_filters_and_writes_md_json_html`, `test_timeout_turns_a_slow_variant_into_a_listed_failure`, `test_json_report_shape` |
| Live mode (real GitHub + deploy) | external | live runs against ShopLite; not automated |
| ✅ Replay runs all scenarios < 10 min, no network/LLM | done | `test_eval.py::test_replay_reproduces_identical_rankings_offline_in_budget`; full `make eval` wall time 3.0 s |
| Metrics (top-1/3, function-level, time to hypothesis, false attribution, blast P/R, fix verification), bootstrap CI | done | `test_metrics.py` (6 tests) |
| ✅ Weights versioned; held-out never used for tuning (enforced) | done | `test_fit.py::test_a_test_split_case_never_reaches_the_fitter`, `test_guard_runs_before_any_scoring`, `test_write_refuses_to_overwrite_the_base_weights`; `investigate/weights/v1.json` |
| Report, ablations, failure analysis under `docs/results/` | done | `docs/results/latest.{md,json,html}`, `fit-latest.md` |
| ✅ One command regenerates the report | done | `make eval`; `test_eval.py::test_cli_filters_and_writes_md_json_html` |
| Full-loop E2E in CI with assertions per stage | done | `test_full_loop.py::test_s1_end_to_end` (scripted fake model; the LLM replay cache is exercised by `test_llm_golden.py`) |
| Live-mode script + checklist | partial | `docs/demo.md` checklist; `make eval-live` for the agent; no scripted live ShopLite loop |
| ✅ Red when any stage regresses | done | `test_full_loop.py` asserts graph and Postgres state after detect, rank, explain, fix, verify, merge, memory |
| Threat model, least privilege, CI scanning, sandbox check, rate/size limits | done | `docs/security.md`; new `test_security.py` (7 tests); webhook body cap fixed in `api/deps.py` |
| ✅ Written review with findings fixed or explicitly accepted | done | `docs/security.md` (14 findings: 2 fixed, 12 accepted with rationale) |
| Load test ingest (target 500 ev/s) | done (496 ev/s at the 500/s target, ~880 ev/s ceiling, rows verified) | `docs/performance.md`; `core/scripts/bench_ingest.py`, `bench_drain.py`, `perf_worker.py` |
| Detection -> hypothesis < 30 s (excl. LLM) | done | p50 194 ms; `test_perf.py::test_detection_to_ranked_hypothesis_is_within_budget`, `core/scripts/bench_hypothesis.py` |
| Chaos: worker crash, FalkorDB restart, duplicate webhooks | done | `test_chaos.py` (5 pass, incl. `test_falkordb_outage_does_not_duplicate_error_telemetry`: idempotent telemetry inserts via `event_key`) |
| ✅ Budgets measured and recorded in docs | done | `docs/performance.md` |
| Architecture + data model, graph schema + cookbook, runbook (deploy, backup/restore, rotate keys, replay), ADRs | done | `docs/architecture.md`, `docs/graph.md` (cookbook run against the S1 fixture graph), `docs/operations.md`, `docs/adr/0001`-`0005` |
| ✅ New teammate can run and deploy from the docs only | partial | `README.md` (incl. Windows), `docs/team-setup.md`, `docs/operations.md`; not yet followed end to end by a second person |
| Demo script S1 -> S8 with graph-vs-baseline numbers | done | `docs/demo.md` |
| One-command demo reset | done | `make demo-reset`, `scripts/demo-reset.sh`, `scripts/demo-reset.ps1`, `scripts/demo_reset.py` (refuses `CD_ENV=prod`; exercised against a scratch database from PowerShell and Git Bash) |
| Screen recording fallback | external | human task (`docs/demo.md` checklist) |
| Pitch slides with measured results | done | `docs/pitch.md` |
| ✅ Demo rehearsed twice on the deployed system | external | human task (`docs/demo.md` checklist) |
| [Stretch] PR risk scoring as a check run | partial | PR Guardian posts a **comment** (`guardian/`, `test_guardian.py::test_function_blamed_in_a_past_incident_is_high_risk`); check-run variant not built |
| [Stretch] Embedding similarity | not started | v1 weighted Jaccard only |
| [Stretch] OTLP ingestion, second language analyzer | not started | |

## #93 Web UI

UI checks: `tsc`, Vitest (40 tests), `npm run build`, Playwright 30/30 in mock mode
(`PW_PROD=1 VISUAL=1 npx playwright test --workers=1` on Windows; CI runs `npx playwright test`).
Backend views: `test_incident_views.py`, `test_dashboard_views.py`.

| Task / criterion | Status | Evidence |
|---|---|---|
| Scaffold, design system, typed API client, mock mode | done | `ui/lib/api/client.ts`, `mock*.ts`; CI regenerates the client and fails on drift (`ci.yml`) |
| ✅ UI runs against the mock API and the real API | done | mock: whole Playwright suite; real: `next.config.ts` rewrites `/api/v1` + `/auth` to `API_URL` |
| Sign-in with GitHub and repository onboarding | partial (tracked in #97) | mocked login, error states, 401 redirect, viewer vs approver: `signin.spec.ts`; real GitHub login needs the App callback URL registered (owner step) |
| Dashboard: service health, incident list, deployments timeline | done | `GET /services/health`, `/repos/{o}/{n}/deployments`; `test_service_health_ok_degraded_and_incident`, `test_deployments_with_correlated_incidents`; e2e "overview shows live service health and the deployment timeline with incident markers" |
| ✅ Seeded incident visible within seconds | done | 5 s polling; e2e "a newly seeded incident appears within seconds, without a reload" |
| Incident list: status, severity, onset, suspected cause; filters + archive | done | `test_incident_list_carries_severity_ack_and_latest_suspect`, `test_incident_list_filters`; e2e "incident list: filters, archive and navigation to the detail page" |
| Ranked candidates with per-feature score bars, confidence / abstention | done | e2e "shows ranked suspects with the score breakdown…"; Vitest "abstains when no candidate has code evidence" |
| ✅ Every claim in the explanation links to evidence | done | claims, causal-chain steps, alternatives and feature evidence ids are citation buttons; Vitest "resolves node and edge citations, rejects unknown ids" |
| Pipeline stage tracker updating live (SSE) | done | `GET /incidents/{id}/stream` (`test_stream_emits_first_status_and_changes`) with polling fallback; e2e "pipeline tracker shows all six stages and advances live without a reload"; Vitest "walks the S1 flow from detection to memory" |
| ✅ Page usable on S1 end-to-end without refresh | done (mock) | e2e pipeline + approve → approved without reload. SSE through the Next.js rewrite against a live backend not yet exercised (polling fallback covers it) |
| Metric charts around onset with deployment markers | done | `GET /incidents/{id}/metrics` (`test_metrics_buckets_error_rate_and_deployments`); `incident-metrics.tsx`, Vitest "scales to a nice maximum and breaks the line at gaps" |
| Evidence graph: type colours/shapes, winning path, blast + history overlays, node inspector | done | Vitest "has a distinct colour and shape for each of the 12 node types", "adds overlay-only nodes and edges…"; `test_blast_overlay_marks_reachable_code_and_keeps_the_base_graph`, `test_node_inspector_commit_function_error` |
| ✅ Clicking a citation focuses the node/edge | done | e2e "clicking a citation focuses its node in the graph and opens the inspector" |
| Keyboard navigation of the graph | done | e2e "graph keyboard navigation: arrows move, Enter inspects, Escape clears" |
| Performance with ~500 nodes | done (model) | Vitest "builds a 500-node model quickly and indexes it" (model build, not a render benchmark) |
| ✅ Readable at demo screen resolution | partial | distinct colour + shape per type with a legend; not yet checked on a projector by a person |
| Remediation view: diff, rationale, revert vs patch, approve/reject with confirmation, verification before/after, PR/Actions links | done | e2e "approving a verified fix asks for confirmation and ends in the approved state", "rejecting a fix requires a reason", "fix section shows the staging verification result…"; Vitest diff parser tests |
| ✅ Approving from the UI results in the PR approved/merged per policy | done (API) | approve → `merge: manual|requested|failed` per `remediation_merge_policy` (`test_app_merge_policy_merges_only_after_approval`, `test_manual_policy_never_merges`); live merge on ShopLite is external |
| History & memory browser: archive, resembles panel with basis + previous fix, hotspots | done | `GET /incidents/{id}/similar` (`test_similar_lists_resembling_incidents_with_their_fix`), `GET /repos/{o}/{n}/hotspots` (`test_hotspots_count_blamed_functions_and_dependencies`); e2e "hotspots lists blamed functions…" |
| ✅ S8 page shows prior incident + reused fix prominently | done (mock) | e2e "S8 shows the resolved S1 incident and the fix it reuses"; Vitest "S8 resembles the resolved S1 incident and reuses its fix" |
| Evaluation dashboard | done | `GET /eval/latest` (`test_eval_latest_serves_the_harness_json_unchanged`); e2e "evaluation page renders latest.json…" |
| ✅ Matches `make eval` output exactly | done | `eval.test.ts` compares every split row (median time included), per-scenario table and misses with `docs/results/latest.md` |
| Playwright: login (mocked), incident flow, approval flow | done | `signin.spec.ts`, `incident.spec.ts`, `incident-flow.spec.ts`, `dashboard.spec.ts` |
| axe checks on key pages | done | `a11y.spec.ts` enforces no serious/critical WCAG 2.1 AA violations on overview, incidents, incident page, hotspots, evaluation |
| Visual regression on the incident page | partial | `visual.spec.ts` (opt-in, `npm run e2e:visual`); Windows baseline committed; Linux baseline not generated (command in `ui/README.md`) |
| ✅ E2E suite green in CI | pending CI | green locally (30/30); CI runs `npx playwright test` on ubuntu, where the opt-in visual spec is skipped |

## Known gaps (not claimed)

* Live items: ShopLite reporter overhead, live revert PR + verification workflow on ShopLite,
  live LLM eval, demo recording and two rehearsals.
* The registered GitHub App keeps `contents: read` until the owner updates it (security S-2).
* Rollback of a prod deploy has not been exercised; backups are not scheduled/shipped to S3 yet.
* Load generator: no 10-minute baseline or staging replay run.
* README quickstart has not been followed by a second person.
* Seeded history authors are invented identities.
* The App was briefly installed on all of the owner's repositories; the allowlist now prevents any
  import outside `CD_ALLOWED_REPOS`, but data imported before that remains until it is deleted.
