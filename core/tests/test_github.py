import time

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from causaldeploy.config import Settings
from causaldeploy.github.client import GitHubClient, app_jwt
from causaldeploy.github.webhooks import normalize, verify_signature


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = k.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pub = (
        k.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return priv, pub


def test_app_jwt_claims(keypair: tuple[str, str]) -> None:
    priv, pub = keypair
    claims = jwt.decode(
        app_jwt("123", priv, now=1_000_000),
        pub,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims == {"iat": 999_940, "exp": 1_000_540, "iss": "123"}


@respx.mock
async def test_installation_token_cached_and_refreshed(keypair: tuple[str, str]) -> None:
    exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    route = respx.post("https://api.github.com/app/installations/7/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "ghs_tok", "expires_at": exp})
    )
    c = GitHubClient(Settings(github_app_id="1", github_app_private_key=keypair[0]))  # type: ignore[arg-type]
    assert await c.installation_token(7) == "ghs_tok"
    assert await c.installation_token(7) == "ghs_tok"
    assert route.call_count == 1
    c._tokens[7] = ("old", time.time() + 10)  # about to expire -> refresh
    assert await c.installation_token(7) == "ghs_tok" and route.call_count == 2
    sent = route.calls[0].request.headers["authorization"]
    assert sent.startswith("Bearer ey")
    await c.aclose()


@respx.mock
async def test_pagination_and_rate_limit_wait(
    keypair: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    exp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    respx.post("https://api.github.com/app/installations/7/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "t", "expires_at": exp})
    )
    waited: list[float] = []

    async def fake_sleep(s: float) -> None:
        waited.append(s)

    monkeypatch.setattr("causaldeploy.github.client.asyncio.sleep", fake_sleep)
    respx.get("https://api.github.com/repos/a/b/pulls", params={"per_page": "100"}).mock(
        side_effect=[
            httpx.Response(403, headers={"x-ratelimit-remaining": "0", "retry-after": "2"}),
            httpx.Response(
                200,
                json=[{"n": 1}],
                headers={"link": '<https://api.github.com/repos/a/b/pulls?page=2>; rel="next"'},
            ),
        ]
    )
    respx.get("https://api.github.com/repos/a/b/pulls?page=2").mock(
        return_value=httpx.Response(200, json=[{"n": 2}])
    )
    c = GitHubClient(Settings(github_app_id="1", github_app_private_key=keypair[0]))  # type: ignore[arg-type]
    items = [i async for i in c.paginate(7, "/repos/a/b/pulls")]
    assert items == [{"n": 1}, {"n": 2}] and waited == [2.0]
    await c.aclose()


def test_signature_verification() -> None:
    import hashlib
    import hmac

    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s", body, hashlib.sha256).hexdigest()
    assert verify_signature("s", body, good)
    assert not verify_signature("s", body, good[:-1] + "0")
    assert not verify_signature("s", body, None) and not verify_signature("", body, good)


def test_normalize_deployment_status_workflow_and_ignored() -> None:
    repo = {"full_name": "a/b"}
    dep = {"id": 9, "sha": "abcdef1", "environment": "prod", "created_at": "2026-10-09T10:00:00Z"}
    [d] = normalize(
        "deployment_status",
        {
            "repository": repo,
            "deployment": dep,
            "deployment_status": {"state": "failure", "created_at": "2026-10-09T10:05:00Z"},
        },
    )
    assert (d.type, d.status, d.env, d.id) == ("deployment", "failure", "prod", "9")  # type: ignore[union-attr]
    assert (
        normalize(
            "deployment_status",
            {"repository": repo, "deployment": dep, "deployment_status": {"state": "inactive"}},
        )
        == []
    )
    [b] = normalize(
        "workflow_run",
        {
            "repository": repo,
            "action": "completed",
            "workflow_run": {
                "id": 5,
                "head_sha": "abcdef1",
                "conclusion": "success",
                "created_at": "2026-10-09T10:00:00Z",
                "updated_at": "2026-10-09T10:04:00Z",
            },
        },
    )
    assert (b.type, b.status) == ("build", "success")  # type: ignore[union-attr]
    assert normalize("issues", {"repository": repo}) == []


@respx.mock
async def test_repo_installation_lookup(keypair: tuple[str, str]) -> None:
    c = GitHubClient(Settings(github_app_id="1", github_app_private_key=keypair[0]))  # type: ignore[arg-type]
    route = respx.get("https://api.github.com/repos/acme/shop/installation").mock(
        return_value=httpx.Response(200, json={"id": 4242})
    )
    assert await c.repo_installation_id("acme/shop") == 4242
    assert route.calls[0].request.headers["authorization"].startswith("Bearer ey")  # App JWT
    respx.get("https://api.github.com/repos/acme/other/installation").mock(
        return_value=httpx.Response(404)
    )
    with pytest.raises(RuntimeError, match="not installed"):
        await c.repo_installation_id("acme/other")
    await c.aclose()
