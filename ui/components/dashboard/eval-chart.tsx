import { methodKind, pct, type EvalSummaryRow } from "@/lib/eval";

const FILL = { graph: "var(--accent)", ablation: "color-mix(in srgb, var(--accent) 45%, transparent)", baseline: "var(--ink-4)" } as const;

/**
 * Top-1 accuracy per method as horizontal bars with the 95% CI as a whisker. Values are direct
 * labels; the graph, its ablations and the baselines differ by fill (legend below).
 */
export function EvalBarChart({ rows, split }: { rows: EvalSummaryRow[]; split: string }) {
  const rowH = 30;
  const labelW = 136;
  const valueW = 48;
  const w = 640;
  const plotW = w - labelW - valueW;
  const h = rows.length * rowH + 24;
  const x = (v: number) => labelW + v * plotW;
  return (
    <figure className="px-6 py-4">
      <svg viewBox={`0 0 ${w} ${h}`} className="h-auto w-full max-w-[760px]" role="img" aria-label={`Top-1 accuracy by method, ${split} split: ${rows.map((r) => `${r.method} ${pct(r.top1)}`).join(", ")}`}>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={0} y2={h - 20} stroke="var(--line)" />
            <text x={x(t)} y={h - 6} fontSize="10.5" fill="var(--ink-3)" textAnchor="middle">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        {rows.map((r, i) => {
          const y = i * rowH + 6;
          const v = r.top1 ?? 0;
          const kind = methodKind(r.method);
          return (
            <g key={r.method} data-method={r.method}>
              <text x={labelW - 8} y={y + 13} fontSize="12" fill="var(--ink-2)" textAnchor="end" fontWeight={kind === "graph" ? 600 : 400}>
                {r.method}
              </text>
              <rect x={labelW} y={y + 2} width={Math.max(1, v * plotW)} height={16} rx={3} fill={FILL[kind]} />
              {r.top1_ci && r.top1_ci.length === 2 && (
                <g stroke="var(--ink-2)" strokeWidth="1.2">
                  <line x1={x(r.top1_ci[0])} x2={x(r.top1_ci[1])} y1={y + 10} y2={y + 10} />
                  <line x1={x(r.top1_ci[0])} x2={x(r.top1_ci[0])} y1={y + 6} y2={y + 14} />
                  <line x1={x(r.top1_ci[1])} x2={x(r.top1_ci[1])} y1={y + 6} y2={y + 14} />
                </g>
              )}
              <text x={w - valueW + 8} y={y + 14} fontSize="12" fill="var(--ink)" className="num">
                {pct(r.top1)}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-3">
        {(["graph", "ablation", "baseline"] as const).map((k) => (
          <span key={k} className="flex items-center gap-1.5">
            <span aria-hidden="true" className="inline-block size-2.5 rounded-[2px]" style={{ background: FILL[k] }} />
            {k === "graph" ? "graph ranking" : k === "ablation" ? "graph with one feature off" : "baselines (B1 BM25, B0 latest)"}
          </span>
        ))}
        <span>whisker: top-1 95% CI</span>
      </figcaption>
    </figure>
  );
}
