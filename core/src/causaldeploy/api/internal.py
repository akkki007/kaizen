"""Scheduled work for serverless hosting, where no worker process runs the detection loop.

Vercel Cron calls `GET /internal/cron` with `Authorization: Bearer $CRON_SECRET`. On a plan that
allows frequent crons this is the 30-second detector; on Hobby it is a daily safety net (the demo
runs detection itself when a problem is injected).
"""

import hmac
import os

from fastapi import APIRouter, Header, HTTPException

from causaldeploy.db.retention import purge_job
from causaldeploy.detect.detector import detect_all

router = APIRouter(prefix="/internal")


@router.get("/cron")
async def cron(authorization: str | None = Header(default=None)) -> dict[str, object]:
    secret = os.environ.get("CRON_SECRET", "")
    if not secret or not hmac.compare_digest(authorization or "", f"Bearer {secret}"):
        raise HTTPException(401, "not allowed")
    opened = await detect_all()
    purged = await purge_job({})
    return {"opened": opened, "purged": purged}
