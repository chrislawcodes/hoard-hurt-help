# Deploy to Railway

The build and start config live in [`railway.json`](../railway.json) (version-controlled),
so you do **not** type a start command into the Railway UI. It runs:

```
alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Hard rule: one instance, one worker, always-on

Two things are in-memory and single-process:

- the per-game scheduler that drives turns ([`app/engine/scheduler.py`](../app/engine/scheduler.py)), and
- the live-spectate SSE fanout ([`app/broadcast.py`](../app/broadcast.py)).

So on Railway:

- **1 replica** — set in `railway.json` (`numReplicas: 1`). Two instances would double-drive every game and split SSE viewers.
- **1 uvicorn worker** — the start command has no `--workers`. Leave it that way.
- **Always-on** — do **not** enable sleep / scale-to-zero. A sleeping app stops advancing game turns. (Crash recovery exists via `resume_active_games_on_startup`, but that's for restarts, not a substitute for staying awake.)

Scaling past one box is a future Redis-Pub/Sub + external-scheduler project, out of scope for v1.

## One-time setup

1. <https://railway.app/> → new project → *Deploy from GitHub repo*.
2. Pick `hoard-hurt-help`. Railway reads `railway.json` and detects Python from `pyproject.toml`.
3. Add a Postgres database (Railway add-on).
4. Set env vars in *Variables*:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}    # pasted verbatim — see note below
SESSION_SECRET=<random 64 hex chars>       # python -c "import secrets; print(secrets.token_hex(32))"
ADMIN_EMAILS=you@gmail.com
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
NIXPACKS_PYTHON_VERSION=3.11               # pin the runtime (.python-version is gitignored)
# These two are filled in on the second pass, once Railway has given you a URL:
BASE_URL=https://<your-app>.up.railway.app
GOOGLE_REDIRECT_URI=https://<your-app>.up.railway.app/auth/google/callback
```

`DATABASE_URL` note: Railway's value is a sync URL (`postgresql://...`). The app normalizes it
to the async driver (`postgresql+asyncpg://...`) at load time in
[`app/config.py`](../app/config.py), so you can paste `${{Postgres.DATABASE_URL}}` as-is.

### Two-pass for the public URL (chicken-and-egg)

You don't know your URL until the first deploy, and OAuth needs it.

1. **Pass 1** — deploy with the vars you have. Railway assigns `https://<app>.up.railway.app`.
2. **Pass 2** — set `BASE_URL` and `GOOGLE_REDIRECT_URI` to that URL, add the callback to your
   Google OAuth client's authorized redirect URIs in Google Cloud Console, then redeploy.

## Custom domain (optional)

Railway → Settings → Custom domain. Update `BASE_URL` and `GOOGLE_REDIRECT_URI` again after DNS propagates.

## Post-deploy verification (don't skip)

"Deployed" ≠ "working." After pass 2, confirm:

- [ ] `GET /healthz` returns `{"status":"ok"}`
- [ ] Deploy logs show `alembic upgrade head` ran; Postgres has the tables
- [ ] Google sign-in works end-to-end
- [ ] Create a game as admin; confirm the scheduler advances turns
- [ ] Connect an MCP client with `X-Agent-Key`; a bogus key returns `401 INVALID_KEY` (proves header auth flows through)
- [ ] Open the spectator page; SSE events stream live (Railway's proxy isn't buffering)
- [ ] Watch logs ~10 min for error spikes

## Cost expectation

- App service (always-on, ~100 MB RAM): $3-8/month
- Postgres add-on (small): free up to a limit, ~$5/month after

Total ~$5-15/month.

## Operational notes

- Logs in Railway → *Deployments* → *Logs*.
- Postgres backups: Railway → *Database* → *Backups*.
- Rolling deploy: push to `main` → Railway auto-deploys.

## Known follow-up (not blocking)

The session cookie is created with `https_only=False` in [`app/main.py`](../app/main.py). Over HTTPS in
prod it should be `Secure`. That file is being edited on another branch right now, so this fix
is intentionally left out of the deploy-prep change to avoid a conflict — apply it on whichever
branch owns `app/main.py` (wire `https_only` to a `COOKIE_SECURE` env setting).
