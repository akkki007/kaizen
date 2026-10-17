"""Security review (#92, docs/security.md): checks not already covered elsewhere.

Covered in other files and not repeated here: bad webhook signature 401 + nothing stored and
delivery dedupe (test_api), ingest 401/403/413/429 + Retry-After (test_api, test_incident_api),
CSRF and approver-only actions (test_api, test_remediate), tampered/expired sessions
(test_api), redaction and prompt injection (test_agent_harness), read-only agent tools
(test_mcp), sandboxed subprocess environment (test_verify).
"""

import json
from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from causaldeploy.api import deps
from causaldeploy.api.auth import _cookie_args, read_session, safe_next
from causaldeploy.api.main import create_app
from causaldeploy.config import get_settings
from causaldeploy.db.models import JobRecord, RawEvent
from causaldeploy.db.session import session_scope
from causaldeploy.graph.store import graph_name
from causaldeploy.remediate.ops import GitHubOps


@pytest_asyncio.fixture
async def client(db: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setattr("causaldeploy.jobs.queue.get_pool", AsyncMock(return_value=AsyncMock()))
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


@pytest.fixture
def fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _counts() -> tuple[int, int]:
    async with session_scope() as s:
        raw = (await s.execute(select(func.count()).select_from(RawEvent))).scalar_one()
        jobs = (await s.execute(select(func.count()).select_from(JobRecord))).scalar_one()
    return raw, jobs


async def test_oversized_webhook_is_413_before_it_is_buffered_or_verified(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The webhook body is read before its signature can be checked, so its size is capped
    (GitHub's 25 MB maximum) for unauthenticated senders. Declared and streamed sizes."""
    assert deps.WEBHOOK_MAX_BYTES == 25 * 1024 * 1024
    monkeypatch.setattr(deps, "WEBHOOK_MAX_BYTES", 2048)
    headers = {"x-github-event": "push", "x-github-delivery": "big-1"}
    big = b"x" * 4096
    r = await client.post("/webhooks/github", content=big, headers=headers)
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"

    async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: counted as it arrives
        for _ in range(4):
            yield b"y" * 1024

    r = await client.post("/webhooks/github", content=chunks(), headers=headers)
    assert r.status_code == 413
    assert await _counts() == (0, 0)

    # under the cap the normal path still applies (bad signature -> 401)
    small = json.dumps({"zen": "ok"}).encode()
    r = await client.post(
        "/webhooks/github", content=small, headers={**headers, "x-hub-signature-256": "sha256=0"}
    )
    assert r.status_code == 401


async def test_webhook_fails_closed_without_a_configured_secret(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, fresh_settings: None
) -> None:
    monkeypatch.setenv("CD_GITHUB_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    body = b'{"zen": "x"}'
    r = await client.post(
        "/webhooks/github",
        content=body,
        headers={"x-github-event": "ping", "x-github-delivery": "d", "x-hub-signature-256": ""},
    )
    assert r.status_code == 503
    assert await _counts() == (0, 0)


async def test_session_and_csrf_cookie_flags(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, fresh_settings: None
) -> None:
    monkeypatch.setenv("CD_GITHUB_OAUTH_CLIENT_ID", "cid")
    get_settings.cache_clear()
    r = await client.get("/auth/login", follow_redirects=False)
    cookies = r.headers.get_list("set-cookie")
    assert cookies and all("HttpOnly" in c and "SameSite=lax" in c for c in cookies)
    assert not any("Secure" in c for c in cookies)  # dev runs on http://localhost

    s = get_settings()
    prod = s.model_copy(update={"env": "prod"})
    assert _cookie_args(prod)["secure"] is True and _cookie_args(prod)["samesite"] == "lax"
    # the CSRF cookie must be readable by the UI (double submit); the session cookie must not
    assert _cookie_args(prod, http_only=False)["httponly"] is False
    assert _cookie_args(prod)["httponly"] is True


def test_unsigned_or_foreign_key_session_tokens_are_rejected() -> None:
    claims = {"sub": "mallory", "repos": {"acme/shop": "approver"}, "exp": 4_102_444_800}
    unsigned = jwt.encode(claims, key=None, algorithm="none")
    assert read_session(unsigned) is None
    foreign = jwt.encode(claims, "x" * 40, algorithm="HS256")
    assert read_session(foreign) is None


@pytest.mark.parametrize(
    "target",
    ["//evil.example/x", "https://evil.example", "javascript:alert(1)", "", None, "evil"],
)
def test_post_login_redirect_is_root_relative_only(target: str | None) -> None:
    assert safe_next(target) == "/"


def test_graph_names_cannot_carry_cypher_or_cross_repo_names() -> None:
    assert graph_name("acme/shop") == "cd_acme_shop"
    hostile = graph_name("a/b`}) MATCH (n) DETACH DELETE n //")
    assert hostile.startswith("cd_") and all(ch.isalnum() or ch == "_" for ch in hostile)


def test_fix_branches_never_target_a_default_branch() -> None:
    ops = GitHubOps()
    for branch in ("main", "master"):
        with pytest.raises(PermissionError):
            ops.push(None, "acme/shop", 1, branch)  # type: ignore[arg-type]
