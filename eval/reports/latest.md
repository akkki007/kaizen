# Kaizen evaluation: graph ranking vs. baselines

28 scenario variants, S1-S10 (`eval/scenarios`), rendered into the graph shape Kaizen ingests, with distractor commits. **Replay benchmark**: deterministic and offline; it measures ranking quality, not live failure injection. All methods rank the same candidate set (the deployment's commits). The culprit is the latest commit in a third of the variants and in half of them its own message carries the error keywords, so neither baseline is set up to lose.

Splits: `tune` = S1, S3, S5, S8 (weights may be fit here), `test` = held out.

## all

| method | top-1 | top-3 | function-level | false attribution | median time |
|---|---|---|---|---|---|
| graph | 89% | 100% | 89% | 0% | 53 ms |
| graph -path | 71% | 100% | 89% | 11% | 48 ms |
| graph -history | 89% | 100% | 89% | 0% | 50 ms |
| b1 keywords | 36% | 64% | 0% | 64% | 7 ms |
| b0 latest | 39% | 57% | 0% | 61% | 2 ms |

## test

| method | top-1 | top-3 | function-level | false attribution | median time |
|---|---|---|---|---|---|
| graph | 83% | 100% | 83% | 0% | 50 ms |
| graph -path | 56% | 100% | 83% | 17% | 47 ms |
| graph -history | 83% | 100% | 83% | 0% | 51 ms |
| b1 keywords | 39% | 61% | 0% | 61% | 7 ms |
| b0 latest | 39% | 50% | 0% | 61% | 2 ms |

## tune

| method | top-1 | top-3 | function-level | false attribution | median time |
|---|---|---|---|---|---|
| graph | 100% | 100% | 100% | 0% | 59 ms |
| graph -path | 100% | 100% | 100% | 0% | 50 ms |
| graph -history | 100% | 100% | 100% | 0% | 46 ms |
| b1 keywords | 30% | 70% | 0% | 70% | 6 ms |
| b0 latest | 40% | 70% | 0% | 60% | 2 ms |

## Per scenario (top-1 correct, by method)

| scenario | variants | graph | graph -path | graph -history | b1 keywords | b0 latest |
|---|---|---|---|---|---|---|
| S1 | 3 | 100% | 100% | 100% | 33% | 33% |
| S2 | 3 | 100% | 33% | 100% | 33% | 33% |
| S3 | 1 | 100% | 100% | 100% | 0% | 100% |
| S4 | 3 | 0% | 0% | 0% | 33% | 33% |
| S5 | 3 | 100% | 100% | 100% | 33% | 33% |
| S6 | 3 | 100% | 100% | 100% | 0% | 0% |
| S7 | 3 | 100% | 100% | 100% | 100% | 100% |
| S8 | 3 | 100% | 100% | 100% | 33% | 33% |
| S9 | 3 | 100% | 0% | 100% | 33% | 33% |
| S10 | 3 | 100% | 100% | 100% | 33% | 33% |

## Blast radius (affected services of the culprit function)

precision 0.97, recall 0.96 over 25 variants.

## Memory (S8 recurrence)

Same incident ranked without and with the earlier resolved incident in the graph: top-1 confidence 0.65 -> 0.78 over 3 variants (the `history` feature). Tool-call counts need a live LLM run and are not part of this offline table.

## Where the graph ranking is wrong

- S4 v0 (Bug in orders, symptom appears in payments): verdict `ambiguous`, top `gs4v0`
- S4 v1 (Bug in orders, symptom appears in payments): verdict `ambiguous`, top `gs4v1`
- S4 v2 (Bug in orders, symptom appears in payments): verdict `ambiguous`, top `gs4v2`

S4 (HTTP failure without a thrown exception) and S9 (rename) are the known weak spots; S6 is correct when the system abstains instead of blaming a commit.
