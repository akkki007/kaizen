from fastapi.testclient import TestClient

from causaldeploy.api.main import app
from causaldeploy.config import Settings

client = TestClient(app)


def test_healthz() -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_secrets_are_not_printed() -> None:
    s = Settings(openai_api_key="sk-secret")  # type: ignore[arg-type]
    assert "sk-secret" not in repr(s)
