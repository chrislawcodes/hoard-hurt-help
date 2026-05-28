# Deploy to Railway

## One-time setup

1. <https://railway.app/> → new project → *Deploy from GitHub repo*.
2. Pick `hoard-hurt-help`. Railway detects Python from `pyproject.toml`.
3. Add a Postgres database (Railway add-on, free tier).
4. Set the start command in *Settings*:

```
.venv/bin/alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

5. Set env vars in *Variables*:

```
BASE_URL=https://<your-app>.up.railway.app
DATABASE_URL=${{Postgres.DATABASE_URL}}   # use Railway template; rewrite to async driver
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://<your-app>.up.railway.app/auth/google/callback
SESSION_SECRET=<random 64 hex chars>
ADMIN_EMAILS=you@gmail.com
```

Note: Railway's Postgres URL is `postgresql://...`. We need `postgresql+asyncpg://...`. Add an init step or use a small helper at startup to rewrite it.

6. Update the Google OAuth client's authorized redirect URIs to include the Railway callback.

## Custom domain (optional)

Railway → Settings → Custom domain. Update `BASE_URL` and `GOOGLE_REDIRECT_URI` after DNS propagates.

## Cost expectation

- App service (always-on, ~100 MB RAM): $3-8/month
- Postgres add-on (small): free up to a limit, ~$5/month after

Total ~$5-15/month.

## Operational notes

- Logs in Railway → *Deployments* → *Logs*.
- Postgres backups: Railway → *Database* → *Backups*.
- Rolling deploy: push to main → Railway auto-deploys.
