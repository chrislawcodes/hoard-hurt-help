# Requirements-adversarial review — spec.md (admin-engagement-dashboard)

## Findings

### HIGH 1 — The funnel's step order contradicts the product's own gate ladder, so the strict-nesting rule (D5) hides the very leak this feature exists to find. [CODE-CONFIRMED]

Spec §4 orders the steps `… picked a handle → started AI hookup → AI connected → built an agent → joined a match …`. The product enforces the opposite order for the last two. `app/routes/nav_context.py` defines the ladder as `NEEDS_HANDLE = 1`, `NEEDS_AGENT = 2`, `NEEDS_MCP_CONNECTION = 3`, and `_first_unmet_gate` returns `NEEDS_AGENT` **before** it ever looks at connections. Agent creation is additionally gated on a handle (`require_user_with_handle` at `app/routes/agents_create.py:106` and `:143`).

Consequence, under D5's "step N counts only if steps 1..N-1 cleared": the cohort the product actually produces — *built an agent, never connected an AI* — is counted at neither "AI connected" nor "built an agent". "Built an agent" is redefined as "connected **and** built", so it shows near-zero incremental drop. The page will report that the agent step is fine and blame the connection step for everything, which is the wrong instruction to Chris. This is the identical failure mode to the archived-agent bug D5 was written to fix, reintroduced by step ordering rather than by filtering.

Related gap in the same list: there is no step for *connection went live*. The product distinguishes `ProviderReadiness.CONNECTED_NOT_LIVE` / `SEEN_NOT_POLLING` from `LIVE` (`app/engine/connection_health.py`, consumed by `_READINESS_TO_FIRST_UNMET`), and per the spec's own "Why" the known sticking point is the CLI restart — i.e. connected-but-never-polling. The spec jumps from "AI connected" straight to "joined a match", so that cohort has nowhere to land.

**Fix:** state the step ladder as `handle → agent → connection → live → joined → played`, matching `PlaySetupStage`, and make the mapping from step to table/column an acceptance criterion.

### HIGH 2 — "Started AI hookup" does not exist for MCP sign-in users, so step 3 and (by strict nesting) every step below it collapses. [CODE-CONFIRMED]

The spec's "Why" names `connection_setups.completed_at` as the data behind "started AI hookup". `connection_setups` rows are created **only** by the machine/paste-in setup flow (`app/routes/connections_machine_setup.py:106`) and consumed by the header-key auth path (`app/deps.py:161-176`). The MCP sign-in path creates the `Connection` directly with `mcp_connected_at` and `first_connected_at` set and **no `ConnectionSetup` row at all** (`app/engine/mcp_connection.py:214-224`).

MCP sign-in is the primary connect path. So a normal user reads as: cleared "picked a handle", failed "started AI hookup", and therefore — by D5's strict subset rule — is excluded from "AI connected", "built an agent", "joined a match" and "played a turn", even though the DB shows all of them. The funnel dies at step 3 with a ~100% drop. The likely "fix" an implementer reaches for is to loosen the nesting rule, which reintroduces the funnel-goes-up bug.

**Fix:** define "started AI hookup" as a union over both paths (a `connection_setups` row **or** any `connections` row for the user), or drop the step; and add a test seeding an MCP-only user that asserts they appear at every later step.

### HIGH 3 — The abandoned-setup population is hard-deleted after 24 hours, so the number the feature is built to surface erases itself. [CODE-CONFIRMED]

`app/engine/pending_connection_gc.py` runs `delete(ConnectionSetup).where(completed_at IS NULL, created_at < now - 24h)` and `delete(Connection).where(status == PENDING, first_connected_at IS NULL, created_at < now - 24h)` — hard deletes, not soft. It is triggered on ordinary page loads: `app/routes/connections_pages.py:94` and `app/routes/agents_create.py:112`.

The spec's headline motivation is "30 of 178 connection setups were started and never completed". In production that count is a rolling ≤24-hour remnant, not history, and it shrinks every time any user opens `/me/connections`. Worse under D5: a user who started a hookup, got GC'd, and later connected fails "started AI hookup" and is therefore dropped from every subsequent step.

**Fix:** either measure started-hookup from a durable signal (a `users`-level or event-level marker written at first hookup attempt), or state explicitly that this step is a 24-hour snapshot and cannot be trended — and revise the motivation, because the dev-DB number does not reproduce in prod.

### HIGH 4 — "Played a turn" is undefined, and the obvious reading counts engine-generated no-shows as engagement. [CODE-CONFIRMED]

`app/engine/resolver.py:49` inserts a `TurnSubmission` with `submitted_at=None` for a player who never answered — the engine writes the row on the user's behalf. `TurnSubmission.submitted_at` is nullable (`app/models/turn.py:73`), and the existing report explicitly skips these rows: `if submission.was_defaulted or submission.submitted_at is None: continue` (`app/read_models/admin_reports.py:182`).

So "played a turn" = "a `turn_submissions` row exists" marks a user whose AI never responded at all as fully converted — the exact inverse of the truth, on the page whose job is to find where people fail. "Played a turn" = "`submitted_at IS NOT NULL`" is the correct reading, but the spec never says which, and the same ambiguity poisons "came back another day" (defined over turns) and the source table's "went on to play a turn" column.

**Fix:** define "played a turn" as a submission with `submitted_at IS NOT NULL` and `was_defaulted` false, name it in the acceptance criteria, and add a test seeding a defaulted-only player who must **not** count.

### HIGH 5 — The capture middleware gives every anonymous visitor a cookie, which is the thing the recorded non-goal exists to avoid, and it refutes D6's stated reasoning. [CODE-CONFIRMED]

`app/main.py:240` mounts Starlette's `SessionMiddleware` — a **signed client-side cookie** (`session_cookie="hhh_session"`), not server-side storage. Starlette emits `Set-Cookie` whenever the session dict is non-empty. Today the session is written only on sign-in-ish paths: `next_after_login` (`app/routes/auth.py:85`), the user id (`app/auth/session.py:22`), fresh connection keys, `agent_connected`. A plain anonymous page view sets **no** cookie today.

§1 writes `session["first_touch"]` on the first request of *any* session. Every anonymous visitor — and every crawler — now receives a cookie carrying `utm_source/medium/campaign`, referrer host and landing path. That is functionally a first-touch attribution cookie set without consent, and the recorded non-goal in `state.json` is: *"No anonymous visitor-ID cookie … would likely require a cookie-consent banner in the EU."* D6's justification ("capture only writes to a `users` row, so crawlers never appear") is refuted by the design in §1 — capture writes to the cookie on every request, crawlers included.

**Fix:** either get an explicit decision from Chris that a first-party attribution cookie for anonymous visitors is acceptable (and update the non-goal), or restrict the write to sessions that already exist / to the sign-in entry points, and rewrite D6.

### MEDIUM 6 — D5's "deleted rows still count" is false for matches and turns: they are hard-deleted, from an admin button. [CODE-CONFIRMED]

`app/engine/match_deletion.py::delete_match` issues `delete(TurnSubmission)`, `delete(TurnMessage)`, `delete(Turn)`, `delete(PlayerState)`, `delete(Player)`, `delete(Match)` — real deletes with no tombstone — and it is wired to `POST /admin/matches/{match_id}/delete` in `app/routes/admin_web.py`, one click from the admin dashboard. `Player.left_at` is a soft leave, but the delete path bypasses it entirely.

So "ever joined a match" and "ever played a turn" are destructible, and last month's funnel silently changes whenever an admin tidies up matches. That directly undercuts the reproducibility argument used to justify D8. The spec asserts an invariant the schema does not provide, and AC 3 would have an implementer write a test asserting a false property.

**Fix:** narrow D5 to "soft-deleted/archived rows still count; hard-deleted matches are gone", and note that funnel numbers are not reproducible across a match deletion.

### MEDIUM 7 — Nothing sets `is_internal` at account creation, and the code paths that mint internal accounts will keep producing unflagged rows. [CODE-CONFIRMED]

D8 says the flag is "set once at account creation and never recomputed", but §2 only specifies the column (`default False`) and §3 only adds a first-touch argument. No creation-time rule is stated anywhere. Meanwhile `app/engine/bots/seating.py:51` (`get_or_create_bots_user`) creates the internal `bots@agentludum.local` house user at runtime on demand — after the migration, in any environment where that row does not already exist, it lands with `is_internal = False` and counts as a real signup. `app/routes/dev_login.py:51` is a third creation site.

The migration backfill is a one-time snapshot of a population that regenerates. The admin toggle (D8) makes this repairable, not prevented.

**Fix:** specify the creation-time rule (same seed-domain/role test as the backfill, applied in a single shared helper used by every `User(...)` construction site), and name `app/engine/bots/seating.py` in the file list.

### MEDIUM 8 — `sync_google_user` has three callers; the spec touches one, so MCP-first signups record NULL source. [CODE-CONFIRMED]

`sync_google_user` is called from `app/routes/auth.py:106` (web OAuth), `mcp_server/oauth_auth.py:156`, and `mcp_server/connection_identity.py:176`. A user whose first-ever sign-in happens through an MCP client's OAuth has their `users` row created on one of the MCP paths — which have no browser session to read first touch from, and which the spec never mentions. Their source is silently NULL. §3's file list ("`app/routes/auth.py`") is incomplete for a change to that function's signature, against the repo's spec rule that a spec names exactly which files change.

**Fix:** list the two `mcp_server/` callers, state what they pass (`None`), and say explicitly that MCP-first signups have no source — so the read model can label them honestly rather than as "direct" (see MEDIUM 10).

### MEDIUM 9 — The "four summary numbers" are never named, and "previous-period comparison" is undefined for the page's own default window. [UNVERIFIED — artifact-internal]

§6 block 1 says "Four summary numbers with a previous-period comparison". Nowhere does the spec say which four. Yet AC 4 and the test plan both depend on them ("Internal users excluded from all four summary numbers"), so the test is unwritable as specified. Separately, the page "follows the existing `/admin/reports` shape", and that route accepts `start_date`/`end_date` as `None` meaning all-time (`app/routes/admin_web.py::admin_reports`). "Previous period" has no meaning for an unbounded window, and the spec sets no default window.

**Fix:** name the four numbers, define the default window (e.g. last 30 days), and define the previous period as the equal-length window immediately before it — or state that the comparison is suppressed when the window is unbounded.

### MEDIUM 10 — "Direct" will absorb every uncaptured signup, making the source table's largest row meaningless. [CODE-CONFIRMED]

D3's precedence is `utm_source → referrer_host → "direct"`. `users` has no source columns today (`app/models/user.py`), and §2 requires existing rows to stay NULL. Every pre-feature account, every MCP-path signup (MEDIUM 8), and every signup where the advisory capture failed open therefore derives as **"direct"**. At 10–20 users that bucket dominates the table and reads as "most signups come direct" when it means "we did not capture this". The feature's second question — which source produced the people who played — is answered wrongly on day one, silently.

**Fix:** require the read model to distinguish NULL (never captured) from captured-with-no-source, with distinct labels such as "unknown (pre-capture)" vs "direct", and add it to the test plan.

### MEDIUM 11 — Retention is defined in UTC days while the page's window controls are timezone-aware; for a US reader one evening session counts as "came back". [CODE-CONFIRMED]

§4 defines "came back another day" as "played turns on 2 or more distinct **UTC** calendar days". The sibling page it is modelled on does the opposite: `admin_reports` takes a `tz` parameter and converts local day boundaries to UTC before filtering (`app/routes/admin_web.py`, `_parse_timezone` / `datetime.combine(...).astimezone(timezone.utc)`).

00:00 UTC is 17:00 PDT. A single continuous evening play session from a US user crosses UTC midnight and scores as two distinct days — i.e. as retention. The final funnel step, the one that says whether anyone actually came back, is systematically inflated for the timezone most alpha users are in.

**Fix:** bucket days in the same timezone the window controls use, and add a test with two submissions 30 minutes apart spanning UTC midnight that must **not** count as returning.

### MEDIUM 12 — The acceptance criteria drifted from the authoritative list. [CODE-CONFIRMED]

The spec says "Authoritative list is the discovery checklist (11 items) in `state.json`", then prints thirteen items in the order 1,2,3,4,**12,13**,5,6,7,8,9,10,11. The two inserted items — D9 distinct-user counting and the D8 `is_internal` toggle — do **not** appear in `state.json`'s `acceptance_criteria` array. If the implement stage follows the artifact the spec calls authoritative, the two decisions added by the previous review round are unenforced. `state.json` AC 4 also still describes the rejected email-based exclusion ("platform admins plus test accounts are excluded behind a single shared filter"), not the stored flag D8 replaced it with.

**Fix:** update `state.json`'s `acceptance_criteria` to the 13 items and renumber the spec list, or drop the "authoritative" claim and make the spec the single source.

### LOW 13 — The middleware skip list names a path that does not exist and misses the real streaming route. [CODE-CONFIRMED]

§1 skips `/static`, `/healthz`, `/api`, `/mcp`, `/sse`. There is no `/sse` route: the SSE endpoints are `GET /games/{game}/matches/{match_id}/stream` and `GET /games/{match_id}/stream` (`app/routes/sse.py:40,47`). The list should be checked against the real router prefixes registered in `app/main.py`.

### LOW 14 — D1 describes the session as server-side; it is a signed client cookie, and the spec's own risk table says so. [CODE-CONFIRMED]

D1: "the value rides in the existing **server-side** session". `app/main.py:240` mounts Starlette `SessionMiddleware`, which is a signed cookie. The Risks table then correctly says "Session is a signed cookie". A decision record that is wrong about its own mechanism will mislead whoever sizes the payload or reasons about tampering (a visitor can set their own attribution by editing the URL — acceptable, but it should be stated, not implied by a wrong premise).

### LOW 15 — The stuck-people list has no defined label, and the stuckest users are exactly the ones with no handle. [CODE-CONFIRMED]

`User.handle` is NULL until the user picks one, and "picked a handle" is funnel step 2 — so users stuck at step 1 have no handle by definition. The spec says the list "names each user" without saying what the name is. `base.html` already uses the `@handle` else `email` pattern for this. The list also has no cap, unlike every other admin list in the codebase (users 50/page, incidents 200, audit 50).

### LOW 16 — The module the spec says to follow excludes smoke-test matches; the spec has no equivalent. [CODE-CONFIRMED]

`app/read_models/admin_reports.py:109` filters `~Match.name.ilike(f"{TEST_NAME_PREFIX}%")` (`TEST_NAME_PREFIX = "prod smoke"`). The engagement spec claims to follow that module's pattern but has no match-level exclusion, so a real user seated in a prod-smoke match counts as having joined a match and played a turn — and the two admin pages will disagree about the same matches.

## Residual Risks

- **Migration downgrade discards admin corrections.** §2 calls the downgrade clean because it drops `is_internal`. A downgrade/re-upgrade cycle also re-runs the seed-domain guess, silently discarding every manual toggle made via the D8 action. Worth one sentence in the migration section.
- **Seed-domain suffix matching is loose.** "Match on the domain suffix only" against `local.test` also matches `notlocal.test`. Anchor on `@` + domain.
- **Users promoted to platform admin after the migration stay `is_internal = False`.** `sync_google_user` re-applies the ADMIN role on every login from `PLATFORM_ADMIN_EMAILS` (`app/routes/auth.py:1240-1241` in the provided context), but the backfill only flags admins *at migration time*. Repairable via the toggle; not stated.
- **Disabled accounts are not addressed.** `users.disabled_at` exists and disabled users still sit in every cohort. Probably correct (they did sign up), but it should be a stated decision, not an accident.
- **Session cookie growth is capped but not budgeted.** Five fields at 120/120/120/255/255 plus JSON and base64 overhead is roughly 1.2 KB of a ~4 KB cookie, sharing space with `next_after_login`, per-connection fresh-key entries, and authlib's OAuth state. Worth an explicit byte budget rather than "five short strings only".
- **`base.html` placement detail.** AC 1 says the link sits "alongside Match Admin, Reporting and Users", but `Users` is outside the `Platform admin` submenu in the current template; only Match Admin and Reporting are inside it.
- **D2's middleware ordering claim is correct** — verified, not a finding. `add_middleware` inserts at index 0, `SessionMiddleware` is added first at `app/main.py:240` and is therefore innermost; adding `FirstTouchMiddleware` before that call leaves it inside the session. The stated risk is real and the stated remedy works.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "Funnel step order contradicts the product's gate ladder", "detail": "The spec puts 'built an agent' after 'AI connected' while nav_context.PlaySetupStage forces agent (2) before connection (3), so under strict nesting the built-an-agent-never-connected cohort vanishes and the drop is blamed on the wrong step."}, {"severity": "HIGH", "title": "'Started AI hookup' does not exist for MCP sign-in users", "detail": "app/engine/mcp_connection.py:214 creates the Connection with no ConnectionSetup row, so MCP users fail step 3 and strict nesting drops them from every later step even though the DB shows they played."}, {"severity": "HIGH", "title": "Abandoned setups are hard-deleted after 24 hours", "detail": "gc_pending_connections deletes incomplete ConnectionSetup and pending Connection rows older than 24h on ordinary page loads, so the started-but-never-finished number the feature exists to surface erases itself in prod."}, {"severity": "HIGH", "title": "'Played a turn' is undefined and counts engine-generated no-shows", "detail": "resolver.py:49 writes a TurnSubmission with submitted_at=None for a player who never answered, so a row-exists reading marks a user whose AI never responded as fully converted."}, {"severity": "HIGH", "title": "Capture cookies every anonymous visitor, against the recorded non-goal", "detail": "Starlette SessionMiddleware is a client cookie set only on sign-in paths today; writing first_touch on the first request of any session gives every visitor and crawler an attribution cookie, which is what the no-visitor-cookie non-goal exists to avoid and refutes D6's reasoning."}, {"severity": "MEDIUM", "title": "D5's 'deleted rows still count' is false for matches and turns", "detail": "delete_match hard-deletes Player, Turn and TurnSubmission rows from an admin button, so 'ever joined'/'ever played' is destructible and past funnels do not reproduce."}, {"severity": "MEDIUM", "title": "No rule sets is_internal at account creation", "detail": "The column defaults to False and get_or_create_bots_user (seating.py:51) mints the internal house user at runtime, so internal accounts created after the migration silently count as real signups."}, {"severity": "MEDIUM", "title": "sync_google_user has three callers; the spec touches one", "detail": "mcp_server/oauth_auth.py:156 and mcp_server/connection_identity.py:176 also create users, so an MCP-first signup records a NULL source and the spec's file list is incomplete."}, {"severity": "MEDIUM", "title": "The four summary numbers and the previous period are undefined", "detail": "AC 4 and the test plan reference 'all four summary numbers' that the spec never names, and 'previous-period comparison' has no meaning for the unbounded default window the /admin/reports shape allows."}, {"severity": "MEDIUM", "title": "NULL source is conflated with 'direct'", "detail": "Pre-existing users, MCP-path signups and fail-open captures all derive to 'direct' under D3, so the source table's biggest row means 'not captured' while reading as a real answer."}, {"severity": "MEDIUM", "title": "Retention uses UTC days while the window controls are timezone-aware", "detail": "00:00 UTC is 17:00 PDT, so one continuous evening session spans two UTC days and inflates 'came back another day' for exactly the users being measured."}, {"severity": "MEDIUM", "title": "Acceptance criteria drifted from the authoritative list", "detail": "The spec calls state.json's 11-item list authoritative then prints 13 items, and the two added by the last review round (D9 distinct counting, D8 toggle) are absent from state.json."}, {"severity": "LOW", "title": "Middleware skip list names a nonexistent /sse path", "detail": "The real streaming routes are /games/{game}/matches/{match_id}/stream and /games/{match_id}/stream in app/routes/sse.py, so the skip list should be rebuilt from the registered router prefixes."}, {"severity": "LOW", "title": "D1 calls the session server-side; it is a signed cookie", "detail": "app/main.py:240 mounts Starlette SessionMiddleware, and the spec's own risk table contradicts D1 on this point."}, {"severity": "LOW", "title": "Stuck-people list has no defined label and no cap", "detail": "Users stuck before the handle step have handle=NULL by definition, and every other admin list in the codebase is paginated or limited."}, {"severity": "LOW", "title": "No smoke-test match exclusion", "detail": "admin_reports.py:109 excludes matches named with TEST_NAME_PREFIX ('prod smoke'); the engagement spec claims to follow that module but omits any equivalent, so the two admin pages will disagree."}]}
```
