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
    { id: "inc-3", repo: "yashchandnani07/shoplite", service: "orders", env: "production", endpoint: "POST /orders", status: "open", onset_ts: ago(14), detected_at: ago(11) },
    { id: "inc-2", repo: "yashchandnani07/shoplite", service: "payments", env: "production", endpoint: "POST /payments", status: "investigating", onset_ts: ago(190), detected_at: ago(186) },
    { id: "inc-1", repo: "yashchandnani07/shoplite", service: "payments", env: "production", endpoint: "POST /payments", status: "resolved", onset_ts: ago(2900), detected_at: ago(2896) },
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

type RankResult = components["schemas"]["RankResult"];

const FEATURE_NAMES = ["temporal", "path", "endpoint", "service", "dependency", "msg_sim", "history", "sequence", "blast"] as const;
const WEIGHTS: Record<(typeof FEATURE_NAMES)[number], number> = {
  temporal: 0.15, path: 0.25, endpoint: 0.15, service: 0.1, dependency: 0.1, msg_sim: 0.05, history: 0.1, sequence: 0.1, blast: -0.1,
};
const features = (v: Record<string, number>) =>
  FEATURE_NAMES.map((name) => ({
    name,
    value: v[name] ?? 0,
    weight: WEIGHTS[name],
    contribution: Math.round(WEIGHTS[name] * (v[name] ?? 0) * 10000) / 10000,
    query: `feature.${name}`,
    cypher: `// ${name}\nMATCH (c:Commit {sha: $sha})-[:modifies_fn]->(f:Function)\nWITH f, shortestPath((f)-[:calls*1..6]->(g)) AS p\nRETURN 1.0 / (1 + min(length(p)))`,
  }));

const mockRanking = (id: string, method: "graph" | "b0" | "b1" = "graph"): RankResult => {
  const graph = [
    { sha: "9f2c1ab", message: "Round amounts in toCents", author: "bruno", deployment: "d-41", rank: 1, score: 0.78, confidence: 0.86,
      features: features({ temporal: 0.9, path: 1, endpoint: 1, service: 1, dependency: 0, msg_sim: 0.2, history: 0.5, sequence: 1, blast: 0.3 }),
      evidence: [{ changed_function: "money-utils:lib/index.js:toCents", thrown_function: "payments:src/app.js:POST /payments", error: "fp-7c1" }] },
    { sha: "4be0d77", message: "docs: update README", author: "alice", deployment: "d-41", rank: 2, score: 0.21, confidence: 0.09, features: features({ temporal: 0.9, sequence: 1 }), evidence: [] },
    { sha: "a11c9e0", message: "chore: bump express", author: "dana", deployment: "d-41", rank: 3, score: 0.12, confidence: 0.05, features: features({ temporal: 0.9 }), evidence: [] },
  ];
  if (method === "graph") return { incident_id: id, method, verdict: "confident", margin: 0.57, candidates: graph };
  const base = [...graph].reverse().map((c, i) => ({ ...c, rank: i + 1, score: 1 - i * 0.4, confidence: 0, features: [], evidence: [] }));
  return { incident_id: id, method, verdict: "confident", margin: 0, candidates: base };
};

const mockDetail = (id: string): components["schemas"]["IncidentDetail"] => {
  const inc = mockIncidents.items.find((i) => i.id === id) ?? mockIncidents.items[0];
  return {
    incident: inc,
    remediation: {
      id: "rem-1", kind: "revert", status: "verified", sha: "9f2c1ab", branch: "kaizen/revert-9f2c1ab",
      pr_url: "https://github.com/yashchandnani07/shoplite/pull/14", approved_by: null,
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
  if (/\/remediations\/[^/]+\/approve$/.test(url.pathname) && method === "POST") {
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
