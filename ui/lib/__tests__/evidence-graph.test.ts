import { describe, expect, it } from "vitest";
import { toElements } from "../evidence-graph";

const graph = {
  incident_id: "inc-1",
  nodes: [
    { id: "Incident:inc-1", label: "Incident", props: {}, highlight: true },
    { id: "Function:toCents", label: "Function", props: { name: "toCents" }, highlight: false },
  ],
  edges: [
    { id: "a", source: "Incident:inc-1", target: "Function:toCents", rel: "AFFECTS", props: {}, highlight: true },
    { id: "dangling", source: "Incident:inc-1", target: "Missing:x", rel: "X", props: {}, highlight: false },
  ],
};

describe("toElements", () => {
  const els = toElements(graph);
  it("marks the causal path and labels nodes by name or key", () => {
    expect(els.find((e) => e.data.id === "Incident:inc-1")).toMatchObject({ classes: "hl", data: { label: "inc-1" } });
    expect(els.find((e) => e.data.id === "Function:toCents")?.data.label).toBe("toCents");
    expect(els.find((e) => e.data.id === "a")?.classes).toBe("hl");
  });
  it("drops edges whose endpoints are not in the graph", () => {
    expect(els.find((e) => e.data.id === "dangling")).toBeUndefined();
  });
});
