// Formatting and derived tables for the evaluation dashboard. Everything is computed from
// docs/results/latest.json (GET /api/v1/eval/latest) with the same rules the harness uses for
// docs/results/latest.md (core/src/causaldeploy/evalharness/report.py), so both show the same
// numbers.
import type { components } from "@/lib/api/schema";

export type EvalResult = components["schemas"]["EvalResult"];
export type EvalSummaryRow = components["schemas"]["EvalSummaryRow"];
export type EvalScenario = components["schemas"]["EvalScenario"];
export type EvalMethodResult = components["schemas"]["EvalMethodResult"];

export const SPLITS = ["all", "test", "tune"] as const;
export type Split = (typeof SPLITS)[number];

/** Python's `f"{x:.0f}"`: round half to even (JS toFixed rounds ties up). */
export function roundHalfEven(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

/** report.py `_pct`: "n/a" for missing, else a whole percentage. */
export const pct = (x: number | null | undefined): string => (x == null ? "n/a" : `${roundHalfEven(100 * x)}%`);

export const ci = (c: number[] | null | undefined): string => (c && c.length === 2 ? `${pct(c[0])}-${pct(c[1])}` : "n/a");

/** Median time as stored in latest.json (0.1 ms). latest.md rounds the unrounded median to whole
 * ms, so the two can differ by up to 0.5 ms (e.g. 53.46 -> "53 ms" in md, 53.5 in json). */
export const ms = (x: number | null | undefined): string => (x == null ? "n/a" : `${Number(x.toFixed(1))} ms`);

export type MethodKind = "graph" | "ablation" | "baseline";

export function methodKind(method: string): MethodKind {
  if (method === "graph") return "graph";
  if (method.startsWith("graph")) return "ablation";
  return "baseline";
}

/** One rendered row of a split table, in the md column order. */
export function splitRow(s: EvalSummaryRow): string[] {
  return [s.method, String(s.n), pct(s.top1), ci(s.top1_ci), pct(s.top3), pct(s.function_level), pct(s.false_attribution), ms(s.median_ms)];
}

export const SPLIT_COLUMNS = ["method", "n", "top-1", "top-1 95% CI", "top-3", "function-level", "false attribution", "median time"];

export type ScenarioGroup = { id: string; split: string; title: string; variants: EvalScenario[] };

/** Scenarios grouped by id, in numeric order (S1, S2, ..., S10). */
export function scenarioGroups(ev: EvalResult): ScenarioGroup[] {
  const by = new Map<string, ScenarioGroup>();
  for (const s of ev.scenarios) {
    const g = by.get(s.id) ?? { id: s.id, split: s.split, title: s.title, variants: [] };
    g.variants.push(s);
    by.set(s.id, g);
  }
  const num = (id: string) => Number(id.replace(/\D/g, "")) || 0;
  return [...by.values()].sort((a, b) => num(a.id) - num(b.id) || a.id.localeCompare(b.id));
}

/** Share of a scenario's variants a method got right at top-1 (null when it never ran). */
export function scenarioTop1(g: ScenarioGroup, method: string): number | null {
  const xs = g.variants.map((v) => v.results[method]).filter(Boolean);
  return xs.length ? xs.filter((r) => r.correct).length / xs.length : null;
}

/** The md "Per scenario" row: scenario, split, variants, then top-1 per method. */
export function scenarioRow(g: ScenarioGroup, methods: string[]): string[] {
  return [g.id, g.split, String(g.variants.length), ...methods.map((m) => pct(scenarioTop1(g, m)))];
}

export type Wrong = { id: string; variant: number; title: string; verdict: string; top: string | null; rank: number | null };

/** "Where the graph ranking is wrong": every variant the graph method missed at top-1. */
export function graphMisses(ev: EvalResult): Wrong[] {
  return ev.scenarios
    .filter((s) => s.results.graph && !s.results.graph.correct)
    .map((s) => ({ id: s.id, variant: s.variant, title: s.title, verdict: s.results.graph.verdict, top: s.results.graph.top, rank: s.results.graph.rank_of_culprit }));
}

/** md wording of one miss, for comparison with latest.md. */
export const missLine = (w: Wrong) => `${w.id} v${w.variant} (${w.title}): verdict \`${w.verdict}\`, top \`${w.top}\`, culprit at rank ${w.rank}`;

export function memorySummary(ev: EvalResult): { without: number; with: number; n: number } | null {
  const m = ev.memory.filter((x) => x.confidence_without != null && x.confidence_with != null);
  if (!m.length) return null;
  const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
  return { without: mean(m.map((x) => x.confidence_without!)), with: mean(m.map((x) => x.confidence_with!)), n: m.length };
}
