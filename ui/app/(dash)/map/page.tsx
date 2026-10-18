"use client";

import { Check, Circle, Database, FlaskConical, Loader2, RotateCcw, Search, Zap } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { Button, Segmented, Tag } from "@/components/app/kit";
import { useWorkspace } from "@/components/app/shell";
import { GraphMapDiagram, GraphMapTable, MAX_FUNCS, NodeExplain, sliceService } from "@/components/graph-map";
import { useIncidentDetail } from "@/lib/api/hooks";
import { useCodeGraph, useInjectProblem, useResetDemo } from "@/lib/api/hooks-graph";
import type { components } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

type Node = components["schemas"]["GraphNode"];

const VIEWS = [
  { id: "diagram", label: "Diagram" },
  { id: "table", label: "Table" },
] as const;

const LABEL = "font-mono text-[11px] uppercase tracking-[0.06em]";
const ALGO: Record<string, string> = { path: "shortestPath", centrality: "algo.pageRank", blast: "variable-length traversal", dependency: "graph traversal" };

type Step = { id: string; label: string; engine: "Postgres" | "FalkorDB" | "Slack"; query?: string; done: boolean };

function Pipeline({ steps, active }: { steps: Step[]; active: boolean }) {
  const current = steps.findIndex((s) => !s.done);
  return (
    <ol aria-label="What Kaizen is doing" className="space-y-2.5">
      {steps.map((s, i) => {
        const state = s.done ? "done" : active && i === current ? "active" : "pending";
        return (
          <li key={s.id} data-step={s.id} data-state={state} className="flex items-start gap-2.5">
            <span className={cn("mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border", state === "done" ? "border-green/50 bg-green/15 text-green" : state === "active" ? "border-amber/60 bg-amber/15 text-amber" : "border-line text-ink-4")}>
              {state === "done" ? <Check size={12} strokeWidth={2.4} aria-hidden /> : state === "active" ? <Loader2 size={12} className="animate-spin" aria-hidden /> : <Circle size={8} aria-hidden />}
            </span>
            <span className="min-w-0 flex-1">
              <span className={cn("block text-[13px]", state === "pending" ? "text-ink-3" : "text-ink")}>{s.label}</span>
              <span className="mt-0.5 flex flex-wrap items-center gap-1.5">
                <span className={cn("rounded px-1.5 py-px text-[10.5px] font-medium", s.engine === "FalkorDB" ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-raised text-ink-3")}>{s.engine}</span>
                {s.query && <code className="truncate font-mono text-[11px] text-ink-3">{s.query}</code>}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** The repository's code graph from FalkorDB: one service at a time as a left-to-right map, or every node as a table. */
function GraphMapPage() {
  const { ws } = useWorkspace();
  const router = useRouter();
  const params = useSearchParams();
  const repo = params.get("repo") ?? ws.repos[0]?.name;
  const [incidentId, setIncidentId] = useState<string | null>(null);
  const graph = useCodeGraph(repo, incidentId !== null);
  const detail = useIncidentDetail(incidentId ?? "");
  const inject = useInjectProblem(repo ?? "");
  const reset = useResetDemo(repo ?? "");
  const [view, setView] = useState<"diagram" | "table">("diagram");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // The last node clicked in the diagram; its explanation shows under the graph.
  const [active, setActive] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [showTests, setShowTests] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!startedAt) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [startedAt]);

  const g = graph.data;
  const services = useMemo(() => g?.nodes.filter((n) => n.kind === "service") ?? [], [g]);
  const failing = g?.nodes.filter((n) => n.state === "failing") ?? [];
  // Open on the service that is failing, else the first one.
  const service = params.get("service") ?? failing[0]?.parent ?? services[0]?.id;
  const slice = useMemo(() => (g && service ? sliceService(g, service, { showAll, showTests }) : null), [g, service, showAll, showTests]);

  const q = query.trim().toLowerCase();
  const match = useCallback((n: Node) => !q || n.label.toLowerCase().includes(q) || n.detail.toLowerCase().includes(q), [q]);
  const hits = (svcId: string) => (q ? (g?.nodes.filter((n) => n.kind === "function" && n.parent === svcId && match(n)).length ?? 0) : 0);

  const inc = detail.data;
  const investigated = !!inc && inc.investigation.status !== "not_run";
  const named = !!inc && inc.ranking.candidates.length > 0 && ["confident", "ambiguous"].includes(inc.ranking.verdict);
  const steps: Step[] = [
    { id: "ingest", label: "Production traffic and errors ingested", engine: "Postgres", done: incidentId !== null },
    { id: "detect", label: "Failure detected: error rate jumped over its baseline", engine: "Postgres", query: "30 s windows, 2 in a row", done: !!inc },
    { id: "map", label: "Stack trace mapped to the function that threw", engine: "FalkorDB", query: "(Error)-[:thrown_in]->(Function)", done: failing.length > 0 },
    { id: "cand", label: "Commits in the deployment gathered from the graph", engine: "FalkorDB", query: "(Deployment)-[:includes]->(Commit)", done: investigated || named },
    { id: "rank", label: "Culprit ranked: path distance, blast radius, PageRank…", engine: "FalkorDB", query: "shortestPath · algo.pageRank", done: named },
    { id: "alert", label: "Team alerted with the culprit, author and evidence", engine: "Slack", done: named },
  ];
  const top = inc?.ranking.candidates[0];
  const demo = g?.demo_enabled ?? false;
  const elapsed = startedAt ? Math.max(0, Math.round((now - startedAt) / 1000)) : 0;

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    router.replace(`/map?${next}`);
    setActive(null);
    setShowAll(false);
  };
  const toggle = (id: string) => {
    const on = !selected.has(id);
    setSelected((s) => {
      const n = new Set(s);
      if (on) n.add(id);
      else n.delete(id);
      return n;
    });
    setActive(on ? id : active === id ? null : active);
  };

  async function onInject() {
    setError(null);
    try {
      const r = await inject.mutateAsync();
      setIncidentId(r.incident_id);
      setStartedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not inject");
    }
  }
  async function onReset() {
    await reset.mutateAsync();
    setIncidentId(null);
    setStartedAt(null);
    setSelected(new Set());
    setActive(null);
    setError(null);
  }

  return (
    <div className="flex flex-1 flex-col">
      <header data-enter className="flex flex-col gap-4 border-b border-line px-6 py-6 sm:flex-row sm:items-start">
        <div className="min-w-0 flex-1">
          <h1 className="text-[20px] font-semibold tracking-[-0.015em]">Graph map</h1>
          <p className="mt-1 text-[13px] text-ink-2">Every function and commit of your repository, stored in FalkorDB. Read left to right: the commits that changed a service, its call path, and what it serves. When something breaks, only the failing function turns red.</p>
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
        <p role="alert" className="px-6 py-16 text-center text-[13px] text-red">Could not load the graph: {graph.error.message}</p>
      ) : !g ? (
        <div role="status" aria-label="Loading the graph" className="m-6 h-[380px] animate-pulse border border-line bg-ink/[0.03]" />
      ) : (
        <div className="grid flex-1 xl:grid-cols-[minmax(0,1fr)_340px]">
          <section aria-label="Code graph" className="flex min-w-0 flex-col gap-4 px-6 py-5 xl:border-r xl:border-line">
            {/* FalkorDB bar, search and filters */}
            <div className="flex flex-wrap items-center gap-2 text-[12px]">
              <span data-testid="fdb-bar" className="inline-flex items-center gap-2 border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1 text-ink">
                <Database size={13} className="text-emerald-600 dark:text-emerald-400" aria-hidden />
                <b className="font-semibold text-emerald-700 dark:text-emerald-300">FalkorDB</b>
                <span className="text-ink-2">
                  {g.nodes.length} nodes · {g.edges.length} edges
                </span>
                <span className="text-ink-4">|</span>
                <span className="text-ink-2" title="Measured on this refresh">
                  {Object.keys(g.queries).length} Cypher queries in {Math.round(Object.values(g.query_ms ?? {}).reduce((a, b) => a + b, 0))} ms
                </span>
              </span>
              {failing.length > 0 && <Tag tone="red">{failing.length} failing</Tag>}
              <span className="flex-1" />
              <label className="relative">
                <Search size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-ink-3" aria-hidden />
                <input type="search" aria-label="Find a function or commit" placeholder="Find a function or commit" value={query} onChange={(e) => setQuery(e.target.value)} className="h-7 w-52 border border-line bg-panel-2 pl-7 pr-2 text-ink placeholder:text-ink-3" />
              </label>
              <label className="inline-flex h-7 items-center gap-1.5 border border-line bg-panel-2 px-2 text-ink-2">
                <input type="checkbox" checked={showTests} onChange={(e) => setShowTests(e.target.checked)} className="accent-[var(--accent)]" />
                Test code
              </label>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
              {view === "diagram" ? (
                <div role="tablist" aria-label="Service" className="flex flex-wrap gap-1">
                  {services.map((s) => {
                    const on = s.id === service;
                    const fail = g.nodes.some((n) => n.parent === s.id && n.state === "failing");
                    const n = hits(s.id);
                    return (
                      <button
                        key={s.id}
                        role="tab"
                        aria-selected={on}
                        onClick={() => setParam("service", s.id)}
                        className={cn("inline-flex h-7 items-center gap-1.5 border px-2.5 transition-colors", LABEL, on ? "border-ink bg-ink text-panel" : "border-line text-ink-2 hover:text-ink", q && !n && !on && "opacity-40")}
                      >
                        {fail && <i className="size-1.5 bg-red" aria-label="failing" />}
                        {s.label}
                        {n > 0 && <span className={on ? "text-panel/70" : "text-accent"}>{n}</span>}
                      </button>
                    );
                  })}
                </div>
              ) : (
                <p className={cn(LABEL, "text-ink-3")}>
                  {g.nodes.filter((n) => n.kind !== "service").length} nodes · {g.edges.length} edges
                </p>
              )}
              <div className="flex items-center gap-3">
                {view === "diagram" && slice && (slice.hidden > 0 || showAll) && slice.total > MAX_FUNCS && (
                  <button type="button" onClick={() => setShowAll((v) => !v)} className={cn(LABEL, "text-link hover:underline")}>
                    {showAll ? `Top ${MAX_FUNCS} only` : `Show all ${slice.total}`}
                  </button>
                )}
                {selected.size > 0 && (
                  <p className={cn(LABEL, "flex items-center gap-2 text-ink-2")}>
                    {selected.size} selected
                    <button
                      type="button"
                      onClick={() => {
                        setSelected(new Set());
                        setActive(null);
                      }}
                      className="text-link hover:underline"
                    >
                      Clear
                    </button>
                  </p>
                )}
              </div>
            </div>

            {view === "diagram" ? (
              slice ? (
                <>
                  <GraphMapDiagram slice={slice} selected={selected} active={active} match={match} onToggle={toggle} />
                  {active ? (
                    <NodeExplain key={active} graph={g} slice={slice} id={active} onClose={() => setActive(null)} />
                  ) : (
                    <p className={cn(LABEL, "text-ink-4")}>Click a node to see what it is and how it connects</p>
                  )}
                </>
              ) : (
                <p className="border border-line px-6 py-16 text-center text-[13px] text-ink-3">No services in this repository&apos;s graph yet.</p>
              )
            ) : (
              <GraphMapTable graph={g} selected={selected} match={match} showTests={showTests} onToggle={toggle} onSet={(ids) => setSelected(new Set(ids))} />
            )}
          </section>

          <aside className="flex flex-col gap-5 border-t border-line p-5 xl:border-t-0">
            <section aria-labelledby="demo-h">
              <h2 id="demo-h" className="flex items-center gap-1.5 text-[13px] font-semibold">
                <FlaskConical size={14} aria-hidden /> Try it
              </h2>
              {demo ? (
                <>
                  <p className="mt-1 text-[12.5px] leading-[1.5] text-ink-3">Simulates a bad deploy on this repository: failing traffic, then Kaizen finds the cause with the real detector and graph queries. Only the traffic is synthetic.</p>
                  <div className="mt-3 flex gap-2">
                    <Button variant="primary" onClick={onInject} disabled={inject.isPending || incidentId !== null} data-testid="inject">
                      {inject.isPending ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <Zap size={13} aria-hidden />} Inject a problem
                    </Button>
                    <Button onClick={onReset} disabled={reset.isPending}>
                      <RotateCcw size={13} aria-hidden /> Reset
                    </Button>
                  </div>
                  {error && (
                    <p role="alert" className="mt-2 text-[12.5px] text-red">
                      {error}
                    </p>
                  )}
                </>
              ) : (
                <p className="mt-1 text-[12.5px] leading-[1.5] text-ink-3">The map updates live: when a deploy breaks production, the failing function turns red and the culprit commit is marked.</p>
              )}
            </section>

            {(incidentId || !demo) && (
              <section aria-labelledby="flow-h">
                <h2 id="flow-h" className="flex items-center justify-between text-[13px] font-semibold">
                  What Kaizen is doing {incidentId && <span className="num text-[12px] font-normal text-ink-3">{elapsed}s</span>}
                </h2>
                <div className="mt-3">
                  <Pipeline steps={steps} active={incidentId !== null} />
                </div>
              </section>
            )}

            {top && named && (
              <section aria-labelledby="res-h" className="border border-line bg-panel-2 p-3" data-testid="result">
                <h2 id="res-h" className="text-[13px] font-semibold">
                  Root cause
                </h2>
                <p className="mt-1.5 text-[13px]">
                  <code className="font-mono">{top.sha.slice(0, 7)}</code> {top.message}
                </p>
                <p className="mt-1 text-[12.5px] text-ink-3">
                  {top.author ? `by ${top.author} · ` : ""}
                  {Math.round(top.confidence * 100)}% confidence
                </p>
                <h3 className="mt-3 text-[12px] font-semibold text-ink-2">Why: graph queries behind the score</h3>
                <ul className="mt-1.5 space-y-1.5" data-testid="why-features">
                  {[...top.features]
                    .sort((a, b) => b.contribution - a.contribution)
                    .slice(0, 4)
                    .map((f) => (
                      <li key={f.name}>
                        <details>
                          <summary className="flex cursor-pointer items-center gap-2 text-[12px]">
                            <span className="w-20 shrink-0 font-medium">{f.name}</span>
                            <span className="h-1.5 flex-1 overflow-hidden rounded bg-raised">
                              <i className="block h-full bg-emerald-500" style={{ width: `${Math.round(Math.min(1, Math.abs(f.value)) * 100)}%` }} />
                            </span>
                            <span className="num w-10 text-right text-ink-3">{f.value.toFixed(2)}</span>
                            {ALGO[f.name] && <span className="rounded bg-emerald-500/15 px-1.5 text-[10.5px] text-emerald-600 dark:text-emerald-400">{ALGO[f.name]}</span>}
                          </summary>
                          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[10.5px] leading-[1.45] text-ink-3">{f.cypher}</pre>
                        </details>
                      </li>
                    ))}
                </ul>
                {inc && (
                  <Link href={`/incidents/${encodeURIComponent(inc.incident.id)}`} className="mt-2 inline-block text-[12.5px] text-link hover:underline">
                    Open evidence, revert PR and score breakdown
                  </Link>
                )}
              </section>
            )}

            <section aria-labelledby="fdb-h">
              <h2 id="fdb-h" className="flex items-center gap-1.5 text-[13px] font-semibold">
                <Database size={14} className="text-emerald-600 dark:text-emerald-400" aria-hidden /> Powered by FalkorDB
              </h2>
              <p className="mt-1 text-[12.5px] leading-[1.5] text-ink-3">This view is built from these Cypher queries, run live against the repository graph.</p>
              <div className="mt-2 space-y-1.5">
                {Object.entries(g.queries).map(([name, cypher]) => (
                  <details key={name} open={incidentId !== null && (name === "failing" || name === "suspects")} className="border border-line bg-panel-2 px-2.5 py-1.5">
                    <summary className="cursor-pointer text-[12px] font-medium">
                      {name.replace("_", " ")} <span className="font-normal text-ink-3">· {g.query_ms?.[name] ?? "-"} ms</span>
                    </summary>
                    <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap font-mono text-[11px] leading-[1.5] text-ink-3">{cypher}</pre>
                  </details>
                ))}
              </div>
            </section>
          </aside>
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
