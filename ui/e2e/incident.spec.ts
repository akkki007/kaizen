import { expect, test } from "@playwright/test";

test.describe("Incident investigation page (mock)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/login");
    await page.getByRole("button", { name: /Sign in with GitHub/i }).click();
    await page.goto("/incidents/inc-2");
  });

  test("shows ranked suspects with the score breakdown and the Cypher behind each feature", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "Suspect commits" })).toBeVisible();
    await expect(page.getByText("Confident", { exact: false }).first()).toBeVisible();
    const top = page.locator(".suspect").first();
    await expect(top).toContainText("9f2c1ab");
    await expect(top.getByRole("list", { name: /Score breakdown/ })).toBeVisible();
    await top.getByRole("button", { name: "Show the query behind path" }).click();
    await expect(top.locator(".feat-cypher")).toContainText("shortestPath");
    await expect(top.getByText("feature.path")).toBeVisible();
  });

  test("graph off: the baseline blames a different commit than the graph", async ({ page }) => {
    await page.getByRole("radio", { name: /Baseline: latest commit/ }).click();
    await expect(page.locator("section[aria-labelledby='sus-title'] .verdict")).toContainText("The baseline blames");
    await expect(page.locator("section[aria-labelledby='sus-title'] .verdict")).toContainText("the graph says");
    await page.getByRole("radio", { name: "Graph" }).click();
    await expect(page.locator("section[aria-labelledby='sus-title'] .verdict")).toContainText("Confident");
  });

  test("shows the agent's tool calls in order with the cited nodes", async ({ page }) => {
    const steps = page.getByRole("list", { name: "Tool calls in order" }).locator("> li");
    await expect(steps).toHaveCount(4);
    await expect(steps.nth(0)).toContainText("get_incident");
    await expect(steps.nth(2)).toContainText("shortest_call_path");
    await expect(page.getByRole("heading", { name: "Explanation" })).toBeVisible();
    await expect(page.getByText("Commit:9f2c1ab").first()).toBeVisible();
  });
});

test("fix section shows the staging verification result and lets an approver approve", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: /Sign in with GitHub/i }).click();
  await page.goto("/incidents/inc-2");
  await expect(page.getByText("Fix tested in staging: passed")).toBeVisible();
  await expect(page.getByText("42.0%")).toBeVisible();
  await expect(page.getByRole("link", { name: "pull request" })).toHaveAttribute("href", /pull\/14/);
  await page.getByRole("button", { name: "Approve fix" }).click();
});
