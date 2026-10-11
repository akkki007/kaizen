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

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

export const mockFetch: typeof fetch = async (input) => {
  const url = new URL(typeof input === "string" ? input : (input as Request).url, "http://mock");
  await new Promise((r) => setTimeout(r, 250)); // exercise loading states
  if (url.pathname === "/auth/me") return json(mockUser);
  if (url.pathname === "/api/v1/incidents") return json(mockIncidents);
  return json({ error: { code: "not_found", message: `no mock for ${url.pathname}`, request_id: "mock" } }, 404);
};
