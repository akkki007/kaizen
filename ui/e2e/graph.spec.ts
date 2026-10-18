import { test, expect } from "@playwright/test";

test.describe("Live graph (mock)", () => {
  test("is green until a problem is injected; then the failing function and culprit show, with FalkorDB called out", async ({ page }) => {
    await page.goto("/graph");
    await expect(page.getByRole("heading", { name: "Live graph" }).first()).toBeVisible();
    await expect(page.getByTestId("code-graph")).toBeVisible();
    await expect(page.getByText(/FalkorDB · \d+ functions/)).toBeVisible();
    await expect(page.getByText("1 failing")).toHaveCount(0); // nothing failing yet

    await page.getByTestId("inject").click();
    await expect(page.getByText("1 failing")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("result")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("result")).toContainText("Root cause");
    // every graph step names its engine
    await expect(page.locator("[data-step='map']")).toContainText("FalkorDB");
    await expect(page.locator("[data-step='rank']")).toContainText("algo.pageRank");

    // the map is searchable and the legend/toolbar are usable
    await page.getByLabel("Find a function or commit").fill("validate");
    await expect(page.getByLabel("Find a function or commit")).toHaveValue("validate");
    await page.getByLabel("Find a function or commit").fill("");
    await page.getByRole("button", { name: "Fit" }).click();

    await page.getByRole("button", { name: "Reset" }).click();
    await expect(page.getByText("1 failing")).toHaveCount(0);
  });
});

test("Try the demo signs in without GitHub and lands on the live graph", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: /Try the demo/i }).click();
  await expect(page).toHaveURL(/\/graph\?repo=/);
  await expect(page.getByTestId("code-graph")).toBeVisible();
});
