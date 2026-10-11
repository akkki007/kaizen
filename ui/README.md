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
* **Every data view handles loading, error and empty** with `components/states.tsx`.
* **Protected routes** live under `app/(app)`; `AppShell` redirects to `/login` on 401.
* **Role-aware**: the session lists `viewer`/`approver` per repository; the API enforces it, the UI
  only hides actions the user cannot perform.
* **Theme**: light/dark via `next-themes`, tokens in `app/globals.css`.
