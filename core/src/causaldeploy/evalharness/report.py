import statistics
from collections import defaultdict

from causaldeploy.evalharness.runner import VARIANTS, Evaluation, Outcome


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "n/a"


def _rate(xs: list[Outcome], key: str) -> str:
    return _pct(sum(1 for o in xs if getattr(o, key)), len(xs))


def render(ev: Evaluation) -> str:
    by: dict[tuple[str, str], list[Outcome]] = defaultdict(list)
    for o in ev.outcomes:
        by[(o.method, o.case.split)].append(o)
        by[(o.method, "all")].append(o)

    lines = [
        "# Kaizen evaluation: graph ranking vs. baselines",
        "",
        f"{len(ev.cases)} scenario variants, S1-S10 (`eval/scenarios`), rendered into the graph "
        "shape Kaizen ingests, with distractor commits. **Replay benchmark**: deterministic and "
        "offline; it measures ranking quality, not live failure injection. All methods rank the "
        "same candidate set (the deployment's commits). The culprit is the latest commit in a "
        "third of the variants and in half of them its own message carries the error keywords, "
        "so neither baseline is set up to lose.",
        "",
        "Splits: `tune` = S1, S3, S5, S8 (weights may be fit here), `test` = held out.",
        "",
    ]
    for split in ("all", "test", "tune"):
        lines += [
            f"## {split}",
            "",
            "| method | top-1 | top-3 | function-level | false attribution | median time |",
            "|---|---|---|---|---|---|",
        ]
        for m in VARIANTS:
            xs = by.get((m, split), [])
            if not xs:
                continue
            med = statistics.median(o.millis for o in xs)
            lines.append(
                f"| {m} | {_rate(xs, 'top1')} | {_rate(xs, 'top3')} | "
                f"{_rate(xs, 'function_hit')} | {_rate(xs, 'false_attribution')} | {med:.0f} ms |"
            )
        lines.append("")

    lines += [
        "## Per scenario (top-1 correct, by method)",
        "",
        "| scenario | variants | graph | graph -path | graph -history | b1 keywords | b0 latest |",
        "|---|---|---|---|---|---|---|",
    ]
    scen = sorted({c.scenario for c in ev.cases}, key=lambda s: int(s[1:]))
    for s in scen:
        row = [s, str(sum(1 for c in ev.cases if c.scenario == s))]
        for m in VARIANTS:
            xs = [o for o in ev.outcomes if o.method == m and o.case.scenario == s]
            row.append(_rate(xs, "top1"))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    if ev.blast:
        p = statistics.fmean(x[2] for x in ev.blast)
        r = statistics.fmean(x[3] for x in ev.blast)
        lines += [
            "## Blast radius (affected services of the culprit function)",
            "",
            f"precision {p:.2f}, recall {r:.2f} over {len(ev.blast)} variants.",
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

    failures = [o for o in ev.outcomes if o.method == "graph" and not o.top1]
    lines += ["## Where the graph ranking is wrong", ""]
    if not failures:
        lines.append("None in this run.")
    for o in failures:
        lines.append(
            f"- {o.case.scenario} v{o.case.variant} ({o.case.title}): verdict `{o.verdict}`, "
            f"top `{o.top_sha}`"
        )
    lines += [
        "",
        "S4 (HTTP failure without a thrown exception) and S9 (rename) are the known weak "
        "spots; S6 is correct when the system abstains instead of blaming a commit.",
        "",
    ]
    return "\n".join(lines)
