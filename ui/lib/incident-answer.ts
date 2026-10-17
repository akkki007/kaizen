import type { components } from "@/lib/api/schema";

type Detail = components["schemas"]["IncidentDetail"];
type Claim = { text: string; cited_node_ids: string[] };

const pct = (x: number) => `${Math.round(x * 100)}%`;
const short = (sha: string) => sha.slice(0, 7);
const fnName = (id: string) => id.split(":").pop() ?? id; // "svc:path/file.js:toCents" -> "toCents"
const stepLine = (name: string, summary: string) => summary.replace(new RegExp(`^${name}:\\s*`), "");

/** Answers an incident question from the stored investigation. Never invents a cause. */
export function answerFromDetail(question: string, d: Detail): string {
  const { ranking, investigation: inv, remediation } = d;
  const top = ranking.candidates[0];
  const claims = ((inv.explanation as { claims?: Claim[] } | null)?.claims ?? []) as Claim[];
  const q = question.toLowerCase();

  if (!top || ranking.verdict === "no_candidates") {
    return "No deployment or commit is linked to this incident yet, so there is no suspect to name.";
  }
  if (ranking.verdict === "no_code_evidence") {
    return `Kaizen found ${ranking.candidates.length} candidate commit(s) but none touches code on the failing path, so it abstains rather than guess.`;
  }
  const lead = `${ranking.verdict === "confident" ? "Most likely cause" : "Leading suspect (ambiguous)"}: commit ${short(top.sha)} "${top.message}" (${pct(top.confidence)} confidence).`;
  const pair = top.evidence[0];
  const where = pair ? ` It changed \`${fnName(pair.changed_function)}\` and the error is thrown in \`${fnName(pair.thrown_function)}\`.` : "";

  if (/step|how|trace|explain/.test(q)) {
    if (!inv.steps.length) return `${lead}${where} No agent trace was recorded for this incident (ranking only).`;
    const steps = inv.steps.map((s) => `${s.n}. ${s.name}: ${stepLine(s.name, s.summary)}`).join("\n");
    return `${lead}${where}\nWhat the agent did:\n${steps}`;
  }
  if (/fix|revert|patch|resolve/.test(q)) {
    return remediation
      ? `Fix status: ${remediation.status}${remediation.pr_url ? ` (${remediation.pr_url})` : ""}.`
      : `${lead} No fix has been proposed yet; open the incident to propose a revert.`;
  }
  const claimText = claims.length ? ` ${claims.map((c) => c.text).join(" ")}` : "";
  return `${lead}${where}${claimText}`;
}
