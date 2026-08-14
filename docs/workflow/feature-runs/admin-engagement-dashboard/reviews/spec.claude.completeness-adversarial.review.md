---
reviewer: "claude"
lens: "completeness-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/admin-engagement-dashboard/spec.md"
artifact_sha256: "8ec540f28d7639fd27b25fa4fd98b4a52f51ba38ac7c70da38d8bc10137ff8ca"
repo_root: "."
git_head_sha: "fae2fbf27abb626dc08c38f8bb1fe5ee7eea94a7"
git_base_ref: "origin/main"
git_base_sha: "0a38ccf04bbb00ad4e47446f20ebd95638a0d4a1"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "Round 2. 14 findings (3 HIGH), all verified before acceptance. Decisive: AgentKind has a third value HUMAN, and web_join._default_human_choice returns True for every brand-new user - manual play is the DEFAULT onboarding path, and under strict nesting a daily human player rendered as stuck at picked-a-handle. This finding is the single largest driver of the revision-3 redesign (D1/D2, AC4). Also fixed: tests/test_mcp.py's five two-parameter sync_google_user fakes added to scope; AdminAction now gains a paired unmark_internal; state.json constraints field corrected where it still asserted the withdrawn no-cookie non-goal; admin_user_actions.py and admin/dashboard.html added to the touch list; users_list colspan; dev@localhost seed domain; timezone default changed from UTC to browser."
raw_output_path: "docs/workflow/feature-runs/admin-engagement-dashboard/reviews/spec.claude.completeness-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec completeness-adversarial

## Findings

### HIGH

**H1. The funnel is blind to human players — the platform's *default* new-user path. [CODE-CONFIRMED]**

`AgentKind` has **three** values, not two: `AI = "ai"`, `BOT = "bot"`, and
`HUMAN = "human"` (`app/models/agent.py:17-24`). The word "human" appears nowhere
in the spec.

A human seat is a real `agents` row with `kind=HUMAN`, no `Connection` and
`provider=None`, created once per (user, game) by
`app/engine/human_player.py` — `Player.agent_id` is non-nullable
(`app/models/player.py:25`), so every human player has one. And this is not an
edge case: `app/routes/web_join.py:113-121`

```python
async def _default_human_choice(db: DbSession, user_id: int) -> bool:
    """Should "Play manually" start ticked? ...
    A brand-new user (no history) defaults to True — the no-setup path.
```

returns **True for every brand-new user**, and `web_join.py:230` comments that a
"new user with no AI can play as a human in one click."

Consequence, traced through D5/D7:

| Step | Human player |
|---|---|
| 3 — built an agent (`kind='ai'`) | **fails** (their agent is `kind='human'`) |
| 4 — AI connected | fails (`web_join.py:459`: "no agent, no connection") |
| 5/6/7 — joined, played, returned | **truncated by strict nesting**, even though the rows exist |

So a user who joins matches and plays real, non-defaulted turns every day renders
as *stuck at "picked a handle"*. That is the exact defect class the page exists to
find, inverted. D8's exclusion discussion only ever contrasts `ai` vs `bot`; every
other read model in the repo uses `Agent.kind != AgentKind.BOT`
(`app/read_models/matches.py:151,170`; `lobby_recent_views.py:33-37`), so the
funnel's `kind='ai'` is the outlier, not the convention.

Fix required in the spec: either add a human-seat branch to the ladder, or define
steps 3/4 as "has an AI agent **or** has played a human seat", and add a test.

**H2. Strict nesting silently deletes MCP users who connect before building an agent. [CODE-CONFIRMED]**

D5 fixed the funnel's *order*; it never says what happens when a user satisfies
steps 3 and 4 out of order — and D7's strict nesting makes that invisible rather
than obvious.

`app/engine/mcp_connection.py` creates the `Connection` and stamps
`first_connected_at` (lines 144-145, 173-174, 205-206, 222) from
`connection_identity._connection_from_token`, which does `sync_google_user` →
`mcp_connection_for` with **no agent lookup or check anywhere in the path**.
`PlaySetupStage` in `nav_context.py` is the *UI's suggested* order, not a data
constraint, and `/me/connections` is directly reachable. So "connected an AI, no
agent yet" is a reachable, common state (per the connect-screen design, the user
pastes the setup prompt into their client first).

Such a user has step 4 but not step 3, so D7 counts them at step 2 and drops them
from steps 4-7. The page then reports a drop at "built an agent" for people who
are demonstrably connected — inventing a leak, which is the same failure mode D5
was raised to prevent.

**H3. Summary number 4 cannot mean "24-hour snapshot", and it counts a population no other number on the page counts. [CODE-CONFIRMED]**

Two independent problems with "Incomplete connection setups **right now** —
labelled as a 24-hour snapshot (D6)":

1. **Nothing collects the rows when the admin looks.** `gc_pending_connections`
   is called from exactly two places, both *user-facing* pages:
   `app/routes/agents_create.py:112` (`/me/agents/new`) and
   `app/routes/connections_pages.py:94` (`/me/connections`). `/admin/engagement`
   will not call it. The number is therefore "incomplete setups not yet
   garbage-collected", which on a quiet week includes rows arbitrarily older than
   24 hours. The query cannot deliver the label unless the spec also specifies an
   explicit `created_at >= now - 24h` filter — which it does not.
2. **It structurally excludes the dominant connect path.** D6 itself establishes
   that `ConnectionSetup` is written in one place (the machine/key path) and that
   MCP users never create one. Meanwhile funnel step 4 counts `first_connected_at`
   for *both* paths. So the admin sees, say, "2 incomplete setups" beside a large
   agent→connected drop and concludes the drop is not a setup problem — when the
   MCP half of that drop is simply not countable by this number. The spec must
   label it machine-path-only or drop it.

Also unspecified and inconsistent with the spec's own rules: whether this number
is filtered by `is_internal` (D8: "Every query filters on that one column") and
whether it is distinct users or raw rows (D9: "Every count is distinct users,
never rows"). `ConnectionSetup.user_id` exists
(`app/models/connection_setup.py:19-21`), so one abandoned user can contribute
several rows.

### MEDIUM

**M4. Adding a first-touch argument to `sync_google_user` breaks five monkeypatched fakes. [CODE-CONFIRMED]**

Build item 3 gives `sync_google_user` an optional first-touch argument and has
both MCP callers pass `"mcp"`. `tests/test_mcp.py` replaces the function at module
scope five times (`monkeypatch.setattr(connection_identity, "sync_google_user",
...)` at lines 266, 340, 421, 518, 613) with

```python
async def fake_sync_google_user(db: object, userinfo: object) -> SimpleNamespace:
```

— exactly two positional parameters. Any extra argument at the call site raises
`TypeError` in all five tests. Neither the build item nor the test plan lists
`tests/test_mcp.py` among the files to touch.

**M5. One `AdminAction` value cannot record a two-way toggle. [CODE-CONFIRMED]**

D8 adds `mark_internal` only. Every other reversible admin action in the enum is a
**pair** — `disable`/`enable`, `promote`/`demote`
(`app/models/admin_audit_log.py:15-20`) — and `AdminAuditLog` has no column that
records the resulting value: only `actor_user_id`, `target_user_id`, `action`,
`reason`, `created_at`. `user_detail.html:93` renders
`{{ entry.log.action.value }}` verbatim, so the audit history would print
`mark_internal` identically for marking and for unmarking. There is no width
reason for a single value: the column is `FlexibleEnumType(AdminAction,
length=16)`, a plain `VARCHAR(16)`, and `unmark_internal` is 15 characters.

Consumers checked and clear: no label map or filter switch on the enum
(`app/models/__init__.py:6`, `admin_user_actions.py`, `user_detail.html`), no
`CHECK` constraint, and `tests/test_admin_audit_log.py` asserts values
individually rather than the whole member set — so no test breaks, which is
precisely why the missing pair would ship unnoticed.

**M6. `state.json`'s constraints field still asserts the superseded no-cookie non-goal. [CODE-CONFIRMED]**

D3 states the discovery non-goal "has been updated in `state.json`", and the
acceptance-criteria section says state.json is authoritative.
`discovery.non_goals[1]` is indeed marked SUPERSEDED — but
`discovery.checklist.constraints` still reads:

> "No third-party analytics service and **no anonymous visitor cookie, so no
> cookie-consent banner is introduced.**"

The plan and tasks stages read the checklist. The same file now says opposite
things about the one decision that was escalated to Chris.

**M7. The backfill rule and the creation rule for `is_internal` disagree — in both directions. [CODE-CONFIRMED]**

- **Backfill catches admins; creation does not.** Backfill = seed domains *plus
  any ADMIN-role user at migration time*. Creation at `auth.py:41` is the
  *domain rule only* (D8's own table). `promote_user`
  (`app/services/admin_user_actions.py:96-104`) never touches `is_internal`
  either. So "admin ⇒ internal" holds only for rows that already existed. A floor
  admin on a public domain who signs in after the migration — the session
  environment shows Chris's own address is `@gmail.com` — is created
  `is_internal=False` and counted as a real user by every number on the page.
- **Creation catches dev-login; backfill does not.** `dev_login._ensure_dev_user`
  creates `dev@localhost` (`app/routes/dev_login.py:31,51`), which D8 marks
  always-internal — but `localhost` is not among the seed domains
  (`agentludum.local`, `house.local`, `local.test`), so an existing dev-login row
  is left unflagged.

The test plan ("Backfill flags seed-domain accounts, leaves a gmail.com user
alone") exercises neither direction.

**M8. The toggle's build item skips `app/services/admin_user_actions.py`. [CODE-CONFIRMED]**

Build item 8 lists `admin_web.py` + `user_detail.html` + `users_list.html`. Every
existing admin action's logic lives in `app/services/admin_user_actions.py`, whose
module docstring spells out the shared four-part contract each helper honours:
optional row lock (`_load_target`), a no-op guard that returns *without* writing an
audit row, a floor-admin refusal where applicable, and exactly one `AdminAuditLog`
row in the same transaction. `admin_web.py`'s handlers are three lines each and
delegate. Omitting the service module from the file list means the toggle either
duplicates that contract or quietly skips it — notably the no-op guard, which is
what stops a double-click writing two audit rows.

**M9. D12's "same filter" does not actually make the two pages agree. [CODE-CONFIRMED]**

`load_turn_timing_report` filters on `Match.state == GameState.COMPLETED` **and**
windows on `Match.completed_at`, in addition to the name prefix
(`app/read_models/admin_reports.py:107-113`). The engagement page windows on
user/player/submission timestamps and must include in-flight matches. Copying only
`~Match.name.ilike(f"{TEST_NAME_PREFIX}%")` therefore does not achieve D12's stated
goal ("or the two admin pages report different numbers for the same week") — they
will differ anyway, for reasons the spec does not name.

Related mismatch on the same pair of pages: `/admin/reports` treats a real turn as
`submission.was_defaulted or submission.submitted_at is None` → skip
(`admin_reports.py:182`), while D10 specifies only `was_defaulted = false`.
`TurnSubmission.submitted_at` is nullable (`app/models/turn.py:73`) and the
defaulting writer sets it to `None` explicitly
(`app/games/hoard_hurt_help/scoring.py`, `submitted_at=None`), so the two
definitions are not interchangeable by construction.

**M10. First touch is thrown away for anyone whose first completed sign-in is the MCP OAuth flow. [CODE-CONFIRMED]**

Build item 3 hardcodes source `"mcp"` for users created via
`mcp_server/oauth_auth.py:156` and `mcp_server/connection_identity.py:176`, on the
stated grounds that the MCP callers "have no web session".

`oauth_auth._sync_signin_user` runs inside `_extract_upstream_claims` during a
**browser-driven** Google OAuth exchange served by our own app — the visitor's
first-touch session cookie exists on that request; it is just not reachable from
the FastMCP hook. So a visitor who lands on `/?utm_source=hermesagent`, browses,
then completes setup through Claude Code's MCP sign-in is attributed to `"mcp"`,
destroying exactly the answer ("did r/hermesagent work?") the feature was built to
produce. The spec should either record this as a known attribution ceiling, or
have the MCP path fall back to `"mcp"` only when no first touch is available.

### LOW

**L11. `users_list.html`'s empty-state `colspan` is a stale consumer of the column count. [CODE-CONFIRMED]**
`app/templates/admin/users_list.html:41` is `<tr><td colspan="7">No users
found.</td></tr>`, matching the 7 `<th>` in the header. Build item 8 adds an
"internal" column and never mentions the colspan, so the empty row will span the
wrong width.

**L12. The admin dashboard's own toolbar is a second nav surface the spec doesn't update. [CODE-CONFIRMED]**
Build item 7 adds "Engagement" only to `base.html:95`.
`app/templates/admin/dashboard.html:6-7,88` carries its own admin toolbar (Users,
Handles) plus a "Request incidents →" link — and `/admin/handles` and
`/admin/incidents` are reachable *only* from there. Adding the new page to one
surface but not the other leaves admin navigation inconsistent.

**L13. D11's timezone fix does not change the default behaviour. [CODE-CONFIRMED]**
The window controls are copied from `/admin/reports`, whose `_parse_timezone`
returns `timezone.utc` when `tz` is absent or blank (`admin_web.py:1373-1382` in
the provided context). So on a plain `/admin/engagement` load, "came back another
day" still evaluates in UTC — the exact US-evening double-count D11 describes is
the *default* result. The spec should either state a non-UTC default or say the
admin must set `tz`.

**L14. D8's creation-site table cites a path that does not exist. [CODE-CONFIRMED]**
The table lists `bots/seating.py:51`; the real file is
`app/engine/bots/seating.py:51`. Worth noting alongside it that
`get_or_create_bots_user` sets no `role`, so only the domain half of the backfill
rule reaches that account.

## Residual Risks

- **Session-cookie size and the SSE/stream path are untraced.** The skip list
  (`/static`, `/healthz`, `/api`, `/mcp`, `/openapi.json`) is correct for the
  routers in `app/main.py`, but the match stream lives at
  `/games/{game}/matches/{id}/stream` and is not skipped. Capture on a
  `text/event-stream` request would make `SessionMiddleware` emit `Set-Cookie` on a
  long-lived streaming response. Almost certainly harmless; not verified.
  [UNVERIFIED]
- **`FlexibleEnumType` coercion on an unknown value.** `_coerce_enum` raises
  `LookupError` for a value not in the enum. If `mark_internal` rows are written and
  the migration is later rolled back below the code, every `/admin/users/{id}`
  render that touches the audit log raises rather than degrading. Not a spec defect,
  but the "additive and reversible" claim in build item 2 covers the columns, not
  the enum data. [CODE-CONFIRMED, out of lens scope]
- **D2's middleware-ordering claim re-checked and holds.** `SessionMiddleware` is
  added first at `app/main.py:240`, then `install_request_logging` (which registers
  via `@app.middleware("http")`), `OAuthRegistrationCompatMiddleware`, and
  `CanonicalHostMiddleware` last/outermost. Adding `FirstTouchMiddleware` before the
  `SessionMiddleware` call does place it inside. No finding. [CODE-REFUTED as a
  defect]
- **`clear_session` has exactly one caller** (`app/routes/auth.py:123`), and no
  other code calls `request.session.clear()`, so build item 1's single-site fix for
  sign-out is complete. Dev login has no separate logout. No finding.
  [CODE-REFUTED as a defect]
- **Whether "played a turn" should count `turn_messages`.** `TurnMessage` carries
  its own `was_defaulted` / `submitted_at` (`app/models/turn.py:75-92`). A user who
  only ever talked would not count as having played. That is probably the intent,
  but the spec never says the talk phase was considered.
- **Whether the four summary numbers are individually `is_internal`-filtered.** D8
  says every query filters on the flag; the numbers are named but their filters are
  not restated per number. Only number 4 is demonstrably ambiguous (H3), but the
  same silence covers 1-3.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "Funnel is blind to human players, the default new-user path", "detail": "AgentKind has a third value HUMAN and web_join._default_human_choice returns True for every brand-new user, so a human who joins and plays real turns fails step 3 (kind='ai') and step 4 (connection) and is then truncated out of steps 5-7 by strict nesting, rendering as stuck at 'picked a handle'."}, {"severity": "HIGH", "title": "Strict nesting deletes MCP users who connect before building an agent", "detail": "mcp_connection.py stamps first_connected_at with no agent check anywhere in connection_identity._connection_from_token, so a connected-but-agentless user satisfies step 4 without step 3 and D7 drops them from steps 4-7, inventing a drop at 'built an agent'."}, {"severity": "HIGH", "title": "Summary number 4 cannot be a 24-hour snapshot and counts a population nothing else on the page counts", "detail": "gc_pending_connections runs only from /me/agents/new and /me/connections so the admin page never collects stale rows, and ConnectionSetup is machine-path-only (D6), so the number both overstates its recency and structurally excludes the MCP users whose drop it is meant to explain."}, {"severity": "MEDIUM", "title": "First-touch argument breaks five monkeypatched sync_google_user fakes", "detail": "tests/test_mcp.py replaces sync_google_user at lines 266, 340, 421, 518 and 613 with a two-positional-parameter fake, so passing an extra argument from the MCP callers raises TypeError in all five, and the file is not in the spec's touch list."}, {"severity": "MEDIUM", "title": "One AdminAction value cannot record a two-way toggle", "detail": "Every other reversible admin action in the enum is a pair and AdminAuditLog has no column for the resulting value, so mark_internal alone makes marking and unmarking render identically in user_detail.html; unmark_internal is 15 chars and fits the VARCHAR(16)."}, {"severity": "MEDIUM", "title": "state.json constraints still assert the superseded no-cookie non-goal", "detail": "discovery.non_goals[1] is marked SUPERSEDED but discovery.checklist.constraints still says 'no anonymous visitor cookie, so no cookie-consent banner is introduced', and the plan/tasks stages read the checklist."}, {"severity": "MEDIUM", "title": "is_internal backfill rule and creation rule disagree in both directions", "detail": "The backfill flags ADMIN-role users but auth.py:41 applies the domain rule only (and promote_user never sets the flag), while dev_login creates dev@localhost as internal even though 'localhost' is not among the backfill's seed domains."}, {"severity": "MEDIUM", "title": "Toggle build item omits app/services/admin_user_actions.py", "detail": "Every existing admin action's row lock, no-op guard, floor-admin refusal and single audit write live in that service module, so building the toggle in admin_web.py alone duplicates or skips that contract."}, {"severity": "MEDIUM", "title": "D12's shared filter does not make the two admin pages agree", "detail": "load_turn_timing_report also filters Match.state == COMPLETED and windows on Match.completed_at, and treats a real turn as non-defaulted AND submitted_at not null, so copying only the TEST_NAME_PREFIX filter leaves the two pages reporting different numbers anyway."}, {"severity": "MEDIUM", "title": "First touch is discarded when the first completed sign-in is the MCP OAuth flow", "detail": "oauth_auth._sync_signin_user runs inside a browser-driven OAuth exchange where the first-touch cookie exists but is unreachable, so a hermesagent visitor who sets up via Claude Code is attributed to 'mcp' — losing the exact answer the feature exists to produce."}, {"severity": "LOW", "title": "users_list.html empty-state colspan is stale after adding the internal column", "detail": "app/templates/admin/users_list.html:41 hardcodes colspan=\"7\" to match the current 7 headers, and build item 8 adds an eighth column without mentioning it."}, {"severity": "LOW", "title": "Admin dashboard toolbar is a second nav surface the spec does not update", "detail": "Build item 7 only touches base.html:95, but admin/dashboard.html carries its own admin toolbar and is the sole entry point for /admin/handles and /admin/incidents."}, {"severity": "LOW", "title": "D11's timezone fix leaves UTC as the default", "detail": "The copied _parse_timezone returns timezone.utc when tz is absent or blank, so on a plain page load 'came back another day' still evaluates in UTC — the exact US-evening double-count D11 describes."}, {"severity": "LOW", "title": "D8's creation-site table cites a nonexistent path", "detail": "The table lists bots/seating.py:51 when the real file is app/engine/bots/seating.py:51, and get_or_create_bots_user sets no role so only the domain half of the backfill rule reaches that account."}]}
```

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Round 2. 14 findings (3 HIGH), all verified before acceptance. Decisive: AgentKind has a third value HUMAN, and web_join._default_human_choice returns True for every brand-new user - manual play is the DEFAULT onboarding path, and under strict nesting a daily human player rendered as stuck at picked-a-handle. This finding is the single largest driver of the revision-3 redesign (D1/D2, AC4). Also fixed: tests/test_mcp.py's five two-parameter sync_google_user fakes added to scope; AdminAction now gains a paired unmark_internal; state.json constraints field corrected where it still asserted the withdrawn no-cookie non-goal; admin_user_actions.py and admin/dashboard.html added to the touch list; users_list colspan; dev@localhost seed domain; timezone default changed from UTC to browser.
