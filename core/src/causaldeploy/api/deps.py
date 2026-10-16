import hashlib
import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from causaldeploy.config import get_settings
from causaldeploy.db.models import ApiKey
from causaldeploy.db.session import session_scope
from causaldeploy.logging import request_id_var


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class RateLimiter:
    """Sliding one-minute window per caller (in-process; single API replica).

    Each hit has a cost (events in a batch), so the limit is in events per minute."""

    WINDOW = 60.0

    def __init__(self) -> None:
        self._hits: dict[str, deque[tuple[float, int]]] = defaultdict(deque)
        self._used: dict[str, int] = defaultdict(int)

    def take(self, caller: str, limit: int, cost: int = 1, now: float | None = None) -> float:
        """Consume `cost` units. Returns 0 when allowed, else the seconds until it would be."""
        t = now if now is not None else time.monotonic()
        q = self._hits[caller]
        while q and t - q[0][0] >= self.WINDOW:
            self._used[caller] -= q.popleft()[1]
        if self._used[caller] + cost <= limit:
            q.append((t, cost))
            self._used[caller] += cost
            return 0.0
        free, wait = limit - self._used[caller], self.WINDOW
        for at, n in q:  # oldest first: when have enough units expired?
            free += n
            if free >= cost:
                wait = at + self.WINDOW - t
                break
        return max(wait, 0.001)

    def check(self, caller: str, limit: int, now: float | None = None) -> bool:
        return self.take(caller, limit, 1, now) == 0.0


ingest_limiter = RateLimiter()


@dataclass(frozen=True)
class IngestCaller:
    repo: str  # the repository the key is scoped to
    key_hash: str


async def ingest_caller(x_api_key: str | None = Header(default=None)) -> IngestCaller:
    """Authenticate a telemetry/event producer by its repository-scoped API key."""
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
    return IngestCaller(repo=row.repo, key_hash=row.key_hash)


def charge_events(caller: IngestCaller, events: int) -> None:
    """Rate limit in events per key per minute; 429 + Retry-After when exhausted."""
    limit = get_settings().ingest_rate_limit_per_min
    if events > limit:
        raise HTTPException(413, f"batch of {events} events exceeds the {limit}/min event limit")
    wait = ingest_limiter.take(caller.key_hash, limit, max(events, 1))
    if wait:
        raise HTTPException(
            429,
            f"rate limit exceeded ({limit} events/min)",
            headers={"Retry-After": str(math.ceil(wait))},
        )


async def ingest_repo(request: Request, x_api_key: str | None = Header(default=None)) -> str:
    """Authenticate and charge one unit; returns the repository the key is scoped to."""
    caller = await ingest_caller(x_api_key)
    charge_events(caller, 1)
    return caller.repo


RepoDep = Depends(ingest_repo)
CallerDep = Depends(ingest_caller)

# Request bodies are capped for these paths before the route reads them (webhooks are not:
# GitHub payloads are signed and can legitimately be large).
LIMITED_PATHS = frozenset({"/api/v1/events"})


class BodySizeLimit:
    """413 for bodies over `ingest_max_bytes`: rejects on Content-Length up front and counts
    the bytes actually received (chunked or lying clients), buffering at most the limit."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in LIMITED_PATHS:
            await self.app(scope, receive, send)
            return
        limit = get_settings().ingest_max_bytes
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = False
            if too_big:
                await _too_large(send, limit)
                return
        chunks: list[bytes] = []
        size = 0
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            body = msg.get("body", b"")
            size += len(body)
            if size > limit:
                await _too_large(send, limit)
                return
            chunks.append(body)
            if not msg.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


async def _too_large(send: Send, limit: int) -> None:
    body = json.dumps(
        {
            "error": {
                "code": "payload_too_large",
                "message": f"request body exceeds {limit} bytes",
                "request_id": request_id_var.get(),
            }
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
