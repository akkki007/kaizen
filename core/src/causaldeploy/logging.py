"""Structured JSON logging with request-id propagation and secret redaction."""

import json
import logging
import re
from contextvars import ContextVar

from causaldeploy.config import Settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
job_id_var: ContextVar[str] = ContextVar("job_id", default="-")
incident_id_var: ContextVar[str] = ContextVar("incident_id", default="-")

_REDACTED = "[REDACTED]"
_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
]


class Redactor:
    def __init__(self, secrets: list[str]) -> None:
        self._secrets = [s for s in secrets if len(s) >= 4]

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, _REDACTED)
        for pattern in _PATTERNS:
            text = pattern.sub(_REDACTED, text)
        return text


class RedactingJsonFormatter(logging.Formatter):
    def __init__(self, redact: Redactor) -> None:
        super().__init__()
        self._redact = redact

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "job_id": job_id_var.get(),
            "incident_id": incident_id_var.get(),
        }
        for key, value in getattr(record, "ctx", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return self._redact(json.dumps(payload, default=str))


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingJsonFormatter(Redactor(settings.secret_values())))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
