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
const MAX_FUNCS = 4;
const MAX_COMMITS = 3;

const LABEL = "font-mono text-[11px] uppercase tracking-[0.06em]";

/** One service of the code graph, read left to right: commits that changed it, its call path, what it serves. */
export type ServiceSlice = {
  service: Node;
  commits: Node[];
  funcs: Node[];
  hidden: number;
  focus: Node | undefined;
  endpoint: string | null;
  callers: Node[];
  calls: { source: string; target: string }[];
};

export function sliceService(g: Graph, serviceId: string): ServiceSlice | null {
  const service = g.nodes.find((n) => n.id === serviceId);
  if (!service) return null;
  const all = g.nodes.filter((n) => n.kind === "function" && n.parent === serviceId);
  const ids = new Set(all.map((f) => f.id));
  const calls = g.edges.filter((e) => e.rel === "calls");
  const modifies = g.edges.filter((e) => e.rel === "modifies" && ids.has(e.target));
  const touched = new Set(modifies.map((e) => e.target));

  // Call order: entry points (nothing in this service calls them) first, then breadth-first.
  const inner = calls.filter((e) => ids.has(e.source) && ids.has(e.target));
  const called = new Set(inner.map((e) => e.target));
  const order: Node[] = [];
  const queue = all.filter((f) => !called.has(f.id));
  const seen = new Set<string>();
  while (queue.length) {
    const f = queue.shift()!;
    if (seen.has(f.id)) continue;
    seen.add(f.id);
    order.push(f);
    for (const e of inner) if (e.source === f.id) queue.push(all.find((x) => x.id === e.target)!);
  }
  for (const f of all) if (!seen.has(f.id)) order.push(f);

  // Keep the functions that matter when there are many: failing, changed, then call order.
  const rank = (f: Node) => (f.state === "failing" ? 0 : touched.has(f.id) ? 1 : 2);
  const keep = new Set([...order].sort((a, b) => rank(a) - rank(b)).slice(0, MAX_FUNCS).map((f) => f.id));
  const funcs = order.filter((f) => keep.has(f.id));

  const commitIds = new Set(modifies.map((e) => e.source));
  const commits = g.nodes
    .filter((n) => n.kind === "commit" && commitIds.has(n.id))
    .sort((a, b) => Number(b.state === "suspect") - Number(a.state === "suspect"))
    .slice(0, MAX_COMMITS);

  const callerSvcs = new Set(
    calls.filter((e) => ids.has(e.target) && !ids.has(e.source)).map((e) => g.nodes.find((n) => n.id === e.source)?.parent).filter(Boolean),
  );
  const endpoint = all.map((f) => /serves (.+)$/.exec(f.detail)?.[1]).find(Boolean) ?? null;

  return {
    service,
    commits,
    funcs,
    hidden: all.length - funcs.length,
    focus: funcs.find((f) => f.state === "failing") ?? funcs[0],
    endpoint,
    callers: g.nodes.filter((n) => callerSvcs.has(n.id)),
    calls: inner.filter((e) => keep.has(e.source) && keep.has(e.target)),
  };
}

function Tile({ id, icon, active, tone, selected, onClick, size = 64, label }: { id: string; icon: ReactNode; active?: boolean; tone?: "red" | "amber"; selected?: boolean; onClick?: () => void; size?: number; label: string }) {
  return (
    <button
      type="button"
      data-tile={id}
      aria-label={label}
      aria-pressed={selected}
      onClick={onClick}
      style={{ width: size, height: size, ...(active ? { background: tone === "red" ? "var(--color-red)" : BLUE, borderColor: "transparent" } : {}) }}
      className={cn(
        "grid shrink-0 place-items-center border transition-[box-shadow,transform] duration-150 hover:-translate-y-px",
        active ? "text-white" : "border-line bg-raised text-ink",
        !active && tone === "amber" && "border-amber text-amber",
        !active && tone === "red" && "border-red text-red",
        selected && "ring-2 ring-offset-2 ring-offset-panel [--tw-ring-color:var(--accent)]",
      )}
    >
      {icon}
    </button>
  );
}

type Line = { d: string; pulse?: boolean };

/** Orthogonal dashed connectors measured from the rendered tiles, so the layout can stay plain flexbox. */
function useConnectors(root: React.RefObject<HTMLDivElement | null>, slice: ServiceSlice) {
  const [lines, setLines] = useState<Line[]>([]);
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const measure = () => {
      const base = el.getBoundingClientRect();
      const box = (t: Element | null) => {
        if (!t) return null;
        const r = t.getBoundingClientRect();
        const l = r.left - base.left + el.scrollLeft;
        const top = r.top - base.top;
        return { l, r: l + r.width, cx: l + r.width / 2, t: top, b: top + r.height, cy: top + r.height / 2 };
      };
      const tile = (id: string) => box(el.querySelector(`[data-tile="${CSS.escape(id)}"]`));
      const out: Line[] = [];
      const funcs = slice.funcs.map((f) => tile(f.id)!);
      const frame = box(el.querySelector("[data-frame]"));
      const end = tile(`${slice.service.id}:endpoint`);
      if (!funcs.length || !frame) return setLines(out);

      const entry = funcs[0];
      const commits = slice.commits.map((c) => tile(c.id)!);
      const bus = (Math.max(0, ...commits.map((c) => c.r)) + frame.l) / 2;
      for (const c of commits) out.push({ d: `M${c.r},${c.cy} H${bus} V${entry.cy}` });
      if (commits.length) out.push({ d: `M${bus},${entry.cy} H${entry.l}` });

      // The pulse runs into the failing function, else out of the highlighted one.
      const focus = slice.focus?.id;
      const failing = slice.focus?.state === "failing";
      const pulseEdge = slice.calls.find((e) => (failing ? e.target === focus : e.source === focus));
      const index = new Map(slice.funcs.map((f, i) => [f.id, i]));
      let lane = 0;
      for (const e of slice.calls) {
        const a = funcs[index.get(e.source)!];
        const b = funcs[index.get(e.target)!];
        const pulse = e === pulseEdge;
        if (Math.abs(index.get(e.source)! - index.get(e.target)!) === 1) {
          out.push(a.l < b.l ? { d: `M${a.r},${a.cy} H${b.l}`, pulse } : { d: `M${a.l},${a.cy} H${b.r}`, pulse });
        } else {
          const y = a.b + 54 + 8 * lane++; // below the name and state labels
          out.push({ d: `M${a.cx},${a.b + 42} V${y} H${b.cx} V${b.b + 42}`, pulse });
        }
      }
      if (end) out.push({ d: `M${frame.r},${end.cy} H${end.l}` });
      setLines(out);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [root, slice]);
  return lines;
}

export function GraphMapDiagram({ slice, selected, onToggle }: { slice: ServiceSlice; selected: Set<string>; onToggle: (id: string) => void }) {
  const root = useRef<HTMLDivElement>(null);
  const lines = useConnectors(root, slice);
  const failing = slice.funcs.some((f) => f.state === "failing");

  return (
    <div
      ref={root}
      className="relative overflow-x-auto border border-line bg-panel px-8 py-10 scrollbar-thin"
    >
      <svg aria-hidden="true" className="pointer-events-none absolute inset-0 size-full overflow-visible">
        {lines.map((l, i) => (
          <g key={i}>
            <path d={l.d} fill="none" stroke="color-mix(in srgb, var(--ink) 40%, transparent)" strokeWidth={1} strokeDasharray="3 3" />
            {l.pulse && (
              <path d={l.d} fill="none" stroke={failing ? "var(--color-red)" : BLUE} strokeWidth={2} strokeLinecap="round" strokeDasharray="24 96" className="animate-map-pulse motion-reduce:hidden" />
            )}
          </g>
        ))}
      </svg>

      <div className="relative mx-auto flex w-max items-center gap-20">
        {/* Inputs */}
        <div data-col="commits" className="flex flex-col items-center gap-3">
          <p className={cn(LABEL, "text-ink-2")}>Commits</p>
          {slice.commits.length === 0 && <p className={cn(LABEL, "text-ink-4")}>None mapped</p>}
          {slice.commits.map((c) => (
            <div key={c.id} className="flex flex-col items-center gap-2">
              <Tile id={c.id} label={`Commit ${c.label}: ${c.detail}`} icon={<GitCommitHorizontal size={22} strokeWidth={1.6} />} tone={c.state === "suspect" ? "amber" : undefined} selected={selected.has(c.id)} onClick={() => onToggle(c.id)} size={56} />
              <span className={cn(LABEL, c.state === "suspect" ? "text-amber" : "text-ink-2")} title={c.detail}>
                {c.label}
              </span>
            </div>
          ))}
        </div>

        {/* The service */}
        <div data-frame className="relative border border-line px-8 pb-20 pt-14">
          <p className={cn(LABEL, "absolute -top-2 left-4 bg-panel px-1.5 font-semibold text-ink")}>{slice.service.label}</p>
          {/* Dashed inner frame; the tiles sit over its lower edge, as in the reference. */}
          <div aria-hidden="true" className="absolute inset-x-5 top-5 h-[60px] border border-b-0 border-dashed border-ink-4" />
          <p className={cn(LABEL, "absolute inset-x-5 top-8 whitespace-nowrap text-center")} style={{ color: failing ? "var(--color-red)" : BLUE }}>
            {failing ? "Failure on the call path" : "Call path"}
          </p>
          <div data-col="funcs" className="relative flex items-start gap-16">
            {slice.funcs.map((f) => {
              const isFocus = f.id === slice.focus?.id;
              const fail = f.state === "failing";
              return (
                <div key={f.id} className="flex w-[88px] flex-col items-center gap-2">
                  <Tile id={f.id} label={`Function ${f.label}`} icon={<Braces size={22} strokeWidth={1.6} />} active={isFocus} tone={fail ? "red" : undefined} selected={selected.has(f.id)} onClick={() => onToggle(f.id)} />
                  <span className={cn(LABEL, "max-w-[120px] truncate normal-case tracking-normal text-ink-2")} title={f.label}>
                    {f.label}
                  </span>
                  {fail && <span className={cn(LABEL, "-mt-1.5 text-red")}>Failing</span>}
                </div>
              );
            })}
          </div>
          {slice.hidden > 0 && <p className={cn(LABEL, "absolute bottom-2 right-3 text-ink-4")}>+{slice.hidden} more in table</p>}
        </div>

        {/* Output */}
        <div data-col="out" className="flex flex-col items-center gap-2">
          <p className={cn(LABEL, "text-ink-2")}>Serves</p>
          <Tile id={`${slice.service.id}:endpoint`} label={slice.endpoint ?? "No endpoint mapped"} icon={<Globe size={22} strokeWidth={1.6} />} selected={selected.has(`${slice.service.id}:endpoint`)} onClick={() => onToggle(`${slice.service.id}:endpoint`)} />
          <span className={cn(LABEL, "max-w-[160px] text-center text-ink-2")}>{slice.endpoint ?? "No endpoint"}</span>
          {slice.callers.length > 0 && (
            <>
              <span className={cn(LABEL, "text-ink-4")}>Called by</span>
              <div className="flex gap-2">
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
  );
}

const STATE_TEXT: Record<Node["state"], string> = { ok: "Healthy", failing: "Failing", suspect: "Suspect" };

/** Every function and commit of the repository, one row each; rows are selectable and drive the diagram highlight. */
export function GraphMapTable({ graph, selected, onToggle, onSet }: { graph: Graph; selected: Set<string>; onToggle: (id: string) => void; onSet: (ids: string[]) => void }) {
  const rows = useMemo(() => {
    const byId = new Map(graph.nodes.map((n) => [n.id, n]));
    const degree = (id: string, side: "source" | "target") => graph.edges.filter((e) => e[side] === id).length;
    return graph.nodes
      .filter((n) => n.kind !== "service")
      .map((n) => ({
        node: n,
        service: n.kind === "function" ? byId.get(n.parent ?? "")?.label : graph.edges.filter((e) => e.source === n.id).map((e) => byId.get(byId.get(e.target)?.parent ?? "")?.label).filter(Boolean).join(", "),
        out: degree(n.id, "source"),
        inn: degree(n.id, "target"),
      }))
      .sort((a, b) => Number(b.node.state !== "ok") - Number(a.node.state !== "ok") || a.node.kind.localeCompare(b.node.kind));
  }, [graph]);
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
export function NodeExplain({ graph, slice, id, onClose }: { graph: Graph; slice: ServiceSlice; id: string; onClose: () => void }) {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const from = (rel: string, target: string) => graph.edges.filter((e) => e.rel === rel && e.target === target).map((e) => byId.get(e.source)!).filter(Boolean);
  const to = (rel: string, source: string) => graph.edges.filter((e) => e.rel === rel && e.source === source).map((e) => byId.get(e.target)!).filter(Boolean);
  const svc = (n: Node | undefined) => byId.get(n?.parent ?? "")?.label;

  let kind: string;
  let title: string;
  let summary: ReactNode;
  let facts: ReactNode;
  let tone: "red" | "amber" | undefined;
  let incident: string | null | undefined;

  if (id === `${slice.service.id}:endpoint`) {
    const serving = graph.nodes.filter((f) => f.kind === "function" && f.parent === slice.service.id && /serves /.test(f.detail));
    kind = "Endpoint";
    title = slice.endpoint ?? "No endpoint mapped";
    summary = slice.endpoint
      ? <>Requests to <code className="font-mono">{slice.endpoint}</code> enter the <b>{slice.service.label}</b> service here. A failure anywhere on its call path shows up to users as errors on this endpoint.</>
      : <>No function in <b>{slice.service.label}</b> is mapped to an HTTP route yet.</>;
    facts = (
      <>
        <Fact label="Service">{slice.service.label}</Fact>
        <Fact label="Handled by"><NodeList nodes={serving} empty="Not mapped" /></Fact>
        <Fact label="Called by services"><NodeList nodes={slice.callers} empty="None (external traffic only)" /></Fact>
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
