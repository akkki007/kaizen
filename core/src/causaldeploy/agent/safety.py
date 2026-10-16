"""What may leave for the model provider, and how tool output is presented to the model.

* `redact_payload` scrubs every string in a request (configured secret values, credential
  formats, key=value secrets, email addresses) before it is sent or stored.
* `wrap_untrusted` fences tool output (commit messages, log lines, error messages: all
  attacker-controllable) in <untrusted_data> delimiters the system prompt tells the model never
  to take instructions from.
* `payload_hash` identifies a request for the audit log without keeping its content.
"""

import hashlib
import json
import re
from typing import Any

from causaldeploy.config import get_settings
from causaldeploy.logging import Redactor

REDACTED = "[REDACTED]"
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}\b")
UNTRUSTED_OPEN = "<untrusted_data"
UNTRUSTED_CLOSE = "</untrusted_data>"


def _redactor() -> Redactor:
    return Redactor(get_settings().secret_values())


def redact_text(text: str, redactor: Redactor | None = None) -> str:
    return _EMAIL.sub(REDACTED, (redactor or _redactor())(text))


def redact_payload(payload: Any, redactor: Redactor | None = None) -> Any:
    """A deep copy of `payload` with every string redacted (dict keys are left alone)."""
    r = redactor or _redactor()
    if isinstance(payload, str):
        return redact_text(payload, r)
    if isinstance(payload, dict):
        return {k: redact_payload(v, r) for k, v in payload.items()}
    if isinstance(payload, list | tuple):
        return [redact_payload(v, r) for v in payload]
    return payload


def wrap_untrusted(source: str, content: str) -> str:
    """Delimit data the model must treat as data. A forged closing tag inside is neutralised."""
    body = content.replace(UNTRUSTED_CLOSE, "</untrusted-data>").replace(
        UNTRUSTED_OPEN, "<untrusted-data"
    )
    return f'{UNTRUSTED_OPEN} source="{source}">\n{body}\n{UNTRUSTED_CLOSE}'


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def payload_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()
