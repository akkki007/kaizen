"""Language-pluggable static analysis interface (JS/TS implemented in `javascript.py`)."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class FunctionDef:
    qname: str
    start: int  # 1-based inclusive
    end: int
    body_hash: str


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    handler: str  # qname of the handler function in the same file, or a raw identifier


@dataclass(frozen=True)
class Import:
    module: str  # specifier as written
    name: str | None  # imported symbol; None for default/namespace/whole-module imports


@dataclass
class FileAnalysis:
    path: str
    functions: list[FunctionDef] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)  # (caller qname, raw callee)
    imports: dict[str, Import] = field(default_factory=dict)  # local alias -> Import
    routes: list[Route] = field(default_factory=list)


class Analyzer(Protocol):
    extensions: tuple[str, ...]

    def analyze_file(self, path: str, source: str) -> FileAnalysis: ...
