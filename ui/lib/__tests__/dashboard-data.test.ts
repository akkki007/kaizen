import { describe, expect, it } from "vitest";
import { toDashboardData } from "../dashboard-data";

const NOW = Date.parse("2026-10-11T12:00:00Z");
const min = (m: number) => new Date(NOW - m * 60_000).toISOString();
const incident = (id: string, service: string, status: string, detected: number, onset: number) => ({
  id, repo: "acme/shop", service, env: "production", status, detected_at: min(detected), onset_ts: min(onset),
});

describe("toDashboardData", () => {
  const data = toDashboardData(
    [
      incident("inc-3", "orders", "open", 10, 14),
      incident("inc-2", "payments", "investigating", 180, 186),
      incident("inc-1", "payments", "resolved", 2000, 2004),
    ],
    { "acme/shop": "approver" },
    NOW,
  );

  it("maps services to people and incidents to review-queue items with status kinds", () => {
    expect(data.people.map((p) => p.handle)).toEqual(["orders", "payments"]);
    expect(data.pullRequests.map((p) => [p.number, p.status.kind])).toEqual([
      [3, "open"], [2, "review"], [1, "merged"],
    ]);
    expect(data.pullRequests[0].today).toBe(true);
    expect(data.pullRequests.every((p) => data.people.some((x) => x.handle === p.author))).toBe(true);
  });

  it("computes per-range KPIs and activity series with matching lengths", () => {
    const today = data.kpis.today.find((k) => k.id === "open");
    expect(today?.value).toBe("2");
    for (const r of data.ranges) {
      const a = data.activity[r.id];
      expect(a.labels.length).toBeGreaterThan(0);
      for (const s of Object.values(a.series)) expect(s).toHaveLength(a.labels.length);
    }
    expect(data.kpis.today.find((k) => k.id === "mttd")?.value).toBe("5.0m");
  });

  it("lists accessible repositories with their open incident counts", () => {
    expect(data.repositories).toMatchObject([{ name: "shop", openPrs: 2 }]);
  });

  it("is safe with no incidents", () => {
    const empty = toDashboardData([], { "acme/shop": "viewer" }, NOW);
    expect(empty.pullRequests).toEqual([]);
    expect(empty.people).toHaveLength(1);
    expect(empty.kpis.week[0].value).toBe("0");
  });
});
