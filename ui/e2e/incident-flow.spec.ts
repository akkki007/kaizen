import { expect, test, type Page } from "@playwright/test";

// Incident page (#93) in mock mode: citations drive the graph, approve/reject dialogs,
// the live pipeline tracker, the S8 similar-incident panel and keyboard graph navigation.

async function open(page: Page, id: string) {
  await page.goto("/login");
  await page.getByRole("button", { name: /Sign in with GitHub/i }).click();
  await page.goto(`/incidents/${id}`);
  await expect(page.getByTestId("explanation")).toBeVisible();
}

test("clicking a citation focuses its node in the graph and opens the inspector", async ({ page }) => {
  await open(page, "inc-2");
  const graph = page.locator("#evidence-graph");
  await expect(graph).toHaveAttribute("data-node-count", /\d+/);
  await page.getByRole("list", { name: "Claims" }).locator('[data-citation="Commit:9f2c1ab"]').first().click();
  await expect(graph).toHaveAttribute("data-selected", "Commit:9f2c1ab");
  const inspector = page.getByTestId("inspector");
  await expect(inspector).toHaveAttribute("data-inspected", "Commit:9f2c1ab");
  await expect(inspector).toContainText("Round amounts in toCents");
  await expect(inspector.getByRole("list", { name: "Changed functions" })).toContainText("toCents");

  // an edge citation selects the edge
  const edge = page.getByRole("list", { name: "Causal chain" }).locator("[data-citation*='|includes|']").first();
  await edge.click();
  await expect(graph).toHaveAttribute("data-selected", /\|includes\|/);
  await expect(page.getByTestId("inspector")).toContainText("includes");
});

test("approving a verified fix asks for confirmation and ends in the approved state", async ({ page }) => {
  await open(page, "inc-2");
  await expect(page.getByText("Fix tested in staging: passed")).toBeVisible();
  await page.getByRole("button", { name: "Approve fix" }).click();
  const dialog = page.getByRole("dialog", { name: "Approve this fix?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Approve fix" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("approved-by")).toContainText("Approved by demo");
  await expect(page.getByTestId("merge-outcome")).toContainText("Kaizen merges approved fixes");
  // the app merge policy merges it and the incident resolves without a reload
  await expect(page.locator('[data-stage="remember"]')).toHaveAttribute("data-state", "done", { timeout: 20_000 });
  await expect(page.getByText("Merged: incident resolved")).toBeVisible();
});

test("rejecting a fix requires a reason", async ({ page }) => {
  await open(page, "inc-8");
  await page.getByRole("button", { name: "Reject fix" }).click();
  const dialog = page.getByRole("dialog", { name: "Reject this fix?" });
  await dialog.getByRole("button", { name: "Reject fix" }).click();
  await expect(dialog.getByRole("alert")).toContainText("A reason is required");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel(/Why is this fix wrong/).fill("Reverting breaks the checkout API contract");
  await dialog.getByRole("button", { name: "Reject fix" }).click();
  await expect(page.getByTestId("rejected-by")).toContainText("Reverting breaks the checkout API contract");
});

test("pipeline tracker shows all six stages and advances live without a reload", async ({ page }) => {
  await open(page, "inc-2");
  const stages = page.getByRole("list", { name: "Incident pipeline" }).locator("> li");
  await expect(stages).toHaveCount(6);
  await expect(stages.nth(0)).toContainText("Detect");
  await expect(page.locator('[data-stage="verify"]')).toHaveAttribute("data-state", "done");
  await expect(page.locator('[data-stage="remember"]')).toHaveAttribute("data-state", "pending");

  await page.goto("/incidents/inc-3");
  await expect(page.locator('[data-stage="investigate"]')).toHaveAttribute("data-state", /active|done/);
  await expect(page.locator('[data-stage="explain"]')).toHaveAttribute("data-state", "done", { timeout: 20_000 });
  await expect(page.locator('[data-stage="verify"]')).toHaveAttribute("data-state", /active|done/, { timeout: 30_000 });
});

test("S8 shows the resolved S1 incident and the fix it reuses", async ({ page }) => {
  await open(page, "inc-8");
  const similar = page.getByRole("list", { name: "Similar incidents" });
  await expect(similar.locator("li").first()).toContainText("inc-1");
  await expect(similar.locator("li").first()).toContainText("Fix that worked");
  await expect(page.getByText("Prior fixes for similar incidents")).toBeVisible();
  await expect(page.getByText("Deterministic template")).toBeVisible();
});

test("graph keyboard navigation: arrows move, Enter inspects, Escape clears", async ({ page }) => {
  await open(page, "inc-2");
  const graph = page.locator("#evidence-graph");
  await graph.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByTestId("graph-live")).toContainText("Incident");
  await page.keyboard.press("Enter");
  await expect(graph).toHaveAttribute("data-selected", "Incident:inc-2");
  await page.keyboard.press("Escape");
  await expect(graph).toHaveAttribute("data-selected", "");
});
