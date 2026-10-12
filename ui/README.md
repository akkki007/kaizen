# CausalDeploy UI

Next.js 15 (App Router) + TypeScript + Tailwind v4 + shadcn/ui-style components + TanStack Query.

```bash
npm install
npm run dev:mock    # no backend needed: fixtures in lib/api/mock.ts (NEXT_PUBLIC_API_MOCK=1)
npm run dev         # real API: API_URL=http://localhost:8000 (default); sign in at /login
npm run gen:api     # regenerate lib/api/schema.d.ts from ../docs/openapi.json (CI fails if stale)
npm run lint        # tsc --noEmit
```

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
