import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

// axe-core scan (WCAG 2.x A/AA). Serious and critical violations fail on the pages this suite
// owns; the incident page is owned elsewhere, so its violations are reported as annotations.
const PAGES = [
  { path: "/overview", ready: "Service health", enforce: true },
  { path: "/incidents", ready: "Search incidents", enforce: true },
  { path: "/evaluation", ready: "Results by split", enforce: true },
  { path: "/hotspots", ready: "Functions", enforce: true },
  { path: "/incidents/inc-2", ready: null, enforce: false },
] as const;

for (const p of PAGES) {
  test(`a11y: ${p.path}`, async ({ page }, info) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await signIn(page);
    await page.goto(p.path);
    if (p.ready) await expect(page.getByRole("heading", { name: p.ready }).or(page.getByRole("searchbox", { name: p.ready })).first()).toBeVisible();
    else await expect(page.getByRole("main")).toBeVisible();
    await page.waitForLoadState("networkidle");
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    const summary = bad.map((v) => `${v.impact} ${v.id}: ${v.help} (${v.nodes.length}) e.g. ${v.nodes[0]?.target.join(" ")}`);
    for (const line of summary) info.annotations.push({ type: "a11y", description: `${p.path}: ${line}` });
    if (p.enforce) expect(summary, `serious/critical axe violations on ${p.path}`).toEqual([]);
  });
}
