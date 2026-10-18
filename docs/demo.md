# Demo: scripted walkthrough (3-4 minutes)

**Story.** A deploy breaks `POST /payments`. Nobody opens a dashboard: Kaizen detects it, walks the
FalkorDB graph from the stack trace back to the commit that changed that function, and posts the
answer in Slack with the author and the evidence. Then it proposes the revert, verifies it, a person
approves, and the graph turns green again.

Everything runs on the public demo account (no GitHub account, no real repository changes). The
traffic of "Inject a problem" is simulated and labelled as such; detection, the graph queries, the
ranking and the Slack alert are the production code.

## Before recording (T-10 min)

| Step | Check |
|---|---|
| 1. Services healthy | `curl https://shoplite-demo.duckdns.org/readyz` -> `{"postgres":true,"redis":true,"falkordb":true}` |
| 2. Clean slate | open <https://kaizen.akkki.tech>, **Try the demo**, *Graph map* -> **Reset** (no red nodes, no incidents) |
| 3. Slack | the alerts channel is open in a second window; the last message is old |
| 4. Hard refresh | `Ctrl+Shift+R` so the latest UI is loaded (clear the site's cache if the old page appears) |

## Script

| Time | Screen | Say |
|---|---|---|
| 0:00-0:20 | Login page, **Try the demo** | "Kaizen watches a repository's deploys. This is ShopLite, three Node services. No account needed for the demo." |
| 0:20-0:50 | *Graph map*: services, functions and commits, one service at a time. Point at "FalkorDB · N functions · N commits · N edges" and open the **Powered by FalkorDB** queries | "Every function and commit is a node in FalkorDB. Calls, changes, endpoints are edges. This whole view is built from these Cypher queries, run live." |
| 0:50-1:10 | Click **Inject a problem** | "Now I simulate a bad deploy: failing traffic on `POST /payments`. I'm not telling Kaizen which commit did it." |
| 1:10-1:50 | The right panel steps tick: ingest, detect (Postgres), then three FalkorDB steps; the failing function turns red and pulses, the commit turns amber | "Detection is two failing 30-second windows over the baseline. From here it is the graph: the error maps to the function that threw, the deployment's commits come from the graph, and the ranking uses path distance, blast radius and PageRank. Only the failing function turned red." |
| 1:50-2:10 | Switch to Slack: the alert | "And nobody had to look: the team gets the culprit commit, the author, the function that changed versus the one that threw, the confidence, and a link." |
| 2:10-2:40 | Open the incident: suspects with the score breakdown; expand `path` to show its Cypher; toggle **graph off** | "The score is deterministic, not the model, and every number has its query. Without the graph, 'latest commit' or keyword search points elsewhere." |
| 2:40-3:10 | **Propose the revert (demo)**: diff of the real commit, inverted; staging verification passes; **Approve** | "The fix is a revert, verified before anyone merges. In the demo no pull request is opened; in production this is a real PR and only an approver can merge it." |
| 3:10-3:30 | Incident resolved; back to *Graph map*: green again | "Resolved, and the fix is written back into the graph so the next similar incident is recognised." |
| 3:30-4:00 | Numbers table below, or `docs/results/latest.html` | read the measured numbers |

## Real repository (optional second take)

To show it on a repository you own instead of the demo: sign in with GitHub, install the App on it,
tick it on `/connect` and press **Start monitoring**; the import shows commits, functions, services
and endpoints as they are read. Live failure detection then needs telemetry from the app (an ingest
key and the snippet on the repository page).

The earlier full scenario, ShopLite's S1 commit deployed for real with the revert PR on GitHub and the
S8 recurrence, is still valid; its script follows.

### Real-deploy script: before the demo (T-30 min)

| Step | Command / action | Check |
|---|---|---|
| 1. Services healthy | `curl https://shoplite-demo.duckdns.org/readyz` | `{"postgres":true,"redis":true,"falkordb":true}` |
| 2. Clean state + seeded history | on the target environment (dev or staging, never prod): `make demo-reset REPO=yashchandnani07/shoplite YES=1` (Windows: `.\scripts\demo-reset.ps1 -Yes`) | prints `demo state ready`; three `hist-*` resolved incidents |
| 3. Code graph present | `make backfill` (only if `--drop-graph` was used) | `/api/v1/repos/yashchandnani07/shoplite/backfill` shows `backfilled_at` |
| 4. ShopLite healthy on the base commit | ShopLite deploy workflow / health check green | no open incident in the UI |
| 5. Logged in as an approver | UI -> Sign in with GitHub | header shows your login, role approver |
| 6. Fallback ready | screen recording open in a tab; `ui` mock mode ready (`npm run dev:mock`, Windows: `$env:NEXT_PUBLIC_API_MOCK='1'; npx next dev`) | |

`demo-reset` refuses `CD_ENV=prod` on purpose. If the demo runs on the production host, use a
staging copy of the stack, or run only the S1 -> S8 flow without a reset.

### Real-deploy script

| Time | Screen | Say |
|---|---|---|
| 0:00-0:25 | UI dashboard, empty incident list; ShopLite storefront | "ShopLite is three Node services deployed from GitHub. Kaizen watches the repo through a GitHub App and gets runtime telemetry from a small reporter middleware. Everything it knows lives in one graph per repository: code, commits, deployments, errors, incidents and fixes." |
| 0:25-0:50 | Merge + deploy the S1 commit in ShopLite (seeded scenario branch), then send traffic (ShopLite load generator) | "This deployment bundles two commits. One refactors payment validation and is broken. Let's not tell Kaizen which." |
| 0:50-1:20 | Incident appears (detector runs every 30 s; ~60-90 s after onset). Open it | "Two 30-second windows of `POST /payments` above baseline opened exactly one incident. Onset is the first bad window, not when we noticed. Severity, affected endpoint and the triggering deployment are linked." |
| 1:20-2:00 | Suspects panel: culprit commit #1, confidence, feature bars; expand `path` and `endpoint` to show their Cypher | "Ranking is deterministic graph scoring, not the LLM. The culprit changed `validatePayment`, which is exactly where the stack trace throws: path distance 0. Every number has the query that produced it. The docs commit in the same deploy scores near zero." Toggle **graph off** (B0/B1): "Without the graph, 'latest commit' and keyword search point at the wrong commit here." |
| 2:00-2:30 | Evidence graph + explanation | "The explanation is generated by the model from read-only graph tools. Every claim cites a node you can click: the function, the endpoint, the deployment, the similar past incident. A citation that does not exist in the graph is rejected; if the model fails twice, a template explanation is used." |
| 2:30-3:15 | Click **Propose fix** -> revert PR on GitHub (`kaizen/fix-<id>`, labels `kaizen`, `incident:<id>`); verification check run turns green; back in the UI, **Approve** | "Revert first: deterministic, small, reviewable. Verification runs in ShopLite's own CI: unit tests, ephemeral staging, and the recorded failing traffic replayed against the fix. A failed verification never resolves the incident. Nothing merges without an approver: I approve, and only then is it merged." |
| 3:15-3:30 | Incident status `resolved`; history timeline | "Every transition is audited: who, when, why. The outcome is written back to the graph: root cause confirmed, fix, verification, time to detect, diagnose, resolve." |
| 3:30-4:10 | S8: deploy the recurring-bug scenario, open the new incident | "Weeks later a similar change breaks payments again. Kaizen finds the earlier incident through a structural similarity (same error family, same function) and shows *why* they resemble each other. The history feature raises its confidence, and the revert PR cites the earlier fix." |
| 4:10-4:50 | Pitch slide / `docs/results/latest.html` | Read the numbers below. |

### Numbers to show (measured)

From `docs/results/latest.md` (28 scenario variants S1-S10, replay benchmark, weights v1):

| | Graph (Kaizen) | B1 keyword (BM25) | B0 latest commit |
|---|---|---|---|
| Top-1 root cause, all variants | **89%** (95% CI 75-100%) | 39% | 32% |
| Top-1, held-out split (never tuned on) | **83%** | 44% | 28% |
| Top-3 | **100%** | 79% | 54% |
| Function-level accuracy | **89%** | 11% | 0% |
| False attribution (confidently wrong) | **0%** | 61% | 68% |

* Ablation: removing the call-path feature drops top-1 from 89% to 57%: the graph path is what
  does the work.
* Memory (S8): top-1 confidence 0.65 -> 0.78 once the earlier incident is in the graph.
* Blast radius: precision 0.97, recall 0.96 (25 variants).
* Speed: ranking median 58 ms per incident; detection -> persisted hypothesis p50 194 ms
  (budget 30 s, excluding the LLM).
* Honest limits: S4 (symptom in another service, no stack trace) is `ambiguous` with the
  culprit in the top 3, never a confident wrong answer.

## If something fails live

| Failure | Fallback |
|---|---|
| **Inject** says an incident is already open | press **Reset** on the Graph map page, then inject again |
| Slack message does not show | the incident page and graph still show the answer; send a test with `POST /api/v1/repos/<repo>/alerts/test` and check `CD_SLACK_WEBHOOK_URL` |
| Page shows the old UI or a 404 after a deploy | hard refresh; clear the site's cache and flush DNS (`resolvectl flush-caches`) |
| Fix stays on "verifying" | wait ~5 s; if it does not move, **Reset** and run it again |
| ShopLite deploy or traffic slow (real-deploy script) | play the recording from 0:50, narrate over it |
| Incident not opened within 2 min | open a pre-recorded incident in mock mode (`dev:mock`) and continue the script |
| Verification workflow slow (> 10 min) | show the check run from the rehearsal PR; say verification takes ~N min (measured at rehearsal) |
| LLM unavailable | the template explanation appears; say so ("the ranking never depends on the model") |

## Human checklist (not automatable)

- [ ] Slack alert seen in the real channel (rotate the webhook after sharing it anywhere)
- [ ] Demo run once on <https://kaizen.akkki.tech> right before recording: inject, alert, fix, approve, green graph, then **Reset**
- [ ] Another person completed Sign in with GitHub -> install -> pick a repo -> Start monitoring on production
- [ ] Rehearsal 1 end to end on the deployed system; note timings for each step: ______
- [ ] Rehearsal 2 end to end on the deployed system; timings: ______
- [ ] Screen recording of a clean run (1080p, 4-5 min), stored with the team and linked here
- [ ] Verification workflow duration measured on ShopLite (target < 10 min): ______
- [ ] App permissions updated to Contents: write and accepted on the ShopLite installation (`docs/security.md` S-2)
- [ ] ShopLite default branch protected (PR review required)
- [ ] S1 and S8 scenario branches/commands confirmed in the ShopLite repo
- [ ] Approver account logged in on the demo machine; second browser tab with GitHub PR list
- [ ] `make demo-reset` run on the target environment within the hour before the demo
- [ ] Pitch slide numbers re-checked against `docs/results/latest.md` after the last `make eval`
