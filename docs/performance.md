# Performance and reliability budget (#92)

| Budget | Target | Measured (this machine) | Status |
|---|---|---|---|
| Ingest throughput (API accept) | 500 events/s | 531 events/s at saturation (p50 batch latency 18 s); 200 events/s sustained with p50 331 ms | **Not met** with headroom; bottleneck below |
| Ingest throughput (worker processing) | 500 events/s | 18 jobs/s default worker, 41 jobs/s tuned | **Not met**; bottleneck below |
| Detection -> ranked hypothesis (excl. LLM) | < 30 s | p50 194 ms, max 7.3 s (cold first run) over 5 runs | **Met** (`tests/test_perf.py` asserts it) |
| Chaos: worker crash, FalkorDB outage, duplicate webhooks | no lost or duplicated effects | 4 tests pass, 1 known gap (error telemetry row duplicated on a graph outage) | Partial |

## Measurement setup and caveats

Measured 2026-10-17 on a developer laptop, not production hardware:

* Intel Core i5-14450HX (10 cores / 16 threads), 23.6 GB RAM, Windows 11 Home 10.0.26200.
* Postgres 16, Redis 7 and FalkorDB 6.0.2 in Docker Desktop 29.2.1 (WSL2) on localhost;
  Postgres round trip ~0.7 ms, an empty SQLAlchemy transaction ~4.8 ms.
* API: one `uvicorn` process (no `--workers`), port 8010, its own database
  (`causaldeploy_perf`), Redis db 7, `CD_INGEST_RATE_LIMIT_PER_MIN=10000000`, log level WARNING.
* The machine was shared: other test suites and a second Kaizen stack were running, and the disk
  was nearly full during the second session. Repeated runs differed by up to 3x (531 vs 168
  events/s at the same settings), so read the numbers as an order of magnitude. A Linux VM
  without Docker Desktop's network hop should do noticeably better; re-measure there before
  sizing production.

## Ingest throughput

```bash
cd core
uv run python -m causaldeploy.cli create-api-key perf/shoplite          # key for the bench repo
uv run python scripts/bench_ingest.py --url http://127.0.0.1:8010 --key cd_... \
    --repo perf/shoplite --rate 500 --seconds 20 --batch 100            # API side
uv run python scripts/bench_drain.py --timeout 120                      # worker side
# or: make perf-ingest KEY=cd_... REPO=perf/shoplite URL=http://127.0.0.1:8010
```

| Run | Target | Accepted | Batch latency | Statuses |
|---|---|---|---|---|
| API + default worker | 1000 ev/s, 20 s, batches of 100 | **531 ev/s** (20 100 in 37.9 s) | p50 18.1 s, p95 20.7 s | 201 x 202 |
| API only (later, busier machine) | 500 ev/s, 15 s | 168 ev/s | p50 26.5 s, p95 29.9 s | 60 x 202, 15 client timeouts |
| API only | 200 ev/s, 15 s | **200 ev/s** | p50 331 ms, p95 3.2 s | 30 x 202 |

Worker drain (`bench_drain.py`, completions observed while polling a backlog of ~9 000 jobs):

| Worker | Rate |
|---|---|
| `causaldeploy.jobs.worker.WorkerSettings` (max_jobs 10, arq poll 0.5 s) | **18-20 jobs/s** |
| `scripts/perf_worker.py` with `PERF_POLL_DELAY=0.02 PERF_MAX_JOBS=50` | **41 jobs/s** |

### Where the time goes (profiled)

* **One job per event.** `POST /api/v1/events` inserts one `jobs` row (its own transaction) and
  one arq job per event, 8 at a time: 15 ms per event serially, ~410 events/s at best from one
  process in isolation (`enqueue` profile). Under load the API queues requests and latency grows
  to seconds.
* **Worker polling cadence.** arq picks up at most `max_jobs` (10) jobs per `poll_delay` (0.5 s):
  the default worker cannot exceed ~20 jobs/s however fast a job is, which is exactly what the
  default run measured. Tuning both doubled the rate; beyond that Postgres round trips dominate.
* **Per-job cost ~48 ms serially**: three bookkeeping transactions in `run_job` (lock + mark
  running, mark succeeded) plus ~19 ms in the handler: telemetry insert, and for request events
  `request_spans()`, a query on `body->>'request_id'` with no index (sequential scan, 6.5 ms at
  20 000 rows; it grows linearly with retained telemetry).

### What would reach 500 events/s (not implemented here)

1. Telemetry fast path: the API writes a batch with `store_telemetry_many` (one multi-row INSERT,
   already in `detect/errors.py`) and enqueues **one job per batch** only for the graph work
   (error linking, service-call derivation). Commit/deployment events keep one job each.
2. Expression index `CREATE INDEX ON telemetry (repo, (body->>'request_id')) WHERE kind = 'request'`.
3. More worker processes (arq is single-process asyncio) and `uvicorn --workers N`.

Detection is unaffected: it reads windows from Postgres (`ix_telemetry_window`), not from jobs.

## Detection -> hypothesis latency

```bash
cd core
CD_POSTGRES_DSN=postgresql://causaldeploy:causaldeploy@localhost:5433/causaldeploy_perf \
  uv run python scripts/bench_hypothesis.py --runs 5
uv run pytest -q tests/test_perf.py        # the same flow as an asserted budget (< 30 s)
```

Each run builds an S1-shaped graph, ingests 14 x 30 s windows of ShopLite-shaped telemetry
(the last two fail with 70% HTTP 500 and a stack trace) through the real ingest handler, then
times `detect_repo` (anomaly windows, incident row + graph node + Metric summaries) and
`investigate` (similarity pass, candidate ranking, persisted `candidate` / `likely_caused_by`
edges, investigation row, lifecycle transitions). The agent is disabled (no LLM).

| Run | Detect | Rank + persist | Total | Verdict |
|---|---|---|---|---|
| 1 (cold: imports, graph schema/index creation) | 463 ms | 6 835 ms | 7 298 ms | confident, culprit first |
| 2 | 58 ms | 136 ms | 194 ms | confident, culprit first |
| 3 | 44 ms | 120 ms | 164 ms | confident, culprit first |
| 4 | 104 ms | 223 ms | 327 ms | confident, culprit first |
| 5 | 52 ms | 140 ms | 192 ms | confident, culprit first |

p50 194 ms, worst 7.3 s: well inside the 30 s budget. Not included, by design: the time to
*notice* the anomaly. With the defaults (`CD_DETECT_WINDOW_SECONDS=30`,
`CD_DETECT_CONSECUTIVE=2`, detector cron every 30 s) an incident opens 60-90 s after onset, and
its `onset_ts` is the first anomalous window, not the detection time. The investigation job then
waits at most one worker poll (0.5 s). The LLM explanation adds model latency on top
(per-run budget `CD_LLM_MAX_STEPS`/`CD_LLM_MAX_TOKENS`/`CD_LLM_MAX_COST_USD`); the ranking is
already persisted and visible before it finishes.

## Chaos checks (`core/tests/test_chaos.py`, `make perf`)

No real container is restarted: a FalkorDB outage is simulated by a graph handle whose every
command opens a connection to a closed port, which raises the same `redis.ConnectionError` a
restart produces.

| Check | Test | Result |
|---|---|---|
| Worker killed mid-job (no cleanup), any OS | `test_hard_killed_worker_job_is_retried_exactly_once_on_any_os` (Popen.kill = SIGKILL on POSIX, TerminateProcess on Windows); POSIX `SIGKILL` variant in `test_jobs.py` | job retried by a second worker, succeeded with attempts = 2, effect completed once |
| FalkorDB unavailable during a commit/deployment job | `test_falkordb_unavailable_job_is_retried_without_duplicate_graph_effects` | job fails, is retried, succeeds; exactly one Commit, Deployment, `includes` and `authored_by` (MERGE); redelivery after success is a no-op |
| FalkorDB unavailable during an error telemetry job | `test_falkordb_outage_does_not_duplicate_error_telemetry` (`xfail(strict=True)`) | **known gap**: the telemetry row is written to Postgres before the graph link, so each retry stores it again (2 rows after one outage, up to 5 with the default 5 attempts). This inflates the error rate the detector sees during a graph outage. Fix: give telemetry rows an idempotency key (e.g. a unique hash of the event) and insert with `ON CONFLICT DO NOTHING`, or split the graph link into its own job. The strict xfail turns red when it is fixed, so the marker gets removed |
| Same webhook delivered concurrently 6 times | `test_concurrent_duplicate_webhook_deliveries_are_processed_once` | 1 accepted, 5 duplicate, 1 raw event, 1 job |
| Same push redelivered under a new delivery id | `test_same_push_under_a_new_delivery_id_queues_no_second_import` | 2 raw events, 1 backfill job (effect keyed on `push:<repo>:<after>`) |
| Transient failure then success, dead after max attempts | `test_jobs.py::test_failure_retries_then_succeeds_without_duplicate_effect`, `test_job_goes_dead_after_max_attempts` | pass |

FalkorDB itself persists with AOF (`appendfsync everysec`) + RDB, so a real restart keeps the
graph (`docs/operations.md`); the restore drill (`make drill`, CI job) proves backup/restore.

## Re-running

| What | Command |
|---|---|
| Budget + chaos tests | `make perf` |
| Ingest load (local API + worker only) | `make perf-ingest KEY=... REPO=...` |
| Worker tuning experiment | `cd core && PERF_POLL_DELAY=0.02 PERF_MAX_JOBS=50 PYTHONPATH=scripts uv run arq perf_worker.WorkerSettings` |
| Detection -> hypothesis numbers | `cd core && uv run python scripts/bench_hypothesis.py --runs 5` (scratch database) |
