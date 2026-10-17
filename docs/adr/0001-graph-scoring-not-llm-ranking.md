# ADR 0001: Rank suspects with a deterministic graph score, not the LLM

Status: accepted (PLAN.md §2 gap 2, §8; implemented in #89)

## Context

The core claim is measurable: graph-based investigation names the true culprit more often than
flat approaches. An LLM choosing the culprit would make that claim unreproducible (sampling,
model updates, prompt drift), expensive to evaluate on every scenario variant, and attackable:
commit messages and log lines are attacker-controlled text the model reads.

## Decision

* The ranking is plain code over graph features (`investigate/features.py`, `scorer.py`): each
  feature is a Cypher query returning `{value, evidence_ids, explanation}`; the score is a
  weighted sum with weights in a versioned file (`investigate/weights/v1.json`), stamped on every
  result and persisted edge.
* Confidence is a normalised score; a small margin or endpoint-only evidence yields `ambiguous`
  (top 3, tentative), no function-level evidence yields `no_code_evidence`. Abstaining is a
  valid answer.
* The LLM only **explains** the decided ranking with citations to graph ids; the validator
  rejects unknown citations and the agent cannot override a confident ranking
  (`test_agent.py::test_agent_cannot_override_a_confident_ranking`).
* Weights are fitted only on the tune split, with a leak guard (`evalharness/fit.py`,
  `test_fit.py`); the fitted weights were worse on held-out data, so `v1` stays.

## Consequences

* Reproducible evaluation offline in seconds (`make eval`: graph 89% top-1 vs. B1 BM25 39% and
  B0 latest commit 32% over 28 variants, 0% false attribution, `docs/results/latest.md`).
* The UI can show the Cypher behind every number.
* Features need graph data: with no stack trace and only service-level cross-service edges (S4),
  the honest verdict is `ambiguous`. Improving that means better ingestion, not a smarter prompt.
* Explanations still cost tokens, but a missing or failed LLM never blocks ranking or the fix.
