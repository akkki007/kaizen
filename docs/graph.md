# Knowledge graph (FalkorDB)

One graph per repository (`cd_<owner>_<name>`). Labels, keys and the allowed edge pairs live in
`core/src/causaldeploy/graph/schema.py`; nothing outside `causaldeploy/graph/` writes Cypher
(ingest goes through `GraphStore.upsert_nodes/upsert_edges`, MERGE-based, batched).
Schema is applied by `GraphStore.ensure_schema()` and recorded as a `SchemaMeta` version node.

## Core queries (`graph/queries.py`)

1. `incident_deployment_commits`  2. `changed_to_thrown_paths` (shortest call path, both directions)
3. `blast_radius`  4. `dependency_impact`  5. `similar_resolved_incidents`  6. `function_incident_history`

All are bounded (`MAX_DEPTH`) and run with a 2 s server-side timeout; a ShopLite-scale test asserts < 200 ms.

## Evidence graph

`GET /api/v1/incidents/{id}/evidence-graph[?expand=<Label>:<key>]` returns exactly the nodes/edges an
explanation may cite. Node id = `<Label>:<key>`, edge id = `<src>|<rel>|<dst>`; the top candidate's
path is `highlight: true`. `EvidenceGraph.unresolved(citations)` is the citation validator.

## Static analysis limits (JS/TS, tree-sitter)

Resolved: local calls, `import`/`require` bindings (named, default, namespace, renamed), `this.method`,
Express routes (`app|router.<verb>('/path', handler)`), workspace packages, package.json/lockfile deps.
Not resolved: calls through instances (`new X().m()`), dynamic `require`, re-export chains, default-export
function calls. Unresolved calls are skipped, never guessed.

## Hunk -> function mapping and S9 (rename/move)

Added lines map via post-change ranges, deleted lines via pre-change ranges. Works: file renames
(identity by qualified name), function moved between files with identical body (`moved_from` on the
`modifies_fn` edge). Falls back to `Commit-[:modifies {fallback}]->File`: hunks outside any function,
non-code files, moves with several candidates (`ambiguous-move`). A moved *and* edited function appears
as an add in the new file plus a removal in the old one.

## Ranking features (Cypher-first, `core/src/causaldeploy/investigate/features.py`)

Every feature is a FalkorDB query; Python only applies weights. The UI shows the query name and
Cypher next to each score (`feature.<name>`).

| Feature | Meaning | Graph constructs |
|---|---|---|
| `temporal` | onset shortly after the deployment finished | `exp()` over deployment/incident timestamps |
| `path` | calls distance between a changed and a thrown function | `shortestPath` (WITH form), both directions |
| `endpoint` | changed function handles / is called by the handler of the failing endpoint | variable-length `calls*0..4` |
| `service` | changed function's service reaches the failing service | `Service-[:calls*0..3]->`, derived from request ids |
| `dependency` | a changed dependency is used on the failing path | `changes_dep`, `depends_on`, `calls*0..3` |
| `msg_sim` | error words vs. commit message and changed function names | list comprehension over `split()` |
| `history` | function blamed in resolved, resembling incidents | `resembles`, `likely_caused_by` |
| `sequence` | candidate is in the deployment before the signature first appeared | `first_seen`, deployment ordering |
| `centrality` | how central the changed code is | **`algo.pageRank`** over `Function`/`calls`, normalized to the top node |
| `blast` | breadth of the change (penalty) | `calls*1..3` callers |

Verdicts: `confident` needs a stack-level link (`path` or `dependency`) and a score margin;
endpoint/service-only evidence is `ambiguous`; no function-level evidence is `no_code_evidence`.

FalkorDB 6.0.2 notes: `shortestPath` must appear in `WITH`/`RETURN`; traversal patterns are not
allowed inside `exists()`; available algorithms include `algo.pageRank`, `algo.betweenness`,
`algo.BFS`, `algo.SPpaths`, `algo.WCC`, `algo.labelPropagation`.

## Agent trace graph (schema v2/v3)

`(Incident)-[:investigated_by]->(Investigation)-[:step {n}]->(ToolCall)-[:cited]->(node)`,
`(Fix)-[:reverts]->(Commit)`, `(Incident)-[:fixed_by]->(Fix)-[:verified_by]->(VerificationRun)`,
`(Developer)-[:resolved]->(Incident)`.

## Query cookbook

Worked examples, each run against the S1 fixture graph (`GraphBuilder.s1_payment_validation()`
plus two ranked candidates and one resolved, similar incident with its revert fix) on FalkorDB
6.0.2; the result shown is what that graph returned. Run them in the FalkorDB Browser
(`http://localhost:3001`, graph `cd_<owner>_<name>`), with `redis-cli -p 6380 GRAPH.RO_QUERY
cd_<owner>_<name> "<cypher>"` (inline the parameters), or from Python with
`GraphStore("<owner>/<name>").read(cypher, params)`. Read-only: use `GRAPH.RO_QUERY`/`read()`.
Parameters: `$inc = 'inc-1'`, `$fn = 'payments:src/payment/validation.js:validatePayment'`.

**What is in the graph?**

```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC
```
`File 3, Commit 2, Function 2, Incident 2, Deployment 1, Developer 1, Endpoint 1, Environment 1, Error 1, Fix 1, SchemaMeta 1, Service 1`

**Which commits shipped in the deployment that triggered the incident?** (core query 1)

```cypher
MATCH (d:Deployment)-[:triggers]->(i:Incident {id: $inc})
MATCH (d)-[:includes]->(c:Commit)
RETURN d.id, c.sha, c.message ORDER BY c.ts
```
`dep-842 n0000001 "docs"` · `dep-842 c0ffee01 "Refactor payment validation"`

**Where was the error thrown?** (stack frames mapped to functions, innermost first)

```cypher
MATCH (i:Incident {id: $inc})-[:evidenced_by]->(e:Error)-[t:thrown_in]->(f:Function)
RETURN e.fingerprint, f.id, t.frame_depth ORDER BY t.frame_depth
```
`fp-undefined-currency payments:src/payment/validation.js:validatePayment 0` ·
`fp-undefined-currency payments:src/routes.js:handlePayment 1`

**How far is each suspect's changed code from the failure?** (core query 2; FalkorDB needs
`shortestPath` in `WITH`/`RETURN`)

```cypher
MATCH (i:Incident {id: $inc})-[:evidenced_by]->(:Error)-[:thrown_in]->(t:Function)
MATCH (i)-[:candidate]->(c:Commit)-[:modifies_fn]->(f:Function)
WITH c, f, t, shortestPath((f)-[:calls*0..6]-(t)) AS p
RETURN c.sha, f.id, t.id, length(p) AS hops ORDER BY hops
```
`c0ffee01 ...validatePayment -> ...validatePayment 0` · `c0ffee01 ...validatePayment -> ...handlePayment 1`
(the docs-only commit changes no function, so it has no path)

**Ranked suspects as persisted by the scorer**

```cypher
MATCH (i:Incident {id: $inc})-[r:candidate]->(c)
RETURN r.rank, c.sha, r.score ORDER BY r.rank
```
`1 c0ffee01 0.91` · `2 n0000001 0.12`

**Blast radius: who calls the changed function, and which endpoints does that reach?** (core query 3)

```cypher
MATCH (f:Function {id: $fn})<-[:calls*0..3]-(caller:Function)
OPTIONAL MATCH (caller)-[:handles]->(e:Endpoint)
RETURN DISTINCT caller.id, e.id ORDER BY caller.id
```
`...validatePayment null` · `...handlePayment payments:POST /payments`

**Has this happened before, and what fixed it?** (core query 5)

```cypher
MATCH (i:Incident {id: $inc})-[r:resembles]->(o:Incident {status: 'resolved'})-[:fixed_by]->(x:Fix)
RETURN o.id, r.similarity, r.basis, x.id, x.kind ORDER BY r.similarity DESC
```
`old-1 0.8 ["thrown_in", "error_family"] fix-1 revert`

**Hotspot history: every incident that blamed a function** (core query 6)

```cypher
MATCH (i:Incident)-[:likely_caused_by|candidate]->(c:Commit)-[:modifies_fn]->(f:Function {id: $fn})
RETURN DISTINCT i.id, c.sha
```
`inc-1 c0ffee01`

**Which function handles each endpoint?**

```cypher
MATCH (f:Function)-[:handles]->(e:Endpoint) RETURN e.id, f.id ORDER BY e.id
```
`payments:POST /payments payments:src/routes.js:handlePayment`

Production equivalents with bounds and timeouts live in `graph/queries.py` (core queries 1-7)
and `investigate/features.py` (one query per ranking feature; the UI shows each next to its score).
