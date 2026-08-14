# Feasibility-adversarial review — spec.md revision 2

Round-2 lens: the 30 round-1 findings are not re-reported. Everything below is
either a hole a round-1 fix opened, a round-1 fix that only half-closed its hole,
or a case the corrected design still misses. Each is tagged against real code.

## Findings

### HIGH — Autopilot submissions are `was_defaulted = False`, so D10 does not exclude them [CODE-CONFIRMED]

D10 rests on one creation site (`scoring.py:190`, the deadline-miss default). There
is a second, larger one it misses.

`app/engine/bots/service.py:144-177` (`_auto_submit_autopilot`) auto-plays every
turn for a human who left a match mid-game (`agent.kind == HUMAN and
player.autopilot_at is not None`, line 69). It calls:

```python
await module.record_submission(db, turn, player, move, existing=existing_submission)
```

with **no `is_connector_fallback=True`**. Both game modules set the flag straight
from that parameter — `app/games/hoard_hurt_help/game.py:220` (`was_defaulted =
is_connector_fallback`) and `app/games/liars_dice/game.py:278/291` — and
`app/games/base.py:191/210/399` defaults it to `False`.

So a user who joins a match and walks away produces a `was_defaulted=False`
submission on **every remaining turn**. Under D5 they clear step 6 "Played a turn",
and (with D11's day bucketing) can clear step 7 "Came back another day" without
ever coming back. Acceptance criterion 10 and the test-plan line "a defaulted-only
player is not 'played a turn'" both pass while the real number is wrong — exactly
the silent-inflation class this spec exists to prevent. "Played a turn" needs
`was_defaulted = false` **AND** an exclusion of autopilot seats
(`players.autopilot_at IS NULL`, or `left_at IS NULL`).

### HIGH — Seat-hold expiry hard-deletes `players` rows, so step 5 "ever joined" is not durable — and it erases precisely the stuck users [CODE-CONFIRMED]

D7 names only `delete_match` and `reset_handle` as destructive. There is a third,
and it is automatic rather than an admin action:

- `app/engine/seat_hold.py:87` — `release_held_seats` deletes every still-held seat
  at match start.
- `app/engine/seat_hold.py:119` — `sweep_held_seats` runs **every poller tick** and
  `await db.delete(player)` on any held seat past its deadline.
- `app/routes/web_seat_connect.py:154` — same delete on an expired hold.

A user who clicks Join but whose AI never goes live has their `Player` row deleted
within the hold window (~15 min). That user is the single most interesting row on
this dashboard, and step 5 will never see them. This is not a "past funnels aren't
reproducible" caveat — it is an ongoing, silent, minutes-scale deletion of the
target population.

The mirror problem: a hold that has **not** yet expired is a live `players` row, so
step 5 counts it as "Joined a match" — while `app/engine/player_counts.py:23-30`
and `app/engine/arena.py:110` both say explicitly that a held seat is *not* a real
player. The funnel and the rest of the product would report different join counts
for the same match.

### HIGH — Agents are hard-deleted when they have no game history, so step 3 "any agents row, ever" is not durable either [CODE-CONFIRMED]

`app/routes/agents_lifecycle.py:154-169`:

```python
has_history = (await db.execute(select(Player.id).where(Player.agent_id == agent.id).limit(1))).first() is not None
if has_history:
    agent.archived_at = ...          # soft
else:
    ... delete(AgentVersion) ...; await db.delete(agent)   # hard
```

D7's fix was "archived agents still count" — true, but it only covers the branch
with history. The branch **without** history is hard-deleted, and "built an agent,
never played, deleted it" is exactly the cohort step 3 exists to hold. Those users
silently fall back to "picked a handle", inventing a drop at the agent step. Same
bug class D5 was written to fix.

### HIGH — D8's three creation sites cannot flag the accounts D8 was written to exclude, and the sign-in "domain rule" can never fire [CODE-CONFIRMED]

The site count is right: `User(` appears in exactly three places in `app/`,
`mcp_server/` and `scripts/` — `app/routes/auth.py:41`,
`app/engine/bots/seating.py:51`, `app/routes/dev_login.py:51`. The problem is what
those three can actually reach.

1. **`auth.py:41` (the domain rule) is unreachable for the seed domains.** That row
   is only created after a completed Google OAuth exchange. Google will never issue
   an id_token for `@house.local`, `@agentludum.local`, or `@local.test`. So the
   rule the spec assigns to the one site that runs in production can never match a
   seed domain. It is dead code as specified.
2. **The polluting accounts come from a fourth path.** `ludumlabs@house.local`,
   `ludumlabs_flat_6@house.local`, `ludumlabs_no_repeats@house.local`,
   `sims@agentludum.local`, `harness-A/B/C@local.test` — 500+ of the 646 player
   rows in D8's own evidence table — appear **nowhere in the repo** outside this
   spec and its review prompts (repo-wide grep over `*.py`, `*.md`, `*.sh`,
   `*.json`). They were created by an out-of-repo tool or by hand. That path is not
   covered, so the next harness run re-pollutes the dashboard.
3. Only `seating.py` (`bots@agentludum.local`) and `dev_login.py` genuinely
   self-flag. `get_or_create_bots_user` (`seating.py:45-58`) also does not touch
   `is_internal` on the already-exists branch, so it depends on the backfill too.

Net: after the migration, ongoing correctness rests entirely on Chris noticing a
polluted number and hitting the manual toggle. The spec presents it as
"set at creation, never recomputed" — a much stronger claim than the code supports.
Either the backfill must be re-runnable, or `is_internal` needs a derivation the
out-of-repo path can't bypass (e.g. flag on `google_sub` prefix / non-Google subs).

### MEDIUM — D5's reordered funnel still breaks for MCP-first users: a connection exists with no handle and no agent [CODE-CONFIRMED]

The gate ladder D5 copied (`NEEDS_HANDLE=1 → NEEDS_AGENT=2 → NEEDS_MCP_CONNECTION=3`,
`app/routes/nav_context.py:57-73`) governs the **web join flow only**. The MCP path
does not climb it:

- `mcp_server/oauth_auth.py:128-156` — `_bootstrap_signin_connection_from_idp` calls
  `sync_google_user` inside the OAuth token exchange. That creates the `users` row
  with `handle = NULL`.
- `mcp_server/connection_identity.py:176` → `app/engine/mcp_connection.py:210-224` —
  the first MCP request creates `Connection(..., first_connected_at=now)`.
- Neither path checks a handle or an agent. The `require_user_with_handle`
  dependency guards only the web routes (`app/routes/connections_pages.py:87, 211,
  270, 337, 357`).

So `first_connected_at IS NOT NULL` with `handle_key IS NULL` and zero agents is a
reachable state. Under D5 + D7 that user is pinned at step 1 and disappears from
step 4 "AI connected" — the same truncation D5 was written to eliminate, now
pointing the other way. Worth a step-order caveat plus an explicit test
("MCP-first user with a connection and no handle").

### MEDIUM — D6's replacement signal is strictly weaker than the step it replaces, not equivalent [CODE-CONFIRMED]

Both of D6's reasons for dropping "started AI hookup" check out
(`app/engine/pending_connection_gc.py:22-38`; `ConnectionSetup(` written only at
`app/routes/connections_machine_setup.py:106`). The claim that does not hold is the
replacement: *"The leak it was meant to show survives as the drop between built an
agent and AI connected."*

That drop pools at least three unlike populations, and cannot separate them:

1. Users who never attempted a hookup at all.
2. Users who attempted and stalled — whose `ConnectionSetup` **and** whose pending
   `Connection` are both hard-deleted after 24h (the same GC also runs
   `delete(Connection).where(status == PENDING, first_connected_at IS NULL,
   created_at < cutoff)`, lines 31-37). No row survives to distinguish them.
3. MCP-first users, who are excluded upstream by the finding above.

The old step measured "tried and stalled". The new one measures "did not arrive",
which is not the same question. Either say so on the page, or keep the live 24-hour
snapshot as the only stall signal and stop describing it as a survivor of the old
step.

### MEDIUM — Human players never own a `kind='ai'` agent, so strict nesting zeroes them out of steps 4-7 [CODE-CONFIRMED]

`app/engine/human_player.py:77-108` (`get_or_create_human_agent`) creates
`kind=AgentKind.HUMAN` agents on demand for web play; `AgentKind` has three values
(`app/models/agent.py:17-24`). D5 step 3 requires `kind='ai'`, but steps 5-7 read
`players` and `turn_submissions` rows that include human seats
(`app/routes/web_play.py:86`, `app/routes/web_join.py:143`).

A real human who signs up, picks a handle, joins a match and plays every turn is
counted at steps 1-2, dropped at step 3, and their genuine match and turn activity
is invisible below it. The spec never states the funnel is AI-path-only, and
acceptance criterion 2 ("never increases") will pass while the shape is wrong.
Either scope the funnel explicitly to the AI path (and exclude human seats from
steps 5-7 too) or admit human seats at step 3.

### MEDIUM — The "real prefixes, verified" skip list misses the OAuth/discovery surface the catch-all root mount exposes [CODE-CONFIRMED]

`app/main.py:311-314` mounts the whole FastMCP app at the **root**:

```python
app.mount("/", mcp_asgi_app, name="mcp")
```

and `mcp_server/server.py:78-92` documents why: "the auth discovery URLs stay
rooted at `/.well-known/...` while the MCP endpoint itself remains `/mcp`". So
`/mcp` is correctly skipped, but everything else the MCP app serves is not:
`/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource`,
and the OAuth `authorize` / `token` / `register` routes all fall through the root
mount and hit `FirstTouchMiddleware`. Also absent from the list: the SSE path the
spec itself names (`/games/{game}/matches/{id}/stream`) and `/favicon.ico`
(`app/main.py:307`).

Two consequences. Machine POSTs from AI clients get a `Set-Cookie` they never
asked for. And a user whose first browser contact with the site is the MCP
`/authorize` page has first touch permanently pinned to `landing_path=/authorize`
with no source — "never overwrite" then blocks the real source when they later land
on `/?utm_source=...`. The list is presented as verified; it is not complete.

### MEDIUM — D3's length cap is necessary but not sufficient: the session already accumulates unbounded per-object keys [CODE-CONFIRMED]

The session is a single signed cookie with no size guard and no `max_age`
(`app/main.py:240-245`). What already competes for the ~4KB with first touch:

- `app/routes/connections_machine_setup.py:115` and `:125` write
  `fresh_connection_key_setup_{setup.id}` — and **nothing ever pops it**; line 174
  only `.get`s it. One key per setup, accumulating for the life of the cookie.
- `app/routes/connections_credentials.py:41` — `fresh_connection_key_{connection.id}`.
- `app/routes/web_my_matches.py:178` — `ai_type_{player.id}`.
- authlib 1.7.2 (`uv.lock:224`) parks per-attempt OAuth state in the same cookie.

First touch's ~1.1KB raw (≈1.5KB after JSON + base64 + signature) is spent against a
budget that already leaks. Overflow raises nothing: the browser silently drops the
oversized cookie and the user is signed out. D3's statement — "first touch cannot
push a session over the limit" — is only true in isolation. The cap is right; the
missing half is a total-session budget check (or popping the setup key on
completion).

### MEDIUM — D11 step 7 reads one long sitting as a return visit, and finding 1 makes that automatic [CODE-CONFIRMED]

"Played on 2+ distinct days" over `turn_submissions` counts any two local dates.
A match that crosses local midnight — a stalled or slow one, which this repo has a
documented history of — yields two dates from a single sitting. Layered on the
autopilot finding, a user who left a match still emits non-defaulted submissions on
both sides of midnight and is scored as retained. Step 7 needs a distinct-*session*
or distinct-*match-day* definition, or at minimum the autopilot exclusion.

### LOW — `submitted_at` is nullable on rows the funnel keeps; the day bucketing needs the same guard `/admin/reports` already uses [CODE-CONFIRMED]

`app/models/turn.py:73` — `submitted_at: Mapped[datetime | None]`.
`app/read_models/admin_reports.py:182` defends against exactly this:
`if submission.was_defaulted or submission.submitted_at is None: ...`. D11's
local-date bucketing must apply the same guard or a NULL will crash or mis-bucket
the retention count.

### LOW — One `mark_internal` audit action cannot distinguish setting from clearing [CODE-CONFIRMED]

`AdminAction` is a closed enum of five values with a 16-char column
(`app/models/admin_audit_log.py:15-21, 37-39`), and `reason` is the only free
field. A toggle logged under a single value leaves the log ambiguous about which
direction it moved. Two values (`mark_internal` / `unmark_internal`, 15 chars, both
fit) or a mandated `reason` string.

### LOW — The dev-login account's domain is not in the backfill seed list [CODE-CONFIRMED]

`app/routes/dev_login.py:36` — `_DEV_USER_EMAIL = "dev@localhost"`. D8's seed
domains are `agentludum.local`, `house.local`, `local.test`. New dev-login users
are created internal (site 3), but any pre-existing `dev@localhost` row is missed
by the backfill. Dev-DB only — but the dev DB is where D8's whole evidence table
was measured.

## Residual Risks

- **The `is_internal` post-deploy verification has no ground truth in prod.** The
  build plan says to read back the flagged row count "against the known internal
  accounts". That list was measured in the dev DB and, per the HIGH above, those
  accounts do not exist in code — so there is no way to confirm the prod list is
  the same. The verification step as written cannot fail informatively. Suggest
  instead: publish the flagged emails after the backfill and have Chris eyeball
  them once, before any number on the page is trusted.
- **`FirstTouchMiddleware` implementation style.** `install_request_logging`
  already installs a `BaseHTTPMiddleware` via `@app.middleware("http")`
  (`app/request_logging.py:199`), and it wraps the root-mounted streamable-HTTP MCP
  app today, so precedent exists. Adding a second `BaseHTTPMiddleware` in that
  stack is not obviously safe for long-lived streams; a pure ASGI middleware avoids
  the question entirely. [UNVERIFIED — depends on implementation choice.]
- **`handle` vs `handle_key`.** D5 step 2 keys on `handle_key IS NOT NULL` while the
  product's gate keys on `handle is None` (`nav_context.py:236`). They are written
  together (`app/routes/handle_web.py:161`, `dev_login.py:55-56`) and cleared
  together (`app/services/admin_user_actions.py:135-137`), so they agree today —
  but the funnel and the gate reading different columns is a drift waiting to
  happen. Pick one.
- **D12's filter is `ilike`, not the repo's own predicate.**
  `app/read_models/admin_reports.py:109` uses `~Match.name.ilike("prod smoke%")`,
  while `app/match_naming.py:19` (`is_test_match_name`) strips leading whitespace
  first. Copying the `ilike` keeps the two admin pages consistent (which is D12's
  actual goal) but inherits a name like `" prod smoke ..."` slipping through both.
- **Backfill suffix matching.** Matching `local.test` as a bare suffix also matches
  `notlocal.test`. Anchor on `@` + domain. (Flagged by the requirements lens in
  round 1; repeating only because it lands in the migration, which is the
  data-critical step.)
- **Cookie consent remains open.** D3 is honest that a consent banner is out of
  scope. Worth restating that shipping this puts an analytics cookie on every
  anonymous EU visitor from the day it deploys, not from the day the banner lands.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "Autopilot submissions are was_defaulted=False, so D10 does not exclude them", "detail": "_auto_submit_autopilot (app/engine/bots/service.py:144-177) calls record_submission without is_connector_fallback=True, so every auto-played turn for a human who left a match is recorded as a genuine non-defaulted turn and clears funnel steps 6 and 7."}, {"severity": "HIGH", "title": "Seat-hold expiry hard-deletes players rows, erasing the stuck users step 5 must count", "detail": "app/engine/seat_hold.py:87 and :119 (and web_seat_connect.py:154) db.delete(player) on every expired or unconfirmed held seat, so 'joined a match, AI never came online' leaves no row within ~15 minutes, and an unexpired hold is counted as a join the rest of the product does not count."}, {"severity": "HIGH", "title": "Agents with no game history are hard-deleted, so step 3 'any agents row, ever' is not durable", "detail": "app/routes/agents_lifecycle.py:154-169 only sets archived_at when Player rows exist and otherwise db.delete(agent), erasing exactly the built-an-agent-never-played cohort D7's 'archived agents still count' was meant to preserve."}, {"severity": "HIGH", "title": "D8's three creation sites cannot flag the accounts it was written to exclude", "detail": "The seed-domain rule at auth.py:41 can never fire (Google issues no id_token for .local addresses), and ludumlabs/sims/harness accounts — 500+ of the 646 player rows in D8's own evidence — appear nowhere in the repo, so they come from an uncovered fourth creation path."}, {"severity": "MEDIUM", "title": "D5's order still breaks for MCP-first users: connection with no handle and no agent", "detail": "mcp_server/oauth_auth.py:156 creates the user at OAuth token exchange with handle NULL and app/engine/mcp_connection.py:222 then creates a Connection with first_connected_at set, with no handle or agent gate on that path, so strict nesting pins the user at step 1 and drops them from 'AI connected'."}, {"severity": "MEDIUM", "title": "D6's replacement signal is strictly weaker than the step it replaces", "detail": "The 'built an agent to AI connected' drop pools never-tried users, users whose ConnectionSetup and pending Connection were both GC'd after 24h (pending_connection_gc.py:31-37), and MCP-first users, and cannot separate 'tried and stalled' from 'never arrived'."}, {"severity": "MEDIUM", "title": "Human players never own a kind='ai' agent, so strict nesting zeroes them out of steps 4-7", "detail": "app/engine/human_player.py:77-108 creates kind=HUMAN agents for web play, so a real human who joins and plays every turn is dropped at step 3 while their players and turn_submissions rows still feed steps 5-7."}, {"severity": "MEDIUM", "title": "Skip-prefix list misses the OAuth/discovery surface exposed by the catch-all root mount", "detail": "app/main.py:314 mounts the FastMCP app at '/', so /.well-known/*, authorize/token/register, the SSE stream path and /favicon.ico all reach the middleware, setting cookies on machine POSTs and letting /authorize permanently claim a user's first touch."}, {"severity": "MEDIUM", "title": "D3's length cap is necessary but not sufficient for the shared session cookie", "detail": "connections_machine_setup.py:115/125 writes fresh_connection_key_setup_{id} and never pops it, alongside fresh_connection_key_{id}, ai_type_{id} and authlib OAuth state, so first touch's ~1.5KB is spent against an already-leaking 4KB budget whose overflow silently signs the user out."}, {"severity": "MEDIUM", "title": "D11 step 7 counts one midnight-crossing sitting as a return visit", "detail": "'Played on 2+ distinct days' over turn_submissions turns a single long or stalled match into retention, and autopilot rows keep emitting on both sides of local midnight for a user who already left."}, {"severity": "LOW", "title": "submitted_at is nullable on rows the funnel keeps", "detail": "app/models/turn.py:73 allows NULL and app/read_models/admin_reports.py:182 already guards for it, so D11's local-date bucketing needs the same guard or it will crash or mis-bucket."}, {"severity": "LOW", "title": "One mark_internal audit action cannot distinguish setting from clearing", "detail": "AdminAction is a closed enum with a 16-char column and only a free-text reason field, so a toggle logged under a single value leaves the audit log ambiguous about direction."}, {"severity": "LOW", "title": "dev@localhost is not covered by the backfill seed domains", "detail": "app/routes/dev_login.py:36 uses dev@localhost while D8's seed list is agentludum.local, house.local and local.test, so any pre-existing dev-login row is missed by the backfill in the very DB where D8's evidence was measured."}]}
```
