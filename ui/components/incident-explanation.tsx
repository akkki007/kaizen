"use client";

import { Tag, type TagTone } from "@/components/app/kit";
import { Citation } from "@/components/incident-focus";

type Claim = { text: string; cited_node_ids: string[] };
type Step = { step: string; cited_node_ids: string[] };
type Alternative = { sha?: string | null; reason: string; cited_node_ids?: string[] };
export type Explanation = {
  verdict?: string;
  culprit_sha?: string | null;
  claims?: Claim[];
  causal_chain?: Step[];
  confidence_statement?: string | null;
  alternatives?: Alternative[];
  suggested_fix?: string | null;
  responders?: string[];
  source?: "model" | "template";
};

/** What each agent run status means for the reader; every status the agent can end in. */
export const AGENT_STATUS: Record<string, { label: string; tone: TagTone; copy: string }> = {
  explained: { label: "Explained", tone: "green", copy: "The model's explanation passed citation validation: every claim cites nodes or edges in the evidence graph." },
  fallback: { label: "Template fallback", tone: "amber", copy: "The model's answers failed validation twice, so Kaizen wrote this explanation from the deterministic ranking instead. It is still fully cited." },
  budget_exhausted: { label: "Budget exhausted", tone: "amber", copy: "The agent hit its token or cost budget before finishing. The ranking above is complete and does not depend on the agent." },
  explanation_invalid: { label: "Explanation rejected", tone: "red", copy: "The agent's explanation cited evidence that is not in the graph, so it was discarded. The ranking above is unaffected." },
  error: { label: "Agent error", tone: "red", copy: "The agent run failed. The ranking above is unaffected." },
  not_run: { label: "Not run", tone: "neutral", copy: "The agent has not run for this incident yet. The ranking above is already computed." },
};

function Cites({ ids }: { ids: string[] | undefined }) {
  if (!ids?.length) return null;
  return (
    <p className="mt-1.5 flex flex-wrap gap-1" aria-label="Evidence">
      {ids.map((n) => (
        <Citation key={n} id={n} />
      ))}
    </p>
  );
}

/** The agent's explanation: claims, causal chain and alternatives, each citation a graph link. */
export function IncidentExplanation({ status, explanation, errors }: { status: string; explanation: Explanation | null; errors: string[] }) {
  const meta = AGENT_STATUS[status] ?? { label: status, tone: "neutral" as TagTone, copy: "" };
  const ambiguous = explanation?.verdict === "ambiguous";
  return (
    <div className="flex flex-col gap-3" data-testid="explanation">
      <div className="flex flex-wrap items-center gap-2">
        <Tag tone={meta.tone}>{meta.label}</Tag>
        {explanation?.source && (
          <Tag tone={explanation.source === "model" ? "accent" : "amber"}>{explanation.source === "model" ? "Written by the model" : "Deterministic template"}</Tag>
        )}
      </div>
      <p className="text-[12.5px] leading-[1.55] text-ink-3">{meta.copy}</p>
      {errors.length > 0 && (
        <details className="text-[12px] text-ink-3">
          <summary className="cursor-pointer">Validation errors ({errors.length})</summary>
          <ul className="mt-1 list-disc pl-5">
            {errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </details>
      )}
      {explanation && (
        <div className="rounded-lg border border-line bg-panel-2 p-4">
          {ambiguous && (
            <div role="alert" className="mb-4 rounded-md border border-amber/40 bg-amber/10 p-3">
              <p className="text-[13.5px] font-semibold text-amber">Kaizen abstains: the evidence does not single out one change</p>
              <p className="mt-1 text-[12.5px] text-ink-2">Review the top candidates below and confirm the root cause yourself.</p>
            </div>
          )}
          <h3 className="text-[13px] font-semibold">Explanation</h3>
          <ul className="mt-2.5 flex flex-col gap-3" aria-label="Claims">
            {(explanation.claims ?? []).map((c, i) => (
              <li key={i}>
                <p className="text-[13px] leading-[1.55] text-ink">{c.text}</p>
                <Cites ids={c.cited_node_ids} />
              </li>
            ))}
          </ul>
          {!!explanation.causal_chain?.length && (
            <>
              <h3 className="mt-5 text-[13px] font-semibold">Causal chain</h3>
              <ol className="mt-2.5 flex flex-col gap-2.5" aria-label="Causal chain">
                {explanation.causal_chain.map((s, i) => (
                  <li key={i} className="grid grid-cols-[22px_minmax(0,1fr)] gap-2">
                    <span className="num grid size-[20px] place-items-center rounded-full border border-line text-[11px] text-ink-2">{i + 1}</span>
                    <div>
                      <p className="text-[13px] text-ink">{s.step}</p>
                      <Cites ids={s.cited_node_ids} />
                    </div>
                  </li>
                ))}
              </ol>
            </>
          )}
          {explanation.confidence_statement && (
            <p className="mt-4 border-l-2 border-accent pl-3 text-[12.5px] leading-[1.55] text-ink-2">{explanation.confidence_statement}</p>
          )}
          {!!explanation.alternatives?.length && (
            <>
              <h3 className="mt-5 text-[13px] font-semibold">{ambiguous ? "Other top candidates" : "Alternatives considered"}</h3>
              <ul className="mt-2 flex flex-col gap-2.5" aria-label="Alternatives">
                {explanation.alternatives.map((a, i) => (
                  <li key={i}>
                    <p className="text-[12.5px] text-ink-2">
                      {a.sha && <code className="mr-1.5 font-mono text-link">{a.sha.slice(0, 12)}</code>}
                      {a.reason}
                    </p>
                    <Cites ids={a.cited_node_ids} />
                  </li>
                ))}
              </ul>
            </>
          )}
          {(explanation.suggested_fix || explanation.responders?.length) && (
            <p className="mt-4 text-[12.5px] text-ink-3">
              {explanation.suggested_fix && <>Suggested fix: <span className="text-ink-2">{explanation.suggested_fix}</span>. </>}
              {!!explanation.responders?.length && <>Responders: {explanation.responders.join(", ")}</>}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
