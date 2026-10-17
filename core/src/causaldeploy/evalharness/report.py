"""Markdown, JSON and HTML renderings of one evaluation run (docs/results/latest.*)."""

import html
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from causaldeploy.evalharness import metrics as M
from causaldeploy.evalharness.runner import VARIANTS, Evaluation, Outcome

SPLITS = ("all", "tune", "test")


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def _by(ev: Evaluation) -> dict[tuple[str, str], list[Outcome]]:
    by: dict[tuple[str, str], list[Outcome]] = defaultdict(list)
    for o in ev.outcomes:
        by[(o.method, o.case.split)].append(o)
        by[(o.method, "all")].append(o)
    return by


def summaries(ev: Evaluation) -> dict[str, list[M.Summary]]:
    by = _by(ev)
    return {
        split: [M.summarize(m, by[(m, split)]) for m in VARIANTS if by.get((m, split))]
        for split in SPLITS
    }


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


def to_json(ev: Evaluation, generated_at: datetime | None = None) -> dict[str, Any]:
    """The machine-readable result the UI renders (shape fixed by #92)."""
    sums = summaries(ev)
    scen = []
    for c in ev.cases:
        results = {}
        for m in VARIANTS:
            o = next(
                (
                    x
                    for x in ev.outcomes
                    if x.method == m
                    and x.case.scenario == c.scenario
                    and x.case.variant == c.variant
                ),
                None,
            )
            if o is not None:
                results[m] = {
                    "top": o.top_sha,
                    "rank_of_culprit": o.rank_of_culprit,
                    "verdict": o.verdict,
                    "correct": o.top1,
                }
        scen.append(
            {
                "id": c.scenario,
                "variant": c.variant,
                "title": c.title,
                "split": c.split,
                "culprit": c.culprit_id or c.culprit_sha or "",
                "results": results,
            }
        )
    blast = M.blast_precision_recall(ev.blast_sets)
    fix = M.fix_verification_rate([c.fix_verified for c in ev.cases])
    return {
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(timespec="seconds"),
        "weights_version": ev.weights_version,
        "methods": list(VARIANTS),
        "splits": {
            split: [
                {
                    "method": s.method,
                    "n": s.n,
                    "top1": _r(s.top1),
                    "top3": _r(s.top3),
                    "function_level": _r(s.function_level),
                    "false_attribution": _r(s.false_attribution),
                    "median_ms": _r(s.median_ms, 1),
                    "top1_ci": [round(s.top1_ci[0], 4), round(s.top1_ci[1], 4)]
                    if s.top1_ci
                    else None,
                }
                for s in rows
            ]
            for split, rows in sums.items()
        },
        "blast_radius": {"precision": _r(blast[0]), "recall": _r(blast[1]), "n": len(ev.blast)}
        if blast
        else None,
        "fix_verification_rate": fix,
        "memory": [
            {"variant": v, "confidence_without": _r(a), "confidence_with": _r(b)}
            for v, a, b in ev.memory
        ],
        "runtime_seconds": round(ev.seconds, 1),
        "scenarios": scen,
        "failures": list(ev.failures),
    }


def render(ev: Evaluation) -> str:
    sums = summaries(ev)
    lines = [
        "# Kaizen evaluation: graph ranking vs. baselines",
        "",
        f"{len(ev.cases)} scenario variants, S1-S10 (`eval/scenarios`), rendered into the graph "
        "shape Kaizen ingests, with distractor commits. **Replay benchmark**: deterministic and "
        "offline; it measures ranking quality, not live failure injection. All methods rank the "
        "same candidate set (commits, config and dependency changes of the deployments in "
        "scope). The culprit is the latest commit in a third of the variants and in half of them "
        "its own message carries the error keywords, so neither baseline is set up to lose.",
        "",
        f"Weights `{ev.weights_version}`. Splits: `tune` = S1, S3, S5, S8 (weights may be fit "
        "here), `test` = held out. Top-1 95% CI: seeded percentile bootstrap, n=1000. "
        f"Wall time {ev.seconds:.1f}s.",
        "",
        "Methods: `graph` = deterministic graph score; `graph -X` = the same with feature X's "
        "weight set to 0 (ablation); `b1 bm25` = BM25 of the error text against commit message, "
        "changed file paths and function names (IDF over the candidates, no graph traversal); "
        "`b0 latest` = the most recent commit.",
        "",
    ]
    for split in ("all", "test", "tune"):
        lines += [
            f"## {split}",
            "",
            "| method | n | top-1 | top-1 95% CI | top-3 | function-level | false attribution "
            "| median time |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for s in sums[split]:
            ci = f"{_pct(s.top1_ci[0])}-{_pct(s.top1_ci[1])}" if s.top1_ci else "n/a"
            med = f"{_r(s.median_ms, 1)} ms" if s.median_ms is not None else "n/a"
            lines.append(
                f"| {s.method} | {s.n} | {_pct(s.top1)} | {ci} | {_pct(s.top3)} | "
                f"{_pct(s.function_level)} | {_pct(s.false_attribution)} | {med} |"
            )
        lines.append("")

    lines += [
        "## Per scenario (top-1 correct, by method)",
        "",
        "| scenario | split | variants | " + " | ".join(VARIANTS) + " |",
        "|---|---|---|" + "---|" * len(VARIANTS),
    ]
    scen = sorted({c.scenario for c in ev.cases}, key=lambda s: int(s[1:]))
    for sid in scen:
        cases = [c for c in ev.cases if c.scenario == sid]
        row = [sid, cases[0].split, str(len(cases))]
        for m in VARIANTS:
            xs = [o for o in ev.outcomes if o.method == m and o.case.scenario == sid]
            row.append(_pct(M.top1(xs)))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    blast = M.blast_precision_recall(ev.blast_sets)
    if blast:
        lines += [
            "## Blast radius (affected services of the culprit function)",
            "",
            f"precision {blast[0]:.2f}, recall {blast[1]:.2f} over {len(ev.blast)} variants.",
            "",
        ]
    fix = M.fix_verification_rate([c.fix_verified for c in ev.cases])
    lines += [
        "## Fix verification",
        "",
        "n/a: the replay scenarios carry the expected fix kind, not a verification outcome "
        "(verification runs in the live staging loop, `docs/verification.md`)."
        if fix is None
        else f"{_pct(fix)} of proposed fixes verified.",
        "",
    ]

    if ev.memory:
        first = statistics.fmean(m[1] for m in ev.memory)
        again = statistics.fmean(m[2] for m in ev.memory)
        lines += [
            "## Memory (S8 recurrence)",
            "",
            f"Same incident ranked without and with the earlier resolved incident in the graph: "
            f"top-1 confidence {first:.2f} -> {again:.2f} over {len(ev.memory)} variants "
            "(the `history` feature). Tool-call counts need a live LLM run and are not part of "
            "this offline table.",
            "",
        ]

    wrong = [o for o in ev.outcomes if o.method == "graph" and not o.top1]
    lines += ["## Where the graph ranking is wrong", ""]
    if not wrong:
        lines.append("None in this run.")
    for o in wrong:
        lines.append(
            f"- {o.case.scenario} v{o.case.variant} ({o.case.title}): verdict `{o.verdict}`, "
            f"top `{o.top_sha}`, culprit at rank {o.rank_of_culprit}"
        )
    lines += ["", *KNOWN_LIMITATIONS, ""]
    if ev.failures:
        lines += ["## Failed variants", "", *[f"- {f}" for f in ev.failures], ""]
    return "\n".join(lines)


KNOWN_LIMITATIONS = [
    "### Known limitations",
    "",
    "- **S4 (bug in orders, symptom in payments, HTTP 422 without a thrown exception).** "
    "Without a stack there is no `thrown_in` edge, so no candidate gets the stack-level `path` "
    "evidence and the verdict is `ambiguous` by design. The graph has no function-level edge "
    "for the orders -> payments HTTP call (only service-level `calls`), so a change in the "
    "failing endpoint's own handler tree (payments) and a change in the upstream caller "
    "(orders) are both plausible from the graph alone, and the decoy inside payments' handler "
    "tree leads because it collects the endpoint and same-service evidence. Telling them "
    "apart needs either the HTTP status class on the error node "
    "(a 4xx blames the caller's input) or function-level cross-service call edges from "
    "ingestion. Neither is in the ingested graph today, so the scorer is not tuned to S4. "
    "The verdict stays `ambiguous` (never a confident false attribution) and the culprit is "
    "within the top 3 that an ambiguous result persists.",
    "- **S7 (latent bug, culprit deployed long before onset).** Lookback-window deployments "
    "(#89) now add the later deployment's commits as suspects, and the temporal feature favours "
    "them. The `triggered` feature (the detector's incident -> deployment link) keeps the "
    "culprit on top; its weight was set a priori (equal to `service`), not fitted, because no "
    "tune scenario has more than one deployment. The margin over the later decoys is small, "
    "so the verdict is often `ambiguous`, the honest answer for a latent bug.",
    "- S6 is correct when the config change itself is named (or, without one in the graph, "
    "when the system abstains). Blaming a commit with confidence is a false attribution.",
]


def _bar_chart(rows: list[M.Summary], title: str) -> str:
    """Inline SVG, one bar per method (single series: no legend; values are direct labels)."""
    bar_h, gap, label_w, plot_w, pad = 22, 10, 140, 360, 8
    h = pad * 2 + len(rows) * (bar_h + gap)
    w = label_w + plot_w + 60
    parts = [
        f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(title)}" '
        f'width="100%" style="max-width:{w}px">',
        f'<line class="axis" x1="{label_w}" y1="{pad - 4}" x2="{label_w}" y2="{h - pad + 4}"/>',
    ]
    for frac in (0.5, 1.0):
        x = label_w + plot_w * frac
        parts.append(f'<line class="grid" x1="{x}" y1="{pad - 4}" x2="{x}" y2="{h - pad + 4}"/>')
    for i, s in enumerate(rows):
        y = pad + i * (bar_h + gap)
        v = s.top1 or 0.0
        bw = max(plot_w * v, 0.0)
        ci = f" (95% CI {_pct(s.top1_ci[0])}-{_pct(s.top1_ci[1])})" if s.top1_ci else ""
        tip = html.escape(f"{s.method}: top-1 {_pct(s.top1)}{ci}, n={s.n}")
        cls = "bar lead" if s.method == "graph" else "bar"
        parts += [
            f'<g class="mark"><title>{tip}</title>',
            f'<rect class="hit" x="0" y="{y - gap / 2}" width="{w}" height="{bar_h + gap}"/>',
            f'<text class="label" x="{label_w - 8}" y="{y + bar_h / 2 + 4}" '
            f'text-anchor="end">{html.escape(s.method)}</text>',
            f'<rect class="{cls}" x="{label_w}" y="{y}" width="{bw:.1f}" height="{bar_h}" rx="4"/>',
            f'<text class="value" x="{label_w + bw + 6:.1f}" y="{y + bar_h / 2 + 4}">'
            f"{_pct(s.top1)}</text>",
            "</g>",
        ]
    parts.append("</svg>")
    return "".join(parts)


def _table(rows: list[M.Summary]) -> str:
    head = (
        "<tr><th>method</th><th>n</th><th>top-1</th><th>95% CI</th><th>top-3</th>"
        "<th>function-level</th><th>false attribution</th><th>median ms</th></tr>"
    )
    body = []
    for s in rows:
        ci = f"{_pct(s.top1_ci[0])}-{_pct(s.top1_ci[1])}" if s.top1_ci else "n/a"
        med = f"{_r(s.median_ms, 1)}" if s.median_ms is not None else "n/a"
        body.append(
            f"<tr><td>{html.escape(s.method)}</td><td>{s.n}</td><td>{_pct(s.top1)}</td>"
            f"<td>{ci}</td><td>{_pct(s.top3)}</td><td>{_pct(s.function_level)}</td>"
            f"<td>{_pct(s.false_attribution)}</td><td>{med}</td></tr>"
        )
    return f"<table>{head}{''.join(body)}</table>"


CSS = """
:root{--bg:#ffffff;--ink:#1f2328;--muted:#59636e;--rule:#d1d9e0;--bar:#8fb8f5;--lead:#1f6feb}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--ink:#e6edf3;--muted:#9198a1;
--rule:#30363d;--bar:#2f5d9e;--lead:#58a6ff}}
body{background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,sans-serif;margin:0 auto;
max-width:860px;padding:24px 16px}
h1{font-size:22px}h2{font-size:17px;margin-top:28px}
p,li{color:var(--ink)}.muted{color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0 16px;display:block;
overflow-x:auto}th,td{border-bottom:1px solid var(--rule);padding:4px 8px;text-align:left;
white-space:nowrap}
.chart .axis{stroke:var(--muted);stroke-width:1}.chart .grid{stroke:var(--rule);stroke-width:1}
.chart .bar{fill:var(--bar)}.chart .bar.lead{fill:var(--lead)}
.chart .label,.chart .value{fill:var(--ink);font-size:12px}.chart .hit{fill:transparent}
.chart .mark:hover .bar{opacity:.85}
"""


def render_html(ev: Evaluation) -> str:
    sums = summaries(ev)
    sections = []
    for split in ("all", "test", "tune"):
        rows = sums[split]
        if not rows:
            continue
        sections.append(
            f"<h2>{split}</h2>{_bar_chart(rows, f'Top-1 accuracy by method, {split} split')}"
            f"{_table(rows)}"
        )
    wrong = [o for o in ev.outcomes if o.method == "graph" and not o.top1]
    wrong_html = "".join(
        f"<li>{html.escape(o.case.scenario)} v{o.case.variant}: verdict "
        f"<code>{html.escape(o.verdict)}</code>, culprit at rank {o.rank_of_culprit}</li>"
        for o in wrong
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>Kaizen evaluation</title><style>{CSS}</style></head><body>"
        "<h1>Kaizen evaluation: graph ranking vs. baselines</h1>"
        f'<p class="muted">{len(ev.cases)} scenario variants, weights '
        f"<code>{html.escape(ev.weights_version)}</code>. Replay benchmark: deterministic and "
        "offline. Top-1 bars per method; hover a bar for its 95% bootstrap CI. Full tables in "
        "<code>latest.md</code>, data in <code>latest.json</code>.</p>"
        + "".join(sections)
        + "<h2>Where the graph ranking is wrong</h2>"
        + (f"<ul>{wrong_html}</ul>" if wrong_html else "<p>None in this run.</p>")
        + "</body></html>\n"
    )
