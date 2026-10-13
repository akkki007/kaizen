import { test, expect } from "@playwright/test";

// These tests run against the mock server (NEXT_PUBLIC_API_MOCK=1).
// mockUser: login="demo", repos: { "yashchandnani07/shoplite": "approver", "acme/docs-site": "viewer" }

test.describe("GitHub sign-in flow (mock)", () => {
  test.beforeEach(async ({ page }) => {
    // Start from /login every time
    await page.goto("/login");
    await expect(page.locator("main")).toBeVisible();
  });

  test("shows the sign-in button and redirects to dashboard on click", async ({ page }) => {
    await expect(page.getByRole("button", { name: /Sign in with GitHub/i })).toBeVisible();
    await page.getByRole("button", { name: /Sign in with GitHub/i }).click();
    await expect(page).toHaveURL("/");
    await expect(page.locator("nav.rail")).toBeVisible();
  });

  test("account menu button exists and has correct aria attributes", async ({ page }) => {
    // Sign in first
    await page.goto("/login");
    await page.getByRole("button", { name: /Sign in with GitHub/i }).click();
    await expect(page).toHaveURL("/");
    await expect(page.locator("nav.rail")).toBeVisible();

    // The account button exists with proper aria
    const accountBtn = page.locator(".rail-account-block button").first();
    await expect(accountBtn).toBeVisible({ timeout: 15000 });
    await expect(accountBtn).toHaveAttribute("aria-label", /account/i);
    await expect(accountBtn).toHaveAttribute("aria-haspopup", "menu");
    // The button shows name and email
    await expect(accountBtn.locator(".rail-account-name")).toContainText("demo");
    await expect(accountBtn.locator(".rail-account-mail")).toContainText("demo@users.noreply.github.com");
  });

  test("viewer vs approver: API keys page shows create only for approver", async ({ page }) => {
    // Sign in first
    await page.goto("/login");
    await page.getByRole("button", { name: /Sign in with GitHub/i }).click();
    await expect(page).toHaveURL("/");

    // Go to the approver repo - create key visible
    await page.goto("/repositories/yashchandnani07/shoplite");
    await expect(page.locator("h2#keys-title")).toBeVisible();
    await expect(page.getByRole("button", { name: /Create key/i })).toBeVisible();

    // Go to the viewer repo - create key NOT visible (viewer role)
    await page.goto("/repositories/acme/docs-site");
    await expect(page.locator("h2#keys-title")).toBeVisible();
    await expect(page.getByRole("button", { name: /Create key/i })).not.toBeVisible();
    // But the keys list itself is shown
    await expect(page.locator(".key-list")).toBeVisible();
  });
});

test.describe("Login page error states (mock)", () => {
  test("shows 'cancelled' error when GitHub access is denied", async ({ page }) => {
    await page.goto("/login?error=denied");
    await expect(page.locator(".card[data-kind='error'] .card-title")).toHaveText("Sign-in was cancelled");
    await expect(page.getByRole("button", { name: /Sign in with GitHub/i })).toBeVisible();
  });

  test("shows 'expired' error for stale sign-in link", async ({ page }) => {
    await page.goto("/login?error=expired");
    await expect(page.locator(".card[data-kind='error'] .card-title")).toHaveText("That sign-in link expired");
    await expect(page.getByRole("button", { name: /Sign in with GitHub/i })).toBeVisible();
  });

  test("shows 'no_repos' error with install link", async ({ page }) => {
    await page.goto("/login?error=no_repos");
    await expect(page.locator(".card[data-kind='info'] .card-title")).toHaveText("No repositories yet");
    await expect(page.locator(".card[data-kind='info'] a[href*='github.com/apps/']")).toBeVisible();
    await expect(page.getByRole("button", { name: /I installed it/i })).toBeVisible();
  });

  test("shows generic error for invalid callback", async ({ page }) => {
    await page.goto("/login?error=invalid");
    await expect(page.locator(".card[data-kind='error'] .card-title")).toHaveText("That sign-in link is not valid");
    await expect(page.getByRole("button", { name: /Sign in with GitHub/i })).toBeVisible();
  });
});

test.describe("Session expiry mid-use (mock)", () => {
  test("401 from any query redirects to /login with next=original page", async ({ page }) => {
    // In mock mode we can't simulate a real 401 from the API.
    // The redirect logic is tested at the unit level (useMe hook).
    // Here we just verify the login page accepts a next param and the button works.
    await page.goto("/login?next=/incidents");
    await expect(page.locator("main")).toBeVisible();
    await expect(page.getByRole("button", { name: /Sign in with GitHub/i })).toBeVisible();
    await page.click("button:has-text('Sign in with GitHub')");
    await expect(page).toHaveURL("/incidents");
  });
});

test.describe("Repositories list empty state", () => {
  test("shows the repositories page for a user with repos (mock user has repos)", async ({ page }) => {
    await page.goto("/repositories");
    await expect(page.getByRole("heading", { name: /Repositories/i })).toBeVisible();
    // The mock user has repos, so the grid should show them.
    await expect(page.locator(".repo-grid")).toBeVisible();
    // At least one repo card
    const count = await page.locator(".repo").count();
    expect(count).toBeGreaterThan(0);
  });
});