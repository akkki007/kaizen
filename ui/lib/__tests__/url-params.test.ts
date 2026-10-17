import { describe, expect, it } from "vitest";
import { applyPatch, baseQuery, settle } from "../url-params";

describe("url params without lost updates", () => {
  it("applies patches, deleting null and empty values", () => {
    expect(applyPatch("status=open&q=x", { q: null, severity: "sev1" })).toBe("status=open&severity=sev1");
    expect(applyPatch("", { q: "" })).toBe("");
  });

  it("a second update builds on the first while the URL has not caught up", () => {
    const current = "status=all"; // what the page last rendered
    let queue: string[] = [];
    const first = applyPatch(baseQuery(queue, current), { severity: "sev1" });
    queue = [...queue, first];
    // the debounced search fires before the router committed `first`
    const second = applyPatch(baseQuery(queue, current), { q: "orders" });
    expect(new URLSearchParams(second).get("severity")).toBe("sev1"); // not lost
    expect(new URLSearchParams(second).get("q")).toBe("orders");
    queue = [...queue, second];
    // the URL catches up step by step
    expect(settle(queue, first)).toEqual([second]);
    expect(settle(queue, second)).toEqual([]);
  });

  it("an outside navigation (back button) clears the queue", () => {
    expect(settle(["a=1", "a=1&b=2"], "c=3")).toEqual([]);
    expect(baseQuery([], "c=3")).toBe("c=3");
  });
});
