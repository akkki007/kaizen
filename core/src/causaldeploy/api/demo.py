"""Live code graph for the UI, plus the demo controls (inject a simulated bad deploy, reset)."""

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from causaldeploy.alerts.notify import send_slack
from causaldeploy.api.auth import DEMO_LOGIN, CurrentUser, csrf_protect, require_role
from causaldeploy.config import get_settings
from causaldeploy.db.models import AuditLog
from causaldeploy.db.session import session_scope
from causaldeploy.demo import inject as demo
from causaldeploy.graph.store import GraphStore

router = APIRouter(prefix="/api/v1")

QUERIES: dict[str, str] = {
    "functions": "MATCH (f:Function)-[:belongs_to]->(s:Service) "
    "RETURN f.id, f.name, f.path, s.id LIMIT 500",
    "calls": "MATCH (a:Function)-[:calls]->(b:Function) RETURN a.id, b.id LIMIT 2000",
    "endpoints": "MATCH (f:Function)-[:handles]->(e:Endpoint) RETURN f.id, e.id",
    "recent_commits": "MATCH (c:Commit)-[:modifies_fn]->(f:Function) "
    "RETURN c.sha, c.message, c.ts, collect(f.id) ORDER BY c.ts DESC LIMIT 30",
    "failing": "MATCH (i:Incident)-[:evidenced_by]->(:Error)-[:thrown_in]->(f:Function) "
    "WHERE i.status <> 'resolved' RETURN DISTINCT f.id, i.id",
    "suspects": "MATCH (i:Incident)-[:likely_caused_by]->(c:Commit) WHERE i.status <> 'resolved' "
    "RETURN DISTINCT c.sha, i.id",
}


class GraphNode(BaseModel):
    id: str
    kind: Literal["service", "function", "commit"]
    label: str
    parent: str | None = None
    state: Literal["ok", "failing", "suspect"] = "ok"
    incident_id: str | None = None
    detail: str = ""


class GraphEdge(BaseModel):
    source: str
    target: str
    rel: str


class CodeGraph(BaseModel):
    repo: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    queries: dict[str, str]  # the Cypher that produced this view, shown in the UI
    query_ms: dict[str, float] = {}  # measured time of each query against FalkorDB
    demo_enabled: bool


def _build(repo: str) -> CodeGraph:
    store = GraphStore(repo)
    q: dict[str, list[list[Any]]] = {}
    ms: dict[str, float] = {}
    for k, v in QUERIES.items():
        t0 = time.perf_counter()
        q[k] = store.read(v)
        ms[k] = round((time.perf_counter() - t0) * 1000, 1)
    failing = {r[0]: r[1] for r in q["failing"]}
    suspects = {r[0]: r[1] for r in q["suspects"]}
    endpoint_of = {r[0]: r[1] for r in q["endpoints"]}
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    for fid, name, path, svc in q["functions"]:
        sid = f"svc:{svc}"
        nodes.setdefault(sid, GraphNode(id=sid, kind="service", label=svc))
        detail = f"{path}" + (
            f" · serves {endpoint_of[fid].split(':', 1)[-1]}" if fid in endpoint_of else ""
        )
        nodes[fid] = GraphNode(
            id=fid, kind="function", label=(name or fid).split(".")[-1], parent=sid,
            state="failing" if fid in failing else "ok", incident_id=failing.get(fid),
            detail=detail,
        )  # fmt: skip
    for a, b in q["calls"]:
        if a in nodes and b in nodes:
            edges.append(GraphEdge(source=a, target=b, rel="calls"))
    for sha, message, _ts, fns in q["recent_commits"]:
        cid = f"commit:{sha}"
        nodes[cid] = GraphNode(
            id=cid, kind="commit", label=sha[:7], detail=message or "",
            state="suspect" if sha in suspects else "ok", incident_id=suspects.get(sha),
        )  # fmt: skip
        edges += [GraphEdge(source=cid, target=f, rel="modifies") for f in fns if f in nodes]
    return CodeGraph(
        repo=repo, nodes=list(nodes.values()), edges=edges, queries=QUERIES, query_ms=ms,
        demo_enabled=get_settings().is_demo_repo(repo),
    )  # fmt: skip


@router.get("/repos/{owner}/{name}/code-graph", response_model=CodeGraph)
async def code_graph(owner: str, name: str, user: CurrentUser) -> CodeGraph:
    repo = f"{owner}/{name}"
    require_role(user, repo, "viewer")
    return await asyncio.to_thread(_build, repo)


class InjectResult(BaseModel):
    incident_id: str
    simulated: bool
    service: str
    endpoint: str
    function: str
    commit: str
    commit_message: str


def _demo_guard(repo: str, user: Any) -> None:
    require_role(user, repo, "viewer")
    if user.login != DEMO_LOGIN:  # real users need approver; the demo session is viewer-only
        require_role(user, repo, "approver")
    if not get_settings().is_demo_repo(repo):
        raise HTTPException(404, "demo controls are not enabled for this repository")


@router.post(
    "/repos/{owner}/{name}/demo/inject",
    response_model=InjectResult,
    dependencies=[Depends(csrf_protect)],
)
async def inject_problem(owner: str, name: str, user: CurrentUser) -> InjectResult:
    repo = f"{owner}/{name}"
    _demo_guard(repo, user)
    try:
        result = await demo.inject(repo)
    except demo.DemoError as e:
        raise HTTPException(409, str(e)) from None
    async with session_scope() as s:
        s.add(AuditLog(actor=user.login, action="demo.inject", target=repo, detail=result))
    return InjectResult(**result)


@router.post("/repos/{owner}/{name}/demo/reset", dependencies=[Depends(csrf_protect)])
async def reset_demo(owner: str, name: str, user: CurrentUser) -> dict[str, int]:
    repo = f"{owner}/{name}"
    _demo_guard(repo, user)
    out = await demo.reset(repo)
    async with session_scope() as s:
        s.add(AuditLog(actor=user.login, action="demo.reset", target=repo, detail=out))
    return out


@router.post("/repos/{owner}/{name}/alerts/test", dependencies=[Depends(csrf_protect)])
async def test_alert(owner: str, name: str, user: CurrentUser) -> dict[str, bool]:
    """Send a test message to the configured Slack webhook."""
    repo = f"{owner}/{name}"
    require_role(user, repo, "approver")
    ok = await send_slack(f":white_check_mark: Kaizen alerts are connected for *{repo}*.")
    if ok:
        async with session_scope() as s:
            s.add(AuditLog(actor=user.login, action="alert.test", target=repo, detail={}))
    return {"sent": ok}
