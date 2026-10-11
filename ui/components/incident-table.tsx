"use client";

import { Badge } from "@/components/ui/badge";
import type { components } from "@/lib/api/schema";

type Incident = components["schemas"]["IncidentSummary"];

const TONE = { open: "danger", investigating: "warning", resolved: "success" } as const;

export function relativeTime(iso: string, now = Date.now()): string {
  const minutes = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

export function IncidentTable({ incidents }: { incidents: Incident[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <table className="w-full text-sm">
        <thead className="text-left text-muted-foreground">
          <tr>
            <th className="px-4 py-2 font-medium">Incident</th>
            <th className="px-4 py-2 font-medium">Service</th>
            <th className="px-4 py-2 font-medium">Status</th>
            <th className="px-4 py-2 font-medium">Onset</th>
            <th className="px-4 py-2 font-medium">Detected</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((i) => (
            <tr key={i.id} className="border-t">
              <td className="px-4 py-3 font-mono">{i.id}</td>
              <td className="px-4 py-3">
                {i.service} <span className="text-muted-foreground">({i.env})</span>
              </td>
              <td className="px-4 py-3">
                <Badge tone={TONE[i.status as keyof typeof TONE] ?? "neutral"}>{i.status}</Badge>
              </td>
              <td className="px-4 py-3 text-muted-foreground">{relativeTime(i.onset_ts)}</td>
              <td className="px-4 py-3 text-muted-foreground">{relativeTime(i.detected_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
