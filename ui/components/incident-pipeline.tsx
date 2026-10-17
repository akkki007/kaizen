"use client";

import { Check, Circle, CircleDot, Minus, X } from "lucide-react";
import type { LiveMode } from "@/lib/api/hooks-incident";
import { currentStage, type StageView } from "@/lib/pipeline";
import { cn } from "@/lib/utils";

const ICON = { done: Check, active: CircleDot, pending: Circle, failed: X, skipped: Minus } as const;
const TONE = { done: "text-green border-green/40 bg-green/10", active: "text-amber border-amber/50 bg-amber/10", pending: "text-ink-4 border-line", failed: "text-red border-red/40 bg-red/10", skipped: "text-ink-3 border-line" } as const;
const STATE_TEXT = { done: "done", active: "in progress", pending: "not started", failed: "failed", skipped: "skipped" } as const;

/** detect → investigate → explain → fix → verify → remember, with the live-update mode. */
export function PipelineTracker({ stages, mode }: { stages: StageView[]; mode: LiveMode }) {
  const current = currentStage(stages);
  return (
    <div className="flex flex-col gap-2">
      <ol aria-label="Incident pipeline" className="grid grid-cols-3 gap-2 sm:grid-cols-6">
        {stages.map((s, i) => {
          const Icon = ICON[s.state];
          return (
            <li key={s.id} data-stage={s.id} data-state={s.state} aria-current={s.id === current ? "step" : undefined} className="relative flex min-w-0 flex-col gap-1">
              <div className="flex items-center gap-1.5">
                <span className={cn("grid size-6 shrink-0 place-items-center rounded-full border", TONE[s.state], s.state === "active" && "animate-pulse")}>
                  <Icon size={13} strokeWidth={2.2} aria-hidden="true" />
                </span>
                <span className={cn("text-[12.5px] font-medium", s.state === "pending" ? "text-ink-3" : "text-ink")}>
                  {s.label}
                  <span className="sr-only">: {STATE_TEXT[s.state]}</span>
                </span>
                {i < stages.length - 1 && <i aria-hidden="true" className={cn("ml-1 hidden h-px flex-1 sm:block", s.state === "done" ? "bg-green/50" : "bg-line")} />}
              </div>
              {s.note && <span className="pl-[30px] text-[11.5px] leading-[1.35] text-ink-3">{s.note}</span>}
            </li>
          );
        })}
      </ol>
      <p className="text-[11.5px] text-ink-4" data-testid="live-mode">
        {mode === "live" ? "Live: updates stream from the server" : mode === "connecting" ? "Connecting to live updates…" : "Auto-refreshing every few seconds"}
      </p>
    </div>
  );
}
