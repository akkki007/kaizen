"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { StatusGlyph } from "@/components/app/kit";
import type { DeploymentView } from "@/lib/api/hooks-dashboard";
import { statusMeta } from "@/lib/status";
import { ago, when } from "@/lib/workspace";

const DEPLOY_COLOR: Record<string, string> = { success: "var(--ink-2)", rollback: "var(--amber)", failure: "var(--red)" };
const TONE_COLOR: Record<string, string> = { red: "var(--red)", amber: "var(--amber)", green: "var(--green)", accent: "var(--accent)", neutral: "var(--ink-3)", outline: "var(--ink-3)" };

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(720);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/**
 * Deployments as ticks on a time axis, each incident as a dot at its onset joined to the
 * deployment it followed, so deployment -> incident correlation reads at a glance.
 */
export function DeployTimeline({ deployments, hours, now = Date.now() }: { deployments: DeploymentView[]; hours: number; now?: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const from = now - hours * 3_600_000;
  const pad = 16;
  const h = 132;
  const x = (iso: string) => pad + ((Date.parse(iso) - from) / (now - from)) * (width - 2 * pad);
  const deployY = 34;
  const incY = 96;
  const ticks = Array.from({ length: 5 }, (_, i) => from + (i / 4) * (now - from));
  const incidents = deployments.flatMap((d) => d.incidents.map((i) => ({ ...i, dep: d })));
  const summary = `${deployments.length} deployments and ${incidents.length} incidents in the last ${hours} hours`;

  return (
    <div className="px-6 py-4">
      <div ref={ref} className="w-full">
        <svg width={width} height={h} role="group" aria-label={summary} className="block overflow-visible">
          <line x1={pad} x2={width - pad} y1={h - 16} y2={h - 16} stroke="var(--line)" />
          {ticks.map((t, i) => (
            <text key={i} x={pad + (i / 4) * (width - 2 * pad)} y={h - 2} fontSize="10.5" fill="var(--ink-3)" textAnchor={i === 0 ? "start" : i === 4 ? "end" : "middle"}>
              {i === 4 ? "now" : `-${ago(now - t)}`}
            </text>
          ))}
          <text x={pad} y={12} fontSize="10.5" fill="var(--ink-3)">Deployments</text>
          <text x={pad} y={incY - 16} fontSize="10.5" fill="var(--ink-3)">Incident onsets</text>
          {incidents.map((i) => (
            <line key={`l-${i.id}`} x1={x(i.dep.started_at)} y1={deployY + 6} x2={x(i.onset)} y2={incY - 6} stroke={TONE_COLOR[statusMeta(i.status).tone]} strokeOpacity="0.45" strokeDasharray={i.triggered ? undefined : "3 3"} />
          ))}
          {deployments.map((d) => (
            <g key={d.id}>
              <title>{`${d.id} (${d.status}) ${d.sha ?? ""}, ${d.commits} commits, ${when(d.started_at)}`}</title>
              <rect x={x(d.started_at) - 1.5} y={deployY - 10} width={3} height={20} rx={1.5} fill={DEPLOY_COLOR[d.status] ?? "var(--ink-3)"} />
            </g>
          ))}
          {incidents.map((i) => (
            <a key={i.id} href={`/incidents/${encodeURIComponent(i.id)}`} aria-label={`${i.id}, ${statusMeta(i.status).label}, after ${i.dep.id}`}>
              <title>{`${i.id}: ${statusMeta(i.status).label}, onset ${when(i.onset)}, after ${i.dep.id}`}</title>
              <circle cx={x(i.onset)} cy={incY} r={6} fill={TONE_COLOR[statusMeta(i.status).tone]} stroke="var(--panel)" strokeWidth="2" />
            </a>
          ))}
        </svg>
      </div>
      <ul aria-label="Deployments" className="mt-3 flex flex-col">
        {[...deployments].reverse().map((d) => (
          <li key={d.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line py-1.5 text-[12.5px]">
            <span aria-hidden="true" className="inline-block size-2 rounded-[2px]" style={{ background: DEPLOY_COLOR[d.status] ?? "var(--ink-3)" }} />
            <code className="font-mono text-ink">{d.id}</code>
            {d.sha && <code className="font-mono text-ink-3">{d.sha.slice(0, 7)}</code>}
            <span className="text-ink-3">
              {d.env} · {d.status} · {d.commits} {d.commits === 1 ? "commit" : "commits"} · {ago(now - Date.parse(d.started_at))} ago
            </span>
            <span className="ml-auto flex flex-wrap items-center gap-2">
              {d.incidents.length === 0 ? (
                <span className="text-ink-4">no incidents</span>
              ) : (
                d.incidents.map((i) => (
                  <Link key={i.id} href={`/incidents/${encodeURIComponent(i.id)}`} className="inline-flex items-center gap-1 font-mono text-[12px] text-link hover:underline">
                    <StatusGlyph status={i.status} size={12} />
                    {i.id}
                  </Link>
                ))
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
