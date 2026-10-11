import json
import logging

import pytest
from pydantic import ValidationError

from causaldeploy.config import Settings
from causaldeploy.logging import RedactingJsonFormatter, Redactor


def test_invalid_config_fails_fast_and_names_the_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CD_FALKORDB_PORT", "99999")
    with pytest.raises(ValidationError, match="falkordb_port"):
        Settings()
    monkeypatch.setenv("CD_FALKORDB_PORT", "6380")
    monkeypatch.setenv("CD_ENV", "staging-ish")
    with pytest.raises(ValidationError, match="env"):
        Settings()


def test_secrets_not_in_repr_or_logs_or_exceptions() -> None:
    s = Settings(
        openai_api_key="sk-supersecretvalue",
        github_webhook_secret="whsec-abc123",  # type: ignore[arg-type]
        github_app_private_key="-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----",
    )  # type: ignore[arg-type]
    assert "supersecret" not in repr(s) and "whsec-abc123" not in str(s.model_dump())

    fmt = RedactingJsonFormatter(Redactor(s.secret_values()))
    try:
        raise RuntimeError("call failed with key sk-supersecretvalue and whsec-abc123")
    except RuntimeError:
        import sys

        rec = logging.LogRecord(
            "t",
            logging.ERROR,
            "f",
            1,
            "token ghp_%s and %s",
            ("a" * 30, "whsec-abc123"),
            sys.exc_info(),
        )
    out = fmt.format(rec)
    assert "supersecret" not in out and "whsec-abc123" not in out and "ghp_aaaa" not in out
    assert "MIIB" not in out
    assert json.loads(out)["level"] == "ERROR"


def test_private_key_accepts_pem_escaped_newlines_and_base64() -> None:
    import base64

    pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    for given in (
        pem,
        pem.replace("\n", "\\n"),
        "base64:" + base64.b64encode(pem.encode()).decode(),
    ):
        s = Settings(github_app_private_key=given)  # type: ignore[arg-type]
        assert s.github_app_private_key.get_secret_value() == pem
