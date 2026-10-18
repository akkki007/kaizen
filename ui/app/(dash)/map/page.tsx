"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { Segmented } from "@/components/app/kit";
import { useWorkspace } from "@/components/app/shell";
import { GraphMapDiagram, GraphMapTable, NodeExplain, sliceService } from "@/components/graph-map";
import { useCodeGraph } from "@/lib/api/hooks-graph";
import { cn } from "@/lib/utils";

const VIEWS = [
  { id: "diagram", label: "Diagram" },
  { id: "table", label: "Table" },
] as const;

/** The code graph as a clean left-to-right map, one service at a time, or as a selectable table. */
function GraphMapPage() {
  const { ws } = useWorkspace();
  const router = useRouter();
  const params = useSearchParams();
  const repo = params.get("repo") ?? ws.repos[0]?.name;
  const graph = useCodeGraph(repo, false);
  const [view, setView] = useState<"diagram" | "table">("diagram");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // The last node clicked in the diagram; its explanation shows under the graph.
  const [active, setActive] = useState<string | null>(null);

  const services = useMemo(() => graph.data?.nodes.filter((n) => n.kind === "service") ?? [], [graph.data]);
  // Open on the service that is failing, else the first one.
  const failingSvc = graph.data?.nodes.find((n) => n.state === "failing")?.parent;
  const service = params.get("service") ?? failingSvc ?? services[0]?.id;
  const slice = graph.data && service ? sliceService(graph.data, service) : null;

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    router.replace(`/map?${next}`);
    setActive(null);
  };
  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <div className="flex flex-1 flex-col">
      <header data-enter className="flex flex-col gap-4 border-b border-line px-6 py-6 sm:flex-row sm:items-start">
        <div className="min-w-0 flex-1">
          <h1 className="text-[20px] font-semibold tracking-[-0.015em]">Graph map</h1>
          <p className="mt-1 text-[13px] text-ink-2">Commits, the call path they change, and what it serves, read left to right from FalkorDB.</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {ws.repos.length > 1 && (
            <select aria-label="Repository" value={repo} onChange={(e) => setParam("repo", e.target.value)} className="h-7 rounded-md border border-line bg-panel-2 px-2 text-[12.5px]">
              {ws.repos.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name}
                </option>
              ))}
            </select>
          )}
          <Segmented label="View" options={VIEWS} value={view} onChange={setView} />
        </div>
      </header>

      {!repo ? (
        <p className="px-6 py-16 text-center text-[13px] text-ink-3">Connect a repository to see its graph.</p>
      ) : graph.isError ? (
        <p role="alert" className="px-6 py-16 text-center text-[13px] text-red">Could not load the graph.</p>
      ) : !graph.data ? (
        <div role="status" aria-label="Loading" className="m-6 h-[380px] animate-pulse border border-line bg-ink/[0.03]" />
      ) : (
        <div data-enter className="flex flex-col gap-4 px-6 py-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            {view === "diagram" ? (
              <div role="tablist" aria-label="Service" className="flex flex-wrap gap-1">
                {services.map((s) => {
                  const on = s.id === service;
                  const fail = graph.data!.nodes.some((n) => n.parent === s.id && n.state === "failing");
                  return (
                    <button
                      key={s.id}
                      role="tab"
                      aria-selected={on}
                      onClick={() => setParam("service", s.id)}
                      className={cn("inline-flex h-7 items-center gap-1.5 border px-2.5 font-mono text-[11px] uppercase tracking-[0.06em] transition-colors", on ? "border-ink bg-ink text-panel" : "border-line text-ink-2 hover:text-ink")}
                    >
                      {fail && <i className="size-1.5 bg-red" aria-label="failing" />}
                      {s.label}
                    </button>
                  );
                })}
              </div>
            ) : (
              <p className="font-mono text-[11px] uppercase tracking-[0.06em] text-ink-3">
                {graph.data.nodes.filter((n) => n.kind !== "service").length} nodes · {graph.data.edges.length} edges
              </p>
            )}
            {selected.size > 0 && (
              <p className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.06em] text-ink-2">
                {selected.size} selected
                <button type="button" onClick={() => setSelected(new Set())} className="text-link hover:underline">
                  Clear
                </button>
              </p>
            )}
          </div>

          {view === "diagram" ? (
            slice ? (
              <>
                <GraphMapDiagram
                  slice={slice}
                  selected={selected}
                  onToggle={(id) => {
                    const on = !selected.has(id);
                    toggle(id);
                    setActive(on ? id : active === id ? null : active);
                  }}
                />
                {active ? (
                  <NodeExplain key={active} graph={graph.data} slice={slice} id={active} onClose={() => setActive(null)} />
                ) : (
                  <p className="font-mono text-[11px] uppercase tracking-[0.06em] text-ink-4">Click a node to see what it is and how it connects</p>
                )}
              </>
            ) : (
              <p className="border border-line px-6 py-16 text-center text-[13px] text-ink-3">No services in this repository&apos;s graph yet.</p>
            )
          ) : (
            <GraphMapTable graph={graph.data} selected={selected} onToggle={toggle} onSet={(ids) => setSelected(new Set(ids))} />
          )}
        </div>
      )}
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <GraphMapPage />
    </Suspense>
  );
}
