"use client";

import { useState, type MouseEvent } from "react";
import { Segmented, Skeleton } from "@/components/app/kit";
import { useIncidentMetrics } from "@/lib/api/hooks-incident";
import { geometry } from "@/lib/metric-chart";

const W = 640;
const H = 180;
type Metric = "error_rate" | "p95_ms";
const OPTIONS = [
  { id: "error_rate", label: "Error rate" },
  { id: "p95_ms", label: "p95 latency" },
] as const;
const fmt = (m: Metric, v: number) => (m === "error_rate" ? `${(v * 100).toFixed(v < 0.1 ? 1 : 0)}%` : `${Math.round(v)} ms`);
const hhmm = (t: number) => new Date(t).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit", hour12: false });

/** Error rate (or p95) per minute around the onset, with deployment and onset markers. */
export function IncidentMetricsChart({ incidentId }: { incidentId: string }) {
  const [metric, setMetric] = useState<Metric>("error_rate");
  const [hover, setHover] = useState<number | null>(null);
  const q = useIncidentMetrics(incidentId);

  if (q.isLoading) return <Skeleton className="h-[220px] w-full" />;
  if (q.error || !q.data) return <p className="text-[13px] text-ink-3">Metrics are not available for this incident.</p>;
  const d = q.data;
  const t0 = Date.parse(d.since);
  const t1 = Date.parse(d.until);
  const onset = Date.parse(d.onset);
  const pts = d.series.map((p) => ({ t: Date.parse(p.ts), v: metric === "error_rate" ? p.error_rate : (p.p95_ms ?? null) }));
  const g = geometry(pts, t0, t1, W, H);
  const color = metric === "error_rate" ? "var(--red)" : "var(--orange)";
  const before = pts.filter((p) => p.t < onset && p.v !== null);
  const after = pts.filter((p) => p.t >= onset && p.v !== null);
  const avg = (xs: typeof pts) => (xs.length ? xs.reduce((a, p) => a + (p.v ?? 0), 0) / xs.length : null);
  const peak = after.reduce<number | null>((m, p) => (m === null || (p.v ?? 0) > m ? p.v : m), null);
  const base = avg(before);
  const summary =
    pts.length === 0
      ? "No telemetry in this window."
      : `${OPTIONS.find((o) => o.id === metric)!.label} ${base !== null ? `averaged ${fmt(metric, base)} before the onset` : ""}${peak !== null ? ` and peaked at ${fmt(metric, peak)} after it` : ""}. ${d.deployments.length} deployment${d.deployments.length === 1 ? "" : "s"} in the window.`;
  const hp = hover !== null ? pts[hover] : null;

  function onMove(e: MouseEvent<SVGSVGElement>) {
    if (!pts.length) return;
    const r = e.currentTarget.getBoundingClientRect();
    const t = t0 + ((e.clientX - r.left) / r.width) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < pts.length; i++) if (Math.abs(pts[i].t - t) < Math.abs(pts[best].t - t)) best = i;
    setHover(best);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Segmented label="Metric" options={OPTIONS} value={metric} onChange={setMetric} />
        <span className="ml-auto text-[12px] text-ink-3">
          {d.service} · {d.env} · {d.endpoint} · 1-minute buckets
        </span>
      </div>
      <figure className="relative">
        <svg viewBox={`0 0 ${W} ${H + 18}`} className="h-[220px] w-full overflow-visible" role="img" aria-label={summary} onMouseMove={onMove} onMouseLeave={() => setHover(null)} data-testid="metric-chart">
          {[0.25, 0.5, 0.75, 1].map((f) => (
            <g key={f}>
              <line x1={0} x2={W} y1={g.y(g.max * f)} y2={g.y(g.max * f)} stroke="var(--line)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
              <text x={2} y={g.y(g.max * f) - 3} fontSize={10} fill="var(--ink-3)">
                {fmt(metric, g.max * f)}
              </text>
            </g>
          ))}
          {g.area && <path d={g.area} fill={color} opacity={0.14} />}
          {g.line && <path d={g.line} fill="none" stroke={color} strokeWidth={1.8} vectorEffect="non-scaling-stroke" />}
          {d.deployments.map((dep) => {
            const x = g.x(Date.parse(dep.started_at));
            return (
              <g key={dep.id} data-marker="deployment">
                <line x1={x} x2={x} y1={0} y2={H} stroke="var(--amber)" strokeWidth={1.5} strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
                <text x={x + 3} y={10} fontSize={10} fill="var(--amber)">
                  {dep.id}
                  {dep.sha ? ` (${dep.sha.slice(0, 7)})` : ""}
                </text>
              </g>
            );
          })}
          <g data-marker="onset">
            <line x1={g.x(onset)} x2={g.x(onset)} y1={0} y2={H} stroke="var(--red)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
            <text x={g.x(onset) + 3} y={24} fontSize={10} fill="var(--red)">
              onset
            </text>
          </g>
          {hp && hp.v !== null && (
            <g>
              <line x1={g.x(hp.t)} x2={g.x(hp.t)} y1={0} y2={H} stroke="var(--ink-3)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
              <circle cx={g.x(hp.t)} cy={g.y(hp.v)} r={3.5} fill={color} />
            </g>
          )}
          {[t0, (t0 + t1) / 2, t1].map((t, i) => (
            <text key={t} x={g.x(t)} y={H + 14} fontSize={10} fill="var(--ink-3)" textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"}>
              {hhmm(t)}
            </text>
          ))}
        </svg>
        <figcaption className="mt-1 text-[12px] text-ink-3" aria-live="polite">
          {hp && hp.v !== null ? `${hhmm(hp.t)}: ${fmt(metric, hp.v)} (${d.series[hover!].errors}/${d.series[hover!].requests} failed)` : summary}
        </figcaption>
      </figure>
    </div>
  );
}
