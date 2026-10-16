import { describe, expect, it } from "vitest";
import { ago, serviceColor, toWorkspace } from "../workspace";

const NOW = Date.parse("2026-10-11T12:00:00Z");
const min = (m: number) => new Date(NOW - m * 60_000).toISOString();
const incident = (id: string, service: string, status: string, detected: number, onset: number, repo = "acme/shop") => ({
  id, repo, service, env: "production", endpoint: "POST /orders", status, detected_at: min(detected), onset_ts: min(onset),
});

describe("toWorkspace", () => {
  const ws = toWorkspace(
    [
      incident("inc-1", "payments", "resolved", 2000, 2004),
      incident("inc-3", "orders", "open", 10, 14),
      incident("inc-2", "payments", "investigating", 180, 186),
    ],
    { "acme/shop": "approver", "acme/docs": "viewer" },
    NOW,
  );

  it("sorts incidents newest first and splits out the open ones", () => {
    expect(ws.incidents.map((i) => i.id)).toEqual(["inc-3", "inc-2", "inc-1"]);
    expect(ws.open.map((i) => i.id)).toEqual(["inc-3", "inc-2"]);
  });

  it("counts incidents per service, busiest first, with a stable color", () => {
    expect(ws.services).toEqual([
      { name: "payments", color: serviceColor("payments"), total: 2, open: 1 },
      { name: "orders", color: serviceColor("orders"), total: 1, open: 1 },
    ]);
  });

  it("lists every accessible repository, including ones without incidents", () => {
    expect(ws.repos.map((r) => [r.name, r.role, r.open, r.total])).toEqual([
      ["acme/shop", "approver", 2, 3],
      ["acme/docs", "viewer", 0, 0],
    ]);
    expect(ws.repos[1].last).toBeNull();
    expect(ws.repos[0].daily).toHaveLength(14);
  });

  it("buckets per day and averages time to detect", () => {
    expect(ws.week).toHaveLength(7);
    expect(ws.week.at(-1)).toBe(2);
    expect(ws.month.reduce((a, b) => a + b, 0)).toBe(3);
    expect(ws.mttdMinutes).toBeCloseTo(14 / 3);
  });

  it("is safe with no incidents", () => {
    const empty = toWorkspace([], {}, NOW);
    expect(empty.services).toEqual([]);
    expect(empty.mttdMinutes).toBeNull();
    expect(empty.week.every((n) => n === 0)).toBe(true);
  });
});

describe("ago", () => {
  it("formats compact ages", () => {
    expect(ago(20_000)).toBe("now");
    expect(ago(5 * 60_000)).toBe("5m");
    expect(ago(3 * 3_600_000)).toBe("3h");
    expect(ago(3 * 86_400_000)).toBe("3d");
  });
});
