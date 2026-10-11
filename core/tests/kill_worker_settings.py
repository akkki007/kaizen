"""Worker used by test_jobs to prove a SIGKILLed worker's job is retried, not lost or duplicated."""

import asyncio
import os
from typing import Any

from causaldeploy.jobs import handlers, worker


async def probe(_: dict[str, Any]) -> None:
    path = os.environ["PROBE_FILE"]
    with open(path, "a") as f:
        f.write("start\n")
    await asyncio.sleep(float(os.environ.get("PROBE_SLEEP", "0")))
    with open(path, "a") as f:
        f.write("done\n")


handlers.HANDLERS["ingest_event"] = probe


class WorkerSettings:
    # arq reads the class __dict__ only, so inheritance would drop everything: copy explicitly
    functions = worker.WorkerSettings.functions
    cron_jobs = worker.WorkerSettings.cron_jobs
    redis_settings = worker.WorkerSettings.redis_settings
    max_jobs = worker.WorkerSettings.max_jobs
    max_tries = worker.WorkerSettings.max_tries
    retry_jobs = worker.WorkerSettings.retry_jobs
    handle_signals = worker.WorkerSettings.handle_signals
    job_timeout = 3  # in-progress marker expires quickly so the retry is observable in a test
