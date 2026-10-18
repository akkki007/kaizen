"use client";

import { Check, Circle, Database, FlaskConical, Loader2, RotateCcw, Zap } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { Button, Tag } from "@/components/app/kit";
import { useWorkspace } from "@/components/app/shell";
import { workspaceTabs } from "@/components/app/tabs";
import { Topbar } from "@/components/app/topbar";
import { CodeGraphView } from "@/components/code-graph";
import { Maximize2, Search } from "lucide-react";
import { LogoMark } from "@/components/landing/logo";
import { useIncidentDetail } from "@/lib/api/hooks";
import { useCodeGraph, useInjectProblem, useResetDemo } from "@/lib/api/hooks-graph";
import { cn } from "@/lib/utils";

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
                <span className={cn("rounded px-1.5 py-px text-[10.5px] font-medium", s.engine === "FalkorDB" ? "bg-emerald-500/15 text-emerald-400" : "bg-raised text-ink-3")}>{s.engine}</span>
                {s.query && <code className="truncate font-mono text-[11px] text-ink-3">{s.query}</code>}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function GraphPage() {
  const { ws } = useWorkspace();
  const router = useRouter();
  const params = useSearchParams();
  const repo = params.get("repo") ?? ws.repos[0]?.name;
  const [incidentId, setIncidentId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [showTests, setShowTests] = useState(false);
  const [showCalls, setShowCalls] = useState(false);
  const [fit, setFit] = useState(0);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const graph = useCodeGraph(repo, incidentId !== null);
  const detail = useIncidentDetail(incidentId ?? "");
  const inject = useInjectProblem(repo ?? "");
  const reset = useResetDemo(repo ?? "");
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!startedAt) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [startedAt]);

  const failing = graph.data?.nodes.filter((n) => n.state === "failing") ?? [];
  const suspects = graph.data?.nodes.filter((n) => n.state === "suspect") ?? [];
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
  const sel = useMemo(() => graph.data?.nodes.find((n) => n.id === selected), [graph.data, selected]);
  const links = useMemo(() => {
    const g = graph.data;
    const name = (id: string) => g?.nodes.find((n) => n.id === id)?.label ?? id;
    if (!g || !selected) return { callers: [] as string[], callees: [] as string[], commits: [] as string[] };
    return {
      callers: g.edges.filter((e) => e.rel === "calls" && e.target === selected).map((e) => name(e.source)),
      callees: g.edges.filter((e) => e.rel === "calls" && e.source === selected).map((e) => name(e.target)),
      commits: g.edges.filter((e) => e.rel === "modifies" && e.target === selected).map((e) => name(e.source)),
    };
  }, [graph.data, selected]);
  const demo = graph.data?.demo_enabled ?? false;
  const elapsed = startedAt ? Math.max(0, Math.round((now - startedAt) / 1000)) : 0;

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
    setSelected(null);
    setError(null);
  }

  return (
    <div className="flex flex-1 flex-col">
      <Topbar
        crumbs={[{ label: <span className="inline-flex items-center gap-1.5"><LogoMark className="size-3.5 text-accent" />Kaizen</span>, href: "/overview" }, { label: "Live graph" }]}
        tabs={workspaceTabs(ws, "graph")}
      />
      <header data-enter className="flex flex-wrap items-end gap-3 border-b border-line px-6 py-6">
        <div className="min-w-0 flex-1">
          <h1 className="text-[20px] font-semibold tracking-[-0.015em]">Live graph</h1>
          <p className="mt-1 text-[13px] text-ink-2">Every function and commit of your repository, stored in FalkorDB. Healthy code is green; when something breaks, only the node that failed turns red.</p>
        </div>
        {ws.repos.length > 1 && (
          <select aria-label="Repository" value={repo} onChange={(e) => router.replace(`/graph?repo=${encodeURIComponent(e.target.value)}`)} className="h-7 rounded-md border border-line bg-panel-2 px-1.5 text-[12.5px] text-ink">
            {ws.repos.map((r) => (
              <option key={r.name} value={r.name}>{r.name}</option>
            ))}
          </select>
        )}
      </header>

      {!repo ? (
        <p className="px-6 py-6 text-[13px] text-ink-3">Connect a repository to see its graph.</p>
      ) : graph.isError ? (
        <p role="alert" className="px-6 py-6 text-[13px] text-red">Could not load the graph: {graph.error.message}</p>
      ) : !graph.data ? (
        <p role="status" className="px-6 py-6 text-[13px] text-ink-3">Loading the graph</p>
      ) : (
        <div className="grid flex-1 gap-0 lg:grid-cols-[1fr_360px]">
          <section aria-label="Code graph" className="flex min-h-[640px] flex-col border-b border-line lg:border-b-0 lg:border-r">
            <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5 text-[12px]">
              <span data-testid="fdb-bar" className="inline-flex items-center gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1 text-ink">
                <Database size={14} className="text-emerald-400" aria-hidden />
                <b className="font-semibold text-emerald-300">FalkorDB</b>
                <span className="text-ink-2">{graph.data.nodes.length} nodes · {graph.data.edges.length} edges</span>
                <span className="text-ink-3">|</span>
                <span className="text-ink-2" title="Measured on this refresh">{Object.keys(graph.data.queries).length} Cypher queries in {Math.round(Object.values(graph.data.query_ms ?? {}).reduce((a, b) => a + b, 0))} ms</span>
              </span>
              {failing.length > 0 && <Tag tone="red">{failing.length} failing</Tag>}
              <span className="flex-1" />
              <label className="relative">
                <Search size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-ink-3" aria-hidden />
                <input type="search" aria-label="Find a function or commit" placeholder="Find a function or commit" value={query} onChange={(e) => setQuery(e.target.value)} className="h-7 w-52 rounded-md border border-line bg-panel-2 pl-7 pr-2 text-ink placeholder:text-ink-3" />
              </label>
              <label className="inline-flex items-center gap-1.5 rounded-md border border-line bg-panel-2 px-2 py-1 text-ink-2"><input type="checkbox" checked={showCalls} onChange={(e) => setShowCalls(e.target.checked)} />All call lines</label>
              <label className="inline-flex items-center gap-1.5 rounded-md border border-line bg-panel-2 px-2 py-1 text-ink-2"><input type="checkbox" checked={showTests} onChange={(e) => setShowTests(e.target.checked)} />Test code</label>
              <button type="button" onClick={() => setFit((n) => n + 1)} className="inline-flex h-7 items-center gap-1 rounded-md border border-line bg-panel-2 px-2 text-ink-2 hover:text-ink"><Maximize2 size={12} aria-hidden />Fit</button>
            </div>
            <div className="relative min-h-0 flex-1">
            <div className="pointer-events-none absolute bottom-3 left-4 z-10 flex flex-wrap gap-3 text-[11.5px] text-ink-3"><span className="text-ink-4">Click a node to see what calls it and what it calls · hover to peek at its lines</span>
              <span><i className="mr-1 inline-block size-2.5 rounded-full bg-emerald-400" />healthy</span>
              <span><i className="mr-1 inline-block size-2.5 rounded-full bg-red-500" />failing</span>
              <span><i className="mr-1 inline-block size-2.5 rotate-45 bg-amber-400" />suspect commit</span>
            </div>
            <CodeGraphView graph={graph.data} selected={selected} query={query} showTests={showTests} showCalls={showCalls} fitSignal={fit} onSelect={setSelected} />
            {sel && (
              <div role="status" className="absolute right-4 top-3 z-10 max-w-[260px] rounded-lg border border-line bg-panel-2/95 p-3 text-[12.5px] shadow-md">
                <p className="font-medium">{sel.label} <span className="font-normal text-ink-3">· {sel.kind}</span></p>
                {sel.detail && <p className="mt-0.5 break-words text-ink-3">{sel.detail}</p>}
                {links.callers.length > 0 && <p className="mt-1.5 text-ink-3"><span className="text-ink-2">Called by</span> {links.callers.slice(0, 6).join(", ")}{links.callers.length > 6 ? ` +${links.callers.length - 6}` : ""}</p>}
                {links.callees.length > 0 && <p className="mt-1 text-ink-3"><span className="text-ink-2">Calls</span> {links.callees.slice(0, 6).join(", ")}{links.callees.length > 6 ? ` +${links.callees.length - 6}` : ""}</p>}
                {links.commits.length > 0 && <p className="mt-1 text-ink-3"><span className="text-ink-2">Changed by</span> {links.commits.slice(0, 5).join(", ")}</p>}
                {sel.incident_id && <Link href={`/incidents/${encodeURIComponent(sel.incident_id)}`} className="mt-1.5 inline-block text-link hover:underline">Open the incident</Link>}
              </div>
            )}
            </div>
          </section>

          <aside className="flex flex-col gap-5 p-5">
            <section aria-labelledby="demo-h">
              <h2 id="demo-h" className="flex items-center gap-1.5 text-[13px] font-semibold"><FlaskConical size={14} aria-hidden /> Try it</h2>
              {demo ? (
                <>
                  <p className="mt-1 text-[12.5px] leading-[1.5] text-ink-3">Simulates a bad deploy on this repository: failing traffic, then Kaizen finds the cause with the real detector and graph queries. Only the traffic is synthetic.</p>
                  <div className="mt-3 flex gap-2">
                    <Button variant="primary" onClick={onInject} disabled={inject.isPending || incidentId !== null} data-testid="inject">
                      {inject.isPending ? <Loader2 size={13} className="animate-spin" aria-hidden /> : <Zap size={13} aria-hidden />} Inject a problem
                    </Button>
                    <Button onClick={onReset} disabled={reset.isPending}><RotateCcw size={13} aria-hidden /> Reset</Button>
                  </div>
                  {error && <p role="alert" className="mt-2 text-[12.5px] text-red">{error}</p>}
                </>
              ) : (
                <p className="mt-1 text-[12.5px] leading-[1.5] text-ink-3">The graph updates live: when a deploy breaks production, the failing function turns red here and the culprit commit is marked.</p>
              )}
            </section>

            {(incidentId || !demo) && (
              <section aria-labelledby="flow-h">
                <h2 id="flow-h" className="flex items-center justify-between text-[13px] font-semibold">What Kaizen is doing {incidentId && <span className="num text-[12px] font-normal text-ink-3">{elapsed}s</span>}</h2>
                <div className="mt-3"><Pipeline steps={steps} active={incidentId !== null} /></div>
              </section>
            )}

            {top && named && (
              <section aria-labelledby="res-h" className="rounded-lg border border-line bg-panel-2 p-3" data-testid="result">
                <h2 id="res-h" className="text-[13px] font-semibold">Root cause</h2>
                <p className="mt-1.5 text-[13px]"><code className="font-mono">{top.sha.slice(0, 7)}</code> {top.message}</p>
                <p className="mt-1 text-[12.5px] text-ink-3">{top.author ? `by ${top.author} · ` : ""}{Math.round(top.confidence * 100)}% confidence</p>
                <h3 className="mt-3 text-[12px] font-semibold text-ink-2">Why: graph queries behind the score</h3>
                <ul className="mt-1.5 space-y-1.5" data-testid="why-features">
                  {[...top.features].sort((a, b) => b.contribution - a.contribution).slice(0, 4).map((f) => (
                    <li key={f.name}>
                      <details>
                        <summary className="flex cursor-pointer items-center gap-2 text-[12px]">
                          <span className="w-20 shrink-0 font-medium">{f.name}</span>
                          <span className="h-1.5 flex-1 overflow-hidden rounded bg-raised"><i className="block h-full bg-emerald-400" style={{ width: `${Math.round(Math.min(1, Math.abs(f.value)) * 100)}%` }} /></span>
                          <span className="num w-10 text-right text-ink-3">{f.value.toFixed(2)}</span>
                          {ALGO[f.name] && <span className="rounded bg-emerald-500/15 px-1.5 text-[10.5px] text-emerald-400">{ALGO[f.name]}</span>}
                        </summary>
                        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[10.5px] leading-[1.45] text-ink-3">{f.cypher}</pre>
                      </details>
                    </li>
                  ))}
                </ul>
                {inc && <Link href={`/incidents/${encodeURIComponent(inc.incident.id)}`} className="mt-2 inline-block text-[12.5px] text-link hover:underline">Open evidence, revert PR and score breakdown</Link>}
              </section>
            )}

            <section aria-labelledby="fdb-h">
              <h2 id="fdb-h" className="flex items-center gap-1.5 text-[13px] font-semibold"><Database size={14} className="text-emerald-400" aria-hidden /> Powered by FalkorDB</h2>
              <p className="mt-1 text-[12.5px] leading-[1.5] text-ink-3">This view is built from these Cypher queries, run live against the repository graph{suspects.length ? "" : ""}.</p>
              <div className="mt-2 space-y-1.5">
                {Object.entries(graph.data.queries).map(([name, cypher]) => (
                  <details key={name} open={incidentId !== null && (name === "failing" || name === "suspects")} className="rounded-md border border-line bg-panel-2 px-2.5 py-1.5">
                    <summary className="cursor-pointer text-[12px] font-medium">{name.replace("_", " ")} <span className="font-normal text-ink-3">· {graph.data.query_ms?.[name] ?? "-"} ms</span></summary>
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
      <GraphPage />
    </Suspense>
  );
}
