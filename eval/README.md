# Evaluation (issue #92)

```bash
make eval        # writes docs/results/latest.{md,json,html}
```

* `scenarios/S1..S10.yaml` are copied from the ShopLite repo (`scenarios/`), where each variant was
  verified against live load. They carry the ground truth: culprit function, affected services,
  whether the cause is code at all.
* `core/src/causaldeploy/evalharness/` renders each variant into the graph Kaizen would ingest
  (functions, calls, endpoints, deployments, config changes, an error signature, distractor
  commits) and ranks it with the graph scorer, three ablations (no `path`, no `history`, no
  `dependency`) and the baselines: **B0** = latest commit in scope, **B1** = BM25 between the error
  text and each candidate's message, changed file paths and function names (no graph traversal).
* Deterministic and offline. It measures ranking quality, not live failure injection. Tool-call
  counts and LLM explanation quality need a live run with `CD_OPENAI_API_KEY`.
* `tests/test_eval.py` gates CI: graph top-1 >= 70%, above both baselines overall and on the
  held-out split, above the no-path ablation, false attribution <= 10%, replay identical and
  under 10 minutes.

Runner options (`cd core && uv run python -m causaldeploy.evalharness --help`):

| flag | meaning |
|---|---|
| `--scenarios S1,S3` | only these scenarios |
| `--split tune\|test\|all` | `tune` = S1, S3, S5, S8 (weights may be fit here), `test` = held out |
| `--jobs N` | variants in parallel (each gets its own throwaway graph) |
| `--timeout SECONDS` | a variant slower than this is listed under failures |
| `--out PATH` | markdown path; `.json` and `.html` are written beside it |
| `--record DIR` | also write each variant's normalized graph writes as JSONL |
| `--replay DIR` | evaluate recorded JSONL instead of rendering scenarios (no network, no LLM) |
| `--weights VERSION` | score with `investigate/weights/<VERSION>.json` |

Weight fitting uses the tune split only (a guard rejects held-out cases):
`uv run python -m causaldeploy.evalharness.fit [--write v2] [--report ../docs/results/fit-latest.md]`.
It never changes the default weights; see `docs/results/fit-latest.md` for the last run.

Refreshing scenarios: copy `shoplite/scenarios/S*.yaml` over `eval/scenarios/`.
