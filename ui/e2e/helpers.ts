import { expect, type Page } from "@playwright/test";

/** Mock-mode sign-in: the mocked GitHub flow lands on /overview. */
export async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: /Sign in with GitHub/i }).click();
  await expect(page).toHaveURL("/overview");
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
}
