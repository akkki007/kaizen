import { describe, expect, it } from "vitest";
import { answerFromDetail } from "../incident-answer";

const base = {
  incident: {} as never,
  remediation: null,
  investigation: { explanation: null, id: 1, status: "explained", steps: [{ n: 1, name: "rank_candidates", summary: "ranked 2", args: {}, latency_ms: 1, node_ids: [] }], validation_errors: [] },
  ranking: {
    incident_id: "i", method: "graph" as const, verdict: "confident" as const, margin: 0.4,
    candidates: [{ sha: "c0ffee01aaaa", message: "Refactor validation", confidence: 0.9, evidence: [{ changed_function: "a:validate", thrown_function: "a:validate", error: "fp" }], features: [], author: null, deployment: null, rank: 1, score: 1 }],
  },
};

describe("answerFromDetail", () => {
  it("names the culprit from the stored ranking, not a canned sentence", () => {
    const a = answerFromDetail("what is main fault", base as never);
    expect(a).toContain("c0ffee0 ");
    expect(a).toContain("`validate`"); // function ids are shortened to their names
    expect(a).not.toContain("a:validate");
    expect(a).toContain("Refactor validation");
    expect(a).not.toContain("is mapping this incident");
  });
  it("lists the agent trace for step by step", () => {
    const t = answerFromDetail("step by step explain", base as never);
    expect(t).toContain("1. rank_candidates: ranked 2");
    expect(t).not.toContain("rank_candidates: rank_candidates");
  });
  it("abstains when no candidate has code evidence", () => {
    const d = { ...base, ranking: { ...base.ranking, verdict: "no_code_evidence" as const } };
    expect(answerFromDetail("why", d as never)).toContain("abstains");
  });
});
