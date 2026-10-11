"""Resolve per-file analyses into a service-level code graph and persist it."""

import json
import posixpath
from collections.abc import Callable
from dataclasses import dataclass, field

from causaldeploy.graph.store import Edge, GraphStore
from causaldeploy.ingest.analyzer.base import Analyzer, FileAnalysis
from causaldeploy.ingest.analyzer.javascript import JavaScriptAnalyzer
from causaldeploy.ingest.manifests import parse_manifest

_SKIP = ("node_modules/", "dist/", "build/", ".git/", "coverage/")
ANALYZERS: list[Analyzer] = [JavaScriptAnalyzer()]


@dataclass(frozen=True)
class FnInfo:
    id: str
    qname: str
    path: str
    service: str
    start: int
    end: int
    body_hash: str


@dataclass
class CodeGraph:
    functions: dict[str, FnInfo] = field(default_factory=dict)
    file_service: dict[str, str] = field(default_factory=dict)
    calls: set[tuple[str, str]] = field(default_factory=set)
    endpoints: dict[str, tuple[str, str]] = field(default_factory=dict)  # id -> (service, route)
    handles: set[tuple[str, str]] = field(default_factory=set)
    file_imports: set[tuple[str, str]] = field(default_factory=set)
    fn_deps: set[tuple[str, str]] = field(default_factory=set)  # (function id, package)
    service_deps: dict[str, dict[str, str]] = field(default_factory=dict)
    analyses: dict[str, FileAnalysis] = field(default_factory=dict)


def fn_id(service: str, path: str, qname: str) -> str:
    return f"{service}:{path}:{qname}"


def _service_roots(files: dict[str, str]) -> dict[str, dict[str, object]]:
    roots: dict[str, dict[str, object]] = {}
    for path, text in files.items():
        if posixpath.basename(path) == "package.json":
            try:
                meta = json.loads(text)
            except json.JSONDecodeError:
                meta = {}
            roots[posixpath.dirname(path)] = meta if isinstance(meta, dict) else {}
    return roots


def _owner(path: str, roots: dict[str, dict[str, object]]) -> str:
    d = posixpath.dirname(path)
    while True:
        if d in roots:
            return d
        if d in ("", "."):
            return ""
        d = posixpath.dirname(d)


def _package_of(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def make_service_of(package_dirs: set[str], repo_name: str = "root") -> Callable[[str], str]:
    """Map a file path to its service: the nearest ancestor dir holding a package.json."""
    roots: dict[str, dict[str, object]] = {d: {} for d in package_dirs}

    def service_of(path: str) -> str:
        return posixpath.basename(_owner(path, roots)) or repo_name

    return service_of


def build_code_graph(files: dict[str, str], repo_name: str = "root") -> CodeGraph:
    cg = CodeGraph()
    files = {p: t for p, t in files.items() if not any(s in p for s in _SKIP)}
    roots = _service_roots(files)
    svc_name = {d: (posixpath.basename(d) or repo_name) for d in {*roots, ""}}
    workspace = {str(meta.get("name")): d for d, meta in roots.items() if meta.get("name")}

    def service_of(path: str) -> str:
        return svc_name[_owner(path, roots)]

    workspace_version = {
        str(meta["name"]): str(meta["version"])
        for meta in roots.values()
        if meta.get("name") and meta.get("version")
    }
    for d, meta in roots.items():
        deps = parse_manifest("package.json", json.dumps(meta))
        # internal packages carry the version actually in the tree, not the declared range
        cg.service_deps[svc_name[d]] = {n: workspace_version.get(n, v) for n, v in deps.items()}

    by_ext = {e: a for a in ANALYZERS for e in a.extensions}
    for path, text in files.items():
        analyzer = by_ext.get("." + path.rsplit(".", 1)[-1]) if "." in path else None
        if analyzer is None:
            continue
        a = analyzer.analyze_file(path, text)
        cg.analyses[path] = a
        cg.file_service[path] = service_of(path)
        for f in a.functions:
            info = FnInfo(
                fn_id(service_of(path), path, f.qname),
                f.qname,
                path,
                service_of(path),
                f.start,
                f.end,
                f.body_hash,
            )
            cg.functions[info.id] = info

    def find(path: str, qname: str) -> str | None:
        fid = fn_id(cg.file_service.get(path, ""), path, qname)
        return fid if fid in cg.functions else None

    def resolve_relative(from_path: str, spec: str) -> str | None:
        base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
        exts = [e for a in ANALYZERS for e in a.extensions]
        for cand in [base, *[base + e for e in exts], *[f"{base}/index{e}" for e in exts]]:
            if cand in cg.analyses:
                return cand
        return None

    def resolve_workspace(pkg: str) -> list[str]:
        d = workspace[pkg]
        return sorted(p for p in cg.analyses if _owner(p, roots) == d)

    for path, a in cg.analyses.items():
        svc = cg.file_service[path]
        for route in a.routes:
            handler = find(path, route.handler)
            ep = f"{svc}:{route.method} {route.path}"
            cg.endpoints[ep] = (svc, f"{route.method} {route.path}")
            if handler:
                cg.handles.add((handler, ep))
        targets: dict[str, list[str]] = {}  # import alias -> candidate target files
        pkg_of_alias: dict[str, str] = {}
        for alias, imp in a.imports.items():
            if imp.module.startswith("."):
                tgt = resolve_relative(path, imp.module)
                if tgt:
                    targets[alias] = [tgt]
            else:
                pkg = _package_of(imp.module)
                pkg_of_alias[alias] = pkg
                if pkg in workspace:
                    targets[alias] = resolve_workspace(pkg)
            cg.file_imports.update((path, t) for t in targets.get(alias, []))
        for caller, raw in a.calls:
            src = find(path, caller)
            if src is None:
                continue
            root, _, member = raw.partition(".")
            dst: str | None = None
            if root in a.imports:
                symbol = member or a.imports[root].name
                if symbol:
                    dst = next((c for t in targets.get(root, []) if (c := find(t, symbol))), None)
                if root in pkg_of_alias:
                    cg.fn_deps.add((src, pkg_of_alias[root]))
            else:
                dst = find(path, raw)
            if dst and dst != src:
                cg.calls.add((src, dst))
    return cg


def dep_id(name: str, version: str) -> str:
    return f"{name}@{version}"


def write_code_graph(store: GraphStore, cg: CodeGraph, sha: str) -> None:
    """Idempotently persist the static graph as seen at `sha`."""
    store.ensure_schema()
    services = sorted(set(cg.file_service.values()))
    store.upsert_nodes("Service", [{"id": s} for s in services])
    store.upsert_nodes(
        "File", [{"id": f"{s}:{p}", "path": p, "service": s} for p, s in cg.file_service.items()]
    )
    store.upsert_nodes(
        "Function",
        [
            {
                "id": f.id,
                "name": f.qname,
                "path": f.path,
                "service": f.service,
                "start_line": f.start,
                "end_line": f.end,
                "body_hash": f.body_hash,
                "sha_seen": sha,
            }
            for f in cg.functions.values()
        ],
    )
    store.upsert_nodes(
        "Endpoint", [{"id": e, "service": s, "route": r} for e, (s, r) in cg.endpoints.items()]
    )
    deps: dict[str, tuple[str, str]] = {}
    for mapping in cg.service_deps.values():
        for name, ver in mapping.items():
            deps[dep_id(name, ver)] = (name, ver)
    edges: list[Edge] = []
    for f in cg.functions.values():
        edges.append(Edge("File", f"{f.service}:{f.path}", "contains", "Function", f.id))
        edges.append(Edge("Function", f.id, "belongs_to", "Service", f.service))
    edges += [Edge("Function", a, "calls", "Function", b) for a, b in sorted(cg.calls)]
    for e, (svc, _) in cg.endpoints.items():
        edges.append(Edge("Endpoint", e, "belongs_to", "Service", svc))
    edges += [Edge("Function", f, "handles", "Endpoint", e) for f, e in sorted(cg.handles)]
    for fid, pkg in sorted(cg.fn_deps):
        svc = cg.functions[fid].service
        ver = cg.service_deps.get(svc, {}).get(pkg, "unknown")
        deps.setdefault(dep_id(pkg, ver), (pkg, ver))
        edges.append(Edge("Function", fid, "depends_on", "Dependency", dep_id(pkg, ver)))
    for svc, mapping in cg.service_deps.items():
        for name, ver in mapping.items():
            edges.append(Edge("Service", svc, "depends_on", "Dependency", dep_id(name, ver)))
    store.upsert_nodes(
        "Dependency", [{"id": i, "name": n, "version": v} for i, (n, v) in deps.items()]
    )
    store.upsert_edges(edges)
