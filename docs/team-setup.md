# Team setup (everyone works on yashchandnani07/shoplite)

All developers share one GitHub App (`causaldeploy-shoplite-demo`) and one target repo.

```bash
git clone https://github.com/akkki007/kaizen && cd kaizen   # branch main
make env        # or ask a teammate for the shared .env; writes .env with the App identity
make bootstrap  # services, deps, migrations, npm install
make backfill   # clones yashchandnani07/shoplite with the App token and builds the graph locally
make api        # terminal 1  (http://localhost:8000)
make worker     # terminal 2
make ui         # terminal 3  (http://localhost:3000, sign in with GitHub)
```

* Windows (port 5432 taken, `python -m uv`, no `make`, mock UI): see `README.md` -> "Windows".
* `make demo-reset YES=1` seeds resolved demo incidents for the walkthrough (`docs/demo.md`).
* `make backfill` needs no webhook: it is the same import the production worker runs on a push.
* Real webhooks only reach production; locally you trigger imports with `make backfill`.
* `CD_ALLOWED_REPOS=yashchandnani07/shoplite` keeps the local stack from importing anything else.
* Local `CD_SESSION_SECRET` is generated per machine and is not production's.

## One-time App setting (owner)
GitHub login from the UI starts at `/auth/login?origin=http://localhost:3000` and redirects back to
`http://localhost:3000/auth/callback`. Add it under the App's **Callback URL** list
(Settings -> Developer settings -> GitHub Apps -> causaldeploy-shoplite-demo).
`http://localhost:8000/auth/callback` and the production URL are already registered.
