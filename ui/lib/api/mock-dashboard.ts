// Mock fixtures for the dashboard routes (#93): incident list with filters, service health,
// deployments, hotspots and the evaluation result. The evaluation result is the real
// docs/results/latest.json (copied into ui/ so the UI image builds from ui/ alone; `npm run sync:eval`
// refreshes it and eval.test.ts fails when it drifts), so the mock shows exactly what `make eval` produced.
import latestEval from "./fixtures/eval-latest.json";
import type { components } from "./schema";

type IncidentPage = components["schemas"]["IncidentPage"];
type Incident = components["schemas"]["IncidentSummary"];
type ServiceHealth = components["schemas"]["ServiceHealth"];
type DeploymentView = components["schemas"]["DeploymentView"];
type Hotspots = components["schemas"]["Hotspots"];
type EvalResult = components["schemas"]["EvalResult"];

const NOW = Date.now();
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();
const SHOP = "yashchandnani07/shoplite";
const DOCS = "acme/docs-site";

/** Remediation ids approved in this page session (the mock API remembers its own writes). */
export const mockApproved = new Set<string>();

/** Set by the e2e "seeded incident" test: an incident detected at that moment appears on the next poll. */
export const MOCK_SEED_KEY = "kaizen.mock.seed";

const base: Incident[] = [
  { id: "inc-3", repo: SHOP, service: "orders", env: "production", endpoint: "POST /orders", status: "open", severity: "sev2", acknowledged_by: null, suspect: null, onset_ts: ago(14), detected_at: ago(11) },
  {
    id: "inc-2", repo: SHOP, service: "payments", env: "production", endpoint: "POST /payments", status: "explained", severity: "sev1", acknowledged_by: "demo",
    suspect: { sha: "9f2c1ab", kind: "commit", message: "Round amounts in toCents", confidence: 0.86 }, onset_ts: ago(190), detected_at: ago(186),
  },
  // inc-4 / inc-8 match the incident-page scenarios in mock-incident.ts (same ids, services, onsets)
  {
    id: "inc-8", repo: SHOP, service: "checkout", env: "production", endpoint: "POST /checkout", status: "verifying", severity: "sev2", acknowledged_by: "demo",
    suspect: { sha: "money-utils:1.9.0->2.0.0@d-45", kind: "dependency", message: "money-utils 1.9.0 -> 2.0.0", confidence: 0.71 }, onset_ts: ago(25), detected_at: ago(23),
  },
  {
    id: "inc-4", repo: SHOP, service: "inventory", env: "production", endpoint: "PUT /stock", status: "identified", severity: "sev3", acknowledged_by: null,
    suspect: { sha: "5e1a9c3", kind: "commit", message: "Batch stock updates", confidence: 0.38 }, onset_ts: ago(60), detected_at: ago(57),
  },
  {
    id: "inc-5", repo: SHOP, service: "search", env: "production", endpoint: "GET /search", status: "needs_human", severity: "sev3", acknowledged_by: null,
    suspect: { sha: "3d9e7f0", kind: "commit", message: "Cache search facets", confidence: 0.44 }, onset_ts: ago(1500), detected_at: ago(1496),
  },
  {
    id: "inc-1", repo: SHOP, service: "payments", env: "production", endpoint: "POST /payments", status: "resolved", severity: "sev1", acknowledged_by: "demo",
    suspect: { sha: "c41d2e0", kind: "commit", message: "Parse amount as float in toCents", confidence: 0.83 }, onset_ts: ago(2900), detected_at: ago(2896),
  },
  { id: "inc-0", repo: DOCS, service: "docs", env: "production", endpoint: "GET /", status: "closed", severity: "sev3", acknowledged_by: "demo", suspect: null, onset_ts: ago(8000), detected_at: ago(7996) },
];

function seeded(): Incident[] {
  let at: number | null = null;
  try {
    const v = typeof window !== "undefined" ? window.localStorage.getItem(MOCK_SEED_KEY) : null;
    at = v ? Number(v) : null;
  } catch {
    at = null;
  }
  if (!at || Number.isNaN(at)) return [];
  const t = new Date(at).toISOString();
  return [
    { id: "inc-seeded", repo: SHOP, service: "payments", env: "production", endpoint: "POST /refunds", status: "open", severity: "sev1", acknowledged_by: null, suspect: null, onset_ts: new Date(at - 60_000).toISOString(), detected_at: t },
  ];
}

export function allIncidents(): Incident[] {
  return [...seeded(), ...base];
}

/** The unfiltered first page, as the shell loads it. */
export const mockIncidents: IncidentPage = { total: base.length, limit: 50, offset: 0, items: base };

/** GET /api/v1/incidents with the backend's filters applied (status/severity repeat). */
export function filterIncidents(params: URLSearchParams): IncidentPage {
  const status = params.getAll("status");
  const severity = params.getAll("severity");
  const service = params.get("service");
  const repo = params.get("repo");
  const since = params.get("since");
  const until = params.get("until");
  const q = params.get("q")?.toLowerCase();
  const limit = Number(params.get("limit") ?? 50);
  const offset = Number(params.get("offset") ?? 0);
  const hits = allIncidents()
    .filter((i) => !status.length || status.includes(i.status))
    .filter((i) => !severity.length || (i.severity != null && severity.includes(i.severity)))
    .filter((i) => !service || i.service === service)
    .filter((i) => !repo || i.repo === repo)
    .filter((i) => !since || Date.parse(i.onset_ts) >= Date.parse(since))
    .filter((i) => !until || Date.parse(i.onset_ts) <= Date.parse(until))
    .filter((i) => !q || [i.id, i.repo, i.service, i.env, i.endpoint].some((v) => v.toLowerCase().includes(q)))
    .sort((a, b) => b.detected_at.localeCompare(a.detected_at));
  return { total: hits.length, limit, offset, items: hits.slice(offset, offset + limit) };
}

const health = (h: Omit<ServiceHealth, "window_seconds" | "current_windows" | "env"> & { env?: string }): ServiceHealth => ({
  env: "production",
  window_seconds: 30,
  current_windows: 2,
  ...h,
});

export function mockHealth(repo: string | null): ServiceHealth[] {
  const active = allIncidents().filter((i) => !["resolved", "closed"].includes(i.status));
  const open = (service: string) => active.find((i) => i.service === service)?.id ?? null;
  const rows: ServiceHealth[] = [
    health({ repo: SHOP, service: "payments", requests: 412, error_rate: 0.381, p95_ms: 1240, baseline_error_rate: 0.004, baseline_p95_ms: 180, status: "incident", open_incident_id: open("payments") }),
    health({ repo: SHOP, service: "orders", requests: 288, error_rate: 0.142, p95_ms: 410, baseline_error_rate: 0.006, baseline_p95_ms: 150, status: "incident", open_incident_id: open("orders") }),
    health({ repo: SHOP, service: "checkout", requests: 196, error_rate: 0.021, p95_ms: 230, baseline_error_rate: 0.012, baseline_p95_ms: 210, status: "incident", open_incident_id: open("checkout") }),
    health({ repo: SHOP, service: "inventory", requests: 150, error_rate: 0.087, p95_ms: 330, baseline_error_rate: 0.01, baseline_p95_ms: 140, status: "incident", open_incident_id: open("inventory") }),
    health({ repo: SHOP, service: "search", requests: 520, error_rate: 0.034, p95_ms: 960, baseline_error_rate: 0.01, baseline_p95_ms: 240, status: "incident", open_incident_id: open("search") }),
    health({ repo: SHOP, service: "catalog", requests: 840, error_rate: 0.002, p95_ms: 95, baseline_error_rate: 0.003, baseline_p95_ms: 92, status: "ok", open_incident_id: null }),
    health({ repo: SHOP, service: "notifications", requests: 64, error_rate: 0.125, p95_ms: 310, baseline_error_rate: 0.01, baseline_p95_ms: 280, status: "degraded", open_incident_id: null }),
    health({ repo: DOCS, service: "docs", requests: 120, error_rate: 0, p95_ms: 48, baseline_error_rate: 0, baseline_p95_ms: 51, status: "ok", open_incident_id: null }),
  ];
  return rows.filter((r) => !repo || r.repo === repo);
}

const inc = (id: string, triggered = false) => {
  const i = allIncidents().find((x) => x.id === id)!;
  return { id, status: i.status, severity: i.severity ?? null, onset: i.onset_ts, triggered };
};

export function mockDeployments(repo: string, hours: number): DeploymentView[] {
  const d = (id: string, sha: string, minutesAgo: number, commits: number, incidents: DeploymentView["incidents"], status = "success"): DeploymentView => ({
    id, sha, env: "production", status, started_at: ago(minutesAgo), finished_at: ago(minutesAgo - 3), commits, incidents,
  });
  const all =
    repo === SHOP
      ? [
          d("d-37", "1b7f0c2", 4300, 3, []),
          d("d-38", "e09a4d1", 3000, 5, [inc("inc-1", true)]),
          d("d-39", "7c3b2aa", 2950, 1, [], "rollback"),
          d("d-40", "a51e9d3", 760, 4, []),
          d("d-42", "bb04e61", 1520, 2, [inc("inc-5")]),
          d("d-41", "9f2c1ab", 200, 3, [inc("inc-2", true)]),
          d("d-43", "5e1a9c3", 70, 2, [inc("inc-4", true)]),
          d("d-45", "c2d7e40", 30, 1, [inc("inc-8", true)]),
          d("d-44", "8a4f0b1", 18, 2, [inc("inc-3", true)]),
        ]
      : repo === DOCS
        ? [d("d-7", "40ac1e2", 8100, 2, [inc("inc-0")])]
        : [];
  return all
    .filter((x) => NOW - Date.parse(x.started_at) <= hours * 3_600_000)
    .sort((a, b) => a.started_at.localeCompare(b.started_at));
}

export function mockHotspots(repo: string): Hotspots {
  if (repo !== SHOP) return { functions: [], dependencies: [] };
  return {
    functions: [
      { id: "money-utils:lib/index.js:toCents", name: "toCents", service: "payments", incidents: 2, confirmed: 1, last_incident: "inc-2", last_onset: ago(190), incident_ids: ["inc-2", "inc-1"] },
      { id: "search:src/facets.js:cacheFacets", name: "cacheFacets", service: "search", incidents: 1, confirmed: 0, last_incident: "inc-5", last_onset: ago(1500), incident_ids: ["inc-5"] },
      { id: "payments:src/validate.js:validateAmount", name: "validateAmount", service: "payments", incidents: 1, confirmed: 0, last_incident: "inc-1", last_onset: ago(2900), incident_ids: ["inc-1"] },
    ],
    dependencies: [{ name: "money-utils", incidents: 2, incident_ids: ["inc-1", "inc-8"] }],
  };
}

export const mockEval = latestEval as unknown as EvalResult;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

/** Answers the dashboard routes; null for anything else (mock.ts handles those). */
export function dashboardMock(url: URL, method: string): Response | null {
  if (method !== "GET") return null;
  const p = url.pathname;
  if (p === "/api/v1/incidents") return json(filterIncidents(url.searchParams));
  if (p === "/api/v1/services/health") return json(mockHealth(url.searchParams.get("repo")));
  if (p === "/api/v1/eval/latest") return json(mockEval);
  const m = /^\/api\/v1\/repos\/([^/]+)\/([^/]+)\/(deployments|hotspots)$/.exec(p);
  if (m) {
    const repo = `${decodeURIComponent(m[1])}/${decodeURIComponent(m[2])}`;
    return m[3] === "deployments" ? json(mockDeployments(repo, Number(url.searchParams.get("hours") ?? 72))) : json(mockHotspots(repo));
  }
  return null;
}
