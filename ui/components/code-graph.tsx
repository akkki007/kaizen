"use client";

import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { useEffect, useMemo, useRef } from "react";
import type { components } from "@/lib/api/schema";

type CodeGraph = components["schemas"]["CodeGraph"];
type GNode = components["schemas"]["GraphNode"];

const GREEN = "#d9f99d #34d399";
const RED = "#fca5a5 #dc2626";
const AMBER = "#fde68a #f59e0b";
const CELL_W = 96;
const CELL_H = 62;
const MAX_ROW_W = 1180;

export const isTestFile = (n: GNode) => /(^|[/_.-])(test|tests|spec|__tests__)([/_.-]|$)/i.test(n.detail.split(" · ")[0] ?? "");

/**
 * Readable map of a repository. Services are boxes laid out in rows with no overlap; functions sit on
 * a grid inside their service. Call lines stay hidden until they matter: the failing path, a hovered
 * or selected node. Everything is green; only what failed is red, and the suspect commit is amber.
 */
export function CodeGraphView({
  graph,
  selected,
  query,
  showTests,
  showCalls,
  fitSignal,
  onSelect,
}: {
  graph: CodeGraph;
  selected: string | null;
  query: string;
  showTests: boolean;
  showCalls: boolean;
  fitSignal: number;
  onSelect: (id: string | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);
  const built = useRef("");
  const focusedOn = useRef("");
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  const visible = useMemo(() => {
    const keep = new Set<string>();
    for (const n of graph.nodes) {
      if (n.kind === "function" && !showTests && isTestFile(n)) continue;
      keep.add(n.id);
    }
    return keep;
  }, [graph.nodes, showTests]);

  // build (positions are computed here, so nothing overlaps and the layout is stable between polls)
  useEffect(() => {
    if (!host.current) return;
    const sig = [...visible].join("|") + "#" + graph.edges.length;
    if (cy.current && !cy.current.destroyed() && built.current === sig) {
      for (const n of graph.nodes) cy.current.getElementById(n.id).data("state", n.state);
      return;
    }
    built.current = sig;
    cy.current?.destroy();

    const services = graph.nodes.filter((n) => n.kind === "service");
    const fnsBy = new Map<string, GNode[]>();
    for (const n of graph.nodes) if (n.kind === "function" && visible.has(n.id)) fnsBy.set(n.parent ?? "", [...(fnsBy.get(n.parent ?? "") ?? []), n]);
    const pos = new Map<string, { x: number; y: number }>();
    // shelf-pack service boxes: biggest first, left to right, wrapping at MAX_ROW_W
    const boxes = services
      .map((s) => {
        const fns = (fnsBy.get(s.id) ?? []).sort((a, b) => a.label.localeCompare(b.label));
        const cols = Math.max(1, Math.ceil(Math.sqrt(fns.length * 1.5)));
        return { s, fns, cols, rows: Math.ceil(fns.length / cols) || 1 };
      })
      .filter((b) => b.fns.length > 0)
      .sort((a, b) => b.fns.length - a.fns.length);
    let x0 = 0;
    let y0 = 0;
    let rowH = 0;
    for (const b of boxes) {
      const w = b.cols * CELL_W + 70;
      const h = b.rows * CELL_H + 90;
      if (x0 > 0 && x0 + w > MAX_ROW_W) {
        x0 = 0;
        y0 += rowH + 40;
        rowH = 0;
      }
      b.fns.forEach((f, i) => pos.set(f.id, { x: x0 + 35 + CELL_W / 2 + (i % b.cols) * CELL_W, y: y0 + 60 + CELL_H / 2 + Math.floor(i / b.cols) * CELL_H }));
      x0 += w + 40;
      rowH = Math.max(rowH, h);
    }
    // commits in their own lane under the services
    const commits = graph.nodes.filter((n) => n.kind === "commit");
    const laneY = y0 + rowH + 110;
    const perRow = Math.max(6, Math.floor(MAX_ROW_W / 66));
    commits.forEach((c, i) => pos.set(c.id, { x: 40 + (i % perRow) * 66, y: laneY + Math.floor(i / perRow) * 56 }));

    const elements: ElementDefinition[] = [
      ...boxes.map((b) => ({ data: { id: b.s.id, label: `${b.s.label}  ·  ${b.fns.length}`, kind: "service", state: "ok" } })),
      ...graph.nodes
        .filter((n) => n.kind !== "service" && visible.has(n.id) && (n.kind === "commit" || pos.has(n.id)))
        .map((n) => ({
          data: { id: n.id, label: n.label, kind: n.kind, parent: n.kind === "function" ? n.parent ?? undefined : undefined, state: n.state },
          position: pos.get(n.id),
        })),
      ...graph.edges
        .filter((e) => visible.has(e.source) && visible.has(e.target))
        .map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target, rel: e.rel } })),
    ];
    const grad = (c: string) => ({ "background-fill": "linear-gradient", "background-gradient-stop-colors": c, "background-gradient-direction": "to-bottom-right" });
    const instance = cytoscape({
      container: host.current,
      elements,
      layout: { name: "preset" },
      minZoom: 0.15,
      maxZoom: 3,
      wheelSensitivity: 0.25,
      boxSelectionEnabled: false,
      style: [
        { selector: "node[kind='service']", style: { shape: "round-rectangle", "background-color": "#10b981", "background-opacity": 0.06, "border-width": 1, "border-color": "#10b981", "border-opacity": 0.4, label: "data(label)", "text-valign": "top", "text-halign": "center", "text-margin-y": -6, color: "#6ee7b7", "font-size": 15, "font-weight": 600, "compound-sizing-wrt-labels": "include", "min-zoomed-font-size": 0, "z-index": 1 } as never },
        { selector: "node[kind='function']", style: { shape: "ellipse", width: 22, height: 22, ...grad(GREEN), "border-width": 1.5, "border-color": "#10b981", label: "data(label)", color: "#cbd5e1", "font-size": 10, "text-valign": "bottom", "text-margin-y": 4, "text-wrap": "ellipsis", "text-max-width": "88px", "min-zoomed-font-size": 6, "z-index": 5 } as never },
        { selector: "node[kind='commit']", style: { shape: "diamond", width: 16, height: 16, ...grad(GREEN), "border-width": 1.5, "border-color": "#10b981", label: "data(label)", color: "#94a3b8", "font-size": 9, "text-valign": "bottom", "text-margin-y": 4, "min-zoomed-font-size": 7, "z-index": 5 } as never },
        { selector: "node[state='failing']", style: { ...grad(RED), "border-color": "#ef4444", "border-width": 3, width: 32, height: 32, color: "#fecaca", "font-size": 12, "font-weight": 700, "min-zoomed-font-size": 0, "z-index": 30 } as never },
        { selector: "node[state='suspect']", style: { ...grad(AMBER), "border-color": "#f59e0b", "border-width": 3, width: 28, height: 28, color: "#fde68a", "font-size": 12, "font-weight": 700, "min-zoomed-font-size": 0, "z-index": 29 } as never },
        { selector: "edge", style: { width: 1.5, "line-color": "#94a3b8", "line-opacity": 0.55, "curve-style": "bezier", "target-arrow-shape": "triangle", "target-arrow-color": "#94a3b8", "arrow-scale": 0.8, display: "none" } as never },
        { selector: "edge.peek", style: { display: "element", "line-opacity": 0.9 } as never },
        { selector: "edge.show", style: { display: "element" } as never },
        { selector: "edge[rel='modifies']", style: { "line-style": "dashed", "line-color": "#fbbf24", "target-arrow-color": "#fbbf24" } as never },
        { selector: "edge.hot", style: { width: 3.5, "line-color": "#ef4444", "target-arrow-color": "#ef4444", "line-opacity": 0.95, "z-index": 25, "line-style": "solid" } as never },
        { selector: ".dim", style: { opacity: 0.18 } },
        { selector: "node.match", style: { "border-color": "#38bdf8", "border-width": 4 } },
        { selector: "node:selected", style: { "overlay-color": "#ffffff", "overlay-opacity": 0.14, "overlay-padding": 7 } },
      ],
    });
    instance.on("tap", "node[kind!='service']", (e) => onSelectRef.current(e.target.id()));
    instance.on("tap", (e) => e.target === instance && onSelectRef.current(null));
    instance.on("mouseover", "node[kind!='service']", (e) => e.target.connectedEdges().addClass("peek"));
    instance.on("mouseout", "node[kind!='service']", (e) => e.target.connectedEdges().removeClass("peek"));
    cy.current = instance;
    focusedOn.current = ""; // a fresh instance gets its own auto-focus
    instance.fit(undefined, 36);
    let alive = true;
    const pulse = () => {
      if (!alive || !cy.current) return;
      const bad = cy.current.nodes("[state='failing']");
      if (bad.length === 0) return void setTimeout(pulse, 800);
      bad.animate({ style: { "border-width": 8 } }, { duration: 650 }).animate({ style: { "border-width": 3 } }, { duration: 650, complete: pulse });
    };
    pulse();
    return () => {
      alive = false;
    };
  }, [graph, visible]);

  // which lines are visible, and what is dimmed: depends on state, selection, search and toggles
  useEffect(() => {
    const c = cy.current;
    if (!c || c.destroyed()) return;
    c.batch(() => {
      c.elements().removeClass("dim hot show match");
      const failing = c.nodes("[state='failing']");
      const suspect = c.nodes("[state='suspect']");
      const focus = selected ? c.getElementById(selected) : null;
      c.edges().forEach((e) => {
        const a = e.source();
        const b = e.target();
        const onPath = e.data("rel") === "calls" && a.data("state") === "failing" && b.data("state") === "failing";
        const toSuspect = e.data("rel") === "modifies" && (a.data("state") === "suspect" || b.data("state") === "failing" && a.data("state") === "suspect");
        const touchesFocus = !!focus && focus.length > 0 && (a.id() === selected || b.id() === selected);
        if (onPath || toSuspect) e.addClass("show hot");
        else if (touchesFocus || (showCalls && e.data("rel") === "calls")) e.addClass("show");
      });
      const q = query.trim().toLowerCase();
      if (q) {
        const hits = c.nodes("[kind!='service']").filter((n) => String(n.data("label")).toLowerCase().includes(q));
        c.elements().addClass("dim");
        hits.removeClass("dim").addClass("match");
        hits.parent().removeClass("dim");
        if (hits.length) c.animate({ fit: { eles: hits, padding: 120 } }, { duration: 300 });
      } else if (focus && focus.length) {
        const near = focus.closedNeighborhood();
        c.elements().addClass("dim");
        near.removeClass("dim");
        near.parent().removeClass("dim");
        c.nodes(":parent").removeClass("dim");
      } else if (failing.length || suspect.length) {
        // something is wrong: quiet the rest so the red path reads at a glance
        const hot = failing.union(suspect).union(failing.connectedEdges(".hot")).union(suspect.connectedEdges(".hot"));
        c.nodes("[kind!='service']").not(hot).addClass("dim");
        c.nodes(":parent").removeClass("dim");
        c.nodes("[kind='function'][state='ok']").forEach((n) => {
          n.style("opacity", 0.55);
        });
      }
      if (!failing.length && !suspect.length) c.nodes("[kind='function']").style("opacity", 1);
    });
    // when something breaks, zoom to the red path once so it is large enough to read
    const failing = c.nodes("[state='failing']");
    const suspect = c.nodes("[state='suspect']");
    const key = failing.union(suspect).map((n) => n.id()).sort().join("|");
    if (key && key !== focusedOn.current && !query.trim() && !selected) {
      focusedOn.current = key;
      const hot = failing.union(suspect);
      setTimeout(() => !c.destroyed() && c.animate({ fit: { eles: hot.union(hot.parent()), padding: 70 } }, { duration: 500 }), 50);
    } else if (!key) {
      focusedOn.current = "";
    }
  }, [graph, selected, query, showCalls, visible]);

  useEffect(() => {
    if (fitSignal > 0) cy.current?.animate({ fit: { eles: cy.current.elements(), padding: 36 } }, { duration: 300 });
  }, [fitSignal]);

  useEffect(
    () => () => {
      cy.current?.destroy();
      cy.current = null; // React strict mode unmounts once in dev: never keep a destroyed instance
      built.current = "";
    },
    [],
  );

  return <div ref={host} role="img" aria-label="Code graph: green nodes are healthy, red is failing, amber is the suspected commit" className="size-full min-h-[520px]" data-testid="code-graph" />;
}
