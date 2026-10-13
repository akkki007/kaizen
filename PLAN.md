# Kaizen — Refined Project Plan

Status: **planning (v2)** — rescoped for the Oct 18 deadline, team of 4, production-grade, real GitHub, OpenAI. No code written yet. Sources: `Idea.md` + architecture diagram.

---

## 0. Constraints & decisions (v2 — supersedes v1 defaults)

| Decision | Value |
|---|---|
| Deadline | **2026-10-18** (8 days from kickoff on 2026-10-10) |
| Team | 4 people → 4 parallel workstreams (§12) |
| Quality bar | **Production system, not a demo**: authn/authz, secrets handling, migrations, CI, tests, self-observability, deployable, backed up, security-reviewed, documented |
| Language | **Python 3.12** for core, agent harness and eval harness; TypeScript only for the UI and the demo target app |
| Source of truth for code | **Real GitHub** — a GitHub App (webhooks + installation tokens) and GitHub Actions (build, deploy, scheduled health check, fix verification) |
| LLM | **OpenAI** (model is configuration, never hard-coded) |
| UI | A proper product UI (Next.js), not a notebook/Graph-browser embed |

Consequence: the "local-first / simulated events" shortcut from v1 is dropped. Real webhooks, a real deployed demo target and a real public endpoint are in scope. The one-event-contract rule (§7) still holds — it is what keeps GitHub, Actions and the demo target decoupled from the core and makes the eval harness able to *replay* events deterministically.

---

## 1. What we are building (one paragraph)

An agent that, when a deployed service becomes unhealthy, (1) maps the incident to the deployment and the commits inside it, (2) walks a FalkorDB graph of code → deployment → runtime evidence to **rank candidate root causes with an explainable score**, (3) explains the top candidate with evidence the user can click through, (4) proposes a fix (revert / patch / config), (5) **verifies it in an isolated environment** against the same failing traffic, and (6) writes the incident + outcome back into the graph so the next similar incident is diagnosed faster.

**The claim we must be able to prove:** graph-based investigation ranks the true culprit first more often than flat-log / time-based approaches. Everything in this plan is shaped so that claim is *measurable*, not just demo-able.

---

## 2. Gaps in the current idea & how this plan resolves them

| # | Gap in Idea.md | Resolution |
|---|---|---|
| 1 | No ground truth, so "root-cause accuracy" can't be computed | Build a **seeded-incident benchmark** (≈10 scenarios, each with a known culprit commit/function + distractor commits). Phase 5. |
| 2 | "AI ranks root causes" is vague and unauditable | **Ranking is deterministic and graph-feature-based** (weighted score, §6). The LLM explains and writes fixes; it does *not* choose the ranking. |
| 3 | No baseline for "Graph Advantage" | Implement 2 baselines (latest-commit; time-window + keyword over logs/diffs) and run them on the same benchmark. |
| 4 | Graph model lacks the link that makes causality work: *which function changed* and *which function threw* | Add `Commit -[:modifies]-> Function` (hunk→function mapping) and `Error -[:thrown_in]-> Function` (stack frame→function mapping). Add `Endpoint` node. |
| 5 | `Previous Incident` as a separate label duplicates `Incident` | Single `Incident` label with `status`; "previous" = resolved incident reached via `resembles`. |
| 6 | Raw metrics/logs in a graph won't scale or query well | Raw telemetry in Postgres; graph stores **summaries** (`Metric` = window aggregate, `Error` = deduped signature). |
| 7 | Function identity across commits (renames/moves) unaddressed | Stable key `service:path:qualifiedName`; use `git diff -M` rename detection; file-level fallback. Called out as a risk (§10). |
| 8 | Safety of auto-remediation unstated | Fixes **never touch prod**. Verification runs in an isolated compose "staging"; a human approves any rollback/merge. |
| 9 | LLM hallucination risk in the explanation | Every claim in the explanation must cite graph node IDs; a **validator rejects** explanations citing non-existent evidence. |
| 10 | Real GitHub/Actions/cloud wiring is the most fragile part of a hackathon | **v2: real GitHub App + Actions from day one**, but all sources still normalize to one event contract so the eval harness can replay recorded events deterministically. |
| 11 | Pre-deployment risk scoring (Idea §9) | Explicitly **post-MVP**, but the schema/queries are designed so it reuses the same graph (Phase 7, stretch). |

---

## 3. Scope — Must / Should / Could

**Must (MVP, the demo does not exist without these)**
- Demo app: 3 services, git history with seeded bugs, deploy script
- Code ingestion → graph (files, functions, call edges, deps, commits, hunks→functions)
- Deployment + runtime-evidence ingestion (logs, stack traces, request metrics)
- Incident detection (health checker with baseline comparison)
- Graph-based ranking with evidence + confidence
- LLM explanation with citation validation
- Fix proposal (revert + LLM patch) and **verification in staging**
- Incident memory + one scenario where memory visibly helps
- Evaluation harness with graph vs. baselines

**Should**
- Minimal web UI: incident page (ranked candidates, evidence path rendered as a graph, verification result), metrics page for the benchmark
- Blast-radius query (downstream services/functions)
- Real GitHub integration (webhook + API read) behind the same event contract

**Could (post-MVP)**
- Pre-deployment PR risk scoring
- Embedding-based incident similarity (FalkorDB vector index)
- OpenTelemetry ingestion, multi-language analyzers, multi-tenant graphs

---

## 4. Architecture

```
 Demo app (3 services, docker compose "prod")       Git repo (local; GitHub adapter later)
   │ JSON logs / stack traces / request metrics        │ commits, diffs
   ▼                                                   ▼
 ┌───────────────────────── Ingestion layer ──────────────────────────┐
 │ git_ingest   code_analyzer (tree-sitter)   deploy_events   telemetry│
 └───────────────┬──────────────────────────────────┬─────────────────┘
                 ▼                                  ▼
          FalkorDB (graph)                    SQLite (raw telemetry)
                 ▲                                  ▲
 ┌───────────────┴────────── Core ────────────────────┴───────────────┐
 │ detector → investigator (graph queries + scorer) → explainer (LLM)  │
 │            → remediator (revert / LLM patch) → verifier (staging)   │
 │            → memory writer (incident → graph)                       │
 └─────────────────────────────┬───────────────────────────────────────┘
                               ▼
                    FastAPI  ──►  Web UI (incident view, eval view)
```

Design rules:
1. **One event contract** (§7). Sources (local scripts today, GitHub tomorrow) only produce events; the core never knows where they came from.
2. **Idempotent ingestion** (`MERGE` on stable keys) so replays and re-runs are safe.
3. **Deterministic pipeline, LLM at the edges.** The investigator is plain code + graph queries. The LLM is used for explanation, fix generation, and (optionally) a tool-using "ask the graph" chat. This makes the eval reproducible.
4. Every pipeline stage writes its output to the graph, so an investigation is itself auditable data.

---

## 5. Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Core backend | **Python 3.12, FastAPI, pydantic v2**, `uv` for deps | Typed contracts end to end |
| Graph | **FalkorDB** (openCypher) | Reasoning layer; per-repo graph isolation |
| App state & raw telemetry | **PostgreSQL** + Alembic migrations | Replaces v1's SQLite (production) |
| Background jobs | **ARQ on Redis** | Ingestion, investigation, verification runs; retries + idempotency |
| Code analysis | **tree-sitter** (JS/TS first) | Functions, call edges, routes, imports |
| GitHub | **GitHub App** (webhooks, installation tokens) + **GitHub Actions** | push, pull_request, deployment, deployment_status, workflow_run |
| LLM / agent harness | **OpenAI Python SDK** (tool calling + structured outputs), thin in-house harness | Budgets, tracing, replay cache, guardrails; model via `OPENAI_MODEL` |
| Demo target | **ShopLite** (Node/Express, 3 services) in its own real GitHub repo, deployed to a real host | Realistic seeded bugs via real PRs |
| Verification env | GitHub Actions workflow on the fix branch: tests → ephemeral docker-compose staging → traffic replay → report back | Never touches prod |
| UI | **Next.js (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query + Cytoscape.js**, OpenAPI-generated client, Playwright | Graph evidence view is the signature screen |
| Quality | ruff, mypy, pytest, pre-commit, CI on every PR | |
| Self-observability | structured JSON logs, OpenTelemetry traces/metrics, LLM cost/latency logging | |
| Deployment of Kaizen | docker compose on a VM behind Caddy/TLS, public webhook endpoint, backups | Hosting provider TBD (issue tracks it) |

---

## 6. Graph model (refined)

### Nodes
`Repository, Branch, PullRequest, Commit, Developer, File, Function, Dependency, Service, Endpoint*, Build, Deployment, Environment, Incident, Error, Metric, Trace, Fix, Test` (`*` = new). `Previous Incident` removed (see Gap 5).

Key properties
- `Function`: `id = service:path:qualifiedName`, `start_line, end_line, sha_seen`
- `Commit`: `sha, message, ts, files_changed, loc_added/removed`
- `Deployment`: `id, sha_range [from..to], env, started_at, finished_at, status`  ← a deployment can contain **several commits**; ranking is among them
- `Error`: **signature node** — `fingerprint` (hash of exception type + top app frame + normalized message), `first_seen, last_seen, count`
- `Metric`: window aggregate — `name, window_start, window_end, value, baseline, z_score`
- `Incident`: `status (open|investigating|resolved)`, `detected_at`, `resolved_at`, `confirmed_root_cause`, `resolution_type`
- `Fix`: `kind (revert|patch|config|dependency)`, `diff`, `verified (bool)`

### Relationships
Original set from Idea.md, plus:

```
(Commit)-[:modifies {added, removed}]->(File)
(Commit)-[:modifies_fn {added, removed}]->(Function)    // hunk line-range ∩ function range
(Function)-[:handles]->(Endpoint)                         // route registration
(Endpoint)-[:belongs_to]->(Service)
(Service)-[:calls]->(Service)                             // from trace/request-id logs
(Error)-[:thrown_in {frame_depth}]->(Function)            // from stack frames
(Error)-[:observed_on]->(Endpoint)
(Incident)-[:candidate {rank, score, features}]->(Commit) // all ranked candidates, not just #1
(Incident)-[:likely_caused_by]->(Commit)                  // top-1; later promoted to confirmed
(Incident)-[:resembles {similarity, basis}]->(Incident)
(Fix)-[:reverts]->(Commit)
```

### Constraints / indexes
Unique on `Commit.sha`, `Function.id`, `File.path+service`, `Deployment.id`, `Error.fingerprint`, `Incident.id`. Range index on `Commit.ts`, `Deployment.started_at`.

### Core queries we must be able to run (these are the acceptance tests for the schema)
1. Incident → deployment → commits in that deployment
2. Commits → changed functions → **shortest call path** to the function in the error's stack (`thrown_in`)
3. Changed function → downstream `calls*1..N` → affected endpoints/services (blast radius)
4. Changed dependency → functions that `depends_on` it → affected services
5. Incident → structurally similar resolved incidents → their `Fix`
6. Function → all past incidents that ever blamed it (hotspot history)

---

## 7. Event contract (source-agnostic)

```jsonc
// POST /events/commit      {repo, sha, parent, author, ts, message, files:[{path,status,hunks:[{start,end}]}]}
// POST /events/deployment  {id, env, service(s), sha_from, sha_to, started_at, finished_at, status, build_id}
// POST /events/telemetry   {service, ts, kind: "log"|"error"|"request", ...}   // batched
// POST /events/health      {service, env, ts, ok, latency_ms, status_code}
```
Local deploy script emits these exactly as a GitHub Actions `deployment_status` workflow would.

---

## 8. Investigation algorithm (the heart of the project)

1. **Detect** — per service/endpoint, compare rolling error rate & p95 vs. a baseline window; open an `Incident` when `z > threshold` for `k` consecutive windows. Record `onset_ts` (first anomalous window, not detection time).
2. **Scope deployments** — deployments to the affected env within a lookback window; candidates = all commits in `sha_from..sha_to` of those deployments (+ config/dependency changes).
3. **Evidence** — top Error signatures in the anomaly window → `thrown_in` functions, endpoints, services.
4. **Features per candidate commit**

| Feature | Meaning | Range |
|---|---|---|
| `temporal` | `exp(-(onset − deploy_finished)/τ)`, 0 if onset precedes deploy | 0–1 |
| `path` | graph distance from any `modifies_fn` function to a `thrown_in` function via `calls` (0 = same function, 1 = direct caller/callee, … ) → `1/(1+d)` | 0–1 |
| `endpoint` | candidate changed a function that `handles` the failing endpoint (directly/transitively) | 0–1 |
| `dependency` | candidate changed a `Dependency` used by the failing path | 0/1 |
| `msg_sim` | overlap between error message tokens and diff identifiers/strings | 0–1 |
| `history` | candidate's functions/deps appear in resolved incidents resembling this one | 0–1 |
| `sequence` | first deployment after which the signature appeared (vs. later deployments that merely carry it) | 0–1 |
| `blast` | breadth of the change (used as a mild *penalty* for huge commits to avoid "blame the biggest diff") | 0–1 |

5. **Score** = Σ wᵢ·featureᵢ, weights fitted on a **training split** of benchmark scenarios, reported on a held-out split (avoid tuning on the test set).
6. **Confidence & abstention** — softmax-style normalization over candidates; if top-1 margin is small, output "ambiguous, here are the top 3" instead of a confident claim. (Directly supports the *False Attribution Rate* metric.)
7. **Explain** — LLM receives a structured evidence bundle (candidates, feature values, the actual graph path, diff hunks, similar incident + its fix) and must return structured JSON with a citation per claim; validator checks cited IDs exist.
8. **Remediate** — candidate fixes, ordered by safety: (a) `git revert` of top commit (deterministic), (b) LLM patch constrained to the changed function + the regression test, (c) config/dependency correction.
9. **Verify** — apply fix on a temp branch/worktree → run unit tests (+ generated regression test) → deploy to staging compose → replay the **recorded failing request mix** → compare error rate/latency with the failing run and the healthy baseline. Pass = back within baseline tolerance. Failed fix → try next remediation, record the failure.
10. **Remember** — persist incident, ranked candidates, chosen fix, verification outcome, human confirmation; compute `resembles` edges (§9).

---

## 9. Incident memory & similarity

Similarity v1 (no embeddings, explainable): weighted Jaccard over
`{error fingerprint family, thrown_in function, changed functions, changed dependencies, affected service/endpoint}`, returned with `basis` so the UI can say *why* two incidents resemble each other. Embedding-based similarity via a FalkorDB vector index is a stretch goal, evaluated against v1.

Cold start: seed the graph with 2–3 historical resolved incidents in the demo repo's history so memory has something to retrieve.

---

## 10. Demo application & benchmark scenarios

**Demo app "ShopLite"** — `gateway` → `orders` → `payments`; shared lib `money-utils` (a Dependency); realistic request mix via a load generator; structured JSON logging with request IDs; Dockerized; ~40–60 commits of plausible history including noise commits.

**Benchmark scenarios** (each = a git branch/commit set + deploy script + ground-truth label)

| # | Scenario | What it tests |
|---|---|---|
| S1 | The Idea.md example: payment validation refactor throws on a valid input | Core path (commit→function→endpoint→error) |
| S2 | Same file, **two functions changed**, only one is on the failing path | Function-level precision vs. file-level blame |
| S3 | Deployment bundles **5 commits**, one culprit | Ranking among candidates |
| S4 | Bug in `orders`, symptom appears in `payments` | Cross-service causality / blast radius |
| S5 | Dependency bump in `money-utils` breaks rounding | Dependency edges |
| S6 | Config/env var change, no code diff | Non-code candidate; honest "not a code bug" output |
| S7 | Old commit, but bug triggered by new traffic pattern (error begins *later*) | Temporal feature should **not** blindly dominate; abstention |
| S8 | Recurrence of S1's dependency pattern in a different service | Memory visibly helps (faster/more confident, reuses fix) |
| S9 | Refactor that renames/moves the culprit function | Rename-handling weakness (expected partial failure; reported honestly) |
| S10 | Noisy: unrelated flaky endpoint errors present at the same time | False-attribution rate |

Split: S1, S3, S5, S8 = tuning; the rest = held-out test. Randomize seeds / generate variants programmatically (change which function, which service) to get ~30 runs instead of 10.

---

## 11. Evaluation

| Metric (from Idea.md) | Definition | Target (MVP) |
|---|---|---|
| Root-cause accuracy | culprit commit ranked #1 / in top-3 | top-1 ≥ 70%, top-3 ≥ 90% |
| Function-level accuracy | culprit *function* identified | ≥ 60% |
| Investigation time | `incident.detected_at → hypothesis_ready` | < 30 s (excl. LLM) |
| False attribution rate | confident top-1 ≠ culprit | < 10% (abstention counts as not-false) |
| Blast-radius accuracy | precision/recall of affected services/endpoints vs. ground truth | P/R ≥ 0.8 |
| Fix verification rate | proposed fixes passing staging verification | ≥ 60% (revert ≈ 100%) |
| **Graph advantage** | metrics above for **B0 latest-commit**, **B1 time-window + keyword over logs/diffs**, **G graph** on identical scenarios | G > B1 > B0 with the gap reported |

Targets are initial hypotheses; we report actuals even if we miss them. An ablation (graph without `path`/`history` features) shows which edges earn their keep.

---

## 12. Phased roadmap (Oct 10 → Oct 18, team of 4)

Milestones (GitHub milestones with these due dates):

| Milestone | Due | Goal | Exit criteria |
|---|---|---|---|
| **M1 — Foundations & Graph** | Oct 12 | Contracts, skeletons, graph schema, GitHub App, demo target deployed | Real push to ShopLite → commits/functions/deployment appear in FalkorDB; CI green; UI shell renders |
| **M2 — Detect & Rank** | Oct 14 | Runtime evidence in, incidents detected, culprit ranked | Seeded bug (S1) → incident auto-opened → ranked candidates with score breakdown via API; dashboard shows it |
| **M3 — Explain, Fix, Verify, Remember** | Oct 16 | Close the loop | S1 end-to-end: explanation w/ validated citations → revert/patch PR → verified on staging → stored; S8 reuses memory; auth works |
| **M4 — Evaluation, Hardening, Demo** | Oct 18 | Prove it and ship it | `make eval` table (graph vs baselines); full-loop E2E test in CI; security review; deployed; demo + docs |

Suggested workstreams (issues are **unassigned**; this is a suggestion for the team to adopt):

| WS | Focus | Epics |
|---|---|---|
| A | Platform, GitHub integration, code ingestion | Platform & Infra; GitHub & Code Ingestion |
| B | Graph, runtime telemetry, detection, demo target | Graph; Runtime & Detection; Demo Target |
| C | Investigation, agent harness, remediation, eval | Investigation; Agent Harness; Remediation/Verification/Memory; Evaluation |
| D | UI & product experience (+ demo polish/docs) | Web UI; Docs & Demo |

Priorities: **P0** = required for Oct 18, **P1** = should, **P2** = stretch / backlog.
**Scope warning:** 8 days is very tight for a production-grade system. Cut order if we slip: (1) P2 items, (2) LLM-patch remediation (keep revert), (3) UI memory/eval pages, (4) multi-tenant polish. Do **not** cut: the evaluation harness, security basics, or the full-loop E2E test.

---

## 13. Proposed repo layout

```
causaldeploy/
  docker-compose.yml            # falkordb, demo services (prod/staging), core api
  demo/shoplite/{gateway,orders,payments,lib/money-utils}
  demo/scenarios/               # S1..S10 definitions + ground truth + generator
  core/
    causaldeploy/
      ingest/   (git, analyzer, deploy, telemetry)
      graph/    (schema.cypher, queries/*.cypher, client.py)
      detect/   investigate/ (features.py, scorer.py)
      explain/  remediate/   verify/   memory/
      api/      (FastAPI)
    tests/
  eval/         (runner, baselines, metrics, reports)
  ui/           (Next.js)
  docs/         (architecture.md, schema.md, demo-script.md)
```

---

## 14. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Function renames/moves break identity | High | `git diff -M`, signature matching, file-level fallback, S9 reports it honestly |
| Hand-picked demo ⇒ "overfit" criticism | High | Held-out scenarios, generated variants, baselines |
| LLM output nondeterminism in eval | Med | LLM never ranks; temperature 0 + cached outputs for eval runs |
| Staging verification flaky (timing, ports) | Med | Deterministic traffic replay, health-wait loops, retry once, recorded fallback |
| FalkorDB feature gap (specific Cypher construct) | Low–Med | Phase 0 spike; keep ranking features computable via simple queries + Python |
| Overbuilding GitHub integration | Med | Local-first; adapter is Phase 7 |
| Auto-fix safety perception | Med | Never touches prod; human approval gate; revert preferred |
| Scope creep into pre-deployment intelligence | Med | Explicit post-MVP |

---

## 15. Definition of done (MVP)

1. `docker compose up` + one script brings up the demo and the core.
2. Injecting S1 triggers: detection → ranked candidates with evidence → explanation → verified fix → memory entry, with **no manual steps**.
3. S8 shows the previous incident retrieved and its fix reused.
4. `make eval` prints the metrics table including the graph-vs-baselines comparison.
5. A 3-minute demo script and a README exist.

---

## 16. Decisions & remaining open items

Decided (see §0): Oct 18 deadline, team of 4, production-grade, Python harness, real GitHub, OpenAI, full UI.

Still open (tracked inside the relevant issues, not blocking planning):
1. Hosting provider/VM + domain for Kaizen and for ShopLite (needed by M1 for real webhooks).
2. Which OpenAI model(s) and a monthly/per-run budget cap.
3. GitHub org/repo name for ShopLite and who owns the GitHub App registration.
