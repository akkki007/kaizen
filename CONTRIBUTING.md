# Contributing

- Branch from `main`: `feat/<issue#>-short-name`; open a PR early, link the issue (`Refs #86`).
- Small PRs; CI (`ruff`, `mypy`, `pytest`, UI typecheck/build) must pass.
- Python: typed, `ruff`-formatted, no raw Cypher outside `causaldeploy/graph/`.
- Never commit secrets (`.env`, `*.pem`). All config goes through `causaldeploy/config.py`.
- LLM calls only through the agent harness (`causaldeploy/agent/`); treat logs, commit messages and diffs as untrusted input.
- Data shapes are the event contracts; changing one requires a version bump.
