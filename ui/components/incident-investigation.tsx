"use client";

import { Check, ChevronDown, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Button, Segmented, Skeleton, Tag } from "@/components/app/kit";
import { ErrorState } from "@/components/states";
import { useApproveFix, useBaseline, useIncidentDetail, useMe, useProposeFix, type RankMethod } from "@/lib/api/hooks";
import type { components } from "@/lib/api/schema";
import { haptic } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { stepSummary } from "@/lib/workspace";

type Candidate = components["schemas"]["RankedCandidate"];
type Feature = components["schemas"]["FeatureValue"];
type Remediation = components["schemas"]["RemediationView"];

const VIEWS = [
  { id: "graph", label: "Graph" },
  { id: "b1", label: "Baseline: keywords" },
  { id: "b0", label: "Baseline: latest commit" },
] as const;

const VERDICT_COPY: Record<string, string> = {
  confident: "Confident",
  ambiguous: "Ambiguous: top suspects are too close to call",
  no_code_evidence: "No code evidence: nothing ties these changes to the failure",
  no_candidates: "No candidate commits found for this incident",
};

const TONE = { good: "text-green", bad: "text-red", warn: "text-amber", none: "text-ink-2" } as const;

function Verdict({ tone, children }: { tone: keyof typeof TONE; children: ReactNode }) {
  return (
    <p role="status" className={cn("flex items-center gap-1.5 text-[13px]", TONE[tone])}>
      {children}
    </p>
  );
}

/** A detail-page section: hairline header with title and optional controls, then content. */
export function Section({ id, title, aside, children }: { id: string; title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section data-enter aria-labelledby={id} className="border-b border-line">
      <div className="flex min-h-11 flex-wrap items-center gap-2 border-b border-line px-6 py-2">
        <h2 id={id} className="text-[13px] font-semibold">
          {title}
        </h2>
        {aside && <div className="ml-auto">{aside}</div>}
      </div>
      <div className="px-6 py-4">{children}</div>
    </section>
  );
}

function FeatureRow({ f }: { f: Feature }) {
  const [open, setOpen] = useState(false);
  const pct = Math.round(Math.min(1, Math.max(0, f.value)) * 100);
  return (
    <li>
      <div className="grid grid-cols-[84px_minmax(0,1fr)_36px_52px] items-center gap-3 text-[12.5px] sm:grid-cols-[96px_minmax(0,1fr)_40px_56px_auto]">
        <span className="text-ink-2">{f.name}</span>
        <span className="h-1.5 overflow-hidden rounded-full bg-ink/[0.07]" aria-hidden="true">
          <i className={cn("block h-full rounded-full", f.weight < 0 ? "bg-amber" : "bg-accent")} style={{ width: `${pct}%` }} />
        </span>
        <span className="num text-right text-ink-2">{f.value.toFixed(2)}</span>
        <span className="num text-right text-ink-3" title="weight × value">
          {f.contribution >= 0 ? "+" : "−"}
          {Math.abs(f.contribution).toFixed(3)}
        </span>
        <button
          type="button"
          aria-expanded={open}
          aria-label={`Show the query behind ${f.name}`}
          onClick={() => setOpen((o) => !o)}
          className="col-span-4 inline-flex h-5 items-center gap-1 justify-self-start rounded px-1.5 font-mono text-[11px] text-ink-3 transition-colors hover:bg-hover hover:text-ink sm:col-span-1"
        >
          {f.query}
          <ChevronDown size={11} className={cn("transition-transform", open && "rotate-180")} />
        </button>
      </div>
      {open && <pre className="mt-2 overflow-x-auto rounded-md border border-line bg-panel-2 px-3 py-2.5 font-mono text-[11.5px] leading-[1.6] text-ink-2">{f.cypher}</pre>}
    </li>
  );
}

function Suspect({ c, defaultOpen }: { c: Candidate; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const top = c.rank === 1;
  return (
    <li className={cn("rounded-lg border", top ? "border-line bg-panel-2" : "border-transparent")}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="grid w-full grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-hover"
      >
        <span className="num text-[12px] text-ink-3">#{c.rank}</span>
        <span className="min-w-0">
          <span className="flex min-w-0 items-center gap-2">
            <code className="font-mono text-[12px] text-link">{c.sha.slice(0, 7)}</code>
            <span className="truncate text-[13px] text-ink">{c.message || "(no message)"}</span>
          </span>
          <span className="mt-0.5 block text-[12px] text-ink-3">
            {c.author ?? "unknown"}
            {c.deployment ? ` · ${c.deployment}` : ""}
          </span>
        </span>
        <span className="text-right">
          <strong className="num block text-[14px] font-medium">{c.score.toFixed(2)}</strong>
          {c.features.length > 0 && <small className="num text-[11.5px] text-ink-3">{Math.round(c.confidence * 100)}% conf.</small>}
        </span>
      </button>
      {open && c.features.length > 0 && (
        <div className="flex flex-col gap-3 px-3 pb-3 pt-1 animate-rise">
          <ul className="flex flex-col gap-2" aria-label={`Score breakdown for ${c.sha.slice(0, 7)}`}>
            {c.features.map((f) => (
              <FeatureRow key={f.name} f={f} />
            ))}
          </ul>
          {c.evidence.length > 0 && (
            <p className="flex flex-wrap items-center gap-1.5 text-[12px] text-ink-3">
              <span>Path</span>
              {c.evidence.map((e) => (
                <code key={`${e.changed_function}>${e.thrown_function}`} className="rounded bg-ink/[0.06] px-1.5 py-0.5 font-mono text-ink-2">
                  {e.changed_function.split(":").pop()} → {e.thrown_function.split(":").pop()}
                </code>
              ))}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

const REMEDIATION_COPY: Record<string, string> = {
  proposing: "Preparing the revert branch…",
  verifying: "Revert PR opened; verifying in staging…",
  verified: "Fix tested in staging: passed",
  failed: "Fix tested in staging: failed",
  needs_manual: "The commit cannot be reverted cleanly; it needs a manual fix",
  approved: "Approved; waiting for the pull request to be merged",
  merged: "Merged: incident resolved",
};

function FixSection({ incidentId, remediation, canAct, hasSuspect }: { incidentId: string; remediation: Remediation | null; canAct: boolean; hasSuspect: boolean }) {
  const propose = useProposeFix(incidentId);
  const approve = useApproveFix(incidentId);
  const v = remediation?.verification as
    | { passed: boolean; run_url?: string; error_rate_before?: number | null; error_rate_after?: number | null; baseline_error_rate?: number | null }
    | null
    | undefined;
  const pct = (x?: number | null) => (x === null || x === undefined ? "n/a" : `${(x * 100).toFixed(1)}%`);
  const tone = !remediation ? "none" : remediation.status === "failed" || remediation.status === "needs_manual" ? "bad" : remediation.status === "verified" || remediation.status === "merged" ? "good" : "warn";

  return (
    <Section id="fix-title" title="Fix and verification">
      {!remediation ? (
        <div className="flex flex-col items-start gap-3">
          <p className="text-[13px] text-ink-3">No fix has been proposed for this incident yet.</p>
          {canAct && hasSuspect && (
            <Button
              variant="primary"
              disabled={propose.isPending}
              onClick={() => {
                haptic("confirm");
                propose.mutate();
              }}
            >
              {propose.isPending ? "Requesting…" : "Open revert PR for the top suspect"}
            </Button>
          )}
          {propose.isSuccess && <p className="text-[13px] text-ink-3">Requested. The pull request will appear here shortly.</p>}
          {propose.error && <p role="alert" className="text-[13px] text-red">{propose.error.message}</p>}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <Verdict tone={tone}>{REMEDIATION_COPY[remediation.status] ?? remediation.status}</Verdict>
          <p className="text-[13px] text-ink-3">
            Revert of <code className="font-mono text-ink-2">{remediation.sha?.slice(0, 7)}</code> on <code className="font-mono text-ink-2">{remediation.branch}</code>
            {remediation.pr_url && (
              <>
                {" · "}
                <a href={remediation.pr_url} target="_blank" rel="noreferrer" className="text-link hover:underline">
                  pull request
                </a>
              </>
            )}
          </p>
          {v && (
            <dl className="grid grid-cols-2 overflow-hidden rounded-lg border border-line sm:grid-cols-4 [&>div]:border-line [&>div]:px-4 [&>div]:py-3 [&>div:not(:last-child)]:border-r">
              <div>
                <dt className="text-[12px] text-ink-3">Before the fix</dt>
                <dd className="num mt-1 text-[16px] font-medium text-red">{pct(v.error_rate_before)}</dd>
              </div>
              <div>
                <dt className="text-[12px] text-ink-3">In staging after</dt>
                <dd className="num mt-1 text-[16px] font-medium text-green">{pct(v.error_rate_after)}</dd>
              </div>
              <div>
                <dt className="text-[12px] text-ink-3">Baseline</dt>
                <dd className="num mt-1 text-[16px] font-medium">{pct(v.baseline_error_rate)}</dd>
              </div>
              {v.run_url && (
                <div>
                  <dt className="text-[12px] text-ink-3">Run</dt>
                  <dd className="mt-1 text-[14px]">
                    <a href={v.run_url} target="_blank" rel="noreferrer" className="text-link hover:underline">
                      Actions
                    </a>
                  </dd>
                </div>
              )}
            </dl>
          )}
          {canAct && remediation.status === "verified" && (
            <Button
              variant="primary"
              className="self-start"
              disabled={approve.isPending}
              onClick={() => {
                haptic("confirm");
                approve.mutate(remediation.id);
              }}
            >
              {approve.isPending ? "Approving…" : "Approve fix"}
            </Button>
          )}
          {remediation.approved_by && <p className="text-[13px] text-ink-3">Approved by {remediation.approved_by}. A human merges the pull request.</p>}
          {approve.error && <p role="alert" className="text-[13px] text-red">{approve.error.message}</p>}
        </div>
      )}
    </Section>
  );
}

/** Suspects (with the baselines for comparison), the agent's trace and explanation, and the fix. */
export function IncidentInvestigation({ incidentId, evidence }: { incidentId: string; evidence?: ReactNode }) {
  const [view, setView] = useState<RankMethod>("graph");
  const detail = useIncidentDetail(incidentId);
  const baseline = useBaseline(incidentId, view === "graph" ? "b1" : view, view !== "graph");
  const me = useMe();

  if (detail.isLoading) {
    return (
      <div className="flex flex-col gap-3 px-6 py-6">
        <Skeleton className="h-4 w-40" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }
  if (detail.error || !detail.data) {
    return (
      <div className="p-6">
        <ErrorState error={detail.error} onRetry={() => detail.refetch()} />
      </div>
    );
  }

  const graph = detail.data.ranking;
  const shown = view === "graph" ? graph : (baseline.data ?? null);
  const graphTop = graph.candidates[0]?.sha;
  const inv = detail.data.investigation;
  const role = me.data?.repos[detail.data.incident.repo];
  const graphTone = graph.verdict === "confident" ? "good" : graph.verdict === "ambiguous" ? "warn" : "none";

  return (
    <>
      <Section id="sus-title" title="Suspect commits" aside={<Segmented label="Ranking method" options={VIEWS} value={view} onChange={setView} />}>
        <div className="mb-3">
          {view === "graph" ? (
            <Verdict tone={graphTone}>
              {VERDICT_COPY[graph.verdict] ?? graph.verdict}
              {graph.verdict === "confident" || graph.verdict === "ambiguous" ? <span className="text-ink-3"> · margin {graph.margin.toFixed(2)}</span> : null}
            </Verdict>
          ) : shown ? (
            shown.candidates[0]?.sha === graphTop ? (
              <Verdict tone="good">
                <Check size={14} aria-hidden /> The baseline picks the same commit as the graph.
              </Verdict>
            ) : (
              <Verdict tone="bad">
                <X size={14} aria-hidden /> The baseline blames <code className="font-mono">{shown.candidates[0]?.sha.slice(0, 7)}</code>; the graph says{" "}
                <code className="font-mono">{graphTop?.slice(0, 7)}</code>.
              </Verdict>
            )
          ) : null}
        </div>
        {view !== "graph" && baseline.isLoading && <Skeleton className="h-14 w-full" />}
        {shown && shown.candidates.length === 0 && <p className="text-[13px] text-ink-3">No candidate commits.</p>}
        {shown && shown.candidates.length > 0 && (
          <ol aria-label="Suspects" className="-mx-3 flex flex-col gap-1">
            {shown.candidates.map((c, i) => (
              <Suspect key={`${view}-${c.sha}`} c={c} defaultOpen={view === "graph" && i === 0} />
            ))}
          </ol>
        )}
      </Section>

      {evidence}

      <Section id="inv-title" title="Agent investigation" aside={<span className="num text-[12px] text-ink-3">{inv.steps.length} tool call{inv.steps.length === 1 ? "" : "s"}</span>}>
        {inv.status === "not_run" && <p className="text-[13px] text-ink-3">The agent has not run for this incident yet. The ranking above is already computed.</p>}
        {inv.steps.length > 0 && (
          <ol className="relative flex flex-col gap-3 before:absolute before:bottom-2 before:left-[10px] before:top-2 before:w-px before:bg-line" aria-label="Tool calls in order">
            {inv.steps.map((s) => (
              <li key={s.n} className="relative grid grid-cols-[22px_minmax(0,1fr)] gap-3">
                <span className="num z-10 grid size-[21px] place-items-center rounded-full border border-line bg-panel text-[11px] text-ink-2">{s.n}</span>
                <div className="min-w-0">
                  <p className="flex items-baseline gap-2 text-[13px]">
                    <code className="font-mono text-[12.5px] text-ink">{s.name}</code>
                    <span className="num text-[11.5px] text-ink-4">{s.latency_ms.toFixed(0)} ms</span>
                  </p>
                  <p className="mt-0.5 text-[12.5px] text-ink-2">{stepSummary(s.name, s.summary)}</p>
                  {s.node_ids.length > 0 && (
                    <p className="mt-1.5 flex flex-wrap gap-1">
                      {s.node_ids.slice(0, 6).map((n) => (
                        <Tag key={n} tone="outline" className="font-mono">
                          {n}
                        </Tag>
                      ))}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
        {inv.status === "explanation_invalid" && (
          <Verdict tone="bad">The agent's explanation failed citation validation and was discarded. The ranking above is unaffected.</Verdict>
        )}
        {inv.explanation && (
          <div className="mt-5 rounded-lg border border-line bg-panel-2 p-4">
            <h3 className="text-[13px] font-semibold">Explanation</h3>
            <ul className="mt-2.5 flex flex-col gap-3">
              {(inv.explanation.claims as { text: string; cited_node_ids: string[] }[]).map((c, i) => (
                <li key={i}>
                  <p className="text-[13px] leading-[1.55] text-ink">{c.text}</p>
                  <p className="mt-1.5 flex flex-wrap gap-1">
                    {c.cited_node_ids.map((n) => (
                      <Tag key={n} tone="accent" className="font-mono">
                        {n}
                      </Tag>
                    ))}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        )}
      </Section>

      <FixSection
        incidentId={incidentId}
        remediation={detail.data.remediation}
        canAct={role === "approver"}
        hasSuspect={graph.verdict === "confident" || graph.verdict === "ambiguous"}
      />
    </>
  );
}
