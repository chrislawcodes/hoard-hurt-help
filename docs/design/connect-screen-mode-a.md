# Connect screen redesign — "Play with your own AI" (Mode A)

**Branch:** feat/connect-screen-polish (off origin/main)
**Type:** Direct Path, pure-UI build
**Pairs with:** the `mcp-oauth` workstream (worktree `--feat-mcp-oauth`,
`docs/workflow/feature-runs/mcp-oauth/`). This is the connect-screen UI that the OAuth
feature explicitly defers as a follow-up (their AD-8 / tracked follow-ups).

## The problem

Today `/me/connections` blurs three different actions into one wall of connect snippets.
They happen at three different rhythms and the screen doesn't make that visible:

1. **Set up your AI** — once, ever (wire the MCP tools).
2. **Start playing** — each session (kick the AI off; this is what makes you "live").
3. **Join a game** — per match (you pick which games to enter).

And there's a hidden ordering trap: the lobby's **Join is blocked unless your AI is live**,
and your AI is only live while it's running. So the real order is "start your AI → then
join," which the current screen never says.

## The shape: one self-advancing box

The page is **state-aware** — it reads three facts the server already knows and shows only
the next step:

- Has the user ever connected? (a Connection row exists)
- Is a connection live right now? (last_seen within `LIVE_WINDOW_SECONDS` = 90s)
- Does the user have an agent?

| User state | What the page leads with |
|---|---|
| **New** (never connected) | The connect command + "Listening for your AI…" |
| **Returning** (connected before, AI off) | The short "start playing" command; setup collapsed to a tick |
| **Already playing** (AI live now) | "Join a game" — everything else collapsed |

The moment the AI connects, the server marks the user's connection live; the page is
polling for that and **advances itself** ("Listening… → you're live") with no refresh.
After connect: no agent yet → "Create an agent" (links to `/me/agents`); has an agent →
"Join a game" (hands off to the lobby, where the agent is picked).

Agent creation is **not** a step on this screen — it's a contextual nudge shown *after*
connect, and it lives on its own page (the agent loop is visited many times; the machine
setup rarely).

## Connecting (OAuth form)

`/mcp` is becoming OAuth-only (the `mcp-oauth` workstream). So connecting = a **header-less
command + one-click "Sign in with Google"**. No `sk_conn_` key in the paste, ever.

**Clients:** Claude Code (default/hero), Codex, Gemini, and Claude Desktop. Cursor dropped.

The real OAuth connect is **multi-step**, not one paste (a chained one-liner can't work —
the client needs an interactive Google sign-in and a reload before the tools load). The
flow, matching `mcp-oauth`'s `docs/setup-mcp.md` exactly: **add the server → sign in with
Google → paste the play-prompt.**

- **Claude Code** — `claude mcp add --transport http agentludum <url>`, then run `/mcp`
  → Authenticate (browser opens for Google). No `--header`.
- **Codex** — a `~/.codex/config.toml` block (`[mcp_servers.agentludum]` / `url = …`, no
  `http_headers`); browser sign-in on first use.
- **Gemini** — `gemini mcp add agentludum <url> --transport http`; browser sign-in on
  first connect.
- **Claude Desktop** — Settings → Connectors → Add custom connector → paste the `/mcp` URL
  → enable it → sign in with Google. Labeled: great for trying it out; CLI or the always-on
  connector is steadier for long unattended play.

The **play-prompt** (the full talk/act loop, identical across clients) is pasted *after*
sign-in; the page shows it in the live + returning states. The exact command/prompt text
is owned by `mcp-oauth`'s `docs/setup-mcp.md` and mirrored in `_connect_options()` /
`_play_prompt()` — keep them in sync.

The **always-on connector** stays as the collapsed secondary option (true set-and-forget).

## Key states / microcopy

- **Listening:** "Listening for your AI to connect…" (pulsing). After ~60s with nothing:
  "Still waiting — make sure you ran the command and approved the Google sign-in."
- **Live (new, no agent):** "Signed in — your AI is connected and live" → "Create an agent
  to play."
- **Live (has agent):** "Join a game — N open right now" → "Choose games to join →".
- **Returning fast path:** the play command is the hero; "✓ Set up" collapsed, tap to
  reopen (e.g. switching computers → "Use the full setup command").

## Constraints / standards

- Server-rendered HTMX only (no SPA); styles in `app/static/style.css` reusing existing
  vars and the existing `byo-*` tab pattern; type annotations; no suppressions; preflight
  green (ruff + mypy `app/ mcp_server/` + pytest).
- Authed page → verify with route/template tests in `tests/test_connection_management.py`
  (the preview cookie won't stick for signed-in pages).

## Auth-agnostic seam (coordination with mcp-oauth)

The connect **command/steps come from ONE swappable helper**, isolated so the OAuth team's
final command (their Slice 5, where they rewrite `docs/setup-mcp.md`) drops in without
touching layout. We do **not** rewrite `docs/setup-mcp.md` — that's theirs; we consume it.
Build the layout/states/copy/auto-advance now (all auth-agnostic); wire the exact per-client
command when their Slice 4–5 lands. Ship this screen with/after that.

## Out of scope

- The OAuth mechanism itself (mcp_server/, deps.py, the gate) — that's the `mcp-oauth`
  feature.
- Redesigning the Agents page or the lobby Join (we link to them).
- Changing the connector's auth.
