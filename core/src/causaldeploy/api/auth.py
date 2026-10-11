"""GitHub login, signed sessions, CSRF and per-repository authorization.

Users sign in with GitHub (App user-to-server OAuth). The session lists the repositories where
the CausalDeploy App is installed and the user has access, with their role on each:
`approver` (push/maintain/admin) or `viewer` (read).
"""

import secrets
import time
from typing import Annotated, Any, Literal

import httpx
import jwt
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from causaldeploy.config import Settings, get_settings

SESSION_COOKIE = "cd_session"
CSRF_COOKIE = "cd_csrf"
STATE_COOKIE = "cd_oauth_state"
Role = Literal["viewer", "approver"]
_RANK = {"viewer": 1, "approver": 2}

router = APIRouter(prefix="/auth")


class User(BaseModel):
    login: str
    repos: dict[str, Role]  # full_name -> role


def _key(settings: Settings) -> str:
    return settings.session_secret.get_secret_value()


def make_session(user: User, settings: Settings | None = None, now: float | None = None) -> str:
    s = settings or get_settings()
    t = int(now if now is not None else time.time())
    claims = {
        "sub": user.login,
        "repos": user.repos,
        "iat": t,
        "exp": t + s.session_ttl_minutes * 60,
    }
    return jwt.encode(claims, _key(s), "HS256")


def read_session(token: str | None, settings: Settings | None = None) -> User | None:
    if not token:
        return None
    try:
        c = jwt.decode(token, _key(settings or get_settings()), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return User(login=c["sub"], repos=c.get("repos", {}))


async def current_user(
    cd_session: Annotated[str | None, Cookie()] = None,
) -> User:
    user = read_session(cd_session)
    if user is None:
        raise HTTPException(401, "authentication required")
    return user


def require_role(user: User, repo: str, role: Role = "viewer") -> None:
    have = user.repos.get(repo)
    if have is None:
        raise HTTPException(403, "no access to this repository")  # do not reveal existence
    if _RANK[have] < _RANK[role]:
        raise HTTPException(403, f"{role} role required")


async def csrf_protect(
    request: Request,
    cd_csrf: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    """Double-submit token for cookie-authenticated, state-changing requests."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if not cd_csrf or not x_csrf_token or not secrets.compare_digest(cd_csrf, x_csrf_token):
        raise HTTPException(403, "CSRF token missing or invalid")


CurrentUser = Annotated[User, Depends(current_user)]


def _cookie_args(settings: Settings, http_only: bool = True) -> dict[str, Any]:
    return {
        "httponly": http_only,
        "secure": settings.env == "prod",
        "samesite": "lax",
        "max_age": settings.session_ttl_minutes * 60,
        "path": "/",
    }


@router.get("/login")
async def login() -> RedirectResponse:
    s = get_settings()
    if not s.github_oauth_client_id:
        raise HTTPException(503, "GitHub login is not configured")
    state = secrets.token_urlsafe(24)
    url = (
        f"{s.github_web_url}/login/oauth/authorize?client_id={s.github_oauth_client_id}"
        f"&redirect_uri={s.public_base_url}/auth/callback&state={state}"
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(STATE_COOKIE, state, **_cookie_args(s))
    return resp


async def fetch_repo_roles(token: str, settings: Settings) -> dict[str, Role]:
    """Repositories where the App is installed and the user has access, with their role."""
    roles: dict[str, Role] = {}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(
        base_url=settings.github_api_url, headers=headers, timeout=15
    ) as c:
        inst = (await c.get("/user/installations", params={"per_page": 100})).json()
        for i in inst.get("installations", []):
            page = 1
            while True:
                r = await c.get(
                    f"/user/installations/{i['id']}/repositories",
                    params={"per_page": 100, "page": page},
                )
                repos = r.json().get("repositories", [])
                for repo in repos:
                    perms = repo.get("permissions") or {}
                    approver = perms.get("push") or perms.get("maintain") or perms.get("admin")
                    roles[repo["full_name"]] = "approver" if approver else "viewer"
                if len(repos) < 100:
                    break
                page += 1
    return roles


@router.get("/callback")
async def callback(
    code: str, state: str, cd_oauth_state: Annotated[str | None, Cookie()] = None
) -> RedirectResponse:
    s = get_settings()
    if not cd_oauth_state or not secrets.compare_digest(cd_oauth_state, state):
        raise HTTPException(400, "invalid OAuth state")
    async with httpx.AsyncClient(timeout=15) as c:
        tok = await c.post(
            f"{s.github_web_url}/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": s.github_oauth_client_id,
                "client_secret": s.github_oauth_client_secret.get_secret_value(),
                "code": code,
            },
        )
    access = tok.json().get("access_token")
    if not access:
        raise HTTPException(401, "GitHub rejected the login")
    async with httpx.AsyncClient(base_url=s.github_api_url, timeout=15) as c:
        me = await c.get("/user", headers={"Authorization": f"Bearer {access}"})
    login = me.json()["login"]
    user = User(login=login, repos=await fetch_repo_roles(access, s))
    resp = RedirectResponse(s.public_base_url + "/", status_code=302)
    resp.set_cookie(SESSION_COOKIE, make_session(user, s), **_cookie_args(s))
    resp.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(24), **_cookie_args(s, http_only=False))
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


@router.post("/logout", dependencies=[Depends(csrf_protect)])
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"status": "logged_out"}


@router.get("/me")
async def me(user: CurrentUser) -> User:
    return user
