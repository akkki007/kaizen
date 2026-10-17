import { defineConfig, devices } from "@playwright/test";

// E2E runs against `next dev` in mock mode (no backend). The port is configurable so it never
// collides with a running app (3000/8000): PW_PORT=3030 npx playwright test.
const PORT = Number(process.env.PW_PORT ?? 3022);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"], ["html", { open: "never" }]],
  // first compile of a page in dev mode can take a while
  timeout: 60_000,
  expect: { timeout: 15_000, toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: "disabled" } },
  // Visual baselines are per OS; they live next to the spec as <name>-chromium-<platform>.png.
  snapshotPathTemplate: "{testDir}/__screenshots__/{testFilePath}/{arg}-{projectName}-{platform}{ext}",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // env (not a POSIX inline assignment) so this works in every shell, CI included.
    // PW_PROD=1 builds and serves a production mock bundle: far less memory than `next dev`.
    command: process.env.PW_PROD
      ? `node scripts/dev-mock.mjs build && node scripts/dev-mock.mjs start -p ${PORT}`
      : `node scripts/dev-mock.mjs -p ${PORT}`,
    env: { NEXT_PUBLIC_API_MOCK: "1" },
    url: `http://localhost:${PORT}/login`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
