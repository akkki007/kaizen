import { describe, expect, it } from "vitest";
import { mockIncidentFetch } from "../api/mock-incident";
import type { components } from "../api/schema";
import { parseUnifiedDiff } from "../diff";
import { geometry, niceMax } from "../metric-chart";
import { currentStage, pipeline } from "../pipeline";

type S = components["schemas"];

describe("pipeline", () => {
  const base = { investigationStatus: "not_run", remediationStatus: null, history: ["open"] };
  const states = (p: Parameters<typeof pipeline>[0]) => pipeline(p).map((s) => s.state);

  it("walks the S1 flow from detection to memory", () => {
    expect(states({ ...base, status: "open" })).toEqual(["done", "pending", "pending", "pending", "pending", "pending"]);
    expect(states({ ...base, status: "investigating", history: ["open", "investigating"] })).toEqual(["done", "active", "active", "pending", "pending", "pending"]);
    const explained = { ...base, status: "explained", investigationStatus: "explained", history: ["open", "investigating", "explained"] };
    expect(states(explained)).toEqual(["done", "done", "done", "pending", "pending", "pending"]);
    expect(states({ ...explained, status: "remediating", remediationStatus: "proposing" })).toEqual(["done", "done", "done", "active", "pending", "pending"]);
    expect(states({ ...explained, status: "verifying", remediationStatus: "verifying" })).toEqual(["done", "done", "done", "done", "active", "pending"]);
    expect(states({ ...explained, status: "verifying", remediationStatus: "verified" })).toEqual(["done", "done", "done", "done", "done", "pending"]);
    const done = pipeline({ ...explained, status: "resolved", remediationStatus: "merged" });
    expect(done.map((s) => s.state)).toEqual(["done", "done", "done", "done", "done", "done"]);
    expect(currentStage(done)).toBe("remember");
  });

  it("marks failures and skipped memory", () => {
    const s = pipeline({ ...base, status: "needs_human", investigationStatus: "budget_exhausted", remediationStatus: "failed", history: ["open", "investigating", "identified"] });
    expect(s.find((x) => x.id === "explain")).toMatchObject({ state: "failed", note: "LLM budget exhausted" });
    expect(s.find((x) => x.id === "verify")?.state).toBe("failed");
    expect(currentStage(s)).toBe("explain");
    expect(pipeline({ ...base, status: "closed" }).at(-1)?.state).toBe("skipped");
    expect(pipeline({ ...base, status: "explained", investigationStatus: "fallback" })[2].note).toContain("template");
  });
});

describe("parseUnifiedDiff", () => {
  it("numbers added, removed and context lines per hunk", () => {
    const files = parseUnifiedDiff("diff --git a/x.js b/x.js\nindex 1..2 100644\n--- a/x.js\n+++ b/x.js\n@@ -10,3 +10,3 @@ fn\n a\n-b\n+c\n d\n");
    expect(files).toHaveLength(1);
    expect(files[0]).toMatchObject({ oldPath: "x.js", newPath: "x.js", added: 1, removed: 1 });
    expect(files[0].hunks[0].lines).toEqual([
      { type: "ctx", text: "a", oldNo: 10, newNo: 10 },
      { type: "del", text: "b", oldNo: 11, newNo: null },
      { type: "add", text: "c", oldNo: null, newNo: 11 },
      { type: "ctx", text: "d", oldNo: 12, newNo: 12 },
    ]);
  });
});

describe("metric chart geometry", () => {
  it("scales to a nice maximum and breaks the line at gaps", () => {
    expect(niceMax(0.42)).toBe(0.5);
    expect(niceMax(880)).toBe(1000);
    const g = geometry([{ t: 0, v: 0 }, { t: 10, v: 1 }, { t: 20, v: null }, { t: 30, v: 0.5 }], 0, 30, 300, 100, 0);
    expect(g.max).toBe(1);
    expect(g.line.match(/M/g)).toHaveLength(2);
    expect(g.x(30)).toBe(300);
    expect(g.y(1)).toBe(0);
  });
});

async function get<T>(path: string): Promise<T> {
  const r = await mockIncidentFetch(new URL(path, "http://mock"), "GET");
  expect(r?.status).toBe(200);
  return (await r!.json()) as T;
}

describe("mock incident data", () => {
  it.each(["inc-1", "inc-2", "inc-4", "inc-8"])("every citation in %s resolves to the evidence graph", async (id) => {
    const d = await get<S["IncidentDetail"]>(`/api/v1/incidents/${id}`);
    const g = await get<S["EvidenceGraph"]>(`/api/v1/incidents/${id}/evidence-graph`);
    const ids = new Set([...g.nodes.map((n) => n.id), ...g.edges.map((e) => e.id)]);
    const ex = d.investigation.explanation as { claims: { cited_node_ids: string[] }[]; causal_chain?: { cited_node_ids: string[] }[]; alternatives?: { cited_node_ids?: string[] }[] };
    const cited = [...ex.claims, ...(ex.causal_chain ?? []), ...(ex.alternatives ?? [])].flatMap((c) => c.cited_node_ids ?? []);
    const evidence = d.ranking.candidates.flatMap((c) => c.features.flatMap((f) => f.evidence_ids ?? []));
    expect(cited.length).toBeGreaterThan(0);
    expect([...cited, ...evidence].filter((c) => !ids.has(c))).toEqual([]);
    expect(g.edges.every((e) => ids.has(e.source) && ids.has(e.target))).toBe(true);
  });

  it("S8 resembles the resolved S1 incident and reuses its fix", async () => {
    const sim = await get<S["SimilarIncidentView"][]>("/api/v1/incidents/inc-8/similar");
    expect(sim[0]).toMatchObject({ incident_id: "inc-1", status: "resolved", fix: { kind: "revert" } });
    const d = await get<S["IncidentDetail"]>("/api/v1/incidents/inc-8");
    expect(d.remediation?.prior_fixes[0]).toMatchObject({ incident_id: "inc-1" });
    expect(d.remediation?.rationale).toContain("inc-1");
  });

  it("approve needs a verified fix; reject needs a reason", async () => {
    const post = (p: string, body?: unknown) => mockIncidentFetch(new URL(p, "http://mock"), "POST", body ? { method: "POST", body: JSON.stringify(body) } : { method: "POST" });
    expect((await post("/api/v1/remediations/rem-inc-8/reject", { reason: " " }))!.status).toBe(422);
    const ok = await post("/api/v1/remediations/rem-inc-2/approve");
    expect(await ok!.json()).toMatchObject({ status: "approved", approved_by: "demo" });
    expect((await post("/api/v1/remediations/rem-inc-2/approve"))!.status).toBe(409);
    const d = await get<S["IncidentDetail"]>("/api/v1/incidents/inc-2");
    expect(d.remediation).toMatchObject({ status: "approved", approved_by: "demo" });
  });

  it("serves overlays and node details", async () => {
    const g = await get<S["EvidenceGraph"]>("/api/v1/incidents/inc-2/evidence-graph?overlay=blast");
    expect(g.nodes.some((n) => n.overlay === "blast")).toBe(true);
    const n = await get<S["NodeDetail"]>(`/api/v1/incidents/inc-2/nodes/${encodeURIComponent("Commit:9f2c1ab")}`);
    expect(n.commit?.changed_functions[0].function_id).toBe("money-utils:lib/index.js:toCents");
  });
});
