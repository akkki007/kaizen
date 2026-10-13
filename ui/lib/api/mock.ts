// Mock API mode (NEXT_PUBLIC_API_MOCK=1): the UI runs without the backend. Fixtures mirror the
// seeded ShopLite incident so the screens look like the real thing. Shapes are checked against
// the generated OpenAPI types.
import type { components } from "./schema";

type IncidentPage = components["schemas"]["IncidentPage"];
type User = components["schemas"]["User"];

const NOW = Date.now();
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

export const mockUser: User = {
  login: "demo",
  repos: { "yashchandnani07/shoplite": "approver", "acme/docs-site": "viewer" },
};

export const mockIncidents: IncidentPage = {
  total: 3,
  limit: 50,
  offset: 0,
  items: [
    { id: "inc-3", repo: "yashchandnani07/shoplite", service: "orders", env: "production", status: "open", onset_ts: ago(14), detected_at: ago(11) },
    { id: "inc-2", repo: "yashchandnani07/shoplite", service: "payments", env: "production", status: "investigating", onset_ts: ago(190), detected_at: ago(186) },
    { id: "inc-1", repo: "yashchandnani07/shoplite", service: "payments", env: "production", status: "resolved", onset_ts: ago(2900), detected_at: ago(2896) },
  ],
};

const mockKeys: { id: number; label: string; created_at: string; revoked_at: string | null }[] = [
  { id: 1, label: "production agent", created_at: ago(5000), revoked_at: null },
  { id: 2, label: "old agent", created_at: ago(90000), revoked_at: ago(4000) },
];

const mockGraph = (id: string): components["schemas"]["EvidenceGraph"] => ({
  incident_id: id,
  nodes: [
    { id: "Incident:" + id, label: "Incident", props: { id }, highlight: true },
    { id: "Endpoint:POST /payments", label: "Endpoint", props: { name: "POST /payments" }, highlight: true },
    { id: "Function:toCents", label: "Function", props: { name: "toCents", file: "money-utils/src/index.js" }, highlight: true },
    { id: "Commit:9f2c1ab", label: "Commit", props: { sha: "9f2c1ab", message: "Round amounts in toCents" }, highlight: true },
    { id: "Deployment:d-41", label: "Deployment", props: { id: "d-41", env: "production" }, highlight: true },
    { id: "Function:validateAmount", label: "Function", props: { name: "validateAmount" }, highlight: false },
  ],
  edges: [
    { id: "a", source: "Incident:" + id, target: "Endpoint:POST /payments", rel: "AFFECTS", props: {}, highlight: true },
    { id: "b", source: "Endpoint:POST /payments", target: "Function:toCents", rel: "CALLS", props: {}, highlight: true },
    { id: "c", source: "Commit:9f2c1ab", target: "Function:toCents", rel: "CHANGED", props: {}, highlight: true },
    { id: "d", source: "Deployment:d-41", target: "Commit:9f2c1ab", rel: "INCLUDES", props: {}, highlight: true },
    { id: "e", source: "Endpoint:POST /payments", target: "Function:validateAmount", rel: "CALLS", props: {}, highlight: false },
  ],
});

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

export const mockFetch: typeof fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : (input as Request).url, "http://mock");
  const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
  await new Promise((r) => setTimeout(r, 250)); // exercise loading states
  if (url.pathname === "/auth/me") return json(mockUser);
  if (url.pathname === "/auth/install-url") {
    return json({ url: "https://github.com/apps/causaldeploy-shoplite-demo/installations/new" });
  }
  if (url.pathname === "/auth/logout") return json({ status: "logged_out" });
  if (url.pathname === "/api/v1/incidents") return json(mockIncidents);
  if (url.pathname.endsWith("/backfill") && method === "POST") {
    return json({ job_id: "job-bf-2", status: "queued" }, 202);
  }
  if (url.pathname.endsWith("/backfill")) {
    return json({
      repo: url.pathname.split("/").slice(4, 6).map(decodeURIComponent).join("/"),
      installed: true,
      backfilled_at: ago(120),
      job: { id: "job-bf-1", status: "succeeded", attempts: 1, error: null, created_at: ago(125), updated_at: ago(120) },
    });
  }
  if (url.pathname.endsWith("/evidence-graph")) return json(mockGraph(url.pathname.split("/")[4]));
  if (url.pathname.endsWith("/llm-cost")) return json({ incident_id: "inc-3", calls: 4, input_tokens: 5120, output_tokens: 610, cost_usd: 0.0214, latency_ms_total: 6400 });
  if (url.pathname.endsWith("/api-keys") && method === "POST") {
    const id = mockKeys.length + 1;
    mockKeys.push({ id, label: url.searchParams.get("label") ?? "", created_at: new Date().toISOString(), revoked_at: null });
    return json({ id, key: `cd_mock_${id}_${Math.random().toString(36).slice(2, 14)}` }, 201);
  }
  if (/\/api-keys\/\d+$/.test(url.pathname) && method === "DELETE") {
    const key = mockKeys.find((k) => k.id === Number(url.pathname.split("/").pop()));
    if (key) key.revoked_at = new Date().toISOString();
    return json({ status: "revoked" });
  }
  if (url.pathname.endsWith("/api-keys")) return json(mockKeys);
  return json({ error: { code: "not_found", message: `no mock for ${url.pathname}`, request_id: "mock" } }, 404);
};
