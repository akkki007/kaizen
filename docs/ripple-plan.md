# Ripple: plan and architecture

Status: **plan only (pre-event)**. Graph Hacks rule 8 allows ideas, notes, graph-model sketches and diagrams before the hackathon. All code is written after Oct 15, 12:01 AM IST, in a new public repo.

**One line:** paste a pull request from a public repo, and a team of agents walks a FalkorDB graph of code, history and people to explain what the change can break, what broke there before, and who should review it. Every claim cites a graph path.

**Origin:** evolved from our CausalDeploy design (`kaizen`). It keeps the same graph core (commit → function → calls) and moves from "after a deploy broke" to "before merge". The README will say so openly.

**Hackathon:** Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB), Oct 15–18, 2026. Tracks: 01 (primary), 02, 03.

---

## 1. What carries over from Kaizen (knowledge, not code)

| Kaizen learning | Used in Ripple as |
|---|---|
| Hunk → function mapping (innermost nested function, renames, merges not double-counted) | Change ingestion |
| Deterministic, explainable scoring features | `risk_score` tool, whose breakdown the agents interpret |
| Citation validator (claims must cite real graph IDs) | Writer agent guardrail |
| Graph vs. baselines evaluation | Backtest on real history |
| Evidence-path graph UI | Blast-radius view |

## 2. Why it fits the hackathon

| Track | How Ripple covers it |
|---|---|
| 01 Agents that act on connected data | Agents choose graph tools over MCP, reason over multiple hops (change → calls → endpoint/test/past bug) and act: a review comment and reviewer suggestions, each explained by a path |
| 02 Agent memory and coordination | Agents share state as `Finding` nodes in the graph; reviews, outcomes and lessons persist across sessions; one graph per repo (tenant isolation) |
| 03 Company brain | Who knows and owns which code; decisions traced to PRs, reviews and issues; answers with sources |

**FalkorDB features shown:** Cypher multi-hop traversal, built-in algorithms (PageRank, betweenness, shortest path, community detection), MCP tool server, many isolated graphs in one instance.

---

## 3. Architecture

```
                         ┌──────────────────────────── UI (Next.js) ───────────────────────────┐
                         │  PR input · Review page (risk, blast-radius graph, precedents,       │
                         │  reviewers, citations) · Repo hotspots · Backtest results            │
                         └───────────────────────────────┬──────────────────────────────────────┘
                                                         │ REST + SSE (progress)
┌──────────────────────── API (FastAPI) ─────────────────┴──────────────────────────────────────┐
│ POST /repos/{o}/{n}/ingest   POST /reviews {pr_url}   GET /reviews/{id}   GET /subgraph      │
└───────┬─────────────────────────────────────┬───────────────────────────────────────────────┬─┘
        │ jobs (Redis queue)                  │ run review                                    │
┌───────▼──────────── Ingestion ──────┐  ┌────▼──────────────── Agents ──────────────────┐    │
│ git clone → commits, hunks          │  │ Orchestrator                                   │    │
│ tree-sitter → functions, calls,     │  │  ├─ Analyst    (blast radius, centrality, risk)│    │
│   imports, tests, endpoints         │  │  ├─ Historian  (precedents, past outcomes)     │    │
│ GitHub API → PRs, reviews, issues   │  │  ├─ People     (experts, ownership areas)      │    │
│ Miners → reverts, SZZ bug origins   │  │  └─ Writer     (comment + citation validator)  │    │
│ Post-pass → PageRank, communities   │  │ Shared state = Findings written to the graph   │    │
└───────┬─────────────────────────────┘  └────┬───────────────────────────────────────────┘    │
        │ Cypher upserts                      │ MCP tool calls                                 │
        │                         ┌───────────▼──────── MCP server (tools) ─────────────┐      │
        │                         │ blast_radius · centrality · risk_score · precedents │      │
        │                         │ experts · explain_path · similar_reviews · cypher(ro)│      │
        │                         └───────────┬─────────────────────────────────────────┘      │
┌───────▼─────────────────────────────────────▼─────────────────────────────────────────────▼──┐
│ FalkorDB: one graph per repo  `ripple:{owner}/{name}`                                          │
│ code layer · history layer · people layer · memory layer   (all edges carry `ts` for as-of)    │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
        ▲
┌───────┴──────── Eval harness ─────────┐
│ Time-split backtest: graph risk vs     │
│ baselines; reviewer hit@3              │
└────────────────────────────────────────┘
```

**Stack**

| Part | Choice |
|---|---|
| Ingestion, agents, MCP server, API | Python 3.12 (official `mcp` SDK, FastAPI) |
| Parsing | tree-sitter, one language for the MVP |
| Jobs | Redis queue |
| LLM | OpenAI; the model is configuration, never hard-coded |
| UI | Next.js + Cytoscape |
| Database | FalkorDB |
| Deploy | One VM with Docker Compose |

---

## 4. Graph model

**Nodes**

| Layer | Nodes |
|---|---|
| Code | `Repo`, `Module`, `File`, `Function`, `Test`, `Endpoint`, `Package` |
| History | `Commit`, `PR`, `Issue`, `Release` |
| People | `Person` |
| Memory | `Review` (one Ripple run), `Finding`, `Outcome`, `Lesson` |

**Relationships**

```
(File)-[:DEFINES]->(Function)           (Function)-[:CALLS]->(Function)
(File)-[:IMPORTS]->(File|Package)       (Test)-[:COVERS]->(Function)      (Function)-[:HANDLES]->(Endpoint)
(Commit)-[:MODIFIES {added,removed}]->(Function)   (Commit)-[:PART_OF]->(PR)
(Commit)-[:REVERTS]->(Commit)           (Commit)-[:FIXES]->(Issue)
(Commit)-[:INTRODUCED_BUG {method:"szz"|"revert"}]->(Issue|Commit)
(Person)-[:AUTHORED]->(Commit)          (Person)-[:REVIEWED {state}]->(PR)   (Person)-[:COMMENTED]->(PR)
(Review)-[:OF]->(PR)   (Review)-[:HAS]->(Finding)-[:CITES]->(any node)
(Outcome)-[:FOR]->(Review)   (Lesson)-[:ABOUT]->(Function|Module)
```

**Stored algorithm results** (refreshed after each ingest): `Function.pagerank`, `Function.betweenness`, `Function.community`.

**Stable keys:** `Function` = `path:qualifiedName`, `Commit` = SHA, `PR` = number, `Person` = GitHub login.

**No future leakage:** every history edge carries `ts`, and every tool takes `as_of`. A backtest of a PR from March sees only the graph as it was in March.

---

## 5. Agent tools (MCP server)

| Tool | Does | FalkorDB feature shown |
|---|---|---|
| `blast_radius(pr, depth)` | Functions, endpoints and tests reachable from the changed functions, with paths | Variable-length traversal |
| `centrality(functions)` | How central the touched code is | PageRank, betweenness |
| `risk_score(pr, as_of)` | Deterministic score + breakdown: centrality, blast size, breakage history, churn, author familiarity, test proximity | Multi-hop aggregation |
| `precedents(functions, as_of)` | Past reverts and bug origins in this code, with their fixes | History traversal |
| `experts(functions)` | People ranked by authored, reviewed and fixed work on this code; ownership area | Community detection |
| `explain_path(a, b)` | Shortest path between two nodes, for citations | `shortestPath` |
| `similar_reviews(pr)` | Past Ripple reviews on overlapping code, with their outcomes | Memory layer |
| `cypher(query)` | Read-only escape hatch, logged | Raw Cypher |

All tools are read-only except the memory writes, which are made by the agents (`Finding`) and the outcome job (`Outcome`, `Lesson`).

---

## 6. Agent flow

1. The **Orchestrator** creates a `Review` node for the PR and runs the Analyst, Historian and People agents in parallel.
2. Each agent picks its own tools over MCP and writes `Finding` nodes that cite graph nodes. The findings in the graph are the shared state between agents (Track 02).
3. The **Writer** reads the findings back from the graph and drafts the comment. A validator rejects any claim citing an ID that isn't in the graph, and the Writer retries.
4. **Memory:** when a PR is later reverted or linked to a bug, an `Outcome` attaches to the earlier review. `Lesson` nodes (e.g. "changes in `auth/` need someone from community 4") feed future reviews (episodic and procedural memory).

**Output of one review**
- Risk level with the score breakdown
- Blast radius: the functions, endpoints and tests affected, each with its path
- Precedents: past breakages in this code and how they were fixed
- Suggested reviewers, with the reason for each
- Comment text with clickable citations

---

## 7. Evaluation (the claim to prove)

**Claim:** graph signals predict risky changes and the right reviewers better than flat heuristics, on real history.

| Measure | Method | Baselines |
|---|---|---|
| Risk ranking | Time-split the repo's PRs. Label a PR risky if it was later reverted or traced to a bug (explicit reverts are clean labels; SZZ is noisy, so it's reported separately). Report precision@10 and AUC | Lines changed; files changed; touches recently changed files |
| Reviewer suggestion | hit@3 against the actual reviewers | Top committer to these files |
| Ablation | Remove the centrality or history features | Full graph score |

Numbers are reported as they come out, even if they're weak.

---

## 8. UI pages

1. **Home:** pick a repo, paste a PR URL; live agent progress over SSE.
2. **Review:** risk summary with score breakdown, blast-radius graph with paths highlighted, precedents with their fixes, suggested reviewers with why, and the comment with clickable citations.
3. **Repo hotspots:** code that is both central and has a history of breaking (PageRank × breakage).
4. **Backtest:** the evaluation table.

---

## 9. Repo layout (new public repo, created after Oct 15)

```
ripple/
  core/ingest/      git, treesitter, github, miners (revert, szz), algos
  core/graph/       schema, upsert, queries.cypher (listed in README per Rule 10)
  core/mcp/         tool server
  core/agents/      orchestrator, analyst, historian, people, writer, validator
  core/api/         FastAPI + jobs
  eval/             backtest, baselines, report
  ui/               Next.js
  docker-compose.yml  Makefile  README.md (graph model, Cypher, AI-use disclosure)
```

---

## 10. Schedule (team of 4)

| | A: ingestion | B: graph + tools | C: agents | D: UI + delivery |
|---|---|---|---|---|
| **Before Oct 15** (allowed) | Pick the target repo (count reverts/fix PRs) | Finalise this schema sketch | Agent roles, tool contracts | Wireframes, deploy target |
| **Day 1 (Oct 15)** | Clone, commits, hunks → functions; PRs/reviews via API | Schema, indexes, upserts; MCP server + `cypher` | Orchestrator + Analyst on one PR | Shell, PR input, progress view |
| **Day 2 (Oct 16)** | Calls, imports, tests; revert miner | `blast_radius`, `centrality`, `explain_path`, `risk_score` | Writer + citation validator end-to-end | Review page + graph view |
| **Day 3 (Oct 17)** | SZZ miner; as-of timestamps everywhere | `precedents`, `experts`, communities, `similar_reviews` | Historian + People; memory write-back | Precedents/reviewers panels, hotspots |
| **Day 4 (Oct 18)** | Second repo (multi-graph) | Backtest harness + numbers | Hardening, prompt tuning | Deploy, README, demo video, blog post |

**Milestones**
- **End of day 1:** one real PR ingested; the Analyst returns a blast radius through MCP.
- **End of day 2:** full review end-to-end on one PR, with validated citations, in the UI.
- **End of day 3:** all four agents, memory write-back, first backtest numbers.
- **Day 4, 11:59 PM IST:** submission (repo, README, video, deployment, tracks).

**Cut order if behind:** second repo → SZZ (keep reverts) → hotspots page → `Lesson` memory.
**Never cut:** working end-to-end review, citations, backtest numbers, README graph model + Cypher.

---

## 11. Submission checklist (Rule 10)

- [ ] Public repo, created after Oct 15 12:01 AM IST
- [ ] README: setup, graph data model, the Cypher queries and algorithms used, AI-assistant disclosure, "evolved from CausalDeploy" note
- [ ] Demo video of the working product
- [ ] Live deployment (or full local setup instructions)
- [ ] Tracks named: 01, 02, 03
- [ ] Every teammate can explain their part (Rule 12)
- [ ] Only public data in the graph (Rule 6)
- [ ] Optional: blog post (side prize)

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| SZZ labels are noisy | Use explicit reverts as the main label; report SZZ separately |
| GitHub API rate limits | Authenticated token; a mid-size repo (a few thousand PRs) |
| Call graph quality across a large repo | One language; mid-size repo; file-level fallback when a function can't be resolved |
| Agents hallucinate | Citation validator; tools return IDs; deterministic `risk_score` |
| Weak backtest result | Report honestly; show the ablation and per-feature effects |
| Scope slip | Cut order above; day-2 end-to-end milestone |

---

## 13. Decisions still needed

1. **Target repo and language.** One language for tree-sitter, and a mid-size public repo with plenty of reverts and fix PRs. JavaScript/TypeScript plays to the team's ShopLite experience. Count candidates' reverts before Oct 15.
2. **Tracks:** enter all three, with 01 as primary.
3. **Judging criteria:** ask on the FalkorDB Discord, along with whether pre-event dataset repos are allowed.
4. **Owners:** one per workstream (A–D).
