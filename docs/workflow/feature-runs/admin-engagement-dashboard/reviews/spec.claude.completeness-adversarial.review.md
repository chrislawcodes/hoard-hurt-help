---
reviewer: "claude"
lens: "completeness-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/admin-engagement-dashboard/spec.md"
artifact_sha256: "dc6a588aa10055bdc51aceba37457ef383aadca7a1008975c62a2c3bbc122bc0"
repo_root: "."
git_head_sha: "e690920f0740d72d7667fffd85e00cbd482fcb2e"
git_base_ref: "origin/main"
git_base_sha: "0a38ccf04bbb00ad4e47446f20ebd95638a0d4a1"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "12 findings (5 HIGH). All 5 HIGH verified against real code before acceptance. Fixed in spec revision 2: gate-ladder order (D5); ConnectionSetup GC and the MCP path bypassing it (D6); sync_google_user's three callers, with MCP signups now labelled 'mcp' rather than silently 'direct' (build item 3); is_internal toggle moved outside the floor_admin gate that would have hidden it on exactly the flagged accounts (D8); is_internal now set at all three User() creation sites (D8). Also fixed: two disagreeing played-a-turn sources resolved to turn_submissions (D10), smoke-test exclusion (D12), session-cookie truncation at capture time (D3), AdminAction closed-enum extension and an internal column on /admin/users (D8), first_touch cleared on sign-out (build item 1). Accepted as a documented limit, not fixed: admin match-delete and handle-reset make past funnels non-reproducible (D7) - an append-only event log is out of scope and is now a recorded non-goal."
raw_output_path: "docs/workflow/feature-runs/admin-engagement-dashboard/reviews/spec.claude.completeness-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec completeness-adversarial

## Findings

### 1. HIGH — The funnel's step order contradicts the product's own onboarding ladder, and D5's nesting rule turns that into a silent undercount [CODE-CONFIRMED]

The spec's funnel is `signed up → picked a handle → started AI hookup → AI connected → built an agent → joined a match → …`.

The app's real gate ladder is the opposite for two of those rungs. `app/routes/nav_context.py:55-69`:

```
NOT_SIGNED_IN = 0
NEEDS_HANDLE = 1
NEEDS_AGENT = 2
NEEDS_MCP_CONNECTION = 3
NEEDS_LIVE = 4
```

and `_first_unmet_gate` (`app/routes/nav_context.py:245-261`) enforces it: handle, **then agent**, then connection. A user is sent to build an agent *before* they are asked to connect an AI.

Combined with D5 ("a user counts at step N only if they cleared steps 1..N-1"), every user who followed the product's actual order — handle, agent built, AI not yet connected — is truncated at "picked a handle". They vanish from "built an agent" even though they built one. The result:

- "built an agent" is systematically understated;
- the largest drop is manufactured at "started AI hookup" regardless of the truth;
- the page renders cleanly and the nested-set regression test in AC2 still passes, because the sequence is still non-increasing.

This is the exact failure mode the spec was written to prevent, reproduced in the spec's own step list. Either reorder the funnel to `handle → built an agent → started AI hookup → AI connected`, or state explicitly why the dashboard measures a different order than the product enforces.

### 2. HIGH — "Started AI hookup" reads a table that production garbage-collects every 24 hours [CODE-CONFIRMED]

`app/engine/pending_connection_gc.py:24-36` hard-deletes exactly the rows this step counts:

```python
delete(ConnectionSetup).where(
    ConnectionSetup.completed_at.is_(None),
    ConnectionSetup.created_at < cutoff,      # cutoff = now - 24h
)
```

and, right after it, `delete(Connection).where(status == PENDING, first_connected_at.is_(None), created_at < cutoff)`.

This is not dormant code. It runs on every visit to the connections page (`app/routes/connections_pages.py:94`) and on every agent creation (`app/routes/agents_create.py:112`), so in prod it fires constantly.

The rows it deletes are precisely the abandoned hookups — the leak the whole feature exists to surface, and the source of the spec's own headline evidence ("30 of 178 connection setups were started and never completed"). Twenty-four hours after the fact, a started-but-never-completed setup no longer exists, so "started AI hookup" collapses toward "AI connected" and the drop the dashboard was built to show reads as approximately zero, forever.

AC3 / D5 ("ever did X — archived and deleted rows still count") is **not achievable** for this step against a GC'd table. The spec must name a surviving signal (a tombstone column instead of a delete, an audit/event row, or `connections.created_at` as the proxy) or state on the page that this step only covers the last 24 hours. Note that the spec's dev-DB measurement of 178 setups was taken from a database where the GC may not have been running, so the prod number is likely much smaller than the design assumed.

### 3. HIGH — Two admin actions hard-erase the evidence the "ever did X" steps read [CODE-CONFIRMED]

D5 promises step membership survives archiving and deletion. Two existing code paths break that promise by destroying the rows outright, and both are one click away on pages the spec already touches.

**`delete_match`** (`app/engine/match_deletion.py:54-82`) issues real `DELETE`s against `TurnSubmission`, `TurnMessage`, `Turn`, `PlayerState` and `Player`. The button is on the admin dashboard the spec's menu change sits beside (`app/templates/admin/dashboard.html:32`, route at `app/routes/admin_web.py:146`). After an admin deletes a match, a user who genuinely joined it and played turns loses every `players` and `turn_submissions` row. They silently fall back to "built an agent" — and, because the stuck list is "each non-returning user with the furthest step they reached", they get **named on the stuck-people list as someone who never played**. Last month's funnel also stops reproducing.

**`reset_handle`** (`app/services/admin_user_actions.py:126-139`) nulls `handle`, `handle_key` *and* `handle_changed_at`. Nothing on the `users` row then records that a handle was ever picked, so the user drops out of "picked a handle" — and strict nesting truncates them to step 1, removing them from every later step too. The only surviving trace is the `AdminAuditLog` row with `action='handle_reset'`, which the spec never mentions reading.

The spec should either define the ever-did-X predicates against sources that survive these deletes, or state the limitation on the page.

### 4. HIGH — `sync_google_user` has three callers, not one, and the two the spec missed create accounts that get silently labelled "direct" [CODE-CONFIRMED]

Spec §3 modifies `sync_google_user` and names only `app/routes/auth.py`. The real call sites:

| Call site | Creates users rows? | Carries first touch under the spec? |
|---|---|---|
| `app/routes/auth.py:106` | yes | yes |
| `mcp_server/oauth_auth.py:156` (`_sync_signin_user`, driven by `_bootstrap_signin_connection_from_idp` inside the MCP OAuth token exchange) | yes | no |
| `mcp_server/connection_identity.py:176` | yes | no |

An account whose first-ever sign-in is the MCP OAuth flow is created with all five source columns NULL. Under D3's precedence (`utm_source` → `referrer_host` → `"direct"`), NULL renders as **"direct"** — so an MCP-originated signup is affirmatively attributed to direct traffic rather than reported as unknown. The middleware cannot rescue it either: §1's skip list explicitly excludes `/mcp`.

Two required changes the spec does not state: (a) the new parameter must be keyword-optional and the two MCP call sites named as deliberately not passing it, and (b) the derived label must distinguish "no source recorded" from "direct", or every gap in capture reads as a real direct signup.

### 5. HIGH — The `is_internal` toggle has no render path, and the template gates the toolbar against exactly the accounts D8 flags [CODE-CONFIRMED]

Spec §8 lists only `app/routes/admin_web.py`. The button has to live in `app/templates/admin/user_detail.html`, which the spec never names — without that edit AC13 is unreachable, since there is no way to POST to the new route and no way to see the current flag state (the badge row at `user_detail.html:6-12` shows admin / disabled / floor-admin, not internal).

Worse, the entire toolbar is wrapped in `{% if not floor_admin %}` (`user_detail.html:16-42`), falling through to "Floor admin — actions disabled." D8's backfill flags **every user whose role is ADMIN**, which is the group most overlapping with floor admins — so for a floor admin the toggle would be rendered unusable by an existing guard the spec does not mention. The spec must say whether `is_internal` is exempt from the floor-admin lockout (it is a reporting flag, not a privilege change, so it probably should be).

### 6. MEDIUM — `is_internal` is set only by a one-time backfill; two live code paths write `users` rows that never set it [CODE-CONFIRMED]

- `app/engine/bots/seating.py:45-58` — `get_or_create_bots_user()` lazily creates `bots@agentludum.local` (sentinel sub `platform:bots`) the first time an admin seats platform bots.
- `app/routes/dev_login.py:45-61` — `_ensure_dev_user()` creates `dev@localhost`, a domain **not** in the seed list (`agentludum.local`, `house.local`, `local.test`), so it is never flagged in any environment.

Today's prod rows do match the seed domains, so the backfill covers the current data — this is a forward-looking gap rather than a day-one wrong number, hence MEDIUM. But the flag has no enforcement at any write site, so every future internal row (a re-created bots user in a fresh environment, a new harness account, the dev user in the dev DB the spec's own measurements came from) silently enters the funnel as a real signup and can appear by name in the stuck list. Set the flag where the row is written — the bots user is identifiable by its sentinel `google_sub`, not just its domain.

### 7. MEDIUM — "Played a turn" has two candidate sources in the codebase that disagree, and the spec picks neither [CODE-CONFIRMED]

The spec's Why section names both `connections.turns_played` and `turn_submissions.submitted_at` as available data; §4 and §5 never say which one backs the "played a turn" step or the source table's "went on to play" column.

They diverge under a path already confirmed in finding 3: `delete_match` deletes `turn_submissions` rows, while `connections.turns_played` is a monotonic counter (`app/engine/connection_activity.py:199-209`, incremented at `app/engine/agent_play.py:293`) that no deletion path decrements. After any match deletion the two sources give different answers for the same user. If the funnel uses one and a summary number uses the other, two numbers on the same page contradict each other — the risk the spec's own risk table calls out but only guards for the internal-user filter.

Also note `turns_played` lives on `connections`, and the GC in finding 2 hard-deletes pending connections, so it is not a strictly safe fallback either.

### 8. MEDIUM — Deploy smoke-test matches are not excluded, so the page will disagree with `/admin/reports` and the public views [CODE-CONFIRMED]

`app/match_naming.py` defines `TEST_NAME_PREFIX = "prod smoke"` and documents that "several read models and the public front door need to recognise and exclude these." `load_turn_timing_report` — the very module the spec says to follow — applies it at `app/read_models/admin_reports.py:109`:

```python
~Match.name.ilike(f"{TEST_NAME_PREFIX}%")
```

The spec's shared filter covers only `users.is_internal` and `agents.kind != 'ai'`. Nothing excludes smoke-test matches from "joined a match", "played a turn", or the source table's played column. The result is an engagement page whose play numbers are higher than `/admin/reports` and the leaderboard for the same window, with no stated reason. Decide explicitly and state it.

### 9. MEDIUM — Truncation is specified only "before it reaches the DB", but the session cookie is a consumer too [CODE-CONFIRMED]

§1 says "Truncates every stored string to its column length before it reaches the DB." The session is a **signed cookie**, not server-side storage (`app/main.py:240-246`, `SessionMiddleware(..., session_cookie="hhh_session")`), so whatever the middleware puts in `session["first_touch"]` is serialised into that cookie on the very first request.

An untruncated, attacker-supplied `?utm_source=<8KB>` therefore inflates the cookie past the 4KB browser limit; the browser silently drops `hhh_session`, and the visitor can no longer hold a session at all — including the one that carries `next_after_login` and the OAuth state. Because §1 wraps capture as `# fail-open: advisory only`, nothing logs. Truncate at capture time, before the value enters the session, and state a total byte budget for `first_touch`.

### 10. MEDIUM — The audit-log action enum is closed, length-capped, and rendered raw; §8 does not account for any of it [CODE-CONFIRMED]

`AdminAction` (`app/models/admin_audit_log.py:15-20`) is `{disable, enable, promote, demote, handle_reset}`, persisted through `FlexibleEnumType(AdminAction, length=16)`. `_coerce_enum` raises `LookupError` on any unknown value (`app/models/enum_types.py:31-43`), so writing an audit row with a name that is not a member fails at the write, not at read.

§8's "written to the existing `AdminAuditLog` like its neighbours" therefore requires: new enum members, names that fit **16 characters** (`mark_internal` and `unmark_internal` fit; anything longer does not), and — since `user_detail.html:93` prints `entry.log.action.value` verbatim — the chosen strings are admin-visible copy. Whether the enum's storage is a plain string column or a Postgres enum also decides whether a migration is needed; the spec's migration section covers only the `users` columns.

### 11. MEDIUM — Nothing lists which accounts are flagged internal, so the mis-flag D8 exists to fix cannot be found [CODE-CONFIRMED]

D8 justifies the toggle with "fixing a mis-flagged account means hand-editing production." The toggle alone does not solve that: `admin_users_list` (`app/routes/admin_web.py:160-200`) selects users ordered by `created_at` with an email/handle search only, and `admin/users_list.html` has no internal column and no filter. To find the one wrongly-flagged real user among a page of 50, an admin would open each detail page in turn. Add the flag to the list view (a column plus an "internal only" filter), or the correction path is theoretical.

### 12. LOW — First touch survives sign-out, so a second signup in the same browser inherits the first person's source [CODE-CONFIRMED]

`clear_session` (`app/auth/session.py:25-26`) pops only `SESSION_USER_KEY`; it does not clear the session. `session["first_touch"]` therefore persists across logout, and §1's "never overwrite an existing value" rule means the next person to sign up in that browser is attributed to the *previous* visitor's UTM tag. Low frequency, but it is a wrong row in the source table with no way to detect it after the fact. Clearing `first_touch` in `clear_session` is a one-line fix; either do it or record the decision.

## Residual Risks

- **Middleware ordering (D2) checks out.** `SessionMiddleware` is added at `app/main.py:240` and is the first `add_middleware` call after `FastAPI(...)` at line 225; the "Outermost:" comment on the last-added `CanonicalHostMiddleware` (line 253) confirms the last-added-is-outermost convention the spec relies on. Adding `FirstTouchMiddleware` between lines 238 and 240 gives the required inner position. No finding.
- **Disabled accounts.** `users.disabled_at` exists and admins use it, but the spec never says whether a disabled user still counts as a signup. Whichever way it goes, it should be stated so the denominator is defensible.
- **Public-facing counts will not match.** The leaderboard and lobby read models (`app/read_models/leaderboard.py`, `lobby_recent_views.py`) have no notion of `is_internal`. The public site and the dashboard will report different player populations. That is consistent with the "no public-facing changes" non-goal, but it should be an explicit, documented divergence rather than a surprise.
- **Session lifetime caps the capture window.** `SessionMiddleware` is configured without an explicit `max_age`, so Starlette's 14-day default applies. A visitor who lands with a UTM tag and signs up more than 14 days later records nothing. Almost certainly acceptable; worth one line in the spec so the gap is known rather than discovered.
- **Admin nav tests.** `tests/test_admin.py` and `tests/test_role_simplification.py` both assert against the platform-admin submenu that §7 changes. Not verified line-by-line here; the implementer should expect to update them, and AC1 does not mention it.
- **Backfill verification is stated but not specified.** §2 requires reading back the flagged row count after deploy, but gives no expected number. Per the repo's data-critical rule, the spec should carry the actual list of internal accounts in prod so the post-deploy check has something to compare against.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "Funnel step order contradicts the product's real onboarding ladder", "detail": "The app gates handle -> agent -> connection (app/routes/nav_context.py:55-69, 245-261) but the spec's funnel puts 'built an agent' after 'AI connected', so D5's strict nesting truncates every user who followed the product's own order and silently understates the agent step."}, {"severity": "HIGH", "title": "'Started AI hookup' reads a table production garbage-collects after 24 hours", "detail": "app/engine/pending_connection_gc.py:24-36 hard-deletes exactly the incomplete ConnectionSetup rows this step counts, and it runs on every connections-page visit and agent creation, so the drop the dashboard exists to show decays to zero and AC3's 'ever did X' is unachievable for that step."}, {"severity": "HIGH", "title": "delete_match and reset_handle hard-erase the evidence the 'ever did X' steps read", "detail": "app/engine/match_deletion.py:54-82 deletes players and turn_submissions and app/services/admin_user_actions.py:126-139 nulls handle, handle_key and handle_changed_at, so real players silently fall back down the funnel and get named on the stuck-people list."}, {"severity": "HIGH", "title": "sync_google_user has three callers; the two MCP ones create accounts labelled 'direct'", "detail": "mcp_server/oauth_auth.py:156 and mcp_server/connection_identity.py:176 also create users rows, the middleware skips /mcp, and D3 renders a NULL source as 'direct', so MCP-originated signups are affirmatively misattributed to direct traffic."}, {"severity": "HIGH", "title": "The is_internal toggle has no render path and is blocked by the floor-admin guard", "detail": "Spec section 8 names only admin_web.py, but the button must live in app/templates/admin/user_detail.html, whose entire toolbar is wrapped in {% if not floor_admin %} — exactly the ADMIN-role accounts D8's backfill flags."}, {"severity": "MEDIUM", "title": "is_internal is set only by a one-time backfill; two live writers bypass it", "detail": "app/engine/bots/seating.py:45-58 and app/routes/dev_login.py:45-61 create users rows with no flag, and dev@localhost matches no seed domain, so every future internal row silently enters the funnel as a real signup."}, {"severity": "MEDIUM", "title": "'Played a turn' has two sources that disagree and the spec picks neither", "detail": "turn_submissions rows are deleted by delete_match while connections.turns_played is a counter nothing decrements (app/engine/connection_activity.py:199-209), so two numbers on the same page can contradict each other."}, {"severity": "MEDIUM", "title": "Deploy smoke-test matches are not excluded from the play counts", "detail": "app/read_models/admin_reports.py:109 filters TEST_NAME_PREFIX ('prod smoke') and the public views do too, but the spec's shared filter covers only is_internal and agents.kind, so the engagement page will report higher play numbers than /admin/reports for the same window."}, {"severity": "MEDIUM", "title": "Truncation is specified only at the DB write, but the signed session cookie is also a consumer", "detail": "SessionMiddleware stores first_touch in the hhh_session cookie (app/main.py:240-246), so an untruncated attacker-supplied utm_source can push it past the 4KB browser limit and silently break the visitor's session while the fail-open wrapper logs nothing."}, {"severity": "MEDIUM", "title": "AdminAction is a closed 16-char enum rendered raw; section 8 accounts for none of it", "detail": "FlexibleEnumType raises LookupError on an unknown value (app/models/enum_types.py:31-43), the column caps names at 16 characters, and user_detail.html:93 prints action.value verbatim, so new members, their lengths and their wording all need specifying."}, {"severity": "MEDIUM", "title": "No way to find a mis-flagged internal account", "detail": "admin_users_list (app/routes/admin_web.py:160-200) and users_list.html have no is_internal column or filter, so D8's stated goal of fixing a mis-flag without editing production requires opening every user detail page one at a time."}, {"severity": "LOW", "title": "first_touch survives sign-out and leaks to the next signup in that browser", "detail": "clear_session (app/auth/session.py:25-26) pops only user_id, so the never-overwrite rule attributes the next person's signup to the previous visitor's UTM tag."}]}
```

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: 12 findings (5 HIGH). All 5 HIGH verified against real code before acceptance. Fixed in spec revision 2: gate-ladder order (D5); ConnectionSetup GC and the MCP path bypassing it (D6); sync_google_user's three callers, with MCP signups now labelled 'mcp' rather than silently 'direct' (build item 3); is_internal toggle moved outside the floor_admin gate that would have hidden it on exactly the flagged accounts (D8); is_internal now set at all three User() creation sites (D8). Also fixed: two disagreeing played-a-turn sources resolved to turn_submissions (D10), smoke-test exclusion (D12), session-cookie truncation at capture time (D3), AdminAction closed-enum extension and an internal column on /admin/users (D8), first_touch cleared on sign-out (build item 1). Accepted as a documented limit, not fixed: admin match-delete and handle-reset make past funnels non-reproducible (D7) - an append-only event log is out of scope and is now a recorded non-goal.
