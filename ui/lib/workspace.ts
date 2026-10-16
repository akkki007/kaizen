import type { components } from "@/lib/api/schema";

export type Incident = components["schemas"]["IncidentSummary"];
export type Role = "viewer" | "approver";

const DAY = 86_400_000;
/** Muted label colors; a service keeps its color everywhere because it is picked by name. */
const COLORS = ["#ef8f45", "#a48cf0", "#5aa9e6", "#e5689a", "#4fb7a8", "#d6a03c"];

const hash = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
export const serviceColor = (name: string) => COLORS[hash(name) % COLORS.length];

export const isOpen = (i: Incident) => i.status !== "resolved";

export type Service = { name: string; color: string; total: number; open: number };
export type Repo = { name: string; role: Role; open: number; total: number; last: number | null; daily: number[] };

export type Workspace = {
  incidents: Incident[];
  open: Incident[];
  services: Service[];
  repos: Repo[];
  /** Incidents per day for the last 7 days, oldest first. */
  week: number[];
  /** Incidents per day for the last 30 days, oldest first. */
  month: number[];
  /** Mean minutes from onset to detection over the last 30 days; null without data. */
  mttdMinutes: number | null;
};

function perDay(list: Incident[], days: number, now: number) {
  return Array.from({ length: days }, (_, d) => {
    const from = now - (days - d) * DAY;
    return list.filter((i) => {
      const t = Date.parse(i.detected_at);
      return t >= from && t < from + DAY;
    }).length;
  });
}

/** Everything the workspace screens show, derived from the incident list and the user's repositories. */
export function toWorkspace(incidents: Incident[], repos: Record<string, Role>, now: number = Date.now()): Workspace {
  const sorted = [...incidents].sort((a, b) => b.detected_at.localeCompare(a.detected_at));

  const byService = new Map<string, Service>();
  for (const i of sorted) {
    const s = byService.get(i.service) ?? { name: i.service, color: serviceColor(i.service), total: 0, open: 0 };
    s.total += 1;
    if (isOpen(i)) s.open += 1;
    byService.set(i.service, s);
  }

  const recent = sorted.filter((i) => now - Date.parse(i.detected_at) < 30 * DAY);
  const lags = recent.map((i) => (Date.parse(i.detected_at) - Date.parse(i.onset_ts)) / 60_000);

  return {
    incidents: sorted,
    open: sorted.filter(isOpen),
    services: [...byService.values()].sort((a, b) => b.total - a.total || a.name.localeCompare(b.name)),
    repos: Object.entries(repos)
      .map(([name, role]) => {
        const mine = sorted.filter((i) => i.repo === name);
        return {
          name,
          role,
          open: mine.filter(isOpen).length,
          total: mine.length,
          last: mine.length ? Date.parse(mine[0].detected_at) : null,
          daily: perDay(mine, 14, now),
        };
      })
      .sort((a, b) => b.open - a.open || a.name.localeCompare(b.name)),
    week: perDay(sorted, 7, now),
    month: perDay(sorted, 30, now),
    mttdMinutes: lags.length ? lags.reduce((a, b) => a + b, 0) / lags.length : null,
  };
}

/** "3m", "5h", "2d": the compact age used in lists. */
export function ago(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 1) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h}h` : `${Math.round(h / 24)}d`;
}

export const when = (iso: string) =>
  new Date(iso).toLocaleString("en", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

/** Agent step summaries often repeat the tool name ("get_incident: errors=1"); drop it when shown next to the name. */
export const stepSummary = (name: string, summary: string) =>
  summary.startsWith(`${name}:`) ? summary.slice(name.length + 1).trim() : summary;
