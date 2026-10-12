import type { AnalyticsDashboardData } from "@/components/block/analytics-dashboard";
import type { components } from "@/lib/api/schema";

type Incident = components["schemas"]["IncidentSummary"];
type Role = "viewer" | "approver";

const HOUR = 3_600_000;
const COLORS = ["#f08a3c", "#9d8df5", "#2fd06f", "#4aa8ff", "#e5568b", "#e6c229"];

const hash = (s: string) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);

/** Initials avatar as a data URI, so the dashboard needs no image hosting. */
function avatar(handle: string, color: string): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="32" fill="${color}"/>` +
    `<text x="32" y="41" font-family="sans-serif" font-size="26" font-weight="600" text-anchor="middle" fill="#fff">${handle.slice(0, 2).toUpperCase()}</text></svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function relative(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 60) return `${Math.max(m, 1)}m`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h}h` : `${Math.round(h / 24)}d`;
}

const when = (iso: string) =>
  new Date(iso).toLocaleString("en", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export const STATUS = {
  open: { kind: "open", label: "Open" },
  investigating: { kind: "review", label: "Investigating" },
  resolved: { kind: "merged", label: "Resolved" },
} as const;

type Range = { id: string; label: string; caption: string; spanMs: number; buckets: number; bucketLabel: (i: number, start: number) => string };

const RANGES: Range[] = [
  {
    id: "today", label: "24 hours", caption: "Last 24 hours", spanMs: 24 * HOUR, buckets: 6,
    bucketLabel: (i, start) => new Date(start + i * 4 * HOUR).toLocaleTimeString("en", { hour: "numeric" }),
  },
  {
    id: "week", label: "Week", caption: "Last 7 days", spanMs: 7 * 24 * HOUR, buckets: 7,
    bucketLabel: (i, start) => new Date(start + i * 24 * HOUR).toLocaleDateString("en", { weekday: "short" }),
  },
  {
    id: "month", label: "Month", caption: "Last 4 weeks", spanMs: 28 * 24 * HOUR, buckets: 4,
    bucketLabel: (i) => `W${i + 1}`,
  },
];

function pct(now: number, before: number): number {
  if (before === 0) return now === 0 ? 0 : 100;
  return Math.round(((now - before) / before) * 100);
}

const inWindow = (iso: string, from: number, to: number) => {
  const t = new Date(iso).getTime();
  return t >= from && t < to;
};

/**
 * Maps CausalDeploy incidents onto the Analytics Dashboard's data model:
 * services are the "people", incidents the "pull requests" (status open / investigating /
 * resolved), repositories the repositories the user can access.
 */
export function toDashboardData(
  incidents: Incident[],
  repos: Record<string, Role>,
  now: number = Date.now(),
): AnalyticsDashboardData {
  const services = [...new Set(incidents.map((i) => i.service))].sort();
  const people = (services.length ? services : ["no-incidents"]).map((handle) => {
    const color = COLORS[hash(handle) % COLORS.length];
    return { handle, avatar: avatar(handle, color), color, role: "Service" };
  });

  const kpis: AnalyticsDashboardData["kpis"] = {};
  const activity: AnalyticsDashboardData["activity"] = {};
  for (const r of RANGES) {
    const from = now - r.spanMs;
    const prev = from - r.spanMs;
    const cur = incidents.filter((i) => inWindow(i.detected_at, from, now));
    const before = incidents.filter((i) => inWindow(i.detected_at, prev, from));
    const open = cur.filter((i) => i.status !== "resolved");
    const resolved = cur.filter((i) => i.status === "resolved");
    const mttd = (list: Incident[]) =>
      list.length
        ? list.reduce((s, i) => s + (new Date(i.detected_at).getTime() - new Date(i.onset_ts).getTime()), 0) /
          list.length / 60_000
        : 0;
    const spark = Array.from({ length: 8 }, (_, k) => {
      const a = from + (k * r.spanMs) / 8;
      return incidents.filter((i) => inWindow(i.detected_at, a, a + r.spanMs / 8)).length;
    });
    kpis[r.id] = [
      { id: "detections", label: "Detections", icon: "commit", value: String(cur.length), delta: pct(cur.length, before.length), lowerIsBetter: true, spark },
      { id: "open", label: "Open incidents", icon: "pull", value: String(open.length), delta: pct(open.length, before.filter((i) => i.status !== "resolved").length), lowerIsBetter: true, spark },
      { id: "resolved", label: "Resolved", icon: "merge", value: String(resolved.length), delta: pct(resolved.length, before.filter((i) => i.status === "resolved").length), spark },
      { id: "mttd", label: "Mean time to detect", icon: "clock", value: `${mttd(cur).toFixed(1)}m`, delta: pct(mttd(cur), mttd(before)), lowerIsBetter: true, spark },
    ];

    const bucketMs = r.spanMs / r.buckets;
    const series: Record<string, number[]> = {};
    const lines: Record<string, { added: number; removed: number }> = {};
    for (const p of people) {
      const mine = cur.filter((i) => i.service === p.handle);
      series[p.handle] = Array.from({ length: r.buckets }, (_, b) =>
        mine.filter((i) => inWindow(i.detected_at, from + b * bucketMs, from + (b + 1) * bucketMs)).length,
      );
      lines[p.handle] = {
        added: mine.filter((i) => i.status === "resolved").length,
        removed: mine.filter((i) => i.status !== "resolved").length,
      };
    }
    activity[r.id] = { labels: Array.from({ length: r.buckets }, (_, b) => r.bucketLabel(b, from)), series, lines };
  }

  const numberOf = (id: string, fallback: number) => Number(id.replace(/\D/g, "")) || fallback;
  const pullRequests = [...incidents]
    .sort((a, b) => b.detected_at.localeCompare(a.detected_at))
    .map((i, idx) => {
      const detected = new Date(i.detected_at).getTime();
      const lag = relative(detected - new Date(i.onset_ts).getTime());
      return {
        number: numberOf(i.id, idx + 1),
        title: `Elevated failures in \`${i.service}\` (${i.env})`,
        author: i.service,
        status: STATUS[i.status as keyof typeof STATUS] ?? STATUS.open,
        repository: i.repo.split("/").pop() ?? i.repo,
        opened: when(i.detected_at),
        age: relative(now - detected),
        today: now - detected < 24 * HOUR,
        additions: 0,
        deletions: 0,
        comments: 0,
        problem: [`${i.service} began failing in ${i.env} at ${when(i.onset_ts)}; detected ${lag} later.`],
        approach: [
          i.status === "resolved"
            ? "Resolved: the cause was identified and the fix verified."
            : "CausalDeploy is mapping this incident to the deployment and commits that preceded the failure.",
        ],
        href: `/incidents/${encodeURIComponent(i.id)}`,
      };
    });

  const repositories = Object.entries(repos).map(([name, role]) => {
    const mine = incidents.filter((i) => i.repo === name);
    const last = mine.map((i) => new Date(i.detected_at).getTime()).sort((a, b) => b - a)[0];
    return {
      name,
      description: `${name} · ${role}`,
      language: "JavaScript",
      languageColor: "#f1e05a",
      visibility: "Public" as const,
      openPrs: mine.filter((i) => i.status !== "resolved").length,
      stars: 0,
      updated: last ? `${relative(now - last)} ago` : "no incidents",
      updatedMinutes: last ? Math.round((now - last) / 60_000) : Number.MAX_SAFE_INTEGER,
      activity: Array.from({ length: 14 }, (_, d) =>
        mine.filter((i) => inWindow(i.detected_at, now - (14 - d) * 24 * HOUR, now - (13 - d) * 24 * HOUR)).length,
      ),
      href: `/repositories/${name}`,
    };
  });

  return {
    people,
    ranges: RANGES.map(({ id, label, caption }) => ({ id, label, caption })),
    kpis,
    activity,
    repositories,
    pullRequests,
  };
}
