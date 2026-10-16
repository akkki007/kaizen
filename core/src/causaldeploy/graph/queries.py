"""The core multi-hop queries (PLAN.md §6) plus the incident impact report (#89).

Typed in, typed out; bounded and timed out."""

from pydantic import BaseModel

from causaldeploy.graph.store import GraphStore

MAX_DEPTH = 6


def _depth(d: int) -> int:
    return max(1, min(d, MAX_DEPTH))


class DeploymentCommits(BaseModel):
    deployment_id: str
    env: str
    started_at: int | None
    commits: list[str]


def incident_deployment_commits(store: GraphStore, incident_id: str) -> list[DeploymentCommits]:
    """(1) incident -> deployment -> commits in that deployment."""
    rows = store.read(
        "MATCH (d:Deployment)-[:triggers]->(:Incident {id: $id}) "
        "OPTIONAL MATCH (d)-[:includes]->(c:Commit) "
        "WITH d, c ORDER BY c.ts "
        "RETURN d.id, d.env, d.started_at, collect(c.sha)",
        {"id": incident_id},
    )
    return [
        DeploymentCommits(deployment_id=r[0], env=r[1], started_at=r[2], commits=r[3]) for r in rows
    ]


class CallPath(BaseModel):
    commit_sha: str
    changed_function: str
    thrown_function: str
    distance: int
    direction: str  # same | calls | called_by
    path: list[str]


def changed_to_thrown_paths(
    store: GraphStore, commit_sha: str, error_fingerprint: str, max_depth: int = 4
) -> list[CallPath]:
    """(2) changed fn -> shortest call path -> fn the error was thrown in.

    `calls` means the changed function (transitively) calls the throwing one;
    `called_by` means the throwing function calls the changed one (bad return value).
    """
    d = _depth(max_depth)
    params = {"sha": commit_sha, "fp": error_fingerprint}
    out: dict[tuple[str, str], CallPath] = {}

    def keep(f: str, g: str, dist: int, direction: str, path: list[str]) -> None:
        cur = out.get((f, g))
        if cur is None or dist < cur.distance:
            out[(f, g)] = CallPath(
                commit_sha=commit_sha,
                changed_function=f,
                thrown_function=g,
                distance=dist,
                direction=direction,
                path=path,
            )

    for r in store.read(
        "MATCH (:Commit {sha: $sha})-[:modifies_fn]->(f:Function)"
        "<-[:thrown_in]-(:Error {fingerprint: $fp}) RETURN f.id",
        params,
    ):
        keep(r[0], r[0], 0, "same", [r[0]])
    for direction, pattern in (
        ("calls", f"shortestPath((f)-[:calls*1..{d}]->(g))"),
        ("called_by", f"shortestPath((g)-[:calls*1..{d}]->(f))"),
    ):
        rows = store.read(
            "MATCH (:Commit {sha: $sha})-[:modifies_fn]->(f:Function) "
            "MATCH (:Error {fingerprint: $fp})-[:thrown_in]->(g:Function) "
            f"WHERE f <> g WITH f, g, {pattern} AS p WHERE p IS NOT NULL "
            "RETURN f.id, g.id, length(p), [n IN nodes(p) | n.id]",
            params,
        )
        for f, g, dist, path in rows:
            keep(f, g, int(dist), direction, path)
    return sorted(out.values(), key=lambda p: (p.distance, p.changed_function))


class BlastRadius(BaseModel):
    function_id: str
    affected_functions: list[str]
    affected_endpoints: list[str]
    affected_services: list[str]


def blast_radius(store: GraphStore, function_id: str, max_depth: int = 3) -> BlastRadius:
    """(3) changed fn -> everything that (transitively) calls it -> endpoints and services."""
    d = _depth(max_depth)
    rows = store.read(
        f"MATCH (f:Function {{id: $id}}) OPTIONAL MATCH (c:Function)-[:calls*1..{d}]->(f) "
        "WITH f, collect(DISTINCT c) + [f] AS fns UNWIND fns AS x "
        "OPTIONAL MATCH (x)-[:handles]->(e:Endpoint) "
        "OPTIONAL MATCH (x)-[:belongs_to]->(s:Service) "
        "RETURN collect(DISTINCT x.id), collect(DISTINCT e.id), collect(DISTINCT s.id)",
        {"id": function_id},
    )
    fns, eps, svcs = rows[0] if rows else ([], [], [])
    if svcs:
        # callers reach this code over the network too: services upstream via service calls
        up = store.read(
            "MATCH (s:Service) WHERE s.id IN $svcs "
            f"OPTIONAL MATCH (u:Service)-[:calls*1..{d}]->(s) RETURN collect(DISTINCT u.id)",
            {"svcs": svcs},
        )
        svcs = sorted({*svcs, *(up[0][0] if up else [])})
    return BlastRadius(
        function_id=function_id,
        affected_functions=sorted(f for f in fns if f != function_id),
        affected_endpoints=sorted(eps),
        affected_services=sorted(svcs),
    )


class DependencyImpact(BaseModel):
    dependency_id: str
    functions: list[str]
    services: list[str]


def dependency_impact(store: GraphStore, dependency_id: str) -> DependencyImpact:
    """(4) changed dependency -> functions that depend on it -> affected services."""
    rows = store.read(
        "OPTIONAL MATCH (f:Function)-[:depends_on]->(:Dependency {id: $id}) "
        "OPTIONAL MATCH (f)-[:belongs_to]->(s:Service) "
        "RETURN collect(DISTINCT f.id), collect(DISTINCT s.id)",
        {"id": dependency_id},
    )
    fns, svcs = rows[0]
    return DependencyImpact(
        dependency_id=dependency_id, functions=sorted(fns), services=sorted(svcs)
    )


class SimilarIncident(BaseModel):
    incident_id: str
    similarity: float | None
    basis: list[str]
    fix_id: str | None
    fix_kind: str | None


def similar_resolved_incidents(store: GraphStore, incident_id: str) -> list[SimilarIncident]:
    """(5) incident -> structurally similar resolved incidents -> the fix that worked."""
    rows = store.read(
        "MATCH (:Incident {id: $id})-[r:resembles]->(j:Incident {status: 'resolved'}) "
        "OPTIONAL MATCH (j)-[:fixed_by]->(x:Fix) "
        "RETURN j.id, r.similarity, r.basis, x.id, x.kind ORDER BY r.similarity DESC, j.id",
        {"id": incident_id},
    )
    return [
        SimilarIncident(
            incident_id=r[0], similarity=r[1], basis=r[2] or [], fix_id=r[3], fix_kind=r[4]
        )
        for r in rows
    ]


class FunctionIncident(BaseModel):
    incident_id: str
    status: str
    via: str  # blamed (a commit touching the function was blamed) | thrown (error thrown in it)


def function_incident_history(store: GraphStore, function_id: str) -> list[FunctionIncident]:
    """(6) function -> every past incident that blamed it or threw in it (hotspot history)."""
    rows = store.read(
        "MATCH (:Function {id: $id})<-[:modifies_fn]-(:Commit)<-[:likely_caused_by]-(i:Incident) "
        "RETURN DISTINCT i.id AS id, i.status AS status, 'blamed' AS via "
        "UNION "
        "MATCH (:Function {id: $id})<-[:thrown_in]-(:Error)<-[:evidenced_by]-(i:Incident) "
        "RETURN DISTINCT i.id AS id, i.status AS status, 'thrown' AS via",
        {"id": function_id},
    )
    return sorted(
        (FunctionIncident(incident_id=r[0], status=r[1], via=r[2]) for r in rows),
        key=lambda x: (x.incident_id, x.via),
    )


class ImpactEntry(BaseModel):
    id: str  # service id or endpoint id
    status: str  # observed (the incident shows errors on it) | potential (reachable only)
    via: list[str]  # changed | caller | callee | service_call | shared_dependency | incident


class SharedDependency(BaseModel):
    dependency_id: str
    functions: list[str]
    services: list[str]


class ImpactReport(BaseModel):
    incident_id: str
    sha: str
    originating: str | None  # service of the changed code (where the bug lives)
    symptomatic: str | None  # service the incident was raised on (where it hurts)
    services: list[ImpactEntry]
    endpoints: list[ImpactEntry]
    shared_dependencies: list[SharedDependency]


def impact_report(
    store: GraphStore, incident_id: str, sha: str, max_depth: int = 3
) -> ImpactReport:
    """(7) Blast radius of one suspect commit, split into what the incident already shows
    (`observed`) and what the change could still reach (`potential`).

    Reach = callers of the changed functions (they consume its results), callees (they receive
    its output), services upstream over service-level calls, and code sharing a dependency the
    changed code uses or the commit changed (`depends_on` fan-out).
    """
    d = _depth(max_depth)
    p = {"inc": incident_id, "sha": sha}
    inc = store.read("MATCH (i:Incident {id: $inc}) RETURN i.service", p)
    symptomatic = inc[0][0] if inc else None
    changed = [
        r[0]
        for r in store.read(
            "MATCH (:Commit {sha: $sha})-[:modifies_fn]->(f:Function) RETURN f.id ORDER BY f.id",
            p,
        )
    ]
    obs = store.read(
        "MATCH (i:Incident {id: $inc}) "
        "OPTIONAL MATCH (i)-[:affects]->(s:Service) "
        "OPTIONAL MATCH (i)-[:evidenced_by]->(e:Error) "
        "OPTIONAL MATCH (e)-[:observed_on]->(ep:Endpoint) "
        "OPTIONAL MATCH (e)-[:thrown_in]->(:Function)-[:belongs_to]->(ts:Service) "
        "RETURN collect(DISTINCT s.id), collect(DISTINCT e.service), collect(DISTINCT ep.id), "
        "collect(DISTINCT ts.id)",
        p,
    )
    o_svc: set[str] = set()
    o_ep: set[str] = set()
    if obs:
        o_svc = {x for x in [*obs[0][0], *obs[0][1], *obs[0][3]] if x}
        o_ep = {x for x in obs[0][2] if x}

    services: dict[str, set[str]] = {}
    endpoints: dict[str, set[str]] = {}

    def add(target: dict[str, set[str]], key: str | None, via: str) -> None:
        if key:
            target.setdefault(key, set()).add(via)

    originating = None
    if changed:
        org = store.read(
            "MATCH (f:Function)-[:belongs_to]->(s:Service) WHERE f.id IN $fns "
            "RETURN s.id, count(f) AS n ORDER BY n DESC, s.id LIMIT 1",
            {"fns": changed},
        )
        originating = org[0][0] if org else None
        for direction, pattern in (
            ("caller", f"(x:Function)-[:calls*0..{d}]->(f)"),
            ("callee", f"(f)-[:calls*1..{d}]->(x:Function)"),
        ):
            rows = store.read(
                f"MATCH (f:Function) WHERE f.id IN $fns MATCH {pattern} "
                "OPTIONAL MATCH (x)-[:handles]->(ep:Endpoint) "
                "OPTIONAL MATCH (x)-[:belongs_to]->(s:Service) "
                "RETURN collect(DISTINCT ep.id), collect(DISTINCT s.id)",
                {"fns": changed},
            )
            if rows:
                for ep in rows[0][0]:
                    add(endpoints, ep, direction)
                for s in rows[0][1]:
                    add(services, s, "changed" if s == originating else direction)
    add(services, originating, "changed")
    if services:
        up = store.read(
            "MATCH (s:Service) WHERE s.id IN $svcs "
            f"MATCH (u:Service)-[:calls*1..{d}]->(s) RETURN collect(DISTINCT u.id)",
            {"svcs": sorted(services)},
        )
        for u in up[0][0] if up else []:
            add(services, u, "service_call")

    changed_deps = {
        r[0]
        for r in store.read(
            "MATCH (:Commit {sha: $sha})-[:changes_dep]->(x:Dependency) RETURN x.id", p
        )
    }
    used_deps = {
        r[0]
        for r in store.read(
            "MATCH (:Commit {sha: $sha})-[:modifies_fn]->(:Function)-[:depends_on]->(x:Dependency) "
            "RETURN DISTINCT x.id",
            p,
        )
    }
    shared: list[SharedDependency] = []
    for dep in sorted(changed_deps | used_deps):
        di = dependency_impact(store, dep)
        if dep not in changed_deps and not [f for f in di.functions if f not in changed]:
            continue  # nobody else uses it: no fan-out
        shared.append(
            SharedDependency(dependency_id=dep, functions=di.functions, services=di.services)
        )
        for s in di.services:
            add(services, s, "shared_dependency")
        for (ep,) in store.read(
            "MATCH (f:Function)-[:handles]->(ep:Endpoint) WHERE f.id IN $fns RETURN DISTINCT ep.id",
            {"fns": di.functions},
        ):
            add(endpoints, ep, "shared_dependency")

    for s in o_svc:
        add(services, s, "incident")
    for ep in o_ep:
        add(endpoints, ep, "incident")

    def entries(m: dict[str, set[str]], observed: set[str]) -> list[ImpactEntry]:
        return [
            ImpactEntry(id=k, status="observed" if k in observed else "potential", via=sorted(v))
            for k, v in sorted(m.items(), key=lambda kv: (kv[0] not in observed, kv[0]))
        ]

    return ImpactReport(
        incident_id=incident_id,
        sha=sha,
        originating=originating,
        symptomatic=symptomatic,
        services=entries(services, o_svc),
        endpoints=entries(endpoints, o_ep),
        shared_dependencies=shared,
    )
