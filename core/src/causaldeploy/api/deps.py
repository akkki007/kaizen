import hashlib
import time
from collections import defaultdict, deque

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select

from causaldeploy.config import get_settings
from causaldeploy.db.models import ApiKey
from causaldeploy.db.session import session_scope


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class RateLimiter:
    """Sliding one-minute window per caller (in-process; single API replica)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, caller: str, limit: int, now: float | None = None) -> bool:
        t = now if now is not None else time.monotonic()
        q = self._hits[caller]
        while q and t - q[0] >= 60:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(t)
        return True


ingest_limiter = RateLimiter()


async def ingest_repo(request: Request, x_api_key: str | None = Header(default=None)) -> str:
    """Authenticate a telemetry/event producer; returns the repository its key is scoped to."""
    if not x_api_key:
        raise HTTPException(401, "missing X-API-Key")
    async with session_scope() as s:
        row = (
            await s.execute(
                select(ApiKey).where(
                    ApiKey.key_hash == hash_key(x_api_key), ApiKey.revoked_at.is_(None)
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(401, "invalid API key")
    if not ingest_limiter.check(row.key_hash, get_settings().ingest_rate_limit_per_min):
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": "60"})
    return row.repo


RepoDep = Depends(ingest_repo)
