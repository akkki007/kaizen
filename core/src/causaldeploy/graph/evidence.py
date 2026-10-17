"""Evidence-path subgraph for one incident: exactly the nodes/edges an explanation may cite."""

from typing import Any, Literal

from pydantic import BaseModel

from causaldeploy.graph.queries import changed_to_thrown_paths
from causaldeploy.graph.schema import NODE_KEYS
from causaldeploy.graph.store import GraphStore

_HIDDEN = {"SchemaMeta"}
_EXPAND_LIMIT = 50


class EvidenceNode(BaseModel):
    id: str  # "<Label>:<key>"; stable, used verbatim in explanation citations
    label: str
    props: dict[str, Any]
    highlight: bool = False
    overlay: Literal["blast", "history"] | None = None  # added by an evidence-graph overlay


class EvidenceEdge(BaseModel):
    id: str  # "<src id>|<rel>|<dst id>"
    source: str
    target: str
    rel: str
    props: dict[str, Any] = {}
    highlight: bool = False
    overlay: Literal["blast", "history"] | None = None


class EvidenceGraph(BaseModel):
    incident_id: str
    nodes: list[EvidenceNode]
    edges: list[EvidenceEdge]

    def citable_ids(self) -> set[str]:
        return {n.id for n in self.nodes} | {e.id for e in self.edges}

    def unresolved(self, citations: list[str]) -> list[str]:
        known = self.citable_ids()
        return [c for c in citations if c not in known]


def node_id(label: str, key: str) -> str:
    return f"{label}:{key}"


class _Collector:
    def __init__(self) -> None:
        self.nodes: dict[str, EvidenceNode] = {}
        self.edges: dict[str, EvidenceEdge] = {}

    def node(self, label: str, props: dict[str, Any], highlight: bool = False) -> str:
        nid = node_id(label, str(props[NODE_KEYS[label]]))
        cur = self.nodes.get(nid)
        if cur is None:
            self.nodes[nid] = EvidenceNode(id=nid, label=label, props=props, highlight=highlight)
        elif highlight:
            cur.highlight = True
        return nid

    def edge(
        self, src: str, rel: str, dst: str, props: dict[str, Any] | None = None, hl: bool = False
    ) -> None:
        eid = f"{src}|{rel}|{dst}"
        cur = self.edges.get(eid)
        if cur is None:
            self.edges[eid] = EvidenceEdge(
                id=eid, source=src, target=dst, rel=rel, props=props or {}, highlight=hl
            )
        elif hl:
            cur.highlight = True

    def rows(self, rows: list[list[Any]]) -> None:
        for la, pa, rel, pr, lb, pb in rows:
            if la in _HIDDEN or lb in _HIDDEN:
                continue
            self.edge(self.node(la, pa), rel, self.node(lb, pb), pr)


_TRIPLE = "labels(a)[0], properties(a), type(r), properties(r), labels(b)[0], properties(b)"
# neighbours in either direction: report edges in their stored direction
_TRIPLE_UNDIRECTED = (
    "labels(startNode(r))[0], properties(startNode(r)), type(r), properties(r), "
    "labels(endNode(r))[0], properties(endNode(r))"
)


def evidence_graph(
    store: GraphStore, incident_id: str, expand: str | None = None
) -> EvidenceGraph | None:
    inc = store.read("MATCH (i:Incident {id: $id}) RETURN properties(i)", {"id": incident_id})
    if not inc:
        return None
    c = _Collector()
    c.node("Incident", inc[0][0], highlight=True)
    p = {"id": incident_id}
    c.rows(store.read(f"MATCH (a:Incident {{id: $id}})-[r]->(b) RETURN {_TRIPLE}", p))
    c.rows(
        store.read(
            f"MATCH (x:Deployment)-[:triggers]->(:Incident {{id: $id}}) "
            f"MATCH (a:Deployment)-[r:triggers|includes|deployed_to]->(b) WHERE a.id = x.id "
            f"RETURN {_TRIPLE}",
            p,
        )
    )
    c.rows(
        store.read(
            f"MATCH (:Incident {{id: $id}})-[:evidenced_by]->(a:Error)-[r]->(b) RETURN {_TRIPLE}", p
        )
    )

    top = store.read("MATCH (:Incident {id: $id})-[:likely_caused_by]->(c:Commit) RETURN c.sha", p)
    top_sha = top[0][0] if top else None
    errors = [
        r[0]
        for r in store.read(
            "MATCH (:Incident {id: $id})-[:evidenced_by]->(e:Error) RETURN e.fingerprint", p
        )
    ]
    candidates = [
        r[0]
        for r in store.read(
            "MATCH (:Incident {id: $id})-[:candidate]->(c:Commit) RETURN c.sha ORDER BY c.sha", p
        )
    ]
    for sha in candidates:
        for fp in errors:
            for cp in changed_to_thrown_paths(store, sha, fp):
                hl = sha == top_sha
                commit = node_id("Commit", sha)
                ids = [node_id("Function", f) for f in cp.path]
                for fn in cp.path:
                    rows = store.read("MATCH (f:Function {id: $f}) RETURN properties(f)", {"f": fn})
                    c.node("Function", rows[0][0], highlight=hl)
                c.edge(commit, "modifies_fn", node_id("Function", cp.changed_function), hl=hl)
                for a, b in zip(ids, ids[1:], strict=False):
                    c.edge(a, "calls", b, hl=hl)
    if top_sha:
        c.nodes[node_id("Commit", top_sha)].highlight = True

    if expand:
        label, _, key = expand.partition(":")
        if label in NODE_KEYS and key and node_id(label, key) in c.nodes:
            k = NODE_KEYS[label]
            c.rows(
                store.read(
                    f"MATCH (a:{label} {{{k}: $k}})-[r]-(b) "
                    f"RETURN {_TRIPLE_UNDIRECTED} "
                    f"LIMIT {_EXPAND_LIMIT}",
                    {"k": key},
                )
            )
    return EvidenceGraph(
        incident_id=incident_id, nodes=list(c.nodes.values()), edges=list(c.edges.values())
    )
