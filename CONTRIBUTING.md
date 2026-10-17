# Contributing

- Branch from `main`: `feat/<issue#>-short-name`; open a PR early, link the issue (`Refs #86`).
- Small PRs; CI (`ruff`, `mypy`, `pytest`, UI typecheck/build) must pass.
- Python: typed, `ruff`-formatted, no raw Cypher outside `causaldeploy/graph/`.
- Never commit secrets (`.env`, `*.pem`). All config goes through `causaldeploy/config.py`.
- LLM calls only through the agent harness (`causaldeploy/agent/`); treat logs, commit messages and diffs as untrusted input.
- Data shapes are the event contracts; changing one requires a version bump.
- A decision that changes the architecture gets an ADR in `docs/adr/` (next number, status, context, decision, consequences).
- Security-relevant changes update `docs/security.md`; performance-relevant ones re-run `make perf` and update `docs/performance.md`.
- New acceptance evidence goes into `docs/verification.md` with the exact test name.
- Parallel checkouts (git worktrees): set `CD_TEST_DB` so test runs do not drop each other's database.
