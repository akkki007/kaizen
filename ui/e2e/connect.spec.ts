import { test, expect } from "@playwright/test";

// Mock mode (NEXT_PUBLIC_API_MOCK=1). `?fresh=1` starts shoplite as not yet imported.

test.describe("Connect flow", () => {
  test("choose a repo, start, watch it import, open the dashboard", async ({ page }) => {
    await page.goto("/connect?fresh=1");
    await expect(page.getByRole("heading", { name: /Which repository should Kaizen watch/i })).toBeVisible();

    const shoplite = page.getByRole("checkbox", { name: "Monitor yashchandnani07/shoplite" });
    // nothing is preselected: the user chooses; a viewer-only repo cannot be started
    await expect(shoplite).not.toBeChecked();
    await expect(page.getByRole("button", { name: "Start monitoring" })).toBeDisabled();
    await expect(page.getByRole("checkbox", { name: "Monitor acme/docs-site" })).toBeDisabled();
    await expect(page.getByText("Needs push access to start")).toBeVisible();

    await shoplite.check();
    await page.getByRole("button", { name: "Start monitoring" }).click();
    await expect(page.getByRole("heading", { name: /Reading your repository/i })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Kaizen is watching/i })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("commits read").first()).toBeVisible();

    await page.getByRole("link", { name: /Open dashboard/i }).click();
    await expect(page).toHaveURL("/overview");
  });

  test("a returning user still sees the list and chooses where to go", async ({ page }) => {
    await page.goto("/connect");
    await expect(page).toHaveURL("/connect");
    await expect(page.getByRole("heading", { name: "Your repositories" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open yashchandnani07/shoplite" })).toBeVisible();
    await page.getByRole("link", { name: "Go to dashboard" }).click();
    await expect(page).toHaveURL("/overview");
  });
});
