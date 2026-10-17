import { expect, test } from "@playwright/test";
import { signIn } from "./helpers";

// Visual regression of the incident page in mock mode. Opt-in (VISUAL=1): font rendering differs
// per OS, so baselines are per platform. Linux baselines (what CI would use) are generated in the
// official Playwright image; see ui/README.md.
test.skip(!process.env.VISUAL, "visual regression is opt-in: VISUAL=1");

test("incident page looks the same", async ({ page }) => {
  // frozen clock: Date is pinned so "detected 3h ago" and the fixtures' timestamps never move;
  // timers still run (the mock API answers after a setTimeout)
  await page.clock.setFixedTime(new Date("2026-10-16T12:00:00Z"));
  await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "light" });
  await page.setViewportSize({ width: 1280, height: 900 });
  await signIn(page);
  await page.goto("/incidents/inc-2");
  await expect(page.getByRole("heading", { name: "Suspect commits" })).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expect(page).toHaveScreenshot("incident-inc-2.png", {
    fullPage: false,
    animations: "disabled",
    caret: "hide",
    // GitHub avatars come from the network
    mask: [page.locator("img[src*='github.com']")],
  });
});
