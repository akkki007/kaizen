"""Read-only views behind the incident page (#93).

metrics around the onset, similar (`resembles`) incidents, the status snapshot the SSE stream
emits, the node inspector, and the blast-radius / history overlays on the evidence graph.
Nothing here writes: every function reads Postgres or the incident's own repository graph.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select, text

from causaldeploy.db.models import (
    IncidentRow,
    IncidentTransition,
    Investigation,
    RemediationRun,
    Telemetry,
)
from causaldeploy.db.session import session_scope
from causaldeploy.graph.evidence import EvidenceEdge, EvidenceGraph, EvidenceNode, node_id
from causaldeploy.graph.queries import blast_radius, impact_report
from causaldeploy.graph.schema import NODE_KEYS
from causaldeploy.graph.store import GraphStore

Overlay = Literal["blast", "history"]

# size caps: the inspector and overlays stay small enough to render and to send
MAX_PROPS = 40
MAX_PROP_CHARS = 500
MAX_NEIGHBOURS = 40
MAX_CHANGED = 50
MAX_SAMPLES = 10
MAX_OVERLAY_FUNCTIONS = 60
MAX_SIMILAR = 20
MAX_HISTORY_INCIDENTS = 10

# -- metrics -------------------------------------------------------------------------------


class MetricPoint(BaseModel):
    ts: datetime  # start of the 1-minute bucket
    requests: int
    errors: int
    error_rate: float
    p95_ms: float | None


class DeploymentMark(BaseModel):
    id: str
    sha: str | None
    started_at: datetime
    status: str | None


class IncidentMetrics(BaseModel):
    incident_id: str
    onset: datetime
    since: datetime
    until: datetime
    service: str
    env: str
    endpoint: str
    series: list[MetricPoint]
    deployments: list[DeploymentMark]


_SERIES = text(
    """
    SELECT date_bin(make_interval(mins => 1), ts, TIMESTAMPTZ '2000-01-01') AS bucket,
           count(*) AS n,
           count(*) FILTER (WHERE status_code >= 400
                              OR (kind = 'health' AND (body->>'ok') = 'false')) AS errors,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
    FROM telemetry
    WHERE repo = :repo AND service = :service AND env = :env
      AND ts >= :since AND ts < :until AND kind IN ('request', 'health')
      AND (:endpoint = '-' OR endpoint = :endpoint)
    GROUP BY 1 ORDER BY 1
    """
)


def window(onset: datetime, minutes: int) -> tuple[datetime, datetime]:
    """Two thirds of the window before the onset (the baseline), one third after it."""
    before = timedelta(minutes=minutes * 2 // 3)
    return onset - before, onset - before + timedelta(minutes=minutes)


async def incident_metrics(row: IncidentRow, minutes: int) -> IncidentMetrics:
    import asyncio

    since, until = window(row.onset_ts, minutes)
    async with session_scope() as s:
        rows = (
            await s.execute(
                _SERIES,
                {
                    "repo": row.repo,
                    "service": row.service,
                    "env": row.env,
                    "since": since,
                    "until": until,
                    "endpoint": row.endpoint or "-",
                },
            )
        ).all()
    series = [
        MetricPoint(
            ts=b,
            requests=int(n),
            errors=int(e),
            error_rate=round(int(e) / int(n), 4) if n else 0.0,
            p95_ms=round(float(p), 1) if p is not None else None,
        )
        for b, n, e, p in rows
    ]
    deps = await asyncio.to_thread(
        _deployments, GraphStore(row.repo), row.env, int(since.timestamp()), int(until.timestamp())
    )
    return IncidentMetrics(
        incident_id=row.id,
        onset=row.onset_ts,
        since=since,
        until=until,
        service=row.service,
        env=row.env,
        endpoint=row.endpoint,
        series=series,
        deployments=deps,
    )


def _deployments(store: GraphStore, env: str, since: int, until: int) -> list[DeploymentMark]:
    rows = store.read(
        "MATCH (d:Deployment) WHERE d.env = $env AND d.started_at >= $a AND d.started_at < $b "
        "RETURN d.id, d.sha_to, d.started_at, d.status ORDER BY d.started_at LIMIT 50",
        {"env": env, "a": since, "b": until},
    )
    return [
        DeploymentMark(
            id=str(r[0]),
            sha=r[1],
            started_at=datetime.fromtimestamp(int(r[2]), UTC),
            status=r[3],
        )
        for r in rows
    ]


# -- similar incidents ---------------------------------------------------------------------


class FixRef(BaseModel):
    id: str
    kind: str | None
    pr_url: str | None


class SimilarIncidentView(BaseModel):
    incident_id: str
    similarity: float
    basis: list[str]
    status: str | None
    fix: FixRef | None
    confirmed_root_cause: str | None


def _similar_rows(store: GraphStore, incident_id: str) -> list[list[Any]]:
    return store.read(
        "MATCH (:Incident {id: $i})-[r:resembles]->(j:Incident) "
        "OPTIONAL MATCH (j)-[:fixed_by]->(f:Fix) "
        "OPTIONAL MATCH (j)-[:confirmed_cause]->(c:Commit) "
        "RETURN j.id, r.similarity, r.basis, j.status, f.id, f.kind, f.pr_url, "
        "coalesce(f.accepted, false), c.sha "
        "ORDER BY r.similarity DESC, j.id",
        {"i": incident_id},
    )


async def similar_incidents(row: IncidentRow) -> list[SimilarIncidentView]:
    import asyncio

    rows = await asyncio.to_thread(_similar_rows, GraphStore(row.repo), row.id)
    best: dict[str, SimilarIncidentView] = {}
    accepted: dict[str, bool] = {}
    for j, sim, basis, status, fid, kind, url, acc, sha in rows:
        cur = best.get(j)
        fix = FixRef(id=fid, kind=kind, pr_url=url) if fid else None
        if cur is None:
            best[j] = SimilarIncidentView(
                incident_id=j,
                similarity=float(sim or 0.0),
                basis=list(basis or []),
                status=status,
                fix=fix,
                confirmed_root_cause=sha,
            )
            accepted[j] = bool(acc)
        elif fix and (cur.fix is None or (acc and not accepted[j])):
            cur.fix, accepted[j] = fix, bool(acc)  # prefer the fix that was accepted
    out = list(best.values())[:MAX_SIMILAR]
    if out:  # Postgres is the source of truth for status and the confirmed cause
        async with session_scope() as s:
            pg = {
                r.id: r
                for r in (
                    await s.execute(
                        select(IncidentRow).where(
                            IncidentRow.id.in_([x.incident_id for x in out]),
                            IncidentRow.repo == row.repo,
                        )
                    )
                ).scalars()
            }
        for x in out:
            if (p := pg.get(x.incident_id)) is not None:
                x.status = p.status
                x.confirmed_root_cause = p.confirmed_root_cause or x.confirmed_root_cause
    return out


# -- status snapshot (SSE) -----------------------------------------------------------------


class StatusSnapshot(BaseModel):
    status: str
    updated_at: datetime
    remediation_status: str | None
    investigation_status: str


async def status_snapshot(incident_id: str) -> StatusSnapshot | None:
    async with session_scope() as s:
        inc = await s.get(IncidentRow, incident_id)
        if inc is None:
            return None
        last_t = (
            await s.execute(
                select(IncidentTransition.created_at)
                .where(IncidentTransition.incident_id == incident_id)
                .order_by(IncidentTransition.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        inv = (
            await s.execute(
                select(Investigation)
                .where(Investigation.incident_id == incident_id)
                .order_by(Investigation.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        rem = (
            await s.execute(
                select(RemediationRun)
                .where(RemediationRun.incident_id == incident_id)
                .order_by(RemediationRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    stamps = [inc.detected_at, last_t, inv.created_at if inv else None]
    stamps.append(rem.created_at if rem else None)
    agent = ((inv.result or {}).get("agent") or {}) if inv else {}
    return StatusSnapshot(
        status=inc.status,
        updated_at=max(t for t in stamps if t is not None),
        remediation_status=rem.status if rem else None,
        investigation_status=str(agent.get("status") or "not_run"),
    )


# -- node inspector ------------------------------------------------------------------------


class Neighbour(BaseModel):
    id: str
    label: str
    rel: str
    direction: Literal["out", "in"]
    name: str | None


class ChangedFunction(BaseModel):
    function_id: str
    path: str | None
    start_line: int | None
    end_line: int | None
    lines_added: int | None
    lines_removed: int | None
    moved_from: str | None


class ChangedFile(BaseModel):
    file_id: str
    path: str | None
    status: str | None


class CommitDetail(BaseModel):
    sha: str
    message: str | None
    author: str | None
    ts: datetime | None
    changed_functions: list[ChangedFunction]
    changed_files: list[ChangedFile]


class FunctionDetail(BaseModel):
    path: str | None
    service: str | None
    start_line: int | None
    end_line: int | None
    callers: list[str]
    callees: list[str]


class ErrorSample(BaseModel):
    ts: datetime
    error_type: str | None
    message: str | None
    endpoint: str | None
    status_code: int | None


class NodeDetail(BaseModel):
    id: str
    label: str
    props: dict[str, Any]
    neighbours: list[Neighbour]
    neighbours_total: int
    commit: CommitDetail | None = None
    function: FunctionDetail | None = None
    error_samples: list[ErrorSample] = []


def _cap_props(props: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in sorted(props)[:MAX_PROPS]:
        v = props[k]
        if isinstance(v, str) and len(v) > MAX_PROP_CHARS:
            v = v[:MAX_PROP_CHARS] + "…"
        elif isinstance(v, list) and len(v) > MAX_NEIGHBOURS:
            v = v[:MAX_NEIGHBOURS]
        out[k] = v
    return out


def _name(label: str, props: dict[str, Any]) -> str | None:
    for k in ("name", "message", "route", "path"):
        if props.get(k):
            return str(props[k])[:120]
    return str(props.get(NODE_KEYS.get(label, "id"), "")) or None


def parse_node_id(nid: str) -> tuple[str, str] | None:
    label, _, key = nid.partition(":")
    if label not in NODE_KEYS or not key or label == "SchemaMeta":
        return None
    return label, key


def node_detail(store: GraphStore, nid: str) -> NodeDetail | None:
    parsed = parse_node_id(nid)
    if parsed is None:
        return None
    label, key = parsed
    k = NODE_KEYS[label]
    found = store.read(f"MATCH (n:{label} {{{k}: $k}}) RETURN properties(n)", {"k": key})
    if not found:
        return None
    props: dict[str, Any] = found[0][0]
    total = store.read(f"MATCH (n:{label} {{{k}: $k}})-[r]-() RETURN count(r)", {"k": key})
    rows = store.read(
        f"MATCH (n:{label} {{{k}: $k}})-[r]-(b) "
        "RETURN type(r), id(startNode(r)) = id(n), labels(b)[0], properties(b) "
        f"ORDER BY type(r) LIMIT {MAX_NEIGHBOURS}",
        {"k": key},
    )
    neighbours = [
        Neighbour(
            id=node_id(lb, str(pb.get(NODE_KEYS[lb]))),
            label=lb,
            rel=rel,
            direction="out" if out else "in",
            name=_name(lb, pb),
        )
        for rel, out, lb, pb in rows
        if lb in NODE_KEYS
    ]
    detail = NodeDetail(
        id=nid,
        label=label,
        props=_cap_props(props),
        neighbours=neighbours,
        neighbours_total=int(total[0][0]) if total else len(neighbours),
    )
    if label == "Commit":
        detail.commit = _commit_detail(store, key, props)
    elif label == "Function":
        detail.function = _function_detail(store, key, props)
    return detail


def _commit_detail(store: GraphStore, sha: str, props: dict[str, Any]) -> CommitDetail:
    author = store.read(
        "MATCH (:Commit {sha: $s})-[:authored_by]->(d:Developer) RETURN d.id LIMIT 1", {"s": sha}
    )
    fns = store.read(
        "MATCH (:Commit {sha: $s})-[m:modifies_fn]->(f:Function) "
        "RETURN f.id, f.path, f.start_line, f.end_line, m.added, m.removed, m.moved_from "
        f"ORDER BY f.id LIMIT {MAX_CHANGED}",
        {"s": sha},
    )
    files = store.read(
        "MATCH (:Commit {sha: $s})-[m:modifies]->(f:File) RETURN f.id, f.path, m.status "
        f"ORDER BY f.id LIMIT {MAX_CHANGED}",
        {"s": sha},
    )
    ts = props.get("ts")
    return CommitDetail(
        sha=sha,
        message=props.get("message"),
        author=author[0][0] if author else None,
        ts=datetime.fromtimestamp(int(ts), UTC) if isinstance(ts, int | float) else None,
        changed_functions=[
            ChangedFunction(
                function_id=r[0],
                path=r[1],
                start_line=r[2],
                end_line=r[3],
                lines_added=r[4],
                lines_removed=r[5],
                moved_from=r[6],
            )
            for r in fns
        ],
        changed_files=[ChangedFile(file_id=r[0], path=r[1], status=r[2]) for r in files],
    )


def _function_detail(store: GraphStore, fid: str, props: dict[str, Any]) -> FunctionDetail:
    callers = store.read(
        "MATCH (c:Function)-[:calls]->(:Function {id: $f}) RETURN c.id ORDER BY c.id "
        f"LIMIT {MAX_NEIGHBOURS}",
        {"f": fid},
    )
    callees = store.read(
        "MATCH (:Function {id: $f})-[:calls]->(c:Function) RETURN c.id ORDER BY c.id "
        f"LIMIT {MAX_NEIGHBOURS}",
        {"f": fid},
    )
    return FunctionDetail(
        path=props.get("path"),
        service=props.get("service"),
        start_line=props.get("start_line"),
        end_line=props.get("end_line"),
        callers=[r[0] for r in callers],
        callees=[r[0] for r in callees],
    )


async def error_samples(repo: str, fingerprint: str) -> list[ErrorSample]:
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Telemetry)
                .where(Telemetry.repo == repo, Telemetry.fingerprint == fingerprint)
                .order_by(Telemetry.ts.desc())
                .limit(MAX_SAMPLES)
            )
        ).scalars()
        return [
            ErrorSample(
                ts=r.ts,
                error_type=(r.body or {}).get("error_type"),
                message=str((r.body or {}).get("message") or "")[:MAX_PROP_CHARS] or None,
                endpoint=r.endpoint,
                status_code=r.status_code,
            )
            for r in rows
        ]


# -- evidence graph overlays ---------------------------------------------------------------


class _Overlay:
    """Adds nodes/edges to an evidence graph without touching the ones already in it."""

    def __init__(self, store: GraphStore, graph: EvidenceGraph, kind: Overlay) -> None:
        self.store, self.graph, self.kind = store, graph, kind
        self.nodes = {n.id: n for n in graph.nodes}
        self.edges = {e.id: e for e in graph.edges}

    def node(self, label: str, props: dict[str, Any], extra: dict[str, Any] | None = None) -> str:
        nid = node_id(label, str(props[NODE_KEYS[label]]))
        if nid not in self.nodes:
            n = EvidenceNode(
                id=nid, label=label, props={**_cap_props(props), **(extra or {})}, overlay=self.kind
            )
            self.nodes[nid] = n
            self.graph.nodes.append(n)
        return nid

    def edge(self, src: str, rel: str, dst: str, props: dict[str, Any] | None = None) -> None:
        eid = f"{src}|{rel}|{dst}"
        if eid not in self.edges and src in self.nodes and dst in self.nodes:
            e = EvidenceEdge(
                id=eid, source=src, target=dst, rel=rel, props=props or {}, overlay=self.kind
            )
            self.edges[eid] = e
            self.graph.edges.append(e)

    def fetch(self, label: str, keys: list[str]) -> dict[str, dict[str, Any]]:
        if not keys:
            return {}
        k = NODE_KEYS[label]
        rows = self.store.read(
            f"MATCH (n:{label}) WHERE n.{k} IN $keys RETURN n.{k}, properties(n)", {"keys": keys}
        )
        return {str(r[0]): r[1] for r in rows}


def _culprit(store: GraphStore, incident_id: str) -> str | None:
    for rel in ("confirmed_cause", "likely_caused_by"):
        rows = store.read(
            f"MATCH (:Incident {{id: $i}})-[:{rel}]->(c:Commit) RETURN c.sha LIMIT 1",
            {"i": incident_id},
        )
        if rows:
            return str(rows[0][0])
    return None


def add_blast_overlay(store: GraphStore, graph: EvidenceGraph) -> EvidenceGraph:
    """Everything the culprit's changed functions can reach: callers, endpoints, services."""
    sha = _culprit(store, graph.incident_id)
    if sha is None:
        return graph
    o = _Overlay(store, graph, "blast")
    report = impact_report(store, graph.incident_id, sha)
    changed = [
        r[0]
        for r in store.read(
            "MATCH (:Commit {sha: $s})-[:modifies_fn]->(f:Function) RETURN f.id ORDER BY f.id",
            {"s": sha},
        )
    ]
    fns: list[str] = []
    for f in changed:
        for g in [f, *blast_radius(store, f).affected_functions]:
            if g not in fns:
                fns.append(g)
    fns = fns[:MAX_OVERLAY_FUNCTIONS]
    commit = o.fetch("Commit", [sha])
    if sha in commit:
        o.node("Commit", commit[sha])
    for props in o.fetch("Function", fns).values():
        o.node("Function", props)
    for f in changed:
        o.edge(node_id("Commit", sha), "modifies_fn", node_id("Function", f))
    for a, b in store.read(
        "MATCH (a:Function)-[:calls]->(b:Function) WHERE a.id IN $f AND b.id IN $f "
        "RETURN a.id, b.id",
        {"f": fns},
    ):
        o.edge(node_id("Function", a), "calls", node_id("Function", b))
    impact = {e.id: e for e in [*report.endpoints, *report.services]}
    eps = o.fetch("Endpoint", [e.id for e in report.endpoints])
    svcs = o.fetch("Service", [s.id for s in report.services])
    for label, found in (("Endpoint", eps), ("Service", svcs)):
        for key, props in found.items():
            entry = impact[key]
            o.node(label, props, {"impact": entry.status, "via": entry.via})
    for f, ep in store.read(
        "MATCH (f:Function)-[:handles]->(e:Endpoint) WHERE f.id IN $f RETURN f.id, e.id",
        {"f": fns},
    ):
        o.edge(node_id("Function", f), "handles", node_id("Endpoint", ep))
    for f, s in store.read(
        "MATCH (f:Function)-[:belongs_to]->(s:Service) WHERE f.id IN $f RETURN f.id, s.id",
        {"f": fns},
    ):
        o.edge(node_id("Function", f), "belongs_to", node_id("Service", s))
    for a, b in store.read(
        "MATCH (a:Service)-[:calls]->(b:Service) WHERE a.id IN $s AND b.id IN $s RETURN a.id, b.id",
        {"s": list(svcs)},
    ):
        o.edge(node_id("Service", a), "calls", node_id("Service", b))
    return graph


def add_history_overlay(store: GraphStore, graph: EvidenceGraph) -> EvidenceGraph:
    """Incidents this one resembles, the fixes that resolved them and what those fixes undid."""
    o = _Overlay(store, graph, "history")
    me = node_id("Incident", graph.incident_id)
    rows = store.read(
        "MATCH (:Incident {id: $i})-[r:resembles]->(j:Incident) "
        "RETURN properties(j), r.similarity, r.basis ORDER BY r.similarity DESC, j.id "
        f"LIMIT {MAX_HISTORY_INCIDENTS}",
        {"i": graph.incident_id},
    )
    for props, sim, basis in rows:
        j = o.node("Incident", props)
        o.edge(me, "resembles", j, {"similarity": sim, "basis": list(basis or [])})
        for fix, rel, commit in store.read(
            "MATCH (:Incident {id: $j})-[:fixed_by]->(f:Fix) "
            "OPTIONAL MATCH (f)-[r:reverts|patches]->(c:Commit) "
            "RETURN properties(f), type(r), properties(c)",
            {"j": props["id"]},
        ):
            fx = o.node("Fix", fix)
            o.edge(j, "fixed_by", fx)
            if commit and rel:
                o.edge(fx, rel, o.node("Commit", commit))
    return graph


def add_overlay(store: GraphStore, graph: EvidenceGraph, overlay: Overlay) -> EvidenceGraph:
    return (add_blast_overlay if overlay == "blast" else add_history_overlay)(store, graph)
