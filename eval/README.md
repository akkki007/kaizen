# Evaluation (issue #92)

```bash
make eval        # writes eval/reports/latest.md and prints it
```

* `scenarios/S1..S10.yaml` are copied from the ShopLite repo (`scenarios/`), where each variant was
  verified against live load. They carry the ground truth: culprit function, affected services,
  whether the cause is code at all.
* `core/src/causaldeploy/evalharness/` renders each variant into the graph Kaizen would ingest
  (functions, calls, endpoints, deployments, an error signature, distractor commits) and ranks it
  with the graph scorer, two ablations (no `path`, no `history`) and the baselines:
  **B0** = latest commit in the deployment, **B1** = keyword overlap, no graph traversal.
* Deterministic and offline. It measures ranking quality, not live failure injection. Tool-call
  counts and LLM explanation quality need a live run with `CD_OPENAI_API_KEY`.
* `tests/test_eval.py` gates CI: graph top-1 >= 70%, above both baselines and the no-path ablation,
  false attribution <= 10%.

Refreshing scenarios: copy `shoplite/scenarios/S*.yaml` over `eval/scenarios/`.
