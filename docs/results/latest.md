# Kaizen evaluation: graph ranking vs. baselines

28 scenario variants, S1-S10 (`eval/scenarios`), rendered into the graph shape Kaizen ingests, with distractor commits. **Replay benchmark**: deterministic and offline; it measures ranking quality, not live failure injection. All methods rank the same candidate set (commits, config and dependency changes of the deployments in scope). The culprit is the latest commit in a third of the variants and in half of them its own message carries the error keywords, so neither baseline is set up to lose.

Weights `v1`. Splits: `tune` = S1, S3, S5, S8 (weights may be fit here), `test` = held out. Top-1 95% CI: seeded percentile bootstrap, n=1000. Wall time 8.2s.

Methods: `graph` = deterministic graph score; `graph -X` = the same with feature X's weight set to 0 (ablation); `b1 bm25` = BM25 of the error text against commit message, changed file paths and function names (IDF over the candidates, no graph traversal); `b0 latest` = the most recent commit.

## all

| method | n | top-1 | top-1 95% CI | top-3 | function-level | false attribution | median time |
|---|---|---|---|---|---|---|---|
| graph | 28 | 89% | 75%-100% | 100% | 89% | 0% | 43.9 ms |
| graph -path | 28 | 57% | 39%-75% | 100% | 89% | 11% | 41.8 ms |
| graph -history | 28 | 89% | 75%-100% | 100% | 89% | 0% | 42.6 ms |
| graph -dependency | 28 | 89% | 75%-100% | 100% | 89% | 0% | 41.4 ms |
| b1 bm25 | 28 | 39% | 21%-57% | 79% | 11% | 61% | 7.9 ms |
| b0 latest | 28 | 32% | 14%-50% | 54% | 0% | 68% | 2.4 ms |

## test

| method | n | top-1 | top-1 95% CI | top-3 | function-level | false attribution | median time |
|---|---|---|---|---|---|---|---|
| graph | 18 | 83% | 61%-100% | 100% | 83% | 0% | 43.9 ms |
| graph -path | 18 | 39% | 17%-61% | 100% | 83% | 17% | 41.8 ms |
| graph -history | 18 | 83% | 61%-100% | 100% | 83% | 0% | 42.4 ms |
| graph -dependency | 18 | 83% | 61%-100% | 100% | 83% | 0% | 41.1 ms |
| b1 bm25 | 18 | 44% | 22%-67% | 78% | 17% | 56% | 7.8 ms |
| b0 latest | 18 | 28% | 11%-50% | 44% | 0% | 72% | 2.3 ms |

## tune

| method | n | top-1 | top-1 95% CI | top-3 | function-level | false attribution | median time |
|---|---|---|---|---|---|---|---|
| graph | 10 | 100% | 100%-100% | 100% | 100% | 0% | 43.7 ms |
| graph -path | 10 | 90% | 70%-100% | 100% | 100% | 0% | 44.0 ms |
| graph -history | 10 | 100% | 100%-100% | 100% | 100% | 0% | 45.7 ms |
| graph -dependency | 10 | 100% | 100%-100% | 100% | 100% | 0% | 41.9 ms |
| b1 bm25 | 10 | 30% | 0%-60% | 80% | 0% | 70% | 8.2 ms |
| b0 latest | 10 | 40% | 10%-70% | 70% | 0% | 60% | 2.4 ms |

## Per scenario (top-1 correct, by method)

| scenario | split | variants | graph | graph -path | graph -history | graph -dependency | b1 bm25 | b0 latest |
|---|---|---|---|---|---|---|---|---|
| S1 | tune | 3 | 100% | 67% | 100% | 100% | 33% | 33% |
| S2 | test | 3 | 100% | 33% | 100% | 100% | 33% | 33% |
| S3 | tune | 1 | 100% | 100% | 100% | 100% | 0% | 100% |
| S4 | test | 3 | 0% | 0% | 0% | 0% | 33% | 33% |
| S5 | tune | 3 | 100% | 100% | 100% | 100% | 33% | 33% |
| S6 | test | 3 | 100% | 100% | 100% | 100% | 100% | 0% |
| S7 | test | 3 | 100% | 0% | 100% | 100% | 33% | 33% |
| S8 | tune | 3 | 100% | 100% | 100% | 100% | 33% | 33% |
| S9 | test | 3 | 100% | 0% | 100% | 100% | 33% | 33% |
| S10 | test | 3 | 100% | 100% | 100% | 100% | 33% | 33% |

## Blast radius (affected services of the culprit function)

precision 0.97, recall 0.96 over 25 variants.

## Fix verification

n/a: the replay scenarios carry the expected fix kind, not a verification outcome (verification runs in the live staging loop, `docs/verification.md`).

## Memory (S8 recurrence)

Same incident ranked without and with the earlier resolved incident in the graph: top-1 confidence 0.65 -> 0.78 over 3 variants (the `history` feature). Tool-call counts need a live LLM run and are not part of this offline table.

## Where the graph ranking is wrong

- S4 v0 (Bug in orders, symptom appears in payments): verdict `ambiguous`, top `4ebea482eb27`, culprit at rank 3
- S4 v1 (Bug in orders, symptom appears in payments): verdict `ambiguous`, top `9aa3d3992391`, culprit at rank 2
- S4 v2 (Bug in orders, symptom appears in payments): verdict `ambiguous`, top `ec6eee1d2006`, culprit at rank 3

### Known limitations

- **S4 (bug in orders, symptom in payments, HTTP 422 without a thrown exception).** Without a stack there is no `thrown_in` edge, so no candidate gets the stack-level `path` evidence and the verdict is `ambiguous` by design. The graph has no function-level edge for the orders -> payments HTTP call (only service-level `calls`), so a change in the failing endpoint's own handler tree (payments) and a change in the upstream caller (orders) are both plausible from the graph alone, and the decoy inside payments' handler tree leads because it collects the endpoint and same-service evidence. Telling them apart needs either the HTTP status class on the error node (a 4xx blames the caller's input) or function-level cross-service call edges from ingestion. Neither is in the ingested graph today, so the scorer is not tuned to S4. The verdict stays `ambiguous` (never a confident false attribution) and the culprit is within the top 3 that an ambiguous result persists.
- **S7 (latent bug, culprit deployed long before onset).** Lookback-window deployments (#89) now add the later deployment's commits as suspects, and the temporal feature favours them. The `triggered` feature (the detector's incident -> deployment link) keeps the culprit on top; its weight was set a priori (equal to `service`), not fitted, because no tune scenario has more than one deployment. The margin over the later decoys is small, so the verdict is often `ambiguous`, the honest answer for a latent bug.
- S6 is correct when the config change itself is named (or, without one in the graph, when the system abstains). Blaming a commit with confidence is a false attribution.
