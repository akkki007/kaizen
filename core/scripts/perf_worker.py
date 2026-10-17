"""Worker settings for throughput experiments (docs/performance.md), not for production.

Same functions and cron jobs as `causaldeploy.jobs.worker.WorkerSettings`, but the queue poll
interval and the concurrency come from the environment, to measure how much of the drain rate
is arq's polling cadence (default: up to `max_jobs` jobs picked up every `poll_delay` seconds):

    PERF_POLL_DELAY=0.02 PERF_MAX_JOBS=50 PYTHONPATH=scripts uv run arq perf_worker.WorkerSettings
"""

import os

from causaldeploy.jobs import worker


class WorkerSettings:
    # arq reads the class __dict__ only, so inheritance would drop everything: copy explicitly
    functions = worker.WorkerSettings.functions
    cron_jobs = worker.WorkerSettings.cron_jobs
    on_startup = worker.WorkerSettings.on_startup
    redis_settings = worker.WorkerSettings.redis_settings
    max_tries = worker.WorkerSettings.max_tries
    job_timeout = worker.WorkerSettings.job_timeout
    retry_jobs = worker.WorkerSettings.retry_jobs
    handle_signals = worker.WorkerSettings.handle_signals
    max_jobs = int(os.environ.get("PERF_MAX_JOBS", worker.WorkerSettings.max_jobs))
    poll_delay = float(os.environ.get("PERF_POLL_DELAY", "0.5"))  # arq's default is 0.5 s
