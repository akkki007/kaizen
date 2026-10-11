"""GitHub App auth (JWT -> cached installation token) and a small paginated REST client."""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import jwt

from causaldeploy.config import Settings, get_settings

_MAX_RATE_WAIT = 60.0


def app_jwt(app_id: str, private_key_pem: str, now: float | None = None) -> str:
    t = int(now if now is not None else time.time())
    return jwt.encode({"iat": t - 60, "exp": t + 9 * 60, "iss": app_id}, private_key_pem, "RS256")


class GitHubClient:
    def __init__(
        self, settings: Settings | None = None, http: httpx.AsyncClient | None = None
    ) -> None:
        self._s = settings or get_settings()
        self._http = http or httpx.AsyncClient(base_url=self._s.github_api_url, timeout=20.0)
        self._tokens: dict[int, tuple[str, float]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def installation_token(self, installation_id: int) -> str:
        cached = self._tokens.get(installation_id)
        if cached and cached[1] - time.time() > 60:
            return cached[0]
        key = self._s.github_app_private_key.get_secret_value()
        if not self._s.github_app_id or not key:
            raise RuntimeError(
                "GitHub App is not configured (CD_GITHUB_APP_ID / CD_GITHUB_APP_PRIVATE_KEY)"
            )
        r = await self._http.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt(self._s.github_app_id, key)}",
                "Accept": "application/vnd.github+json",
            },
        )
        r.raise_for_status()
        body = r.json()
        expires = (
            time.mktime(time.strptime(body["expires_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        )
        self._tokens[installation_id] = (body["token"], expires)
        return str(body["token"])

    async def repo_installation_id(self, repo: str) -> int:
        """The installation id of this App on `repo` (authenticated as the App itself)."""
        key = self._s.github_app_private_key.get_secret_value()
        if not self._s.github_app_id or not key:
            raise RuntimeError(
                "GitHub App is not configured (CD_GITHUB_APP_ID / CD_GITHUB_APP_PRIVATE_KEY)"
            )
        r = await self._http.get(
            f"/repos/{repo}/installation",
            headers={
                "Authorization": f"Bearer {app_jwt(self._s.github_app_id, key)}",
                "Accept": "application/vnd.github+json",
            },
        )
        if r.status_code == 404:
            raise RuntimeError(f"the GitHub App is not installed on {repo}")
        r.raise_for_status()
        return int(r.json()["id"])

    async def request(
        self, installation_id: int, method: str, path: str, **kw: Any
    ) -> httpx.Response:
        for _ in range(3):
            token = await self.installation_token(installation_id)
            r = await self._http.request(
                method,
                path,
                headers={
                    "Authorization": f"token {token}",
                    "Accept": "application/vnd.github+json",
                },
                **kw,
            )
            limited = r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0"
            if not limited:
                r.raise_for_status()
                return r
            reset = float(r.headers.get("x-ratelimit-reset", time.time() + 1))
            wait = float(r.headers.get("retry-after", max(0.0, reset - time.time())))
            if wait > _MAX_RATE_WAIT:
                r.raise_for_status()
            await asyncio.sleep(wait)
        raise RuntimeError("GitHub rate limit retries exhausted")

    async def paginate(self, installation_id: int, path: str, **params: Any) -> AsyncIterator[Any]:
        url: str | None = path
        query: dict[str, Any] | None = {"per_page": 100, **params}
        while url:
            r = await self.request(installation_id, "GET", url, params=query)
            data = r.json()
            for item in data if isinstance(data, list) else data.get("items", []):
                yield item
            url, query = r.links.get("next", {}).get("url"), None
