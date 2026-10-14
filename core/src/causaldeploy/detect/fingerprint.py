"""Error signatures: type + top application frame + normalized message (PLAN section 4)."""

import hashlib
import re

_NODE_INTERNAL = ("node:", "internal/", "node_modules/")
_FRAME = re.compile(
    r"^(?:async\s+)?(?:(?P<fn>.*?)\s+\()?(?P<path>[^()\s]+?):(?P<line>\d+):(?P<col>\d+)\)?$"
)


class Frame:
    __slots__ = ("function", "path", "line")

    def __init__(self, function: str | None, path: str, line: int) -> None:
        self.function, self.path, self.line = function, path, line


def parse_frame(raw: str) -> Frame | None:
    """`validatePayment (/app/payments/src/validation.js:18:12)` or `/app/x.js:3:1`."""
    m = _FRAME.match(raw.strip())
    if not m:
        return None
    return Frame(m.group("fn") or None, m.group("path"), int(m.group("line")))


def app_frames(raw_frames: list[str]) -> list[Frame]:
    """Application frames only, innermost first (runtime and dependency frames are dropped)."""
    out = []
    for raw in raw_frames:
        f = parse_frame(raw)
        if f and not any(marker in f.path for marker in _NODE_INTERNAL):
            out.append(f)
    return out


_NORMALIZERS = [
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
        "<uuid>",
    ),
    (re.compile(r"\b(?:ord|pay|req|inc)_[0-9a-f]+\b", re.I), "<id>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"(?<![\w.])\d+(?:\.\d+)?"), "<n>"),
]


def normalize_message(message: str | None) -> str:
    text = (message or "").strip().lower()
    for pattern, token in _NORMALIZERS:
        text = pattern.sub(token, text)
    return re.sub(r"\s+", " ", text)[:240]


def fingerprint(error_type: str | None, raw_frames: list[str], message: str | None) -> str:
    """Stable across ids, numbers and line-number drift inside the same function."""
    frames = app_frames(raw_frames)
    top = frames[0] if frames else None
    where = f"{top.function or ''}@{top.path.rsplit('/', 1)[-1]}" if top else "no-app-frame"
    basis = "|".join([error_type or "Error", where, normalize_message(message)])
    return hashlib.sha1(basis.encode()).hexdigest()[:16]
