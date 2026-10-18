import { test, expect } from "@playwright/test";

test.describe("Graph map (mock)", () => {
  test("is green until a problem is injected; then the failing function and culprit show, with FalkorDB called out", async ({ page }) => {
    await page.goto("/map");
    await expect(page.getByRole("heading", { name: "Graph map" }).first()).toBeVisible();
    await expect(page.getByTestId("graph-map")).toBeVisible();
    await expect(page.getByTestId("fdb-bar")).toContainText("FalkorDB");
    await expect(page.getByTestId("fdb-bar")).toContainText(/\d+ Cypher queries in \d+ ms/);
    await expect(page.getByText("1 failing")).toHaveCount(0); // nothing failing yet

    await page.getByTestId("inject").click();
    await expect(page.getByText("1 failing")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("result")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("result")).toContainText("Root cause");
    // every graph step names its engine
    await expect(page.locator("[data-step='map']")).toContainText("FalkorDB");
    await expect(page.locator("[data-step='rank']")).toContainText("algo.pageRank");
    await expect(page.getByTestId("why-features")).toBeVisible();

    // the map is searchable and the legend/toolbar are usable
    await page.getByLabel("Find a function or commit").fill("validate");
    await expect(page.getByLabel("Find a function or commit")).toHaveValue("validate");
    await page.getByLabel("Find a function or commit").fill("");

    // clicking a node explains it under the graph
    await page.getByRole("button", { name: "Function validatePayment" }).click();
    await expect(page.getByRole("region", { name: "Function validatePayment" })).toContainText("throwing errors in production");

    await page.getByRole("button", { name: "Reset" }).click();
    await expect(page.getByText("1 failing")).toHaveCount(0);
  });
});

test("Try the demo signs in without GitHub and lands on the graph map", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: /Try the demo/i }).click();
  await expect(page).toHaveURL(/\/map\?repo=/);
  await expect(page.getByTestId("graph-map")).toBeVisible();
});
