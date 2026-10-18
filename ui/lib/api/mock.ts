// Mock API mode (NEXT_PUBLIC_API_MOCK=1): the UI runs without the backend. Fixtures mirror the
// seeded ShopLite incident so the screens look like the real thing. Shapes are checked against
// the generated OpenAPI types.
import { dashboardMock, mockApproved, mockIncidents } from "./mock-dashboard";
import { mockIncidentFetch } from "./mock-incident";
import type { components } from "./schema";

type User = components["schemas"]["User"];

const NOW = Date.now();
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

export const mockUser: User = {
  login: "demo",
  repos: { "yashchandnani07/shoplite": "approver", "acme/docs-site": "viewer" },
};

export { mockIncidents };

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
    { id: "Commit:9f2c1ab", label: "Commit", props: { kind: "commit" as const, node_id: "Commit:9f2c1ab", sha: "9f2c1ab", message: "Round amounts in toCents" }, highlight: true },
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

type RankResult = components["schemas"]["RankResult"];

const FEATURE_NAMES = ["temporal", "path", "endpoint", "service", "dependency", "msg_sim", "history", "sequence", "centrality", "blast"] as const;
const WEIGHTS: Record<(typeof FEATURE_NAMES)[number], number> = {
  temporal: 0.15, path: 0.25, endpoint: 0.15, service: 0.1, dependency: 0.1, msg_sim: 0.05, history: 0.1, sequence: 0.05, centrality: 0.05, blast: -0.1,
};
const features = (v: Record<string, number>) =>
  FEATURE_NAMES.map((name) => ({
    name,
    value: v[name] ?? 0,
    weight: WEIGHTS[name],
    contribution: Math.round(WEIGHTS[name] * (v[name] ?? 0) * 10000) / 10000,
    query: `feature.${name}`,
    explanation: `${name} = ${(v[name] ?? 0).toFixed(2)}`,
    evidence_ids: [] as string[],
    cypher: `// ${name}\nMATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)\nWITH f, shortestPath((f)-[:calls*1..6]->(g)) AS p\nRETURN 1.0 / (1 + min(length(p)))`,
  }));

const mockRanking = (id: string, method: "graph" | "b0" | "b1" = "graph"): RankResult => {
  const graph = [
    { kind: "commit" as const, node_id: "Commit:9f2c1ab", sha: "9f2c1ab", message: "Round amounts in toCents", author: "bruno", deployment: "d-41", rank: 1, score: 0.78, confidence: 0.86,
      features: features({ temporal: 0.9, path: 1, endpoint: 1, service: 1, dependency: 0, msg_sim: 0.2, history: 0.5, sequence: 1, centrality: 0.8, blast: 0.3 }),
      evidence: [{ changed_function: "money-utils:lib/index.js:toCents", thrown_function: "payments:src/app.js:POST /payments", error: "fp-7c1" }] },
    { kind: "commit" as const, node_id: "Commit:4be0d77", sha: "4be0d77", message: "docs: update README", author: "alice", deployment: "d-41", rank: 2, score: 0.21, confidence: 0.09, features: features({ temporal: 0.9, sequence: 1 }), evidence: [] },
    { kind: "commit" as const, node_id: "Commit:a11c9e0", sha: "a11c9e0", message: "chore: bump express", author: "dana", deployment: "d-41", rank: 3, score: 0.12, confidence: 0.05, features: features({ temporal: 0.9 }), evidence: [] },
  ];
  if (method === "graph") return { incident_id: id, method, verdict: "confident", margin: 0.57, weights_version: "v1", candidates: graph };
  const base = [...graph].reverse().map((c, i) => ({ ...c, rank: i + 1, score: 1 - i * 0.4, confidence: 0, features: [], evidence: [] }));
  return { incident_id: id, method, verdict: "confident", margin: 0, weights_version: "v1", candidates: base };
};

const mockDetail = (id: string): components["schemas"]["IncidentDetail"] => {
  const inc = mockIncidents.items.find((i) => i.id === id) ?? mockIncidents.items[0];
  return {
    incident: inc,
    remediation: {
      id: "rem-1", kind: "revert", sha: "9f2c1ab", branch: "kaizen/fix-" + id,
      ...(mockApproved.has("rem-1") ? { status: "approved", approved_by: mockUser.login } : { status: "verified", approved_by: null }),
      pr_url: "https://github.com/yashchandnani07/shoplite/pull/14", labels: ["kaizen", "incident:" + id], prior_fixes: [],
      verification: { passed: true, run_url: "https://github.com/yashchandnani07/shoplite/actions/runs/1", error_rate_before: 0.42, error_rate_after: 0.004, baseline_error_rate: 0.005 },
    },
    ranking: mockRanking(id),
    investigation: {
      id: 1,
      status: "explained",
      explanation: {
        verdict: "confident",
        culprit_sha: "9f2c1ab",
        claims: [
          { text: "toCents was changed in 9f2c1ab and is the function that threw on POST /payments.", cited_node_ids: ["Commit:9f2c1ab", "Function:money-utils:lib/index.js:toCents"] },
          { text: "The failure began 2 minutes after deployment d-41, which contains this commit.", cited_node_ids: ["Deployment:d-41", "Incident:" + id] },
        ],
        suggested_fix: "Revert 9f2c1ab",
      },
      validation_errors: [],
      steps: [
        { n: 1, name: "get_incident", args: { incident_id: id }, summary: "get_incident: errors=1 item(s)", latency_ms: 38, node_ids: ["Incident:" + id, "Error:fp-7c1"] },
        { n: 2, name: "rank_candidates", args: { incident_id: id }, summary: "rank_candidates: verdict=confident, candidates=3 item(s)", latency_ms: 212, node_ids: ["Commit:9f2c1ab"] },
        { n: 3, name: "shortest_call_path", args: { sha: "9f2c1ab", incident_id: id }, summary: "shortest_call_path: paths=1 item(s)", latency_ms: 64, node_ids: ["Function:money-utils:lib/index.js:toCents"] },
        { n: 4, name: "similar_incidents", args: { incident_id: id }, summary: "similar_incidents: similar=0 item(s)", latency_ms: 31, node_ids: [] },
      ],
    },
  };
};

// Connect flow fixtures. `?fresh=1` on the page URL starts shoplite as not yet imported so the whole
// journey (choose, start, importing, ready) can be exercised without a backend.
type Onboarding = components["schemas"]["RepoOnboarding"];
let connectedAt: number | null = null;
const importingPolls = 3;
let polls = 0;

const mockOnboarding = (): Onboarding[] => {
  const fresh = typeof window !== "undefined" && new URLSearchParams(window.location.search).has("fresh");
  const stats = { commits: 148, functions: 37, services: 4, endpoints: 12 };
  let shoplite: Onboarding = { repo: "yashchandnani07/shoplite", role: "approver", state: "ready", detail: null, backfilled_at: ago(120), stats, incidents: 3, has_api_key: true };
  if (fresh && connectedAt === null) {
    shoplite = { ...shoplite, state: "not_started", backfilled_at: null, stats: { commits: 0, functions: 0, services: 0, endpoints: 0 }, incidents: 0, has_api_key: false };
  } else if (fresh && connectedAt !== null) {
    polls += 1;
    const done = polls > importingPolls;
    const f = Math.min(1, polls / (importingPolls + 1));
    shoplite = { ...shoplite, state: done ? "ready" : "importing", backfilled_at: done ? ago(0) : null, incidents: 0, has_api_key: false, stats: { commits: Math.round(stats.commits * f), functions: Math.round(stats.functions * f), services: done ? 4 : 1, endpoints: Math.round(stats.endpoints * f) } };
  }
  const docs: Onboarding = { repo: "acme/docs-site", role: "viewer", state: "not_started", detail: null, backfilled_at: null, stats: { commits: 0, functions: 0, services: 0, endpoints: 0 }, incidents: 0, has_api_key: false };
  return [shoplite, docs];
};

// Code graph fixture for the Live graph page: mock "inject" flips one function red and one commit amber.
let injected = false;
const mockCodeGraph = (repo: string): components["schemas"]["CodeGraph"] => {
  const fn = (svc: string, name: string, detail = "") => ({ id: `${svc}:${name}`, kind: "function" as const, label: name, parent: `svc:${svc}`, state: "ok" as "ok" | "failing" | "suspect", incident_id: null as string | null, detail });
  const nodes: components["schemas"]["GraphNode"][] = [
    { id: "svc:payments", kind: "service", label: "payments", parent: null, state: "ok", incident_id: null, detail: "" },
    { id: "svc:orders", kind: "service", label: "orders", parent: null, state: "ok", incident_id: null, detail: "" },
    fn("payments", "handlePayment", "src/routes.js · serves POST /payments"),
    fn("payments", "validatePayment", "src/payment/validation.js"),
    fn("payments", "toCents"),
    fn("orders", "createOrder", "src/app.js · serves POST /orders"),
    fn("orders", "listOrders"),
    { id: "commit:c0ffee1", kind: "commit", label: "c0ffee1", parent: null, state: "ok", incident_id: null, detail: "Refactor payment validation" },
    { id: "commit:a11ce22", kind: "commit", label: "a11ce22", parent: null, state: "ok", incident_id: null, detail: "docs: update README" },
  ];
  if (injected) {
    nodes.find((n) => n.id === "payments:validatePayment")!.state = "failing";
    nodes.find((n) => n.id === "payments:validatePayment")!.incident_id = "inc-3";
    nodes.find((n) => n.id === "commit:c0ffee1")!.state = "suspect";
  }
  const edges = [
    { source: "payments:handlePayment", target: "payments:validatePayment", rel: "calls" },
    { source: "payments:handlePayment", target: "payments:toCents", rel: "calls" },
    { source: "orders:createOrder", target: "payments:handlePayment", rel: "calls" },
    { source: "orders:createOrder", target: "orders:listOrders", rel: "calls" },
    { source: "commit:c0ffee1", target: "payments:validatePayment", rel: "modifies" },
    { source: "commit:a11ce22", target: "orders:listOrders", rel: "modifies" },
  ];
  return { repo, nodes, edges, demo_enabled: true, query_ms: { functions: 4.1, failing: 1.2 }, queries: { functions: "MATCH (f:Function)-[:belongs_to]->(s:Service) RETURN f.id, f.name, f.path, s.id", failing: "MATCH (i:Incident)-[:evidenced_by]->(:Error)-[:thrown_in]->(f:Function) WHERE i.status <> 'resolved' RETURN DISTINCT f.id, i.id" } };
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

export const mockFetch: typeof fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : (input as Request).url, "http://mock");
  // the real API redirects/serves a trailing slash as the collection, so the mock does too
  if (url.pathname.length > 1 && url.pathname.endsWith("/")) url.pathname = url.pathname.slice(0, -1);
  const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
  await new Promise((r) => setTimeout(r, 250)); // exercise loading states
  const incidentPage = await mockIncidentFetch(url, method, init, input); // incident page (#93)
  if (incidentPage) return incidentPage;
  if (url.pathname === "/auth/me") return json(mockUser);
  if (url.pathname === "/auth/install-url") {
    return json({ url: "https://github.com/apps/kaizen-graphacks/installations/new", demo: "true", demo_repo: "yashchandnani07/shoplite" });
  }
  if (url.pathname === "/auth/demo" && method === "POST") {
    return json({ login: "demo", repos: { "yashchandnani07/shoplite": "viewer" } });
  }
  if (url.pathname === "/api/v1/onboarding") return json(mockOnboarding());
  if (url.pathname.endsWith("/connect") && method === "POST") {
    connectedAt = Date.now();
    polls = 0;
    return json({ repo: url.pathname.split("/").slice(4, 6).join("/"), state: "importing", job_id: "job-connect-1" }, 202);
  }
  if (url.pathname.endsWith("/code-graph")) return json(mockCodeGraph(url.pathname.split("/").slice(4, 6).join("/")));
  if (url.pathname.endsWith("/demo/inject") && method === "POST") {
    injected = true;
    return json({ incident_id: "inc-3", simulated: true, service: "payments", endpoint: "POST /payments", function: "payments:validatePayment", commit: "c0ffee1", commit_message: "Refactor payment validation" });
  }
  if (url.pathname.endsWith("/demo/reset") && method === "POST") {
    injected = false;
    return json({ rows_removed: 4 });
  }
  if (url.pathname === "/auth/logout") return json({ status: "logged_out" });
  const dash = dashboardMock(url, method);
  if (dash) return dash;
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
  if (/\/remediations\/[^/]+\/approve$/.test(url.pathname) && method === "POST") {
    mockApproved.add(url.pathname.split("/")[4]);
    return json({ id: "rem-1", status: "approved", approved_by: "demo" });
  }
  if (/\/api\/v1\/incidents\/[^/]+\/remediations$/.test(url.pathname) && method === "POST") {
    return json({ job_id: "job-1", status: "queued" }, 202);
  }
  if (/\/api\/v1\/incidents\/[^/]+\/rank$/.test(url.pathname) && method === "POST") {
    const method2 = (url.searchParams.get("method") ?? "graph") as "graph" | "b0" | "b1";
    return json(mockRanking(url.pathname.split("/")[4], method2));
  }
  if (/\/api\/v1\/incidents\/[^/]+$/.test(url.pathname)) return json(mockDetail(decodeURIComponent(url.pathname.split("/")[4])));
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
