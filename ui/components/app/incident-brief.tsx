"use client";

import { ArrowUp, GitCommitHorizontal } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { fetchIncidentDetail, useIncidentDetail } from "@/lib/api/hooks";
import { answerFromDetail } from "@/lib/incident-answer";
import { EASE, gsap, haptic } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { serviceColor, stepSummary, when, type Incident } from "@/lib/workspace";
import { ServiceDot, Skeleton, Tag } from "./kit";

/** Label/value grid for an incident, like an issue's metadata block. */
export function IncidentFacts({ incident }: { incident: Incident }) {
  const rows: [string, React.ReactNode][] = [
    [
      "Service",
      <span key="s" className="inline-flex items-center gap-1.5">
        <ServiceDot color={serviceColor(incident.service)} />
        {incident.service}
      </span>,
    ],
    ["Repository", incident.repo],
    ...(incident.simulated ? ([["Source", <Tag key="sim" tone="amber">Simulated demo traffic</Tag>]] as typeof rows) : []),
    ["Environment", <Tag key="e" tone={incident.env === "production" ? "red" : "neutral"}>{incident.env}</Tag>],
    ["Endpoint", <code key="p" className="font-mono text-[12px]">{incident.endpoint}</code>],
    ["Onset", when(incident.onset_ts)],
    ["Detected", when(incident.detected_at)],
  ];
  return (
    <dl className="grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-2.5 text-[12.5px]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-3">{k}</dt>
          <dd className="min-w-0 truncate text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

const VERDICT: Record<string, { label: string; tone: "green" | "amber" | "neutral" }> = {
  confident: { label: "Confident", tone: "green" },
  ambiguous: { label: "Ambiguous", tone: "amber" },
  no_code_evidence: { label: "No code evidence", tone: "neutral" },
  no_candidates: { label: "No candidates", tone: "neutral" },
};

const FIX: Record<string, string> = {
  proposing: "Preparing the revert",
  verifying: "Verifying in staging",
  verified: "Verified in staging, waiting for approval",
  failed: "Failed verification",
  needs_manual: "Needs a manual fix",
  approved: "Approved, waiting for merge",
  merged: "Merged",
};

/** What Kaizen concluded: the top suspect, the agent's first steps and the fix state. */
export function CauseSummary({ incidentId }: { incidentId: string }) {
  const detail = useIncidentDetail(incidentId);
  if (detail.isLoading) {
    return (
      <div className="flex flex-col gap-2.5" aria-label="Loading investigation">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-3 w-2/3" />
        <Skeleton className="h-3 w-1/2" />
      </div>
    );
  }
  if (detail.error || !detail.data) return <p className="text-[12.5px] text-ink-3">The investigation could not be loaded.</p>;

  const { ranking, investigation: inv, remediation } = detail.data;
  const top = ranking.candidates[0];
  const verdict = VERDICT[ranking.verdict] ?? { label: ranking.verdict, tone: "neutral" as const };
  const pair = top?.evidence[0];

  return (
    <div className="flex flex-col gap-5">
      <section>
        <div className="flex items-center gap-2">
          <h3 className="text-[13px] font-semibold">Suspected cause</h3>
          <Tag tone={verdict.tone}>{verdict.label}</Tag>
        </div>
        {top && (ranking.verdict === "confident" || ranking.verdict === "ambiguous") ? (
          <div className="mt-2.5 rounded-lg border border-line bg-panel-2 p-3">
            <p className="flex items-center gap-2 text-[13px]">
              <GitCommitHorizontal size={14} className="shrink-0 text-ink-3" />
              <code className="font-mono text-[12px] text-link">{top.sha.slice(0, 7)}</code>
              <span className="truncate text-ink">{top.message}</span>
            </p>
            <div className="mt-2.5 flex items-center gap-2">
              <span className="h-1 flex-1 overflow-hidden rounded-full bg-ink/[0.08]">
                <span className="block h-full rounded-full bg-green" style={{ width: `${Math.round(top.confidence * 100)}%` }} />
              </span>
              <span className="num text-[12px] text-ink-2">{Math.round(top.confidence * 100)}%</span>
            </div>
            {pair && (
              <p className="mt-2 text-[12px] text-ink-3">
                Changed <code className="font-mono text-ink-2">{pair.changed_function.split(":").pop()}</code>, thrown in{" "}
                <code className="font-mono text-ink-2">{pair.thrown_function.split(":").pop()}</code>
              </p>
            )}
          </div>
        ) : (
          <p className="mt-2 text-[12.5px] leading-[1.55] text-ink-2">
            {ranking.verdict === "no_code_evidence"
              ? "None of the candidate commits touches code on the failing path, so Kaizen abstains rather than guess."
              : "No deployment or commit is linked to this incident yet."}
          </p>
        )}
      </section>

      {inv.steps.length > 0 && (
        <section>
          <h3 className="text-[13px] font-semibold">
            What the agent checked <span className="num font-normal text-ink-3">{inv.steps.length}</span>
          </h3>
          <ol className="mt-2 flex flex-col gap-1.5">
            {inv.steps.slice(0, 4).map((s) => (
              <li key={s.n} className="grid grid-cols-[18px_minmax(0,1fr)] gap-2 text-[12.5px]">
                <span className="num text-ink-4">{s.n}</span>
                <span className="truncate text-ink-2">
                  <code className="font-mono text-[11.5px] text-ink">{s.name}</code> {stepSummary(s.name, s.summary)}
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}

      <section>
        <h3 className="text-[13px] font-semibold">Fix</h3>
        <p className="mt-1.5 text-[12.5px] text-ink-2">
          {remediation ? (FIX[remediation.status] ?? remediation.status) : "No fix proposed yet."}
          {remediation?.pr_url && (
            <>
              {" · "}
              <a href={remediation.pr_url} target="_blank" rel="noreferrer" className="text-link hover:underline">
                pull request
              </a>
            </>
          )}
        </p>
      </section>
    </div>
  );
}

type Message = { id: number; q: string; a: string | null };

const SUGGESTIONS = ["Why this commit?", "How did Kaizen trace it?", "Is there a fix?"];

/** Ask about an incident; answers come from the stored investigation (lib/incident-answer.ts). */
export function AskIncident({ incidentId }: { incidentId: string }) {
  const qc = useQueryClient();
  const [threads, setThreads] = useState<Record<string, Message[]>>({});
  const [value, setValue] = useState("");
  const nextId = useRef(1);
  const last = useRef<HTMLDivElement>(null);
  const messages = threads[incidentId] ?? [];
  const latest = messages.at(-1);

  useEffect(() => {
    if (latest?.a && last.current) gsap.fromTo(last.current, { autoAlpha: 0, y: 6 }, { autoAlpha: 1, y: 0, duration: 0.35, ease: EASE });
  }, [latest?.a]);

  async function ask(question: string) {
    const id = nextId.current++;
    const target = incidentId;
    haptic("tap");
    setValue("");
    setThreads((t) => ({ ...t, [target]: [...(t[target] ?? []), { id, q: question, a: null }] }));
    let a: string;
    try {
      a = answerFromDetail(question, await fetchIncidentDetail(qc, target));
      haptic("confirm");
    } catch {
      a = "Something went wrong while answering. Try again.";
      haptic("warn");
    }
    setThreads((t) => ({ ...t, [target]: (t[target] ?? []).map((m) => (m.id === id ? { ...m, a } : m)) }));
  }

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (value.trim()) void ask(value.trim());
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="flex flex-col gap-2.5">
      {latest && (
        <div ref={last} className="rounded-lg border border-line bg-panel-2 p-3" aria-live="polite">
          <p className="text-[12px] text-ink-3">
            You asked <span className="text-ink-2">“{latest.q}”</span>
          </p>
          {latest.a === null ? (
            <p className="mt-1.5 text-[13px] shimmer-text">Kaizen is reading the investigation…</p>
          ) : (
            <p className="mt-1.5 max-h-44 overflow-y-auto whitespace-pre-line text-[13px] leading-[1.55] text-ink scrollbar-thin">
              {latest.a.split("`").map((part, i) =>
                i % 2 ? (
                  <code key={i} className="rounded bg-ink/[0.07] px-1 font-mono text-[12px]">
                    {part}
                  </code>
                ) : (
                  part
                ),
              )}
            </p>
          )}
        </div>
      )}
      {!latest && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Suggested questions">
          {SUGGESTIONS.map((s) => (
            <li key={s}>
              <button
                type="button"
                onClick={() => void ask(s)}
                className="h-6 rounded-full border border-line px-2.5 text-[12px] text-ink-2 transition-[color,background-color,transform] hover:bg-hover hover:text-ink active:scale-[0.97]"
              >
                {s}
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={submit} className="flex items-end gap-2 rounded-lg border border-line bg-panel-2 py-1.5 pl-3 pr-1.5 transition-colors focus-within:border-ink-4">
        <label htmlFor={`ask-${incidentId}`} className="sr-only">
          Ask about {incidentId}
        </label>
        <textarea
          id={`ask-${incidentId}`}
          rows={1}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={`Ask about ${incidentId}…`}
          className="max-h-28 min-h-[22px] flex-1 resize-none bg-transparent py-0.5 text-[13px] leading-[20px] text-ink outline-none placeholder:text-ink-4 [field-sizing:content]"
        />
        <button
          type="submit"
          aria-label="Send"
          disabled={!value.trim()}
          className={cn(
            "grid size-6 shrink-0 place-items-center rounded-md transition-all",
            value.trim() ? "bg-primary text-on-primary" : "text-ink-4",
          )}
        >
          <ArrowUp size={14} strokeWidth={2.2} />
        </button>
      </form>
    </div>
  );
}
