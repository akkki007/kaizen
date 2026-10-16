"""`python -m causaldeploy.evalharness`: graph ranking vs. baselines on the scenario library.

Writes <out>.md, <out>.json and <out>.html (default docs/results/latest.*).
"""

import argparse
import json
import sys
from pathlib import Path

from causaldeploy.evalharness.report import render, render_html, to_json
from causaldeploy.evalharness.runner import run
from causaldeploy.investigate.scorer import load_weights

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUT = ROOT / "docs" / "results" / "latest.md"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="causaldeploy.evalharness")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="markdown path; .json/.html beside it")
    ap.add_argument("--scenarios", help="comma-separated ids, e.g. S1,S3 (default: all)")
    ap.add_argument("--split", choices=("tune", "test", "all"), default="all")
    ap.add_argument("--jobs", type=int, default=1, help="variants evaluated in parallel")
    ap.add_argument("--timeout", type=float, help="seconds per variant before it counts as failed")
    ap.add_argument("--weights", help="weights version (default: the scorer's default)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--record", type=Path, help="write each variant's graph writes as JSONL")
    mode.add_argument("--replay", type=Path, help="evaluate recorded JSONL instead of rendering")
    ap.add_argument("--quiet", action="store_true", help="do not print the markdown")
    a = ap.parse_args(argv)

    ids = {s.strip() for s in a.scenarios.split(",") if s.strip()} if a.scenarios else None
    ev = run(
        ids=ids,
        split=a.split,
        jobs=max(1, a.jobs),
        timeout=a.timeout,
        record=a.record,
        replay=a.replay,
        weights=load_weights(a.weights) if a.weights else None,
    )
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = render(ev)
    out.write_text(text, encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps(to_json(ev), indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    out.with_suffix(".html").write_text(render_html(ev), encoding="utf-8")
    if not a.quiet:
        sys.stdout.write(text + "\n")
    return 1 if ev.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
