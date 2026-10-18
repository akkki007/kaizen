"use client";

import { Braces, GitCommitHorizontal, Globe, Server, X } from "lucide-react";
import Link from "next/link";
import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { components } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

type Graph = components["schemas"]["CodeGraph"];
type Node = components["schemas"]["GraphNode"];

/** The one saturated colour in the diagram, as in the reference: the node the story is about. */
const BLUE = "#0b5cff";
/** Functions drawn per service before "Show all"; the rest stay one click away in the table. */
export const MAX_FUNCS = 12;
const MAX_COMMITS = 6;
const MAX_ENDPOINTS = 4;

const LABEL = "font-mono text-[11px] uppercase tracking-[0.06em]";

export const isTestFile = (n: Node) => /(^|[/_.-])(test|tests|spec|__tests__)([/_.-]|$)/i.test(n.detail.split(" · ")[0] ?? "");
export const endpointId = (serviceId: string, route: string) => `${serviceId}:endpoint:${route}`;
const routeOf = (f: Node) => /serves (.+)$/.exec(f.detail)?.[1] ?? null;

/** One service of the code graph, read left to right: commits that changed it, its functions by call depth, what it serves. */
export type ServiceSlice = {
  service: Node;
  commits: Node[];
  moreCommits: number;
  layers: Node[][];
  hidden: number;
  total: number;
  focus: Node | undefined;
  endpoints: { route: string; fn: string }[];
  callers: Node[];
  calls: { source: string; target: string }[];
  modifies: { source: string; target: string }[];
};

export function sliceService(g: Graph, serviceId: string, opts: { showAll?: boolean; showTests?: boolean } = {}): ServiceSlice | null {
  const service = g.nodes.find((n) => n.id === serviceId);
  if (!service) return null;
  const all = g.nodes.filter((n) => n.kind === "function" && n.parent === serviceId && (opts.showTests || !isTestFile(n)));
  const ids = new Set(all.map((f) => f.id));
  const calls = g.edges.filter((e) => e.rel === "calls");
  const modifies = g.edges.filter((e) => e.rel === "modifies" && ids.has(e.target));
  const touched = new Set(modifies.map((e) => e.target));
  const inner = calls.filter((e) => ids.has(e.source) && ids.has(e.target));

  // Call depth: entry points (nothing in this service calls them) at 0, then breadth-first.
  const called = new Set(inner.map((e) => e.target));
  const depth = new Map<string, number>();
  const queue = all.filter((f) => !called.has(f.id)).map((f) => f.id);
  for (const id of queue) depth.set(id, 0);
  while (queue.length) {
    const id = queue.shift()!;
    for (const e of inner) {
      if (e.source !== id || depth.has(e.target)) continue;
      depth.set(e.target, depth.get(id)! + 1);
      queue.push(e.target);
    }
  }
  for (const f of all) if (!depth.has(f.id)) depth.set(f.id, 0); // cycles with no entry

  // Keep what matters when there are many: failing, entry points and endpoints, changed, then shallow before deep.
  const score = (f: Node) => (f.state === "failing" ? 0 : routeOf(f) || !called.has(f.id) ? 1 : touched.has(f.id) ? 2 : 3) * 100 + depth.get(f.id)!;
  const ranked = [...all].sort((a, b) => score(a) - score(b) || a.label.localeCompare(b.label));
  const keep = new Set((opts.showAll ? ranked : ranked.slice(0, MAX_FUNCS)).map((f) => f.id));
  const layers: Node[][] = [];
  for (const f of all) {
    if (!keep.has(f.id)) continue;
    (layers[depth.get(f.id)!] ??= []).push(f);
  }
  const packed = layers.filter(Boolean).map((l) => l.sort((a, b) => a.label.localeCompare(b.label)));

  const commitIds = new Set(modifies.map((e) => e.source));
  const commitsAll = g.nodes.filter((n) => n.kind === "commit" && commitIds.has(n.id)).sort((a, b) => Number(b.state === "suspect") - Number(a.state === "suspect"));
  const callerSvcs = new Set(calls.filter((e) => ids.has(e.target) && !ids.has(e.source)).map((e) => g.nodes.find((n) => n.id === e.source)?.parent));
  const endpoints = all.flatMap((f) => (routeOf(f) ? [{ route: routeOf(f)!, fn: f.id }] : [])).slice(0, MAX_ENDPOINTS);
  const visible = packed.flat();

  return {
    service,
    commits: commitsAll.slice(0, MAX_COMMITS),
    moreCommits: Math.max(0, commitsAll.length - MAX_COMMITS),
    layers: packed,
    hidden: all.length - keep.size,
    total: all.length,
    focus: visible.find((f) => f.state === "failing") ?? packed[0]?.[0],
    endpoints,
    callers: g.nodes.filter((n) => n.kind === "service" && callerSvcs.has(n.id)),
    calls: inner.filter((e) => keep.has(e.source) && keep.has(e.target)),
    modifies: modifies.filter((e) => keep.has(e.target)),
  };
}

function Tile({ id, icon, active, tone, selected, dim, onClick, size = 56, label }: { id: string; icon: ReactNode; active?: boolean; tone?: "red" | "amber"; selected?: boolean; dim?: boolean; onClick?: () => void; size?: number; label: string }) {
  return (
    <button
      type="button"
      data-tile={id}
      aria-label={label}
      aria-pressed={selected}
      onClick={onClick}
      style={{ width: size, height: size, ...(active ? { background: tone === "red" ? "var(--color-red)" : BLUE, borderColor: "transparent" } : {}) }}
      className={cn(
        "grid shrink-0 place-items-center border transition-[box-shadow,transform,opacity] duration-150 hover:-translate-y-px",
        active ? "text-white" : "border-line bg-raised text-ink",
        !active && tone === "amber" && "border-amber text-amber",
        !active && tone === "red" && "border-red text-red",
        selected && "ring-2 ring-offset-2 ring-offset-panel [--tw-ring-color:var(--accent)]",
        dim && "opacity-25",
      )}
    >
      {icon}
    </button>
  );
}

type Line = { d: string; pulse?: boolean; strong?: boolean };

/** Orthogonal dashed connectors measured from the rendered tiles, so the layout can stay plain flexbox. */
function useConnectors(root: React.RefObject<HTMLDivElement | null>, slice: ServiceSlice, active: string | null) {
  const [lines, setLines] = useState<Line[]>([]);
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const measure = () => {
      const base = el.getBoundingClientRect();
      const box = (t: Element | null) => {
        if (!t) return null;
        const r = t.getBoundingClientRect();
        const l = r.left - base.left;
        const top = r.top - base.top;
        return { l, r: l + r.width, cx: l + r.width / 2, t: top, b: top + r.height, cy: top + r.height / 2 };
      };
      const tile = (id: string) => box(el.querySelector(`[data-tile="${CSS.escape(id)}"]`));
      const frame = box(el.querySelector("[data-frame]"));
      const out: Line[] = [];
      if (!frame) return setLines(out);

      // Commits merge into one bus that enters the service, as in the reference.
      const commits = slice.commits.map((c) => tile(c.id)).filter((c) => c !== null);
      const entryY = frame.t + (frame.b - frame.t) / 2;
      const bus = (Math.max(0, ...commits.map((c) => c.r)) + frame.l) / 2;
      for (const c of commits) out.push({ d: `M${c.r},${c.cy} H${bus} V${entryY}` });
      if (commits.length) out.push({ d: `M${bus},${entryY} H${frame.l}` });
      // The active commit also shows exactly which functions it changed.
      for (const e of slice.modifies) {
        if (e.source !== active) continue;
        const c = tile(e.source);
        const f = tile(e.target);
        if (c && f) out.push({ d: `M${c.r},${c.cy} H${bus} V${f.cy} H${f.l}`, strong: true });
      }

      // Calls: forward edges step between columns; same-column or backward edges route underneath.
      const focus = slice.focus?.id;
      const failing = slice.focus?.state === "failing";
      const pulseEdge = slice.calls.find((e) => (failing ? e.target === focus : e.source === focus));
      let lane = 0;
      for (const e of slice.calls) {
        const a = tile(e.source);
        const b = tile(e.target);
        if (!a || !b) continue;
        const strong = active === e.source || active === e.target;
        const pulse = e === pulseEdge;
        if (b.l > a.r + 4) {
          const mid = a.r + (b.l - a.r) / 2;
          out.push({ d: `M${a.r},${a.cy} H${mid} V${b.cy} H${b.l}`, pulse, strong });
        } else {
          const y = frame.b - 14 - 6 * (lane++ % 4);
          out.push({ d: `M${a.cx},${a.b + 30} V${y} H${b.cx} V${b.b + 30}`, pulse, strong });
        }
      }

      for (const ep of slice.endpoints) {
        const t = tile(endpointId(slice.service.id, ep.route));
        if (t) out.push({ d: `M${frame.r},${t.cy} H${t.l}`, strong: active === ep.fn });
      }
      setLines(out);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [root, slice, active]);
  return lines;
}

export function GraphMapDiagram({ slice, selected, active, match, onToggle }: { slice: ServiceSlice; selected: Set<string>; active: string | null; match: (n: Node) => boolean; onToggle: (id: string) => void }) {
  const root = useRef<HTMLDivElement>(null);
  const lines = useConnectors(root, slice, active);
  const failing = slice.layers.some((l) => l.some((f) => f.state === "failing"));
  const accent = failing ? "var(--color-red)" : BLUE;

  return (
    <div className="overflow-x-auto border border-line bg-panel scrollbar-thin" data-testid="graph-map">
      <div ref={root} className="relative mx-auto w-max px-8 py-10">
        <svg aria-hidden="true" className="pointer-events-none absolute inset-0 size-full overflow-visible">
          {lines.map((l, i) => (
            <g key={i}>
              <path d={l.d} fill="none" stroke={l.strong ? "var(--accent)" : "color-mix(in srgb, var(--ink) 40%, transparent)"} strokeWidth={l.strong ? 1.5 : 1} strokeDasharray={l.strong ? undefined : "3 3"} />
              {l.pulse && <path d={l.d} fill="none" stroke={accent} strokeWidth={2} strokeLinecap="round" strokeDasharray="24 96" className="animate-map-pulse motion-reduce:hidden" />}
            </g>
          ))}
        </svg>

        <div className="relative flex items-center gap-20">
          {/* Inputs */}
          <div data-col="commits" className="flex flex-col items-center gap-3">
            <p className={cn(LABEL, "text-ink-2")}>Commits</p>
            {slice.commits.length === 0 && <p className={cn(LABEL, "text-ink-4")}>None mapped</p>}
            {slice.commits.map((c) => (
              <div key={c.id} className="flex flex-col items-center gap-1.5">
                <Tile id={c.id} label={`Commit ${c.label}: ${c.detail}`} icon={<GitCommitHorizontal size={20} strokeWidth={1.6} />} tone={c.state === "suspect" ? "amber" : undefined} selected={selected.has(c.id)} dim={!match(c)} onClick={() => onToggle(c.id)} size={48} />
                <span className={cn(LABEL, c.state === "suspect" ? "text-amber" : "text-ink-2")} title={c.detail}>
                  {c.label}
                </span>
              </div>
            ))}
            {slice.moreCommits > 0 && <p className={cn(LABEL, "text-ink-4")}>+{slice.moreCommits} more</p>}
          </div>

          {/* The service */}
          <div data-frame className="relative border border-line px-8 pb-24 pt-16">
            <p className={cn(LABEL, "absolute -top-2 left-4 bg-panel px-1.5 font-semibold text-ink")}>
              {slice.service.label}
              <span className="ml-2 font-normal text-ink-4">
                {slice.total} {slice.total === 1 ? "function" : "functions"}
              </span>
            </p>
            {/* Dashed inner frame; the first row of tiles sits over its lower edge, as in the reference. */}
            <div aria-hidden="true" className="absolute inset-x-5 top-5 h-[64px] border border-b-0 border-dashed border-ink-4" />
            <p className={cn(LABEL, "absolute inset-x-5 top-8 whitespace-nowrap text-center")} style={{ color: accent }}>
              {failing ? "Failure on the call path" : "Call path"}
            </p>
            <div data-col="funcs" className="relative flex items-start gap-16">
              {slice.layers.length === 0 && <p className={cn(LABEL, "py-6 text-ink-4")}>No functions mapped</p>}
              {slice.layers.map((layer, depth) => (
                <div key={depth} className="flex flex-col gap-7">
                  {layer.map((f) => {
                    const fail = f.state === "failing";
                    return (
                      <div key={f.id} className="flex w-[104px] flex-col items-center gap-1.5">
                        <Tile id={f.id} label={`Function ${f.label}`} icon={<Braces size={20} strokeWidth={1.6} />} active={f.id === slice.focus?.id} tone={fail ? "red" : undefined} selected={selected.has(f.id)} dim={!match(f)} onClick={() => onToggle(f.id)} />
                        <span className={cn(LABEL, "max-w-[120px] truncate normal-case tracking-normal text-ink-2")} title={f.label}>
                          {f.label}
                        </span>
                        {fail && <span className={cn(LABEL, "-mt-1 text-red")}>Failing</span>}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
            {slice.hidden > 0 && <p className={cn(LABEL, "absolute bottom-3 right-4 text-ink-4")}>+{slice.hidden} more</p>}
          </div>

          {/* Outputs */}
          <div data-col="out" className="flex flex-col items-center gap-3">
            <p className={cn(LABEL, "text-ink-2")}>Serves</p>
            {slice.endpoints.length === 0 && <p className={cn(LABEL, "text-ink-4")}>No endpoint</p>}
            {slice.endpoints.map((ep) => {
              const id = endpointId(slice.service.id, ep.route);
              return (
                <div key={id} className="flex flex-col items-center gap-1.5">
                  <Tile id={id} label={`Endpoint ${ep.route}`} icon={<Globe size={20} strokeWidth={1.6} />} selected={selected.has(id)} onClick={() => onToggle(id)} size={48} />
                  <span className={cn(LABEL, "max-w-[160px] text-center text-ink-2")}>{ep.route}</span>
                </div>
              );
            })}
            {slice.callers.length > 0 && (
              <>
                <span className={cn(LABEL, "mt-1 text-ink-4")}>Called by</span>
                <div className="flex max-w-[160px] flex-wrap justify-center gap-2">
                  {slice.callers.map((s) => (
                    <span key={s.id} title={s.label} className="grid size-7 place-items-center border border-line bg-raised text-ink-2">
                      <Server size={13} strokeWidth={1.8} />
                    </span>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

const STATE_TEXT: Record<Node["state"], string> = { ok: "Healthy", failing: "Failing", suspect: "Suspect" };

/** Every function and commit of the repository, one row each; rows are selectable and drive the diagram highlight. */
export function GraphMapTable({ graph, selected, match, showTests, onToggle, onSet }: { graph: Graph; selected: Set<string>; match: (n: Node) => boolean; showTests: boolean; onToggle: (id: string) => void; onSet: (ids: string[]) => void }) {
  const rows = useMemo(() => {
    const byId = new Map(graph.nodes.map((n) => [n.id, n]));
    const degree = (id: string, side: "source" | "target") => graph.edges.filter((e) => e[side] === id).length;
    return graph.nodes
      .filter((n) => n.kind !== "service" && match(n) && (showTests || n.kind !== "function" || !isTestFile(n)))
      .map((n) => ({
        node: n,
        service: n.kind === "function" ? byId.get(n.parent ?? "")?.label : [...new Set(graph.edges.filter((e) => e.source === n.id).map((e) => byId.get(byId.get(e.target)?.parent ?? "")?.label).filter(Boolean))].join(", "),
        out: degree(n.id, "source"),
        inn: degree(n.id, "target"),
      }))
      .sort((a, b) => Number(b.node.state !== "ok") - Number(a.node.state !== "ok") || a.node.kind.localeCompare(b.node.kind));
  }, [graph, match, showTests]);
  const allIds = rows.map((r) => r.node.id);
  const every = allIds.length > 0 && allIds.every((id) => selected.has(id));
  const some = allIds.some((id) => selected.has(id));

  return (
    <div className="overflow-x-auto border border-line scrollbar-thin">
      <table className="w-full min-w-[720px] text-left text-[13px]">
        <thead className={cn(LABEL, "border-b border-line bg-raised text-ink-3")}>
          <tr>
            <th className="w-10 px-3 py-2.5">
              <input
                type="checkbox"
                aria-label="Select all"
                checked={every}
                ref={(el) => {
                  if (el) el.indeterminate = some && !every;
                }}
                onChange={() => onSet(every ? [] : allIds)}
                className="size-3.5 accent-[var(--accent)]"
              />
            </th>
            <th className="px-3 py-2.5 font-medium">Name</th>
            <th className="px-3 py-2.5 font-medium">Kind</th>
            <th className="px-3 py-2.5 font-medium">Service</th>
            <th className="px-3 py-2.5 font-medium">State</th>
            <th className="px-3 py-2.5 text-right font-medium">Out</th>
            <th className="px-3 py-2.5 text-right font-medium">In</th>
            <th className="px-3 py-2.5 font-medium">Detail</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ node: n, service, out, inn }) => {
            const on = selected.has(n.id);
            return (
              <tr key={n.id} aria-selected={on} onClick={() => onToggle(n.id)} className={cn("cursor-pointer border-b border-line last:border-0 transition-colors", on ? "bg-sel" : "hover:bg-hover")}>
                <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" aria-label={`Select ${n.label}`} checked={on} onChange={() => onToggle(n.id)} className="size-3.5 accent-[var(--accent)]" />
                </td>
                <td className="px-3 py-2 font-mono text-[12.5px]">{n.label}</td>
                <td className={cn(LABEL, "px-3 py-2 text-ink-3")}>{n.kind}</td>
                <td className="px-3 py-2 text-ink-2">{service || "—"}</td>
                <td className="px-3 py-2">
                  <span className={cn(LABEL, "inline-flex items-center gap-1.5", n.state === "failing" ? "text-red" : n.state === "suspect" ? "text-amber" : "text-ink-3")}>
                    <i className={cn("size-1.5", n.state === "failing" ? "bg-red" : n.state === "suspect" ? "bg-amber" : "bg-green")} />
                    {STATE_TEXT[n.state]}
                  </span>
                </td>
                <td className="num px-3 py-2 text-right text-ink-2">{out}</td>
                <td className="num px-3 py-2 text-right text-ink-2">{inn}</td>
                <td className="max-w-[280px] truncate px-3 py-2 text-ink-3" title={n.detail}>
                  {n.detail || "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className={cn(LABEL, "text-ink-3")}>{label}</dt>
      <dd className="text-[13px] text-ink">{children}</dd>
    </div>
  );
}

function NodeList({ nodes, empty }: { nodes: Node[]; empty: string }) {
  if (!nodes.length) return <span className="text-ink-3">{empty}</span>;
  return (
    <span className="flex flex-wrap gap-1.5">
      {nodes.map((n) => (
        <code key={n.id} className="border border-line bg-raised px-1.5 py-0.5 font-mono text-[12px]" title={n.detail}>
          {n.label}
        </code>
      ))}
    </span>
  );
}

/** Plain-language explanation of the clicked node, built from the graph edges around it. */
export function NodeExplain({ graph, slice, id, showTests, onClose }: { graph: Graph; slice: ServiceSlice; id: string; showTests: boolean; onClose: () => void }) {
  // Hidden test code stays out of the explanation too.
  const byId = new Map(graph.nodes.filter((n) => showTests || n.kind !== "function" || !isTestFile(n)).map((n) => [n.id, n]));
  const from = (rel: string, target: string) => graph.edges.filter((e) => e.rel === rel && e.target === target).map((e) => byId.get(e.source)!).filter(Boolean);
  const to = (rel: string, source: string) => graph.edges.filter((e) => e.rel === rel && e.source === source).map((e) => byId.get(e.target)!).filter(Boolean);
  const svc = (n: Node | undefined) => byId.get(n?.parent ?? "")?.label;

  let kind: string;
  let title: string;
  let summary: ReactNode;
  let facts: ReactNode;
  let tone: "red" | "amber" | undefined;
  let incident: string | null | undefined;

  const prefix = `${slice.service.id}:endpoint:`;
  if (id.startsWith(prefix)) {
    const route = id.slice(prefix.length);
    const serving = graph.nodes.filter((f) => f.kind === "function" && f.parent === slice.service.id && routeOf(f) === route);
    kind = "Endpoint";
    title = route;
    summary = (
      <>
        Requests to <code className="font-mono">{route}</code> enter the <b>{slice.service.label}</b> service here. A failure anywhere on its call path shows up to users as errors on this endpoint.
      </>
    );
    facts = (
      <>
        <Fact label="Service">{slice.service.label}</Fact>
        <Fact label="Handled by">
          <NodeList nodes={serving} empty="Not mapped" />
        </Fact>
        <Fact label="Called by services">
          <NodeList nodes={slice.callers} empty="None (external traffic only)" />
        </Fact>
      </>
    );
  } else {
    const n = byId.get(id);
    if (!n) return null;
    tone = n.state === "failing" ? "red" : n.state === "suspect" ? "amber" : undefined;
    incident = n.incident_id;
    if (n.kind === "commit") {
      const changed = to("modifies", n.id);
      const services = [...new Set(changed.map(svc).filter(Boolean))];
      kind = "Commit";
      title = n.label;
      summary = (
        <>
          <span className="text-ink">{n.detail || "No commit message"}</span>
          <br />
          {n.state === "suspect"
            ? <>Kaizen ranks this commit as the <b className="text-amber">likely cause</b> of the open failure: it changed code on the failing call path.</>
            : <>This commit changed {changed.length} {changed.length === 1 ? "function" : "functions"}{services.length ? <> in {services.join(", ")}</> : null}. If one of them starts failing after a deploy, it becomes a suspect.</>}
        </>
      );
      facts = (
        <>
          <Fact label="Changed functions"><NodeList nodes={changed} empty="None mapped" /></Fact>
          <Fact label="Services touched">{services.join(", ") || "—"}</Fact>
          <Fact label="State">{STATE_TEXT[n.state]}</Fact>
        </>
      );
    } else if (n.kind === "function") {
      const callers = from("calls", n.id);
      const callees = to("calls", n.id);
      const commits = from("modifies", n.id);
      const [file, serves] = n.detail.split(" · ");
      kind = "Function";
      title = n.label;
      summary =
        n.state === "failing" ? (
          <>This function is <b className="text-red">throwing errors in production</b>. {callers.length ? <>Its callers ({callers.map((c) => c.label).join(", ")}) inherit the failure</> : <>It is an entry point, so users see the errors directly</>}{commits.length ? <>, and it was recently changed by {commits.map((c) => c.label).join(", ")}.</> : "."}</>
        ) : (
          <>
            <b>{n.label}</b> lives in the <b>{svc(n)}</b> service{file && !file.startsWith("serves") ? <> ({file})</> : null}.{" "}
            {callers.length ? <>It is called by {callers.length} {callers.length === 1 ? "function" : "functions"}</> : <>Nothing in the graph calls it, so it is an entry point</>}
            {callees.length ? <> and calls {callees.length} more</> : null}. A change here can break everything upstream of it.
          </>
        );
      facts = (
        <>
          <Fact label="Called by"><NodeList nodes={callers} empty="Nothing (entry point)" /></Fact>
          <Fact label="Calls"><NodeList nodes={callees} empty="Nothing (leaf)" /></Fact>
          <Fact label="Changed by"><NodeList nodes={commits} empty="No recent commits" /></Fact>
          {(serves || file?.startsWith("serves")) && <Fact label="Endpoint">{(serves ?? file).replace(/^serves /, "")}</Fact>}
        </>
      );
    } else {
      kind = "Service";
      title = n.label;
      summary = <>A service in this repository.</>;
      facts = null;
    }
  }

  return (
    <section aria-label={`${kind} ${title}`} className="border border-line bg-panel-2">
      <header className="flex items-start gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0 flex-1">
          <p className={cn(LABEL, tone === "red" ? "text-red" : tone === "amber" ? "text-amber" : "text-ink-3")}>{kind}{tone ? ` · ${tone === "red" ? "failing" : "suspect"}` : ""}</p>
          <h2 className="mt-1 truncate font-mono text-[16px] font-semibold">{title}</h2>
        </div>
        {incident && (
          <Link href={`/incidents/${encodeURIComponent(incident)}`} className="shrink-0 text-[12.5px] text-link hover:underline">
            Open incident
          </Link>
        )}
        <button type="button" aria-label="Close explanation" onClick={onClose} className="grid size-7 shrink-0 place-items-center rounded-md text-ink-3 hover:bg-hover hover:text-ink">
          <X size={14} />
        </button>
      </header>
      <p className="px-5 pt-4 text-[13.5px] leading-[1.6] text-ink-2">{summary}</p>
      {facts && <dl className="grid gap-4 px-5 py-4 sm:grid-cols-2 lg:grid-cols-4">{facts}</dl>}
    </section>
  );
}
