/**
 * The incident pipeline as six stages, derived only from what the API reports: the incident
 * status, its status history, the agent run status and the latest remediation.
 */

export const STAGES = ["detect", "investigate", "explain", "fix", "verify", "remember"] as const;
export type Stage = (typeof STAGES)[number];
export type StageState = "done" | "active" | "pending" | "failed" | "skipped";

export type StageView = { id: Stage; label: string; state: StageState; note: string };

export type PipelineInput = {
  status: string;
  investigationStatus: string; // not_run | explained | fallback | explanation_invalid | budget_exhausted | error
  remediationStatus: string | null; // proposing|verifying|verified|failed|needs_manual|approved|merged|rejected
  history: string[]; // to_status of every transition, oldest first
};

const LABEL: Record<Stage, string> = {
  detect: "Detect",
  investigate: "Investigate",
  explain: "Explain",
  fix: "Fix",
  verify: "Verify",
  remember: "Remember",
};

const RANKED = new Set(["identified", "explained", "remediating", "verifying", "resolved"]);
const FIX_DONE = new Set(["verifying", "verified", "approved", "merged", "failed"]);
const VERIFY_DONE = new Set(["verified", "approved", "merged"]);

export function pipeline(p: PipelineInput): StageView[] {
  const reached = (s: string) => p.status === s || p.history.includes(s);
  const ranked = [...RANKED].some(reached) || p.investigationStatus !== "not_run";
  const rem = p.remediationStatus;

  const investigate: [StageState, string] = ranked
    ? ["done", "suspects ranked"]
    : p.status === "investigating"
      ? ["active", "ranking suspects"]
      : reached("investigating")
        ? ["failed", "ranking abstained; back to open"]
        : ["pending", ""];

  const inv = p.investigationStatus;
  const explain: [StageState, string] =
    inv === "explained"
      ? ["done", "model explanation, citations validated"]
      : inv === "fallback"
        ? ["done", "template explanation (model output rejected)"]
        : inv === "explanation_invalid"
          ? ["failed", "explanation failed citation checks"]
          : inv === "budget_exhausted"
            ? ["failed", "LLM budget exhausted"]
            : inv === "error"
              ? ["failed", "agent error"]
              : p.status === "investigating" || (ranked && !rem)
                ? ["active", "agent running"]
                : ["pending", ""];

  const fix: [StageState, string] = !rem
    ? p.status === "remediating"
      ? ["active", "preparing the fix"]
      : ["pending", ""]
    : rem === "proposing"
      ? ["active", "opening the pull request"]
      : rem === "needs_manual"
        ? ["failed", "needs a manual fix"]
        : rem === "rejected"
          ? ["failed", "fix rejected"]
          : FIX_DONE.has(rem)
            ? ["done", "pull request open"]
            : ["pending", ""];

  const verify: [StageState, string] =
    rem === "verifying"
      ? ["active", "testing in staging"]
      : rem === "failed"
        ? ["failed", "failed in staging"]
        : rem && VERIFY_DONE.has(rem)
          ? ["done", rem === "verified" ? "passed; awaiting approval" : rem === "approved" ? "approved; awaiting merge" : "merged"]
          : ["pending", ""];

  const remember: [StageState, string] =
    p.status === "resolved"
      ? ["done", "stored for similar incidents"]
      : p.status === "closed"
        ? ["skipped", "closed without a fix"]
        : ["pending", ""];

  const states: Record<Stage, [StageState, string]> = {
    detect: ["done", "incident opened"],
    investigate,
    explain,
    fix,
    verify,
    remember,
  };
  return STAGES.map((id) => ({ id, label: LABEL[id], state: states[id][0], note: states[id][1] }));
}

/** The stage to mark aria-current: the first active one, else the last done one. */
export function currentStage(stages: StageView[]): Stage {
  const active = stages.find((s) => s.state === "active" || s.state === "failed");
  if (active) return active.id;
  const done = stages.filter((s) => s.state === "done");
  return (done[done.length - 1] ?? stages[0]).id;
}
