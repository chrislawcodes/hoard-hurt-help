## Findings

Round-2 scope: the 30 round-1 findings listed in "Review outcomes" are treated as
settled and are not re-reported. Everything below is either something a round-1
fix introduced, something a round-1 fix left half-done, or something no round-1
finding touched.

### HIGH 1 — Summary number 4 is defined three ways at once, and its "24-hour snapshot" label is factually wrong [CODE-CONFIRMED]

Build item 6 defines number 4 as "Incomplete connection setups **right now** —
labelled as a 24-hour snapshot (D6)", and then says "**Each** shows a comparison
against the immediately preceding window of equal length." All three parts fail
against the code.

1. **It is not a 24-hour snapshot.** `gc_pending_connections`
   (`app/engine/pending_connection_gc.py`) is the only thing that deletes
   incomplete `ConnectionSetup` rows, and it is called from exactly two places:
   `app/routes/agents_create.py:112` and `app/routes/connections_pages.py:94`.
   No scheduler job runs it (`app/engine/scheduler.py` has no reference). So a
   row older than 24 hours survives until some *other* user happens to load the
   agent-create page or the connections page. On a 10–20 user alpha that can be
   days or weeks. The real meaning is "every incomplete setup since whoever last
   loaded one of two pages" — unbounded in age, and not something the admin can
   see or reason about. `/admin/engagement` itself does not call the GC, so
   loading the dashboard does not even normalise the number it prints.
2. **The previous-period comparison is impossible for it.** The rows are
   *hard-deleted* (`delete(ConnectionSetup)`), not soft-deleted. Nothing records
   how many incomplete setups existed during the previous window, so "each shows
   a comparison" cannot be satisfied for number 4. An implementer will either
   invent something or silently skip it, and the spec gives no rule.
3. **It ignores the window control while sitting next to three numbers that
   obey it.** Mixing one live number with three windowed numbers in one row is a
   requirements defect on its own: change the date range and three tiles move
   while the fourth does not. The one-word label "snapshot" is doing all the work
   of explaining that, and per point 1 the label is wrong anyway.

**Fix:** either drop number 4, or define it as a windowed count over a durable
column and state that the GC prevents that; and state explicitly, in the AC, that
number 4 has no comparison and does not respond to the window.

### HIGH 2 — "Played a turn", "came back another day" and summary 3 never name a timestamp, and the obvious column is nullable [CODE-CONFIRMED]

D5 step 6 is "a `turn_submissions` row with `was_defaulted = false`"; step 7 is
"played on 2+ distinct days"; summary 3 is "Turns played in the window
(non-defaulted)". None of the three says which timestamp decides the day or the
window.

`TurnSubmission.submitted_at` is `Mapped[datetime | None]` — nullable
(`app/models/turn.py:73`). The repo's existing admin read model does not trust it:
`app/read_models/admin_reports.py:182` skips a row when
`submission.was_defaulted or submission.submitted_at is None`, i.e. it already
treats a non-defaulted row with a NULL timestamp as real and unusable.
`app/engine/agent_play.py:127` and `:219` defend the same way
(`existing.submitted_at or datetime.now(...)`).

So an implementer has at least three defensible choices — use `submitted_at` and
drop NULLs, fall back to `turns.opened_at`, or join through `turns` for the day —
and they give different numbers for the same data. Every step-6, step-7 and
summary-3 figure inherits that choice. Rows predating the column
(`0001_initial` / `0013_two_phase_turns`) make this a live data question, not a
theoretical one.

**Fix:** name the column, and say what happens to a non-defaulted row with a NULL
timestamp (count it via `turns.opened_at`, or exclude it and say so).

### HIGH 3 — The `is_internal` rule at the Google sign-in site is never written down, and it cannot match the backfill [CODE-CONFIRMED]

D8's table says the rule at `auth.py:41` is "domain rule" and nothing else. The
backfill rule is stated in full: seed domains `agentludum.local`, `house.local`,
`local.test`, **plus any `ADMIN`-role user at migration time**.

Those two rules cannot agree, because admin-ness is not a domain and is not fixed
at migration time:

- `sync_google_user` sets `role = ADMIN` when the email is in
  `settings.platform_admin_emails_set` (`app/routes/auth.py`). Chris's own address
  is a gmail.com one — no seed domain matches it. So a *second* platform admin,
  or Chris re-created after the migration, is flagged internal by the backfill but
  **not** by the creation-site rule as written.
- `promote_user` / `demote_user` (`app/routes/admin_web.py:1634`, `:1647`) change
  role at any time afterwards. D8 says the flag is "set at creation, never
  recomputed", so a user promoted to admin next month keeps `is_internal = False`
  and keeps counting as a real user in every number on the page — and a
  migration-time admin who is later demoted stays excluded forever.

This is exactly the failure D8 was written to prevent (a user drifting in and out
of the excluded group), moved from email-drift to role-drift. The manual toggle is
the only backstop, and nothing tells an admin when to use it.

**Fix:** write the creation-site rule out in full, say whether ADMIN role implies
internal at creation and after a later promotion, and if it does not, drop the
ADMIN clause from the backfill so the two rules match.

### HIGH 4 — D3 never states the cookie's lifetime; the default makes it a persistent 14-day cookie, which both caps the attribution and changes the legal picture [CODE-CONFIRMED]

`app/main.py:240–246` adds `SessionMiddleware` with `secret_key`, `same_site`,
`https_only` and `session_cookie` — and **no `max_age`**. Starlette's default is
`max_age = 1209600` (14 days), confirmed against the installed package.

Two consequences the spec does not mention:

- **The feature's stated value is capped at 14 days.** D3 justifies the cookie
  with "full attribution — the source surviving any amount of browsing before
  signup — is the point of the feature." It survives 14 days, not any amount. A
  Reddit reader who bookmarks the site and signs up three weeks later is recorded
  as `"direct"` — silently, and in exactly the population the feature exists to
  measure.
- **It is a persistent cookie, not a session cookie.** That is the fact that
  matters for the consent question D3 raises. D3 calls it "an analytics cookie on
  an anonymous visitor" and stops there. Persistent-vs-session is the distinction
  a reader needs to judge the exposure, and it is absent.

**Fix:** state the lifetime as a requirement (and pick one deliberately — a short
explicit `max_age` for first touch, or accept 14 days and say so), and record the
attribution horizon it implies.

### HIGH 5 — Nothing in the delivery verifies or discloses the one public-facing change, and there is no disclosure surface to build on [CODE-CONFIRMED]

Non-goal 5 says "No public-facing changes **beyond the cookie now being set**".
That cookie is the feature's only public-facing effect, and:

- **No acceptance criterion mentions it.** All 19 ACs are about the admin page,
  the flag, the migration and capture correctness. None says "an anonymous
  visitor's first page view sets the session cookie" or bounds what goes in it.
- **No test-plan item covers it.** The capture tests all assert the *value* is
  recorded; none asserts the cookie behaviour itself.
- **There is nothing on the site to disclose it in.** There is no privacy policy,
  no cookie notice, and no `privacy` route anywhere in `app/`. `app/templates/`
  has `contact.html` and no legal page; the footer in
  `app/templates/base.html:149–156` links only Contact.

D3 says "a consent banner is not part of this feature and remains an open item".
That is honest about the banner but understates the position: the feature starts
setting a persistent cookie on a site with *zero* cookie disclosure of any kind,
and the spec never says that. "Open item for Chris" names one missing artifact
when three are missing (notice, policy text, banner) and does not say whether
shipping before them is acceptable, who owns it, or by when.

**Fix:** add an AC for the cookie's observable behaviour, and state the shipping
decision plainly — either "ships before any disclosure exists, accepted risk,
owner Chris" or a gate.

## Residual Risks

*(MEDIUM and LOW findings, ordered by severity.)*

### MEDIUM 1 — No acceptance criterion covers the four summary numbers at all [UNVERIFIED — spec-internal]

Round 1's finding "Unnamed summary numbers and undefined previous period" is
recorded in "Review outcomes" as accepted and fixed. The fix landed **only** in
build item 6. None of the 19 ACs mentions the summary numbers, their definitions,
or the previous-period rule; the only test-plan line touching them is "internal
users excluded from summary numbers". So the round-1 fix is unverifiable — the
build can ship with the numbers defined any way at all and every AC still passes.
This is the same class of gap as HIGH 1 and is the reason HIGH 1 survived into
revision 2.

### MEDIUM 2 — Eight of the nineteen ACs have no matching test-plan item, including the one D9 calls the risky one [UNVERIFIED — spec-internal]

Mapping every AC to the test plan:

| AC | Covered? |
|---|---|
| 1 (page exists, in menu, 403s) | **No item.** (The 403 claim itself is correct — `require_platform_admin` raises 403 in `app/deps.py:71–80`.) |
| 4 ("ever did X") | **Half.** The archived-agent case is tested; D7 also promises deleted connections still count, and nothing tests that. |
| 5 (excluded *everywhere*) | **Half.** Tested for summary numbers and funnel only — not the source table, not the stuck list. |
| 6 (flag at all three creation sites) | **Two of three.** Bots-user and dev-login are tested. The Google sign-in site is not (and see HIGH 3 — its rule is undefined). |
| 14 (source table, NULL → "unknown") | **No item at all.** |
| 15 (stuck list: handle-less label, 50-row cap) | **No item.** |
| 16 (starts at 100%, no visitor count, note) | **No item.** |
| 18 (toggle + audit + users-list column) | **Partial.** Toggle and floor-admin render are tested; the "internal" column on `/admin/users` is not. |

AC 2, 3, 7, 8, 9, 10, 11, 12, 13, 17, 19 all map cleanly.

The sharpest gap is AC 14. D9 says the funnel is "safe by construction" and names
the **source table** as "the exposed risk" where a naive join reports one busy
user as dozens. The only distinct-user test in the plan is filed under "Funnel
correctness". The one place the spec says the bug will happen has no test.

### MEDIUM 3 — Summary 2 and funnel step 6 look like the same number and are not [UNVERIFIED — spec-internal]

Summary 2 is "Users who played a turn **in the window**". Funnel step 6 is users
who **signed up in the window** and have **ever** played a non-defaulted turn
(step 1 is windowed, D7 makes steps 2–7 "ever"). Different populations, adjacent
on one page, both readable as "people who played". They will disagree, sometimes
badly — a long-time player who played this week is in summary 2 and in no funnel
step. Nothing on the page or in the spec says why. An admin comparing them will
conclude one of them is broken.

**Fix:** label them so the difference is visible ("signed up this window" vs
"active this window"), and say in the AC which population each counts.

### MEDIUM 4 — "Came back another day" is still not defined to exactly one implementation, and the definition contradicts its own stated intent [UNVERIFIED — spec-internal]

D5 step 7 says "played on 2+ distinct days". Three things are unresolved:

- **Within the window, or ever?** Step 1 is windowed and D7 says steps are "ever",
  so "ever" is the likely reading — but step 7 is the one step where "ever" makes
  the number drift upward every day the page is loaded, and the spec never says it.
- **Do both days need a non-defaulted turn?** Step 6 defines "played" as
  non-defaulted. Step 7 says "played" without repeating it. A user who really
  played on Monday and no-showed on Tuesday is a return visit under one reading
  and not under the other.
- **It does not deliver what D11 says it is for.** D11 justifies the timezone rule
  with "a US evening session spans two UTC days and would read as a return visit
  that never happened." Switching to local time moves the midnight boundary; it
  does not remove it. A single session from 11:55pm to 12:05am **local** still
  counts as two distinct local days and still reads as a return visit that never
  happened. The test plan tests only the UTC-vs-local case, so this passes review
  and ships.

Related, and unstated: the timezone comes from the `tz` query parameter (copied
from `/admin/reports`, per D11). So step 7's count changes when the admin changes
the dropdown. D11 secures consistency *within* one page load and never says the
number is not stable *across* them.

**Fix:** define step 7 as a gap rule (e.g. two non-defaulted turns at least N
hours apart) or keep calendar days and change the step's name so it does not
promise more than it measures.

### MEDIUM 5 — D3's 4KB mitigation is stated as a guarantee it cannot provide [CODE-CONFIRMED]

D3 says: "Every captured field is length-capped (D4) so first touch cannot push a
session over the limit and silently drop it."

Capping bounds first touch's own contribution (~870 characters across the five
fields). It cannot bound the session, because the session already grows without
limit elsewhere: `app/routes/connections_machine_setup.py:115` and `:125` write a
plaintext key under `fresh_connection_key_setup_{setup.id}`, and that entry is
only ever **read** (`.get` at `:174`) — never popped. `clear_session`
(`app/auth/session.py:25`) pops only the user key, so signing out does not clear
them either. (The sibling at `connections_pages.py:273` does pop; the setup one
does not.)

So a user who starts many machine setups — precisely the struggling user this
dashboard exists to find — accumulates cookie entries forever, and first touch can
still be the straw that tips the cookie past ~4KB, at which point the browser
drops it and the user is silently signed out. The mitigation is real but partial,
and the spec states it as absolute.

**Fix:** soften the claim to what it is, or add a size check on the session as
part of capture.

### MEDIUM 6 — The sample-size objection is applied to one feature and ignored for three [UNVERIFIED — spec-internal]

Non-goal 4 rejects a cohort retention grid because it is "noise at 10–20 users".
The same 10–20 users then get: a seven-step funnel with a percentage drop at each
step, a retention step (step 7), a source table with a "played" percentage, and
period-over-period comparisons on all four summary numbers. At n=12, one user
leaving is an 8-point move. No AC, design decision or test says how percentages
render on tiny denominators, whether small counts are suppressed, or how the
comparison reads when the previous window had 2 signups. The test plan covers only
"Empty window renders without divide-by-zero" — the zero case, not the tiny case.

### LOW 1 — D9's heading overstates its own rule [UNVERIFIED — spec-internal]

D9 is titled "Every count is distinct users, never rows", but summary number 3 is
"Turns played in the window", which is deliberately a row count. AC 11 states the
rule correctly ("Every **people-count** is distinct users"). The heading should
match the AC, or an implementer reading D9 alone will convert number 3 to a user
count.

### LOW 2 — `"mcp"` as a source has no defined home and can collide [UNVERIFIED — spec-internal]

AC 9 and build item 3 say MCP-created users record source `"mcp"`. D4's precedence
chain is `utm_source` → `first_referrer_host` → `"direct"`, with no slot for a
synthetic label. Presumably `first_utm_source = "mcp"`, but the spec never says,
never says what the other four columns hold for an MCP user, and a real inbound
link carrying `?utm_source=mcp` would be indistinguishable from a genuine MCP
signup. Naming the column and reserving the label would close it.

### LOW 3 — D3's "no cookie at all" is slightly overstated [CODE-CONFIRMED]

D3 says "Today an anonymous visitor gets **no cookie at all**: nothing writes to
`request.session` until sign-in." `google_login` writes
`request.session["next_after_login"] = next` (`app/routes/auth.py:85`) before the
user has signed in, so a visitor who clicks Sign in already receives a cookie
today. The substance of D3 survives — a visitor who only browses gets none — but
the sentence as written is wrong and is load-bearing for the decision it records.

### Not findings — checked and confirmed correct

- AC 1's "403s for a non-admin" matches the code: `require_platform_admin` raises
  `HTTP_403_FORBIDDEN` (`app/deps.py:71–80`). The gap is the missing test, not the
  claim.
- D6's claim that the GC "runs on ordinary page loads" is accurate — it is what
  makes HIGH 1 worse, not a defect in D6 itself.
- D10's `was_defaulted` premise holds: defaulted rows are written with
  `submitted_at=None` (`app/games/hoard_hurt_help/scoring.py:197`,
  `app/engine/resolver.py:49`).

### Risks accepted without a finding

- The funnel's step 2 (`handle_key IS NOT NULL`) can be undone by
  `reset_handle`, which would drop a real player out of steps 3–7 under strict
  nesting. D7 discloses this class of problem as an accepted limit, so it is not a
  new finding — but the note on the page should mention that a handle reset
  removes a user from the funnel entirely, not just that past funnels are
  non-reproducible.
- First-touch data has no stated retention limit and no requirement to clear it
  when an account is disabled or deleted. Small today; grows with the privacy gap
  in HIGH 5.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "Summary number 4 is defined three ways and its \"24-hour snapshot\" label is wrong", "detail": "gc_pending_connections runs only from agents_create.py:112 and connections_pages.py:94 with no scheduler, so incomplete setups persist far past 24 hours; the rows are hard-deleted so the promised previous-period comparison is impossible; and the number ignores the window control that the three tiles beside it obey."}, {"severity": "HIGH", "title": "Steps 6 and 7 and summary 3 never name a timestamp, and the obvious column is nullable", "detail": "TurnSubmission.submitted_at is nullable (app/models/turn.py:73) and admin_reports.py:182 already discards non-defaulted rows whose timestamp is NULL, so \"played a turn\", \"came back another day\" and \"turns in the window\" each have several defensible implementations that give different numbers."}, {"severity": "HIGH", "title": "The is_internal rule at the Google sign-in site is undefined and cannot match the backfill", "detail": "D8 says only \"domain rule\" for auth.py:41 while the backfill also flags every ADMIN-role user at migration time, but role comes from platform_admin_emails_set at sign-in and can change later via promote_user/demote_user (admin_web.py:1634/:1647), so the flag drifts by role exactly the way D8 was written to stop it drifting by email."}, {"severity": "HIGH", "title": "Cookie lifetime is unspecified; the default makes it a persistent 14-day cookie", "detail": "app/main.py:240-246 passes no max_age so Starlette's 1209600-second default applies, which caps the \"survives any amount of browsing\" attribution D3 promises at 14 days and makes this a persistent rather than session cookie — the distinction that matters for the consent question D3 raises."}, {"severity": "HIGH", "title": "Nothing verifies or discloses the cookie, and the site has no disclosure surface at all", "detail": "The cookie is the feature's only public-facing change per non-goal 5, yet no acceptance criterion and no test-plan item covers it, and there is no privacy policy, cookie notice or privacy route anywhere in app/ (footer at base.html:149-156 links only Contact), so \"a consent banner is an open item\" names one missing artifact when three are missing."}, {"severity": "MEDIUM", "title": "No acceptance criterion covers the four summary numbers at all", "detail": "Round 1's \"unnamed summary numbers and undefined previous period\" fix landed only in build item 6, so none of the 19 ACs and only one test-plan clause touch the summary numbers — the fix is unverifiable and is why HIGH 1 survived into revision 2."}, {"severity": "MEDIUM", "title": "Eight of nineteen acceptance criteria have no matching test-plan item", "detail": "ACs 1, 14, 15 and 16 have no test at all and ACs 4, 5, 6 and 18 are only half covered; the worst gap is AC 14, since D9 names the source table as the one place a naive join misreports users and the plan's only distinct-user test is a funnel test."}, {"severity": "MEDIUM", "title": "Summary 2 and funnel step 6 count different populations but read as the same number", "detail": "Summary 2 is users active in the window regardless of signup date while step 6 is window signups who ever played, so the two adjacent numbers will disagree with nothing on the page or in the spec explaining why."}, {"severity": "MEDIUM", "title": "\"Came back another day\" still admits several implementations and contradicts D11's intent", "detail": "The spec never says whether the two days are within the window or ever, nor whether both must be non-defaulted, and \"2+ distinct local days\" still counts an 11:55pm-to-12:05am local session as a return visit — the exact false positive D11 says the timezone rule exists to prevent."}, {"severity": "MEDIUM", "title": "D3's 4KB mitigation is stated as a guarantee it cannot provide", "detail": "Length-capping bounds first touch's own size but not the session: connections_machine_setup.py:115/:125 write fresh_connection_key_setup_{id} entries that are only ever read, never popped, and clear_session (auth/session.py:25) removes only the user key, so the cookie still grows without limit and first touch can tip it past 4KB."}, {"severity": "MEDIUM", "title": "The small-sample objection is applied to one feature and ignored for three", "detail": "Non-goal 4 rejects a retention grid as noise at 10-20 users, yet the page ships per-step drop percentages, a retention step, source-table percentages and four period-over-period comparisons on the same denominators with no rule for small-n display and only a divide-by-zero test."}, {"severity": "LOW", "title": "D9's heading overstates its own rule", "detail": "\"Every count is distinct users, never rows\" contradicts summary number 3, which is deliberately a row count; AC 11's narrower \"every people-count\" is the correct wording."}, {"severity": "LOW", "title": "The \"mcp\" source label has no defined column and can collide", "detail": "AC 9 requires MCP users to record source \"mcp\" but D4's precedence chain has no slot for a synthetic label, the spec never says which column holds it or what the other four hold, and a real ?utm_source=mcp link would be indistinguishable."}, {"severity": "LOW", "title": "D3's \"no cookie at all\" premise is overstated", "detail": "google_login writes request.session[\"next_after_login\"] at app/routes/auth.py:85 before sign-in, so a visitor who clicks Sign in already gets a cookie today; D3's substance holds for browse-only visitors but the sentence is wrong and is load-bearing for the decision it records."}]}
```
