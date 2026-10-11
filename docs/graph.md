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
