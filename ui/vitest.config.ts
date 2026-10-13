import { configDefaults, defineConfig } from "vitest/config";

// Unit tests only; e2e/ is Playwright's (`npx playwright test`).
export default defineConfig({
  test: { exclude: [...configDefaults.exclude, "e2e/**"] },
});
