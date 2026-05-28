# Local development setup

## Prerequisites

- Python 3.11+
- A Google account for OAuth testing (or skip OAuth in tests)

## One-time Google OAuth setup

1. Go to <https://console.cloud.google.com/>.
2. Create a new project (or reuse one).
3. *APIs & Services* → *Credentials* → *Create credentials* → *OAuth client ID*.
4. Application type: *Web application*.
5. Authorized redirect URIs: `http://localhost:8000/auth/google/callback`.
6. Copy the client ID and secret into `.env`:

```
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
```

## Set up the project

```bash
git clone https://github.com/chrislawcodes/hoard-hurt-help.git
cd hoard-hurt-help
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# fill in .env per above
.venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
# paste the output as SESSION_SECRET in .env
.venv/bin/python -m alembic upgrade head
```

## Run the server

```bash
.venv/bin/uvicorn app.main:app --reload
```

Open <http://localhost:8000>.

## Run the tests

```bash
.venv/bin/python -m pytest -q
```

## Create your first game (as admin)

1. Add your email to `ADMIN_EMAILS` in `.env`.
2. Restart the server.
3. Sign in at `/auth/google/login`.
4. Visit `/admin/games/new`.

## Code style

```bash
.venv/bin/ruff check app/ tests/ mcp_server/
.venv/bin/black --check app/ tests/ mcp_server/
```
