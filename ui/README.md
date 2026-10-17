# Kaizen UI

Next.js 15 (App Router) + TypeScript + Tailwind v4 + shadcn/ui-style components + TanStack Query.

```bash
npm install
npm run dev:mock    # no backend needed: fixtures in lib/api/mock.ts (NEXT_PUBLIC_API_MOCK=1)
npm run dev         # real API: API_URL=http://localhost:8000 (default); sign in at /login
npm run gen:api     # regenerate lib/api/schema.d.ts from ../docs/openapi.json (CI fails if stale)
npm run lint        # tsc --noEmit
npm test            # vitest (unit; includes eval dashboard == docs/results/latest.md)
```

`dev:mock` is a small node wrapper (`scripts/dev-mock.mjs`) that sets `NEXT_PUBLIC_API_MOCK=1`, so it
works in PowerShell, cmd and POSIX shells: `npm run dev:mock -- -p 3022`.

## End-to-end, accessibility and visual tests (Playwright)

```bash
npx playwright install chromium   # once
npx playwright test               # starts `next dev` in mock mode on port 3022 (PW_PORT=... to change)
```

* `e2e/signin.spec.ts`, `e2e/dashboard.spec.ts`, `e2e/incident*.spec.ts`: mocked GitHub sign-in,
  dashboard health + deployment timeline, a seeded incident appearing within one poll, list filters /
  archive / navigation, hotspots, evaluation, and the approval flow ending in "Approved by".
* `e2e/a11y.spec.ts`: axe-core (`@axe-core/playwright`) WCAG 2.1 A/AA scan of overview, incidents,
  hotspots, evaluation (serious/critical fail the test) and an incident page (reported as annotations).
* `e2e/visual.spec.ts`: `toHaveScreenshot` of `/incidents/inc-2` with a frozen clock, reduced motion
  and animations disabled. **Opt-in** because font rendering differs per OS:
  `npm run e2e:visual` (sets `VISUAL=1`). Baselines are per platform under `e2e/__screenshots__/`.
  Generate the Linux baselines (the ones CI would compare) in the official image matching
  `@playwright/test`, from `ui/`:

  ```bash
  docker run --rm --ipc=host -v "$(pwd)":/work -v /work/node_modules -v /work/.next -w /work \
    mcr.microsoft.com/playwright:v1.64.0-noble \
    sh -c "npm ci && VISUAL=1 npx playwright test e2e/visual.spec.ts --update-snapshots"
  ```

  (PowerShell: replace `"$(pwd)"` with `"${PWD}"`.) The anonymous `node_modules` / `.next` volumes
  keep the container's Linux binaries out of the host checkout.

## Conventions
* **API access** only through `lib/api/client.ts` (typed `openapi-fetch`, CSRF header added for
  mutations) and hooks in `lib/api/hooks.ts`. Never hand-write request/response types.
* **The browser talks to its own origin**: `next.config.ts` rewrites `/api/v1/*` and `/auth/*` to
  `API_URL`, so the session cookie is first-party.
* **Every data view handles loading, error and empty** states.
* **One shell**: every signed-in page lives under `app/(dash)` inside the analytics dashboard
  (`components/block/analytics-dashboard.tsx`); the URL picks the page, `AuthGate` redirects to
  `/login` on 401. Detail routes (`/incidents/[id]`, `/repositories/[owner]/[name]`) render in the
  same shell with the dashboard's own classes.
* **Role-aware**: the session lists `viewer`/`approver` per repository; the API enforces it, the UI
  only hides actions the user cannot perform.
* **Theme**: light/dark via `next-themes` (Settings → Appearance); dashboard tokens live in
  `components/block/analytics-dashboard.css`.

## Credits
* Analytics dashboard: [ObsidianUI](https://github.com/Atharvsinh-codez/ObsidianUI), MIT, Copyright (c) 2026 ObsidianUI.
* Code block (`components/ui/code-block.tsx`): [Rare UI](https://rareui.com), MIT + Commons Clause +
  Attribution, Copyright (c) 2026 Swami Malode.
