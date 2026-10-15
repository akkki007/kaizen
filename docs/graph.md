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
