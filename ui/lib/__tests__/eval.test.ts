import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import latest from "../../../docs/results/latest.json";
import { mockEval } from "../api/mock-dashboard";
import { SPLITS, ci, graphMisses, missLine, pct, roundHalfEven, scenarioGroups, scenarioRow, splitRow, type EvalResult } from "../eval";

// The evaluation dashboard renders GET /api/v1/eval/latest (= docs/results/latest.json) through
// lib/eval.ts. Its numbers must be exactly the ones `make eval` printed in latest.md.
const ev = latest as unknown as EvalResult;
const md = readFileSync(fileURLToPath(new URL("../../../docs/results/latest.md", import.meta.url)), "utf8").replace(/\r\n/g, "\n");

/** Body rows of the markdown table that follows `## heading`. */
function mdTable(heading: string): string[][] {
  const lines = md.split("\n");
  const at = lines.indexOf(`## ${heading}`);
  expect(at, `section "${heading}" in latest.md`).toBeGreaterThanOrEqual(0);
  const rows: string[][] = [];
  for (const line of lines.slice(at + 1)) {
    if (line.startsWith("## ")) break;
    if (!line.startsWith("|")) {
      if (rows.length) break;
      continue;
    }
    rows.push(line.slice(1, -1).split("|").map((c) => c.trim()));
  }
  return rows.slice(2); // header + separator
}

describe("evaluation dashboard numbers", () => {
  it("serves latest.json unchanged in mock mode", () => {
    // ui/lib/api/fixtures/eval-latest.json is a copy (the UI image builds from ui/ alone);
    // if this fails after `make eval`, run `npm run sync:eval`
    expect(mockEval).toEqual(latest);
    expect(ev.splits.all.length).toBeGreaterThan(0);
  });

  it.each(SPLITS)("split %s matches latest.md row for row", (split) => {
    const page = ev.splits[split].map(splitRow);
    const doc = mdTable(split);
    expect(page.length).toBe(doc.length);
    // every column, median time included, is the same value (both keep 0.1 ms)
    expect(page.map((r) => r.slice(0, 7))).toEqual(doc.map((r) => r.slice(0, 7)));
    page.forEach((r, i) => expect(parseFloat(r[7])).toBe(parseFloat(doc[i][7])));
  });

  it("per-scenario top-1 matches latest.md", () => {
    expect(scenarioGroups(ev).map((g) => scenarioRow(g, ev.methods))).toEqual(mdTable("Per scenario (top-1 correct, by method)"));
  });

  it("lists exactly the graph misses latest.md lists", () => {
    const section = md.split("## Where the graph ranking is wrong")[1].split("###")[0];
    const listed = section.split("\n").filter((l) => l.startsWith("- ")).map((l) => l.slice(2));
    expect(graphMisses(ev).map(missLine)).toEqual(listed);
  });

  it("formats like Python (round half to even)", () => {
    expect(roundHalfEven(12.5)).toBe(12);
    expect(roundHalfEven(13.5)).toBe(14);
    expect(pct(0.8929)).toBe("89%");
    expect(pct(null)).toBe("n/a");
    expect(ci([0.75, 1])).toBe("75%-100%");
  });
});
