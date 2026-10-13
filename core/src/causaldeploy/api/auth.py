"""GitHub login, signed sessions, CSRF and per-repository authorization.

Users sign in with GitHub (App user-to-server OAuth). The session lists the repositories where
the CausalDeploy App is installed and the user has access, with their role on each:
`approver` (push/maintain/admin) or `viewer` (read).
"""

import secrets
import time
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import httpx
import jwt
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from causaldeploy.config import Settings, get_settings

SESSION_COOKIE = "cd_session"
CSRF_COOKIE = "cd_csrf"
STATE_COOKIE = "cd_oauth_state"
NEXT_COOKIE = "cd_oauth_next"  # where to land after sign-in (root-relative path)
ORIGIN_COOKIE = "cd_oauth_origin"  # UI origin the sign-in started from (hosts the callback)
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


def safe_next(next_url: str | None) -> str:
    """Root-relative redirect target only; anything else falls back to /."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


def safe_origin(origin: str | None, settings: Settings) -> str:
    """The UI origin sign-in started from (hosts the OAuth callback and receives the browser back).

    Only the configured public origin is honored in production. Outside production a loopback
    origin is also accepted, because local dev runs the UI on :3000 and the API on :8000.
    Anything else falls back to the public origin. GitHub additionally refuses redirect URIs
    that are not registered on the App, but that only guards the code exchange: the redirects
    back to the browser (including error redirects) are ours, so the allowlist is enforced here.
    """
    public = settings.public_base_url
    if not origin:
        return public
    try:
        parts = urlsplit(origin)
        host = parts.hostname
    except ValueError:
        return public
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/"):
        return public
    normalized = f"{parts.scheme}://{parts.netloc}"
    if normalized == public.rstrip("/"):
        return normalized
    if settings.env != "prod" and host is not None and host.lower() in _LOOPBACK_HOSTS:
        return normalized
    return public


def _login_redirect(origin: str, reason: str, clear: bool = True) -> RedirectResponse:
    """Back to the login page with a reason it can show (denied, expired, github, no_repos)."""
    resp = RedirectResponse(f"{origin}/login?error={reason}", status_code=302)
    if clear:
        resp.delete_cookie(STATE_COOKIE, path="/")
        resp.delete_cookie(NEXT_COOKIE, path="/")
        resp.delete_cookie(ORIGIN_COOKIE, path="/")
    return resp


@router.get("/login")
async def login(
    next_url: Annotated[str | None, Query(alias="next")] = None,
    origin: str | None = None,
) -> RedirectResponse:
    s = get_settings()
    if not s.github_oauth_client_id:
        raise HTTPException(503, "GitHub login is not configured")
    start_origin = safe_origin(origin, s)
    state = secrets.token_urlsafe(24)
    url = (
        f"{s.github_web_url}/login/oauth/authorize?client_id={s.github_oauth_client_id}"
        f"&redirect_uri={start_origin}/auth/callback&state={state}"
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(STATE_COOKIE, state, **_cookie_args(s))
    resp.set_cookie(NEXT_COOKIE, safe_next(next_url), **_cookie_args(s))
    resp.set_cookie(ORIGIN_COOKIE, start_origin, **_cookie_args(s))
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
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
    cd_oauth_state: Annotated[str | None, Cookie()] = None,
    cd_oauth_next: Annotated[str | None, Cookie()] = None,
    cd_oauth_origin: Annotated[str | None, Cookie()] = None,
) -> RedirectResponse:
    """Exchanges the code for a session and lands the user back in the UI.

    Every failure (denied, stale state, GitHub rejection) redirects to /login?error=… so the
    login page can explain it; nothing here surfaces a bare JSON error.
    """
    s = get_settings()
    origin = safe_origin(cd_oauth_origin, s)
    if error or not code:
        return _login_redirect(origin, "denied" if error == "access_denied" else "invalid")
    if not cd_oauth_state or not state or not secrets.compare_digest(cd_oauth_state, state):
        return _login_redirect(origin, "expired")
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
        return _login_redirect(origin, "github")
    async with httpx.AsyncClient(base_url=s.github_api_url, timeout=15) as c:
        me = await c.get("/user", headers={"Authorization": f"Bearer {access}"})
    login = me.json()["login"]
    user = User(login=login, repos=await fetch_repo_roles(access, s))
    next_path = safe_next(cd_oauth_next)
    if not user.repos:
        # Signed in, but the App is not installed anywhere the user can see: the login page
        # explains and links the installation. The session still works for the empty dashboard.
        resp = _login_redirect(origin, "no_repos", clear=False)
    else:
        resp = RedirectResponse(origin + next_path, status_code=302)
    resp.set_cookie(SESSION_COOKIE, make_session(user, s), **_cookie_args(s))
    resp.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(24), **_cookie_args(s, http_only=False))
    resp.delete_cookie(STATE_COOKIE, path="/")
    resp.delete_cookie(NEXT_COOKIE, path="/")
    resp.delete_cookie(ORIGIN_COOKIE, path="/")
    return resp


@router.post("/logout", dependencies=[Depends(csrf_protect)])
async def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"status": "logged_out"}


@router.get("/me")
async def me(user: CurrentUser) -> User:
    return user


@router.get("/install-url")
async def install_url() -> dict[str, str | None]:
    """Where to install the GitHub App (shown on the login and repositories pages)."""
    slug = get_settings().github_app_slug.strip()
    return {"url": f"https://github.com/apps/{slug}/installations/new" if slug else None}
