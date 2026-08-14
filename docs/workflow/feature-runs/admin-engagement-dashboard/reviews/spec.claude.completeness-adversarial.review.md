---
reviewer: "claude"
lens: "completeness-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/admin-engagement-dashboard/spec.md"
artifact_sha256: "d848439036d0b0fd68345a85c8d295aefdc43f5286f5252c99c6a5208efa84d1"
repo_root: "."
git_head_sha: "47f8c864a2d8d78ce29635ef606af53b83e59bc3"
git_base_ref: "origin/main"
git_base_sha: "0a38ccf04bbb00ad4e47446f20ebd95638a0d4a1"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "Round 3. 16 findings (6 HIGH), all verified against real code. Decisive: the spec's call-site list was wrong - human agents are created at human_player.py:101 (not agents_create.py), players at 3 sites, first_connected_at at 6 including connection_activity.mark_seen which sets the field inside a values dict and is invisible to text search. Fixed STRUCTURALLY in revision 4 D3 by recording row-creation milestones from SQLAlchemy after_insert events instead of a hand-maintained call-site list. Also fixed: missing reached_at column (D1), agent_kind and source_match_id columns added so AC7 and AC17 are achievable, reset_handle added to the evidence-destruction table (D4)."
raw_output_path: "docs/workflow/feature-runs/admin-engagement-dashboard/reviews/spec.claude.completeness-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec completeness-adversarial

## Findings

### HIGH 1 — The human-agent creation site is not a named call site, so AC4 cannot be met [CODE-CONFIRMED]

`Agent(...)` is constructed in exactly three places in the app:

| Site | Kind |
|---|---|
| `app/routes/agents_create.py:181` | AI agent (web form) |
| `app/engine/human_player.py:98` | `AgentKind.HUMAN` |
| `app/engine/bots/seating.py:120` | `AgentKind.BOT` |

Build item 6 names **only `agents_create.py`** for the agent-create recorder. The human agent is created by `get_or_create_human_agent()` in a different module (`app/engine/human_player.py`), which `agents_create.py` never calls (`grep` shows its only production caller is `app/routes/web_play.py:31,301`).

This is the exact path the spec's own root-cause section calls "the default for every brand-new user". D1 defines `set_up_a_way_to_play` as "first `Agent` of kind `ai` **or** `human`", and AC4 requires a human player to be counted there — but no named call site can ever produce that row. The redesign's headline correctness claim is unimplementable from its own call-site list.

Fix: add `app/engine/human_player.py` (inside the `if agent is None:` branch at line 95-107, which is already the once-per-user-per-game choke point) to the call-site list and to build item 6.

---

### HIGH 2 — A second `Player` creation site is missed; the direct human-join route bypasses `web_join.py` [CODE-CONFIRMED]

`Player(...)` is constructed in three places:

| Site | Path |
|---|---|
| `app/routes/web_join.py:339` | AI agent seat (`_seat_user_agent`) |
| `app/routes/web_play.py:313` | human seat (`seat_human_player`) |
| `app/engine/bots/seating.py:151` | bot seat (internal) |

Build item 6 names **only `web_join.py`** for `joined_match`. `seat_human_player` lives in `web_play.py`, and it has **two** callers:

- `app/routes/web_join.py:510` (the join screen), and
- `app/routes/web_play.py:344` — the standalone `POST /games/{game}/matches/{match_id}/play/join` endpoint, whose own docstring says it "stays as the direct one-click path".

A recorder placed in `web_join.py` therefore misses every human who joins through the direct endpoint. Combined with HIGH 1, the human path — the spec's stated default — loses both of its milestones.

Fix: put the `joined_match` recorder inside `seat_human_player` (`web_play.py`) and `_seat_user_agent` (`web_join.py`), or in a shared helper both call.

---

### HIGH 3 — The "web" half of connection first-connect has no named file, and the MCP half has four branches, not one [CODE-CONFIRMED]

D3 says the recorder is called at "connection first-connect (web **and** MCP)", but build item 6 lists only `mcp_connection.py`. The web/runner/direct-API transition lives elsewhere:

- `app/engine/connection_activity.py:104,119` — `mark_seen()` computes `first = bot.first_connected_at is None` and stamps it. Its own docstring: *"Called from the single auth choke point (`require_connection`), so it covers every connection method (runner, MCP, direct API) with one hook."* It is imported by `app/deps.py:15` and `mcp_server/connection_identity.py:30`. This file appears nowhere in the spec.
- `app/engine/mcp_connection.py` stamps `first_connected_at` in **four** separate branches — lines 144-145 (match by `oauth_client_id`), 173-174 (match by provider), 205-206 (undeleted connection revived), and 222 (fresh `Connection(...)`). Treating `mcp_connection.py` as one call site means three of the four are missed.

So `ai_connected` — and with it AC5 ("an MCP user who connects before building an agent is counted at both") and AC7 (the AI-share denominator) — is wired to at most one of five real transition points.

---

### HIGH 4 — "Advisory, never blocking" is not achievable as specified; the repo's own code proves the hazard [CODE-CONFIRMED]

D1 asserts `UNIQUE(user_id, milestone)` makes a second write "a no-op, not an error", and D3 says the write is wrapped `# fail-open: advisory only`. Neither is free, and the spec names no mechanism.

Three code-confirmed problems:

1. **A real race exists on the hot path.** `app/engine/connection_activity.py:104` reads `first = bot.first_connected_at is None` in Python, then issues the UPDATE and `await db.commit()` (line 128). Two concurrent authenticated calls can both observe NULL, so both would call `record_milestone(ai_connected)` and one hits the UNIQUE constraint. `mark_seen` runs on **every** authenticated agent API call.
2. **Catching the `IntegrityError` on the shared session does not fail open — it breaks the caller.** After a failed flush, a SQLAlchemy session must be rolled back before any further statement. The repo already knows this: `app/engine/mcp_connection.py:267` wraps its colliding insert in `async with db.begin_nested():` (a SAVEPOINT) precisely so a collision does not poison the outer transaction, and `app/engine/match_creation.py:147-148` does `await db.rollback()` — which, called from a milestone helper, would discard the caller's staged rows (e.g. the `Player` rows `web_join.py:386` deliberately stages before commit). So the advisory wrapper either poisons the session or destroys the triggering write. The spec chooses neither.
3. **Idempotency is dialect-split and unspecified.** A SELECT-then-INSERT check races; `ON CONFLICT DO NOTHING` (Postgres prod) and `INSERT OR IGNORE` (SQLite dev/test) are different statements. AC2 and AC22 both depend on a mechanism the spec never states.

---

### HIGH 5 — AC17 (smoke-test exclusion) is unachievable under the write-time append-only model [CODE-CONFIRMED]

`TEST_NAME_PREFIX = "prod smoke"` (`app/match_naming.py:14`) is a **read-time** filter on `Match.name` (`app/read_models/admin_reports.py:109`, `~Match.name.ilike(...)`). D11 says to match it; AC17 says smoke-test matches "contribute nothing".

But `joined_match` and `played_turn` are written at event time into an append-only row keyed `UNIQUE(user_id, milestone)` with **no match reference of any kind** in the specced schema. Once written, no read-time filter can remove it. A user whose only activity was a smoke-test match permanently reads as having joined and played; a match renamed to the smoke prefix after the fact is likewise unfixable.

This is a consumer that revision 3 carried over from the derived-funnel model without re-checking whether the new storage can still satisfy it. Either the recorder must consult `is_test_match_name()` before writing (and the spec must say so), or AC17 must be scoped to the turn-count summary numbers only.

---

### HIGH 6 — `returned` cannot be both write-time recorded (D1/D3) and read-time timezone-parameterised (D11/AC16) [CODE-CONFIRMED]

D1 records `returned` when "a genuine submission on a second distinct **local day**" happens. D11 and AC16 say the second-distinct-day judgement uses **the page window's timezone**, defaulting to the browser's — a value chosen at render time, per admin, per page load.

A milestone row is written once and never revisited, so changing the page timezone cannot change the `returned` count. The two requirements are mutually exclusive, and the escape hatch is closed by the code: recomputing at read time needs `TurnSubmission.submitted_at`, and `app/engine/match_deletion.py:62,69` hard-deletes `TurnSubmission` rows — the deletion hole D1 exists to close.

The spec never says which timezone the recorder uses at write time. Pick one (UTC, or a stored `reached_at` plus a stored `first_played_local_date`) and correct D11/AC16, or the number silently means whatever the server's clock said.

---

### MEDIUM 7 — "the submission path" is four entry points across two game modules, and one of them serves humans and bots indiscriminately [CODE-CONFIRMED]

`record_submission` is implemented twice — `app/games/hoard_hurt_help/game.py:194` and `app/games/liars_dice/game.py:212` — and reached from four places:

| Caller | Who it is |
|---|---|
| `app/engine/agent_play.py:269` | AI agent (HTTP + MCP) |
| `app/engine/player_move.py:46` (`record_player_action`) | **web human AND scripted bot** |
| `app/engine/turn_drivers.py:136` / `:142` | bot driver / connector fallback |
| `app/engine/bots/service.py:173` | autopilot |

Putting the recorder in `record_submission` catches bots and autopilot, violating D5. Putting it in the callers means four sites, and `record_player_action` is called by both `web_play.py:253` (a genuine human) and `bots/service.py:124` (a scripted bot) with **no parameter distinguishing them** — the recorder would need its own agent-kind lookup. The spec's one-line "and the submission path" hides all of this.

Also note the liars-dice module is a second game whose submissions would count toward `played_turn`; the spec never says whether that is intended.

---

### MEDIUM 8 — The D4 backfill cannot reproduce D5's autopilot rule, so backfilled and live `played_turn` use different definitions [CODE-CONFIRMED]

D5 point 3 is correct that autopilot rows are not marked defaulted: `_auto_submit_autopilot` (`app/engine/bots/service.py:173`) calls `record_submission(...)` without `is_connector_fallback`, so `was_defaulted=False` (`hoard_hurt_help/game.py:220`). Live recording avoids them by not calling the recorder.

The backfill has no such luxury — it reads surviving rows, where an autopilot HOARD is indistinguishable from a genuine HOARD. The only available signal is `Player.autopilot_at` compared against `TurnSubmission.submitted_at`, which the spec never mentions. D4's disclaimer covers *deleted* rows ("floors, not totals"); it does not cover a **rule mismatch** that makes pre-deploy `played_turn` systematically higher than post-deploy for the same behaviour. The page's dated note would not explain the resulting step change.

---

### MEDIUM 9 — A fifth evidence-destroying site is missing from the root-cause table, and it kills `picked_handle` reconstruction [CODE-CONFIRMED]

`reset_handle` (`app/services/admin_user_actions.py:213-218`) sets `handle`, `handle_key` **and** `handle_changed_at` all to `None`. After an admin handle reset there is no surviving evidence the user ever picked a handle, so the D4 backfill cannot reconstruct `picked_handle` for that user. The spec's four-row deletion table does not list it, and D4's floor-disclaimer is framed entirely around row deletion, not field nulling.

Related, smaller: `app/routes/dev_login.py:56` sets `handle_key` at **user creation**, bypassing the `handle_web.py:161` call site entirely, so the dev user would never record `picked_handle` from the named site.

---

### MEDIUM 10 — Two more test files consume the `sync_google_user` signature and are not named [CODE-CONFIRMED]

Build item 12 names only `tests/test_mcp.py` (correct: it does hold exactly five two-parameter `fake_sync_google_user` fakes). But `sync_google_user` is also called directly by:

- `tests/test_auth_user_sync.py` — 7 call sites (lines 49, 60, 65, 78, 96, 108, 131). This is *the* test file for what `sync_google_user` writes, so it also needs new assertions for `is_internal` and `first_source_channel`.
- `tests/test_account_disabled.py` — 2 call sites (lines 138, 161).

If the new parameter is required, both break; either way `test_auth_user_sync.py` is the natural home for AC10/AC11/AC13 coverage and is unlisted.

---

### MEDIUM 11 — `user_milestones` has no timestamp column, but two of the three summary numbers are event-windowed [CODE-CONFIRMED]

D1 defines the table only by `(user_id, milestone)` plus `UNIQUE`. D2 counts over a "signup cohort" (window on `users.created_at`) — fine. But the summary line requires "users who played a genuine turn **in the window**" and "genuine turns **in the window**". Without a `reached_at` column on the milestone row, the first number must be computed from `turn_submissions`, which `app/engine/match_deletion.py:62,69` hard-deletes.

The page would then show a deletion-proof milestone count next to a deletion-prone window count, with the reconstructed-history note (AC19) attached to only one of them and no label saying which is which. Name the timestamp column and state which number reads which table.

---

### MEDIUM 12 — The transaction boundary for an advisory write is never chosen, and the join path proves it matters [CODE-CONFIRMED]

`app/routes/web_join.py:386` deliberately stages `Player` rows without committing ("The caller owns the transaction: staging here so the human seat seated afterward counts these rows via autoflush is deliberate — do not commit"), and `seat_human_player` can still raise `409 This match is full` afterwards (`web_play.py:299-300`). Meanwhile `app/routes/auth.py:106-107` flushes in `sync_google_user` and commits in the caller, while `mcp_server/oauth_auth.py:156` and `mcp_server/connection_identity.py:176` are separate callers with their own commit behaviour.

So a milestone written on the caller's session is silently rolled back when the request later fails, and one written on its own session records an event that never happened. D3 specifies neither, and AC2/AC22 do not distinguish them.

---

### MEDIUM 13 — First touch can be inherited across an account switch that never passes through sign-out [CODE-CONFIRMED]

Build item 3 clears `first_touch` in `clear_session`, and AC12 covers "cleared on sign-out". But `clear_session` (`app/auth/session.py:25-26`) pops only `SESSION_USER_KEY`, and it is called from exactly one place — `POST /auth/logout` (`auth.py:123`). Both sign-in paths bypass it: `google_callback` calls `set_session_user` directly (`auth.py:108`), as does `dev_login` (`dev_login.py:93`).

A second brand-new user signing in on the same browser without logging out therefore inherits the first visitor's `utm_source`. The test plan tests only the sign-out ordering.

---

### LOW 14 — D3 points at the wrong decision for the user-creation sites [CODE-CONFIRMED]

D3 says "user creation (3 places, D8)". D8 is the anonymous-cookie obligation; the three creation sites are tabulated in **D10** (`auth.py:41`, `bots/seating.py:51`, `dev_login.py:51`). A reader following the pointer lands on the wrong section.

---

### LOW 15 — The "fourth creation path is almost certainly `scripts/`" attribution is wrong, so the post-deploy check points at the wrong place [CODE-CONFIRMED]

D10 concludes the `.local` accounts must come from a path "almost certainly `scripts/`". Grepping `User(` across `scripts/` returns **nothing** — no script in the repo constructs a `User`. The three in-app sites plus direct DB seeding are the only candidates. The claim does no harm to the backfill, but D10 hangs a post-deploy verification step on it ("must be verified after deploy by reading back the flagged row count"), and the stated cause would send whoever does that verification hunting in the wrong directory.

---

### LOW 16 — The SSE stream route is not in the skip-prefix list [CODE-CONFIRMED]

D7 correctly retires revision 1's `/sse` prefix — no such route exists. But there *is* a long-lived event stream, mounted under a **non-skipped** prefix: `app/routes/sse.py:40` registers `GET /games/{game}/matches/{match_id}/stream` (plus a legacy alias at `:47`), included at `app/main.py:287`. `FirstTouchMiddleware` would run on it and, for a visitor whose first request in a session is a stream reconnect, could record the stream URL as `landing_path`. Add `/games/.../stream` handling (or skip requests whose `Accept` is `text/event-stream`) to the skip rule.

---

## Residual Risks

- **Middleware ordering (D7) re-verified and correct.** `app/main.py:240` adds `SessionMiddleware` first; `install_request_logging`, `OAuthRegistrationCompatMiddleware` and `CanonicalHostMiddleware` (`:259`) follow, and `add_middleware` inserting at position 0 makes the last-added outermost. A `FirstTouchMiddleware` added before line 240 sits inside `SessionMiddleware`, so `request.session` exists. No finding.
- **`AdminAction` capacity re-verified and correct.** `FlexibleEnumType` (`app/models/enum_types.py`) is `String`-backed, not a Postgres native enum, so `mark_internal` (13) and `unmark_internal` (15) need no `ALTER TYPE` and fit `length=16`. The one audit-log consumer (`app/templates/admin/user_detail.html:93`) renders `entry.log.action.value` raw, so no label map needs updating. No finding.
- **`colspan="7"` re-verified and correct** — `app/templates/admin/users_list.html:41`, the only one. No finding.
- **`admin_reports.py` line references re-verified** — `:109` is the `TEST_NAME_PREFIX` filter, `:182` is `submission.was_defaulted or submission.submitted_at is None`. Both as the spec describes.
- **Not assessed, and worth naming:** whether `record_milestone` should fire for `User` rows created by test fixtures (`tests/` constructs `User` directly in many files) — harmless for correctness but it will shape what the AC2/AC3 tests can assert.
- **Not assessed:** the read-model query shapes for the stuck list and the AI-share denominator (AC7) — the spec names the module but no query, so a plan-stage review should re-check the `ai_connected` denominator once "chose an AI agent" is defined in SQL (`Agent.kind == AI` vs. having a `Connection`, which differ for the MCP-first path).
- **Unchanged from round 2 and still open:** D8's privacy obligation. Nothing in this round changes it; flagging only so it is not lost in the redesign narrative.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "Human-agent creation site is not a named call site, so AC4 is unimplementable", "detail": "The kind='human' Agent is created at app/engine/human_player.py:98, not app/routes/agents_create.py:181 (the only agent-create site the spec names), so the spec's own default new-user path can never record set_up_a_way_to_play."}, {"severity": "HIGH", "title": "Direct human-join route bypasses the only named Player creation site", "detail": "Player rows are created at web_join.py:339, web_play.py:313 and bots/seating.py:151, and web_play.py:344 (POST /play/join) reaches seat_human_player without passing through web_join.py, so a joined_match recorder placed in web_join.py misses the one-click human join."}, {"severity": "HIGH", "title": "Web/runner first-connect file unnamed and MCP first-connect has four branches", "detail": "connection_activity.py:104,119 (mark_seen, the single auth choke point for runner/MCP/direct API) appears nowhere in the spec, and mcp_connection.py stamps first_connected_at at lines 144, 174, 206 and 222 rather than at one site."}, {"severity": "HIGH", "title": "Advisory milestone write has no session-safe mechanism, and the repo proves the hazard", "detail": "mark_seen races on first = bot.first_connected_at is None before committing, and the repo's own collision handling (mcp_connection.py:267 begin_nested, match_creation.py:148 rollback) shows a caught IntegrityError either needs a SAVEPOINT or destroys the caller's staged rows, while D1's 'no-op, not an error' names no ON CONFLICT / INSERT OR IGNORE mechanism across the Postgres/SQLite split."}, {"severity": "HIGH", "title": "AC17 smoke-test exclusion is unachievable under append-only write-time milestones", "detail": "TEST_NAME_PREFIX is a read-time filter on Match.name (admin_reports.py:109) but the milestone row carries no match reference, so a user whose only activity was a smoke-test match permanently counts at joined_match and played_turn with no later filter able to remove it."}, {"severity": "HIGH", "title": "'returned' cannot be write-time recorded and read-time timezone-parameterised at once", "detail": "D1 writes the milestone once at event time while D11/AC16 make the second-distinct-day judgement depend on a timezone chosen per page load, and recomputation at read time is blocked because match_deletion.py:62,69 hard-deletes the TurnSubmission rows it would need."}, {"severity": "MEDIUM", "title": "'The submission path' is four entry points across two game modules", "detail": "record_submission is implemented in both hoard_hurt_help/game.py:194 and liars_dice/game.py:212 and reached from agent_play.py:269, player_move.py:46, turn_drivers.py:136/142 and bots/service.py:173, and record_player_action serves both a genuine human (web_play.py:253) and a scripted bot (bots/service.py:124) with no discriminating parameter."}, {"severity": "MEDIUM", "title": "Backfill cannot reproduce D5's autopilot rule, so backfilled and live played_turn differ", "detail": "Autopilot rows are written with was_defaulted=False (bots/service.py:173), leaving no marker in surviving data, so the backfill would need an unmentioned Player.autopilot_at vs submitted_at comparison and otherwise produces a systematically different definition than live recording."}, {"severity": "MEDIUM", "title": "reset_handle is a fifth evidence-destroying site missing from the root-cause table", "detail": "admin_user_actions.py:213-218 NULLs handle, handle_key and handle_changed_at together, so picked_handle is unreconstructable by the D4 backfill for any admin-reset user, and dev_login.py:56 sets handle_key at creation, bypassing the named handle_web.py site."}, {"severity": "MEDIUM", "title": "Two further test consumers of the sync_google_user signature are unnamed", "detail": "Beyond tests/test_mcp.py, sync_google_user is called directly at 7 sites in tests/test_auth_user_sync.py (the test file for what it writes, and the natural home for AC10/AC11/AC13) and 2 sites in tests/test_account_disabled.py."}, {"severity": "MEDIUM", "title": "user_milestones has no timestamp column but two summary numbers are event-windowed", "detail": "'Users who played a genuine turn in the window' cannot be answered from a (user_id, milestone) row alone, so it falls back to turn_submissions, which match_deletion.py deletes, putting a deletion-proof count next to a deletion-prone one on the same page with no label."}, {"severity": "MEDIUM", "title": "Transaction boundary for the advisory write is never chosen", "detail": "web_join.py:386 deliberately stages Player rows uncommitted and can still 409 afterwards, while auth.py flushes and commits in the caller and the two mcp_server callers differ, so a milestone on the caller's session is silently rolled back and one on its own session records an event that never happened."}, {"severity": "MEDIUM", "title": "First touch is inherited across an account switch that skips sign-out", "detail": "clear_session (app/auth/session.py:25) pops only user_id and is called only from POST /auth/logout, while google_callback (auth.py:108) and dev_login (dev_login.py:93) call set_session_user directly, so a second brand-new user in the same browser inherits the first visitor's source."}, {"severity": "LOW", "title": "D3 points at D8 for the three user-creation sites", "detail": "The three creation sites are tabulated in D10 (auth.py:41, bots/seating.py:51, dev_login.py:51); D8 is the anonymous-cookie obligation."}, {"severity": "LOW", "title": "The 'fourth creation path is almost certainly scripts/' attribution is refuted by the code", "detail": "No file under scripts/ constructs a User row, so the post-deploy verification step D10 hangs on that claim would send the verifier to the wrong place."}, {"severity": "LOW", "title": "The SSE stream route is not covered by the skip-prefix list", "detail": "app/routes/sse.py:40 registers GET /games/{game}/matches/{match_id}/stream under a non-skipped prefix, so FirstTouchMiddleware runs on a long-lived event stream and could record the stream URL as landing_path."}]}
```

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Round 3. 16 findings (6 HIGH), all verified against real code. Decisive: the spec's call-site list was wrong - human agents are created at human_player.py:101 (not agents_create.py), players at 3 sites, first_connected_at at 6 including connection_activity.mark_seen which sets the field inside a values dict and is invisible to text search. Fixed STRUCTURALLY in revision 4 D3 by recording row-creation milestones from SQLAlchemy after_insert events instead of a hand-maintained call-site list. Also fixed: missing reached_at column (D1), agent_kind and source_match_id columns added so AC7 and AC17 are achievable, reset_handle added to the evidence-destruction table (D4).
