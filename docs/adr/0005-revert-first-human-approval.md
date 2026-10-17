# ADR 0005: Revert first, verify in the repo's CI, merge only with human approval

Status: accepted (PLAN.md §2 gap 8, §8 step 8; #91)

## Context

An agent that changes production code can do more damage than the incident. LLM patches can be
wrong, and the monitored repository's tests are untrusted code. A reviewer must be able to trust
that nothing ships without them.

## Decision

* **Revert is the default fix**: deterministic, small, easy to review. Merge commits are reverted
  against the mainline (`-m 1`), squash merges as one commit; a conflict is reported and
  escalated to `needs_human`, never forced.
* An **LLM patch** is optional and off by default (`CD_REMEDIATION_PATCH_ENABLED`). It is limited
  to the culprit's files and `CD_REMEDIATION_PATCH_MAX_LINES/FILES`, applied in a temporary
  worktree, must pass the repo's tests with a scrubbed environment, else it is rejected and the
  revert follows. Fallback chain: patch -> revert -> escalate.
* Kaizen pushes only `kaizen/fix-<incident>` branches (refuses `main`/`master`) and opens a PR
  labelled `kaizen` + `incident:<id>` with the explanation, evidence and similar prior fixes.
* **Verification runs in the target repo's GitHub Actions** (`verify.yml`: unit tests, ephemeral
  staging, replay of the recorded failing traffic, also on the failing SHA for comparison), not on
  the Kaizen host. The verdict is recomputable from stored inputs.
* **Approval gate**: only users with push/maintain/admin on the repo (approver role) can approve,
  only a verified fix can be approved, and every approve/reject is in `audit_log`. A person merges
  on GitHub by default; `CD_REMEDIATION_MERGE_POLICY=app` lets Kaizen merge through the API only
  after a recorded approval. A merge without approval is audited
  (`remediation.merged_without_approval`) and does not resolve the incident. Kaizen never deploys.

## Consequences

* Tests: `test_remediation_flow.py::test_app_merge_policy_merges_only_after_approval`,
  `test_manual_policy_never_merges`, `test_remediate.py::test_failed_verification_never_resolves_and_escalates`,
  `test_revert_conflict_is_reported_not_forced`, `test_security.py::test_fix_branches_never_target_a_default_branch`.
* A revert can remove wanted changes bundled in the culprit commit; the PR text says what is
  reverted and a person decides.
* The GitHub App needs `contents: write` (see `docs/security.md`); protect the default branch.
