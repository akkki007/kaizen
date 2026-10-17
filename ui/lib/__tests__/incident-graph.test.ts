import { describe, expect, it } from "vitest";
import { LEGEND, NODE_STYLE, buildModel, describeNode, mergeGraphs, nextNode, resolveCitation, startNode, type Graph } from "../evidence-graph";

const LABELS = ["Incident", "Deployment", "Commit", "File", "Function", "Endpoint", "Service", "Error", "Fix", "Test", "ConfigChange", "DependencyChange"];

function bigGraph(n: number): Graph {
  const nodes = Array.from({ length: n }, (_, i) => ({ id: i === 0 ? "Incident:inc-1" : `Function:svc:f${i}`, label: i === 0 ? "Incident" : LABELS[i % LABELS.length], props: { name: `f${i}` }, highlight: i < 5 }));
  const edges = [];
  for (let i = 1; i < n; i++) {
    edges.push({ id: `${nodes[Math.floor((i - 1) / 3)].id}|calls|${nodes[i].id}`, source: nodes[Math.floor((i - 1) / 3)].id, target: nodes[i].id, rel: "calls", props: {}, highlight: false });
    if (i % 7 === 0) edges.push({ id: `${nodes[i].id}|calls|${nodes[i - 5].id}`, source: nodes[i].id, target: nodes[i - 5].id, rel: "calls", props: {}, highlight: false });
  }
  return { incident_id: "inc-1", nodes, edges };
}

describe("legend", () => {
  it("has a distinct colour and shape for each of the 12 node types", () => {
    expect(LEGEND).toEqual(LABELS);
    expect(new Set(LEGEND.map((l) => NODE_STYLE[l].color)).size).toBe(12);
    expect(new Set(LEGEND.map((l) => NODE_STYLE[l].shape)).size).toBe(12);
  });
});

describe("buildModel", () => {
  it("builds a 500-node model quickly and indexes it", () => {
    const g = bigGraph(500);
    const t = performance.now();
    const m = buildModel(g);
    const ms = performance.now() - t;
    expect(m.nodes.size).toBe(500);
    expect(m.elements.length).toBe(500 + g.edges.length);
    expect(ms).toBeLessThan(150);
    expect(m.adjacency.get("Incident:inc-1")!.map((x) => x.id)).toEqual(["Function:svc:f1", "Function:svc:f2", "Function:svc:f3"]);
    expect(m.elements.find((e) => e.data.id === "Function:svc:f2")?.data.shape).toBe(NODE_STYLE.Commit.shape);
  });

  it("resolves node and edge citations, rejects unknown ids", () => {
    const m = buildModel(bigGraph(10));
    expect(resolveCitation(m, "Function:svc:f1")).toEqual({ kind: "node", id: "Function:svc:f1" });
    expect(resolveCitation(m, "Incident:inc-1|calls|Function:svc:f1")?.kind).toBe("edge");
    expect(resolveCitation(m, "Commit:ghost")).toBeNull();
  });
});

describe("keyboard navigation", () => {
  const m = buildModel(bigGraph(10));
  const pos = { "Incident:inc-1": { x: 0, y: 0 }, "Function:svc:f1": { x: -100, y: 100 }, "Function:svc:f2": { x: 0, y: 100 }, "Function:svc:f3": { x: 100, y: 100 } };
  it("moves to the connected node in the arrow's direction", () => {
    expect(nextNode(m.adjacency, pos, "Incident:inc-1", "down")).toBe("Function:svc:f2");
    expect(nextNode(m.adjacency, pos, "Function:svc:f3", "up")).toBe("Incident:inc-1");
    expect(nextNode(m.adjacency, pos, "Incident:inc-1", "up")).toBeNull();
    expect(nextNode(m.adjacency, pos, "Function:svc:f2", "right")).toBeNull(); // f3 is not connected to f2
  });
  it("starts at the selection or the incident and describes the focused node", () => {
    expect(startNode(m, null)).toBe("Incident:inc-1");
    expect(startNode(m, "Incident:inc-1|calls|Function:svc:f2")).toBe("Incident:inc-1");
    const text = describeNode(m, "Incident:inc-1");
    expect(text).toContain("Incident f0, on the causal path");
    expect(text).toContain("3 connections");
    expect(text).toContain("Enter opens the inspector");
  });
});

describe("mergeGraphs", () => {
  it("adds overlay-only nodes and edges, keeping base versions", () => {
    const base = bigGraph(4);
    const ov: Graph = {
      incident_id: "inc-1",
      nodes: [{ ...base.nodes[1], overlay: "blast" }, { id: "Endpoint:svc:GET /x", label: "Endpoint", props: {}, highlight: false, overlay: "blast" }],
      edges: [{ id: "Function:svc:f1|handles|Endpoint:svc:GET /x", source: "Function:svc:f1", target: "Endpoint:svc:GET /x", rel: "handles", props: {}, highlight: false, overlay: "blast" }],
    };
    const g = mergeGraphs(base, ov, undefined);
    expect(g.nodes).toHaveLength(5);
    expect(g.nodes.find((n) => n.id === "Function:svc:f1")?.overlay).toBeUndefined();
    expect(g.nodes.find((n) => n.id === "Endpoint:svc:GET /x")?.overlay).toBe("blast");
    expect(g.edges).toHaveLength(base.edges.length + 1);
  });
});
