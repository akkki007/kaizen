import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

// Mock mode (NEXT_PUBLIC_API_MOCK=1): fixtures in lib/api/mock-dashboard.ts.

test.beforeEach(async ({ page }) => {
  await signIn(page);
});

test("overview shows live service health and the deployment timeline with incident markers", async ({ page }) => {
  const health = page.getByRole("list", { name: "Service health" });
  await expect(health).toBeVisible();
  const payments = health.locator("li", { hasText: "payments" }).first();
  await expect(payments).toHaveAttribute("data-status", "incident");
  await expect(payments).toContainText("38%");
  await expect(health.locator("li[data-status=ok]").first()).toContainText("Healthy");

  const timeline = page.getByRole("group", { name: /deployments and \d+ incidents in the last 72 hours/ });
  await expect(timeline).toBeVisible();
  const deploys = page.getByRole("list", { name: "Deployments" });
  const d41 = deploys.locator("li", { hasText: "d-41" });
  await expect(d41).toContainText("inc-2");
  await expect(deploys.locator("li", { hasText: "d-44" })).toContainText("inc-3");
  // the incident row names the suspected cause
  await expect(page.getByRole("link", { name: /inc-2/ }).first()).toBeVisible();
  await expect(page.getByText("Round amounts in toCents").first()).toBeVisible();
});

test("a newly seeded incident appears within seconds, without a reload", async ({ page }) => {
  await expect(page.getByText("inc-seeded")).toHaveCount(0);
  await page.evaluate(() => window.localStorage.setItem("kaizen.mock.seed", String(Date.now())));
  // polling every 5 s: visible on the next refetch
  await expect(page.getByText("inc-seeded").first()).toBeVisible({ timeout: 8_000 });
});

test("incident list: filters, archive and navigation to the detail page", async ({ page }) => {
  await page.goto("/incidents");
  const list = page.getByRole("list", { name: "Incidents" });
  await expect(list.locator("> li")).toHaveCount(7);
  await expect(list.locator("li[data-id='inc-5'] svg[aria-label='Needs a human']")).toBeVisible();

  await page.getByRole("radio", { name: /archive/i }).click();
  await expect(page).toHaveURL(/status=archive/);
  await expect(list.locator("> li")).toHaveCount(2);
  await expect(list).toContainText("inc-1"); // the prior, resolved payments incident
  await expect(list).toContainText("inc-0");

  await page.getByRole("radio", { name: /^all/i }).click();
  await page.getByRole("button", { name: "sev1", exact: true }).click();
  await expect(list.locator("> li")).toHaveCount(2);

  await page.getByRole("searchbox", { name: "Search incidents" }).fill("orders");
  await expect(page).toHaveURL(/q=orders/);
  await expect(page.getByText(/No incidents match these filters/)).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(list.locator("> li")).toHaveCount(7);

  await page.getByRole("combobox", { name: "Status" }).selectOption("verifying");
  await expect(list.locator("> li")).toHaveCount(1);
  await expect(list).toContainText("money-utils");

  await page.getByRole("button", { name: /^inc-8/ }).dblclick();
  await expect(page).toHaveURL("/incidents/inc-8");
});

test("hotspots lists blamed functions and dependencies linking to incidents", async ({ page }) => {
  await page.goto("/hotspots");
  await expect(page.getByRole("heading", { name: "Hotspots", level: 1 })).toBeVisible();
  const row = page.getByRole("row", { name: /toCents/ });
  await expect(row).toContainText("1 confirmed");
  await row.getByRole("link", { name: "inc-1" }).click();
  await expect(page).toHaveURL("/incidents/inc-1");
});

test("evaluation page renders latest.json: split table, chart, drilldown and misses", async ({ page }) => {
  await page.goto("/evaluation");
  const table = page.getByRole("table", { name: "Results, all split" });
  await expect(table).toBeVisible();
  const graph = table.locator("tr[data-method='graph']");
  await expect(graph).toContainText("89%");
  await expect(graph).toContainText("75%-100%");
  await expect(table.locator("tr[data-method='b0 latest']")).toContainText("32%");
  await expect(page.getByRole("img", { name: /Top-1 accuracy by method, all split/ })).toBeVisible();

  await page.getByRole("radio", { name: "test" }).click();
  await expect(page.getByRole("table", { name: "Results, test split" }).locator("tr[data-method='graph']")).toContainText("83%");

  await page.getByRole("button", { name: "S4 variants" }).click();
  await expect(page.getByText("v2 culprit c7a6354d58d9")).toBeVisible();
  await expect(page.getByRole("list", { name: "Graph misses" }).locator("> li")).toHaveCount(3);
});
