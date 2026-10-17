"use client";

import Link from "next/link";
import { ServiceDot, Skeleton, Tag, type TagTone } from "@/components/app/kit";
import type { ServiceHealth } from "@/lib/api/hooks-dashboard";
import { cn } from "@/lib/utils";
import { serviceColor } from "@/lib/workspace";

const HEALTH: Record<string, { label: string; tone: TagTone; bar: string }> = {
  ok: { label: "Healthy", tone: "green", bar: "bg-green" },
  degraded: { label: "Degraded", tone: "amber", bar: "bg-amber" },
  incident: { label: "Incident", tone: "red", bar: "bg-red" },
};

const pct = (x: number | null | undefined) => (x == null ? "–" : `${(100 * x).toFixed(x < 0.1 ? 1 : 0)}%`);
const msf = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(x)} ms`);

function Metric({ label, value, baseline, worse }: { label: string; value: string; baseline: string; worse: boolean }) {
  return (
    <div>
      <dt className="text-[11.5px] text-ink-3">{label}</dt>
      <dd className="mt-0.5 flex items-baseline gap-1.5">
        <span className={cn("num text-[16px] font-medium", worse ? "text-red" : "text-ink")}>{value}</span>
        <span className="num text-[11.5px] text-ink-3">vs {baseline}</span>
      </dd>
    </div>
  );
}

/** One card per service+env: error rate and p95 of the latest windows against the baseline. */
export function HealthCards({ items, loading, error }: { items: ServiceHealth[] | undefined; loading: boolean; error: unknown }) {
  if (loading && !items)
    return (
      <div className="grid gap-3 px-6 py-4 sm:grid-cols-2 xl:grid-cols-3" role="status" aria-label="Loading service health">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-[112px] rounded-lg" />
        ))}
      </div>
    );
  if (error && !items) return <p role="alert" className="px-6 py-4 text-[13px] text-red">Could not load service health: {error instanceof Error ? error.message : "unknown error"}</p>;
  if (!items?.length) return <p className="px-6 py-4 text-[13px] text-ink-3">No telemetry yet. Services appear once Kaizen receives requests from them.</p>;
  const order = { incident: 0, degraded: 1, ok: 2 } as Record<string, number>;
  const sorted = [...items].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3) || a.service.localeCompare(b.service));
  return (
    <ul aria-label="Service health" className="grid gap-3 px-6 py-4 sm:grid-cols-2 xl:grid-cols-3">
      {sorted.map((h) => {
        const st = HEALTH[h.status] ?? HEALTH.ok;
        return (
          <li key={`${h.repo}|${h.service}|${h.env}`} data-status={h.status} className="relative overflow-hidden rounded-lg border border-line bg-panel-2 p-3.5">
            <span aria-hidden="true" className={cn("absolute inset-y-0 left-0 w-[3px]", st.bar)} />
            <div className="flex items-center gap-2">
              <ServiceDot color={serviceColor(h.service)} />
              <h3 className="truncate font-mono text-[13px] font-medium">{h.service}</h3>
              <span className="truncate text-[11.5px] text-ink-3">{h.env}</span>
              <Tag tone={st.tone} className="ml-auto">
                {st.label}
              </Tag>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-3">
              <Metric label="Error rate" value={pct(h.error_rate)} baseline={pct(h.baseline_error_rate)} worse={h.baseline_error_rate != null && h.error_rate > h.baseline_error_rate + 0.05} />
              <Metric label="p95 latency" value={msf(h.p95_ms)} baseline={msf(h.baseline_p95_ms)} worse={h.p95_ms != null && h.baseline_p95_ms != null && h.p95_ms > 2 * h.baseline_p95_ms} />
            </dl>
            <p className="mt-2.5 flex items-center gap-2 text-[11.5px] text-ink-3">
              <span className="num">{h.requests} requests</span>
              <span className="truncate">{h.repo.split("/").pop()}</span>
              {h.open_incident_id && (
                <Link href={`/incidents/${encodeURIComponent(h.open_incident_id)}`} className="ml-auto font-mono text-link hover:underline">
                  {h.open_incident_id}
                </Link>
              )}
            </p>
          </li>
        );
      })}
    </ul>
  );
}
