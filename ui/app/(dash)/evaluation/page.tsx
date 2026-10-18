"use client";

import { ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";
import { SectionHead, Segmented, Tag } from "@/components/app/kit";
import { EvalBarChart } from "@/components/dashboard/eval-chart";
import { useEvalLatest } from "@/lib/api/hooks-dashboard";
import { SPLITS, SPLIT_COLUMNS, graphMisses, memorySummary, pct, scenarioGroups, scenarioRow, splitRow, type EvalResult, type Split } from "@/lib/eval";
import { cn } from "@/lib/utils";

const SPLIT_OPTIONS = SPLITS.map((s) => ({ id: s, label: s }));

/** The latest `make eval` result (docs/results/latest.json), rendered as is. */
export default function EvaluationPage() {
  const q = useEvalLatest();
  return (
    <div className="flex flex-1 flex-col">
      {q.isError ? (
        <div role="alert" className="px-6 py-16 text-center">
          <p className="text-[14px] font-medium">No evaluation result</p>
          <p className="mt-1 text-[13px] text-ink-3">{q.error.message}</p>
        </div>
      ) : !q.data ? (
        <p role="status" className="px-6 py-6 text-[13px] text-ink-3">Loading the evaluation result</p>
      ) : (
        <Report ev={q.data} />
      )}
    </div>
  );
}

function Report({ ev }: { ev: EvalResult }) {
  const [split, setSplit] = useState<Split>("all");
  const rows = ev.splits[split] ?? [];
  const groups = scenarioGroups(ev);
  const misses = graphMisses(ev);
  const memory = memorySummary(ev);
  return (
    <>
      <header data-enter className="border-b border-line px-6 py-6">
        <h1 className="text-[20px] font-semibold tracking-[-0.015em]">Evaluation: graph ranking vs. baselines</h1>
        <p className="mt-1 max-w-3xl text-[13px] text-ink-2">
          Replay benchmark over {ev.scenarios.length} scenario variants: every method ranks the same candidate set. Weights <code className="font-mono">{ev.weights_version}</code>, generated{" "}
          <time dateTime={ev.generated_at}>{new Date(ev.generated_at).toLocaleString("en", { dateStyle: "medium", timeStyle: "short" })}</time>, wall time {ev.runtime_seconds}s.
        </p>
        <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-[12.5px] text-ink-3">
          {ev.blast_radius && (
            <div>
              <dt className="inline">Blast radius </dt>
              <dd className="num inline text-ink-2">
                precision {ev.blast_radius.precision?.toFixed(2)}, recall {ev.blast_radius.recall?.toFixed(2)} (n={ev.blast_radius.n})
              </dd>
            </div>
          )}
          <div>
            <dt className="inline">Fix verification </dt>
            <dd className="inline text-ink-2">{ev.fix_verification_rate == null ? "n/a (verified in the live staging loop)" : pct(ev.fix_verification_rate)}</dd>
          </div>
          {memory && (
            <div>
              <dt className="inline">Memory (S8 recurrence) </dt>
              <dd className="num inline text-ink-2">
                top-1 confidence {memory.without.toFixed(2)} → {memory.with.toFixed(2)} over {memory.n} variants
              </dd>
            </div>
          )}
        </dl>
      </header>

      <section data-enter aria-labelledby="ev-split">
        <div className="flex h-11 items-center gap-2 border-b border-line px-6">
          <h2 id="ev-split" className="text-[13px] font-semibold">
            Results by split
          </h2>
          <span className="ml-auto">
            <Segmented label="Split" options={SPLIT_OPTIONS} value={split} onChange={setSplit} />
          </span>
        </div>
        <div className="overflow-x-auto">
          <table aria-label={`Results, ${split} split`} className="w-full min-w-[720px] text-left text-[13px]">
            <thead className="text-[12px] text-ink-3">
              <tr className="border-b border-line">
                {SPLIT_COLUMNS.map((c, i) => (
                  <th key={c} scope="col" className={cn("py-2 font-medium", i === 0 ? "px-6" : "px-3", i > 0 && "text-right")}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.method} data-method={r.method} className={cn("border-b border-line", r.method === "graph" && "bg-sel")}>
                  {splitRow(r).map((cell, i) => (
                    <td key={i} className={cn("num py-2", i === 0 ? "px-6 font-mono" : "px-3 text-right")}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <EvalBarChart rows={rows} split={split} />
      </section>

      <section data-enter aria-labelledby="ev-scen" className="mt-2">
        <SectionHead id="ev-scen" title="Per scenario" count={`${groups.length} scenarios`} extra={<span className="text-[12px] text-ink-3">top-1 correct, by method; expand for variants</span>} />
        <div className="overflow-x-auto">
          <table aria-label="Top-1 correct per scenario" className="w-full min-w-[860px] text-left text-[13px]">
            <thead className="text-[12px] text-ink-3">
              <tr className="border-b border-line">
                <th scope="col" className="px-6 py-2 font-medium">scenario</th>
                <th scope="col" className="px-3 py-2 font-medium">split</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">variants</th>
                {ev.methods.map((m) => (
                  <th key={m} scope="col" className="px-3 py-2 text-right font-medium">
                    {m}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <ScenarioRows key={g.id} ev={ev} group={g} />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section data-enter aria-labelledby="ev-miss" className="mt-6 pb-8">
        <SectionHead id="ev-miss" title="Where the graph ranking is wrong" count={misses.length} />
        {misses.length === 0 ? (
          <p className="px-6 py-4 text-[13px] text-ink-3">None in this run.</p>
        ) : (
          <ul aria-label="Graph misses">
            {misses.map((w) => (
              <li key={`${w.id}-${w.variant}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-6 py-2 text-[13px]">
                <code className="font-mono">
                  {w.id} v{w.variant}
                </code>
                <span className="text-ink-2">{w.title}</span>
                <Tag tone={w.verdict === "confident" ? "red" : "amber"}>{w.verdict}</Tag>
                <span className="text-ink-3">
                  top <code className="font-mono">{w.top}</code>, culprit at rank {w.rank ?? "–"}
                </span>
              </li>
            ))}
          </ul>
        )}
        {ev.failures.length > 0 && (
          <>
            <h3 className="px-6 pt-4 text-[13px] font-semibold">Failed variants</h3>
            <ul aria-label="Failed variants" className="px-6 py-2 text-[13px] text-red">
              {ev.failures.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </>
        )}
      </section>
    </>
  );
}

function ScenarioRows({ ev, group }: { ev: EvalResult; group: ReturnType<typeof scenarioGroups>[number] }) {
  const [open, setOpen] = useState(false);
  const cells = scenarioRow(group, ev.methods);
  return (
    <Fragment>
      <tr className="border-b border-line">
        <th scope="row" className="px-6 py-2 text-left font-medium">
          <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1 font-mono" title={group.title}>
            <ChevronRight size={13} aria-hidden="true" className={cn("transition-transform", open && "rotate-90")} />
            {group.id}
            <span className="sr-only"> variants</span>
          </button>
          <span className="ml-2 hidden text-[12px] font-normal text-ink-3 lg:inline">{group.title}</span>
        </th>
        {cells.slice(1).map((c, i) => (
          <td key={i} className={cn("num px-3 py-2", i === 0 ? "" : "text-right", i >= 2 && c === "100%" && "text-green", i >= 2 && c === "0%" && "text-red")}>
            {c}
          </td>
        ))}
      </tr>
      {open &&
        group.variants.map((v) => (
          <tr key={v.variant} className="border-b border-line bg-panel-2 text-[12px]">
            <td className="px-6 py-1.5 pl-10 font-mono text-ink-3">
              v{v.variant} culprit {v.culprit.slice(0, 12)}
            </td>
            <td className="px-3 py-1.5" colSpan={2} />
            {ev.methods.map((m) => {
              const r = v.results[m];
              return (
                <td key={m} className="px-3 py-1.5 text-right">
                  {r ? (
                    <span className={cn("inline-flex flex-col items-end", r.correct ? "text-green" : "text-red")}>
                      <span>{r.correct ? "correct" : "wrong"}</span>
                      <span className="font-mono text-ink-3">
                        {r.top?.slice(0, 7) ?? "–"} · rank {r.rank_of_culprit ?? "–"} · {r.verdict}
                      </span>
                    </span>
                  ) : (
                    <span className="text-ink-4">–</span>
                  )}
                </td>
              );
            })}
          </tr>
        ))}
    </Fragment>
  );
}
