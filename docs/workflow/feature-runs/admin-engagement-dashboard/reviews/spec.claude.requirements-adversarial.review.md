---
reviewer: "claude"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/admin-engagement-dashboard/spec.md"
artifact_sha256: "d848439036d0b0fd68345a85c8d295aefdc43f5286f5252c99c6a5208efa84d1"
repo_root: "."
git_head_sha: "47f8c864a2d8d78ce29635ef606af53b83e59bc3"
git_base_ref: "origin/main"
git_base_sha: "0a38ccf04bbb00ad4e47446f20ebd95638a0d4a1"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "Round 3. 19 findings (7 HIGH), all code-confirmed. Decisive: D8's privacy obligation was honest but had NO GATE - docs/deploy-railway.md:87 confirms push to main auto-deploys, so 'raised at the PR' and 'shipped to production' are the same instant. Fixed by shipping first-touch capture behind FIRST_TOUCH_CAPTURE_ENABLED, default false, with AC0 and a test asserting no cookie and no storage when off. Also fixed: the AC2-vs-AC16 contradiction (write-once-at-event-time vs read-time timezone) resolved by splitting durable setup milestones from read-time-derived play metrics (D1); AC1 corrected to 401-anonymous / 403-non-admin; summary numbers given their own criterion and tests; small-numbers rule added suppressing shares below 20 users, which the earlier non-goal implied but never applied."
raw_output_path: "docs/workflow/feature-runs/admin-engagement-dashboard/reviews/spec.claude.requirements-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

Round 3, requirements lens. Rounds 1-2 findings are excluded except where this
round is explicitly asked to check whether the fix landed; those are labelled
**(round-2 gap, still open)** so the orchestrator can tell them from new work.

---

### HIGH 1 — The human path's real code sites appear nowhere in the spec, so AC4 fails as written [CODE-CONFIRMED]

AC4 is the headline fix of revision 3: a human player must be counted at
`set_up_a_way_to_play` and `joined_match`. The spec's call-site list (D3, and
build item 6) names `agents_create.py` for agent creation and `web_join.py` for
player creation. Neither is the human path.

- The human `Agent` (kind `human`) is created in
  `/Users/chrislaw/hoard-hurt-help--admin-engagement-dashboard/app/engine/human_player.py:98`,
  inside `get_or_create_human_agent`. `app/routes/agents_create.py:181` only ever
  builds AI agents.
- The human `Player` row is created in
  `/Users/chrislaw/hoard-hurt-help--admin-engagement-dashboard/app/routes/web_play.py:313`,
  not `web_join.py:339`. There are three `Player(...)` sites in total:
  `app/engine/bots/seating.py:151`, `app/routes/web_join.py:339`,
  `app/routes/web_play.py:313`.

Neither `app/engine/human_player.py` nor `app/routes/web_play.py` is mentioned
anywhere in the spec — not in "What we are building", not in D3. Implemented
exactly as specified, the default new-user path records **no** milestones beyond
`signed_up` / `picked_handle`, which is the precise failure revision 3 was
written to eliminate. The test plan has an AC4 test, so the test would fail — but
only after the implementer has already built to the wrong file list.

**Fix:** name all three `Player` sites and all three `Agent` sites explicitly, and
say which ones record (`web_join.py`, `web_play.py`, `human_player.py`,
`agents_create.py`) and which do not (`bots/seating.py`).

---

### HIGH 2 — AC7's denominator cannot be built from milestones, and the fallback source is hard-deleted [CODE-CONFIRMED]

AC7 requires `ai_connected` to be shown "as a share of users who chose an AI
agent". Nothing in the design can produce that denominator:

- `set_up_a_way_to_play` (D1) is recorded for kind `ai` **or** `human` and stores
  no kind, so the milestone table cannot separate them.
- The only other source is the `agents` table — and
  `/Users/chrislaw/hoard-hurt-help--admin-engagement-dashboard/app/routes/agents_lifecycle.py:157-169`
  archives an agent only when `Player` rows exist and otherwise **hard-deletes**
  it. A user who built an AI agent and never played drops out of the denominator
  entirely, which inflates the `ai_connected` share upward — the opposite of the
  honest number D2 wants.

So AC7 is not testable as written: two engineers would pick two different
denominators (`agents.kind='ai'` today vs. a new `chose_ai` milestone), and one of
them silently shrinks over time. AC7 also has **no test-plan item at all**, and
this is the criterion whose whole job is to stop the human-default path
(`_default_human_choice` returns `True` for a brand-new user,
`app/routes/web_join.py:113`) from reading as an AI setup failure. An untested
share with a shrinking denominator renders a clean page with a wrong headline
number — exactly the `silent-risk=yes` shape.

**Fix:** add an eighth milestone (`chose_ai`, recorded on first kind=`ai` agent),
define the denominator against it, and add a test.

---

### HIGH 3 — `ai_connected` misses the one place that actually stamps `first_connected_at` for non-MCP connections [CODE-CONFIRMED]

D1 records `ai_connected` when "`first_connected_at` first stamped on a
connection". D3 says the call sites are "connection first-connect (web **and**
MCP)", but build item 6 names only `mcp_connection.py`.

`first_connected_at` is stamped in two modules:

- `app/engine/mcp_connection.py:145, :174, :206, :222`
- `app/engine/connection_activity.py:95` (`mark_seen`), whose own docstring says
  it is "Called from the single auth choke point (``require_connection``), so it
  covers every connection method (runner, MCP, direct API) with one hook."

The always-on connector and any direct-API agent connect through
`connection_activity.mark_seen`, never through `mcp_connection.py`. As specified,
those users never record `ai_connected`, and the page reports a fake AI-setup
drop for them. `connection_activity.py` is the correct single site and it is
absent from the spec.

**Fix:** move the `ai_connected` call to `connection_activity.mark_seen`'s
`NULL -> now` branch, and say whether `mcp_connection.py`'s four sites also need
it or are already covered by that choke point.

---

### HIGH 4 — `returned` needs a first-play date the milestone table does not store, and the fallback rows are deleted [CODE-CONFIRMED]

D1 defines the table as "one row the **first** time a user reaches a milestone,
`UNIQUE(user_id, milestone)`". No column list is given, and no timestamp column is
ever named.

`returned` = "a genuine submission on a second distinct local day". Recording that
at write time (D3) requires knowing the day of the **first** genuine submission.
With no timestamp on the milestone row, the recorder must fall back to querying
`TurnSubmission` — and `app/engine/match_deletion.py:62` and `:69` hard-delete
every `TurnSubmission` row for a deleted match. That is the exact durability the
whole redesign claims ("The milestone row survives … the match being deleted").
So after one admin match delete, `returned` becomes unrecordable for that user,
and any already-written `returned` row becomes unverifiable.

D4's dated note ("Milestones before <deploy date> are reconstructed") is also not
computable without a per-row timestamp.

**Fix:** specify the table's columns explicitly, including a `reached_at`
timestamp and a nullable `backfilled` marker, and state that `returned` compares
against the `played_turn` row's `reached_at`.

---

### HIGH 5 — AC2 and AC16 contradict each other: a write-once row cannot honour a read-time, viewer-chosen timezone [CODE-CONFIRMED]

- AC2: a milestone row is written **exactly once** per user per milestone; a
  second attempt is a silent no-op.
- AC16: return detection uses **the page window's timezone**, which "defaults to
  the browser's".

`returned` is written at submission time, permanently. The submission arrives
through an MCP tool or the agent API — `mcp_server/server.py`'s `submit_action`
takes no request context, and the tool-schema test in `tests/test_mcp.py` pins its
fields as `action, agent_turn_token, game_id, match_id, message, target_id,
thinking, turn_token`. There is no browser, no session, and no timezone anywhere
on that path. The writer therefore cannot know the viewer's timezone, and the
viewer's later choice cannot change a row already written.

The test-plan item "US evening session spanning two UTC days is not a return" is
not implementable under D1's write-time model. Two engineers resolve this
oppositely: one hardcodes UTC at write time and quietly drops AC16; the other
moves `returned` to a read-time computation and quietly drops AC2 and D1's
deletion-proof guarantee.

**Fix:** pick one. Either store the raw first-play and second-play UTC timestamps
on the milestone rows and compute `returned` at read time in the window's
timezone (keeps AC16, keeps durability, breaks "recorded at write time" for this
one milestone), or fix the timezone at capture and change AC16 to say so.

---

### HIGH 6 — Summary numbers 2 and 3 need the read-time filter D5 promises will not exist, and autopilot cannot be filtered at read time [CODE-CONFIRMED]

D5 closes with "Because the milestone is recorded at write time (D3), the recorder
simply is not called on those paths — no read-time filter to get wrong." That
holds for milestones, which are first-time-only. It does **not** hold for two of
the three summary numbers: "users who played a genuine turn **in the window**" and
"genuine turns **in the window**" are period counts that milestones cannot answer,
so they must be computed from `TurnSubmission` at read time.

At read time, autopilot rows are indistinguishable from real moves:

- `app/engine/bots/service.py:169` calls `module.record_submission(...)` — the
  same function a real move uses.
- `app/games/hoard_hurt_help/game.py:222-239` sets
  `was_defaulted = is_connector_fallback`, so an autopilot row lands with
  `was_defaulted=False` and a real `submitted_at`.
- The only signal is `Player.autopilot_at`, set when the human leaves
  (`app/routes/web_play.py:372`). It applies to the whole player row, so
  filtering on it also deletes the genuine turns that same person played *before*
  leaving.

AC8 covers only the milestone, not the summary numbers. As written, summary 2 and
3 count abandoners' auto-Hoards as engagement, and there is no AC and no test that
would catch it.

**Fix:** either mark autopilot rows in the schema (a boolean, or a third
`was_defaulted`-style enum) so a read-time filter exists, or define summary 2 and
3 over `submitted_at`-bearing rows joined to `player.autopilot_at IS NULL OR
submitted_at < autopilot_at` and state that rule in the spec.

---

### HIGH 7 — D8's "open obligation" has no gate that can hold it, because merging the PR *is* shipping to production [CODE-CONFIRMED]

D8 says the cookie obligation "must be resolved before this reaches production"
and will be "Raised again at the PR". Those two sentences describe a gate that
does not exist in this repo:

- `docs/deploy-railway.md:87` — "Rolling deploy: push to `main` → Railway
  auto-deploys."
- `railway.json` runs `alembic upgrade head` as `preDeployCommand`.

Merging the PR and shipping to real users are the same event. Raising the issue
"at the PR" and then merging ships the persistent cookie. There is no feature
flag, no default-off switch on `FirstTouchMiddleware`, no acceptance criterion,
and no test — the only artefact is a blockquote in a spec that stops being read
once implementation starts. Non-goal 2 ("deferred, not dismissed") records the
intent but adds no mechanism.

This is the difference between honest and actionable. The wording is honest; the
handling is not actionable, and an unresolved legal obligation therefore does slip
into implementation with nothing holding it.

**Fix (concrete, one line of config):** add `first_touch_capture_enabled: bool =
False` to `app/config.py`, gate the middleware on it, and add an acceptance
criterion plus a test: "with the flag off, no request sets or grows the session
cookie." Then the merge is safe and flipping the flag is the deliberate decision
D8 says Chris has to make.

---

### MEDIUM 8 — "Every criterion has a matching test in the plan below" is false; the round-2 coverage gap is not closed **(round-2 gap, still open)** [UNVERIFIED]

Mapping all 22 criteria against the test plan:

| Criterion | Test-plan item |
|---|---|
| AC7 `ai_connected` as AI-agent share | **none** (see HIGH 2) |
| AC14 uncaptured source renders `"unknown"`, never `"direct"` | **none** |
| AC18 stuck list: handle-less label, 50 cap, remainder count | **none** |
| AC1 "in the Platform admin submenu" | partial — only 403/200 is tested |
| AC9 "bots … excluded everywhere" | partial — only internal users are tested |
| AC16 "defaults to the browser's timezone" | partial — only the two-UTC-day case |
| AC21 "`/admin/users` shows the column" | partial — only the toggle is tested |

AC14 is the one D9 itself calls dangerous ("the largest row in the source table
silently means 'we failed to capture this'"), and it has no test. Three criteria
with zero coverage on the final review round, under a sentence asserting full
coverage, is worse than an acknowledged gap: the assertion is what a reader checks
instead of the table.

Two test-plan items also run the other way — they test behaviour that no
criterion and no design decision states: "internal referrer treated as direct"
(MEDIUM 13) and "Over-long values capped at capture" (MEDIUM 14).

---

### MEDIUM 9 — The three summary numbers have no acceptance criterion, no test, no stated population, and no stated internal filter **(round-2 gap, still open)** [UNVERIFIED]

Round 2 found "No acceptance criterion covers the four summary numbers at all."
Revision 3 dropped one number and added no criterion. The whole top strip of the
page — three numbers plus a period-over-period comparison rule — is unspecified
and untested.

Taking the parent's three questions in turn:

| Number | Window | Population | `is_internal` |
|---|---|---|---|
| new signups in the window | yes, but the timestamp column is never named (`users.created_at` is assumed) | clear | never restated |
| users who played a genuine turn in the window | yes | **ambiguous** — users who *signed up* in the window and played, or any user who played in the window? Milestone counts use "the signup cohort" (D2); the summary numbers say nothing | never restated |
| genuine turns in the window | yes | **undefined** — "genuine" is unbuildable at read time (HIGH 6) | never restated |

AC9 says internal users are excluded "everywhere", which arguably covers all
three — but "everywhere" is exactly the wording that let the round-1/round-2
funnel drift, and the `completeness-risk=yes` note in `state.json` predicts this
failure by name ("the four summary numbers will contradict the funnel below
them"). Nothing tests that the summary numbers and the milestone list apply the
same filter.

Also unspecified: "when the window is unbounded (the default) no comparison is
shown" sits oddly beside AC16's "the window control defaults to the browser's
timezone" — if the default window is unbounded, the spec never says what timezone
the default view uses for `returned`.

---

### MEDIUM 10 — For the default human path, `set_up_a_way_to_play` and `joined_match` are always written in the same request, so the page can never show a drop between them [CODE-CONFIRMED]

`app/routes/web_play.py:302` calls `get_or_create_human_agent(...)` and line 313
creates the `Player` row — in one function, one request, one click. For a human
user the two milestones are therefore always both present or both absent.

Two consequences the spec does not acknowledge:

1. D2's "difference between neighbours" is structurally zero between those two
   rows for every human user. The page shows a flat wall and an admin reads it as
   "no drop-off here", when in fact no drop-off was measurable.
2. The milestone's name misdescribes what happened. "Set up a way to play" implies
   a deliberate setup act; on the default path it is a side effect of clicking
   Join that the user never sees.

This is not the same as round 2's "the funnel is blind to human players" — that is
fixed. This is that the fix makes two of the seven steps degenerate for the
majority path, and the page carries no note saying so (D4 and D9 each got a note;
this did not).

---

### MEDIUM 11 — The internal-domain config key does not exist and is never named, and "one shared predicate" is contradicted by giving the two callers different inputs [CODE-CONFIRMED]

D10's creation table says the `auth.py:41` rule uses a "configured internal-domain
list". There is no such setting: `grep -n internal app/config.py` returns nothing.
The spec never names the key, its type, its default, or its production value.

Worse, D10 then requires "**The backfill and the creation rule must use one
shared predicate** so they cannot disagree" — but hands them different inputs: the
creation rule reads a configured list, and the backfill reads a hardcoded seed
list (`agentludum.local`, `house.local`, `local.test`, `dev@localhost`). If the
shared predicate reads settings, then on prod — where the env var is unset — the
backfill flags **nothing**, and D10's own claim that "the backfill is the only
thing that will ever flag them" fails silently. If it reads the hardcoded list,
the "configured" wording is wrong.

**Fix:** state the constant explicitly (e.g. `INTERNAL_EMAIL_DOMAINS` in
`app/identity/internal_accounts.py`), say whether an env var extends it, and
confirm the backfill uses the same module-level constant, not settings.

---

### MEDIUM 12 — `dev@localhost` is an email address inside a list of domains; a domain match will miss it [CODE-CONFIRMED]

`app/routes/dev_login.py:30` is `_DEV_USER_EMAIL = "dev@localhost"`. Its **domain**
is `localhost`. D10's "Backfill seed domains: `agentludum.local`, `house.local`,
`local.test`, `dev@localhost`" mixes one address into a list of three domains.

The natural implementation — `email.rsplit("@", 1)[1] in DOMAINS` — never matches
`dev@localhost`. Round 2 found "`dev@localhost` is not covered by the backfill
seed domains"; the revision-3 patch put the string in the list without fixing the
kind of string it is, so the bug survives the fix.

**Fix:** either list `localhost` as a domain, or say the predicate matches on full
address **or** domain and give both lists separately.

---

### MEDIUM 13 — The test plan asserts a capture rule ("internal referrer treated as direct") that no design decision and no criterion states [CODE-CONFIRMED]

Test plan, Capture: "External referrer stored as host; **internal referrer treated
as direct**."

D6 lists what is captured (`referrer_host`) and D9 gives the label precedence
(`utm_source` → `first_referrer_host` → `"direct"`). Neither mentions internal
referrers, and no criterion covers them. "Internal" is also not a single value
here: `app/main.py:259` installs `CanonicalHostMiddleware` precisely because the
site answers on both the canonical host and `*.up.railway.app`, and local dev uses
`testserver` / `localhost`.

Left as is, one engineer stores the internal host and the source table grows a
large `agentludum.com` row (which is a self-referral, not a source); another maps
it to `"direct"`; a third maps it to `"unknown"` and collides with AC14.

**Fix:** add a criterion — "a referrer whose host matches the canonical host is
recorded as `direct`, not as a source" — and define "matches" (exact host, or host
plus the Railway alias).

---

### MEDIUM 14 — "Length-capped" appears three times and never carries a number [UNVERIFIED]

D8 says first touch "must be length-capped **at capture time**". D9's heading says
"length-capped". The test plan tests "Over-long values capped at capture, before
the cookie". No cap value is stated anywhere, no column widths are given for the
five source columns or `first_source_channel`, and no criterion covers the cap at
all.

Two engineers pick 64, 200, or 255 and both pass the test as written, which means
the test is not a test. It also means D8's 4KB cookie-budget argument cannot be
checked: without a per-field cap and a field count you cannot say whether first
touch fits beside the `fresh_connection_key_setup_{id}` entries D8 says already
leak.

**Fix:** state one number per field (e.g. 100 chars for each UTM field, 253 for
`referrer_host`, 200 for `landing_path`) and match the migration's column widths
to it.

---

### MEDIUM 15 — The small-numbers tension is unresolved; the rationale was deleted rather than answered [UNVERIFIED]

The parent asked specifically about this. In revision 3, non-goal 5 is now bare —
"No cohort retention grid" — with the "noise at that size" justification removed.
The stated contradiction is gone; the underlying tension is not.

The page still leans on three small-sample statistics with no precision caveat:
neighbour differences on ~10-20 users (D2), a share (`ai_connected`, AC7), and
period-over-period comparisons for all three summary numbers. At n=15, one user is
6-7 percentage points; a two-user week-over-week swing is noise that will read as
a trend, and `state.json`'s own audience note says "at 10-20 users named rows
matter more than percentages".

Meanwhile D4 and D9 each earned a rendered note on the page for a smaller
inaccuracy. There is no equivalent note, and no criterion, for "these numbers move
by whole users".

**Fix:** either drop the percentage and the period-over-period comparison until
the user base supports them, or add a third page note and a criterion for it — and
restore a one-line rationale to non-goal 5 so a future reader knows the grid was
rejected on purpose and why.

---

### MEDIUM 16 — AC9's "everywhere" mixes a testable invariant with an untestable claim [CODE-CONFIRMED]

AC9: "Bots and internal users excluded everywhere, via the stored flag." That is
two different promises:

1. *Every query on the page applies the same `is_internal` filter* — testable, and
   worth pinning.
2. *Every internal human is flagged* — not testable and, by D10's own admission,
   not true: a fourth creation path exists outside the app, and any account it
   creates after the backfill runs gets the column default and is never flagged.

D10 attributes that path to "almost certainly `scripts/`", but nothing in the repo
supports it: `grep -rn "User(" scripts/` returns nothing, and `ludumlabs`,
`harness-A`, `sims@agentludum` appear in no Python file in the tree. So the path is
unidentified, not merely unlisted, and the spec asks for a post-deploy row-count
verification against accounts whose origin nobody has found.

**Fix:** split AC9 into the query invariant (keep, test it) and a separate,
honest line: "accounts created outside the app are flagged only by the backfill or
the admin toggle; the page undercounts internal activity for any created after
deploy." Add finding the fourth path to the plan checkpoint, or say explicitly it
is out of scope.

---

### LOW 17 — D3 points the implementer at the wrong section for the user-creation sites [UNVERIFIED]

D3: "Call sites: user creation (3 places, **D8**)". The three creation sites moved
to D10 in this revision; D8 is now the cookie obligation and contains no table. An
implementer following the pointer lands on the privacy section. One-word fix.

---

### LOW 18 — AC1's "403s for a non-admin" is wrong for the anonymous case [CODE-CONFIRMED]

`app/deps.py:71` `require_platform_admin` calls `require_user` first, which raises
**401 `NOT_SIGNED_IN`** for a signed-out visitor (`app/deps.py:38`); only a
signed-in non-admin gets 403. The test-plan item "403 non-admin, 200 admin" does
not say which case it covers, so a signed-out visitor is untested on a page that
must never leak user emails (the stuck list shows handle-less users **by email**,
AC18).

**Fix:** "401 signed-out, 403 signed-in non-admin, 200 admin", and test all three.

---

### LOW 19 — `was_defaulted` now carries two meanings, so D5's rationale and its 4,614-row figure describe only half the column [CODE-CONFIRMED]

D5 point 1 explains `was_defaulted` purely as a missed deadline. It is no longer
only that: `app/games/hoard_hurt_help/game.py:220-222` comments "Connector
fallbacks reuse the existing `was_defaulted` column so they are identifiable in
the DB without a migration. A genuine move clears the flag."

The exclusion rule is unaffected (both should be excluded), so this is not a bug
in the outcome. But the spec's stated reason is incomplete, and "4,614 of 20,276
rows in the dev DB" is presented as the missed-deadline population when it mixes
two. If a later change ever wants to count connector fallbacks separately — a
model-failure signal that matters — the spec has recorded the wrong premise.

---

## Residual Risks

- **Reviewer independence is thin, and this is the last round.** Three Claude
  lenses on a Claude-authored spec, per the spec's own caveat. Findings HIGH 1,
  HIGH 3 and HIGH 6 are all "the spec names the wrong file", which is the failure
  mode a same-vendor round is worst at catching by agreement. Codex re-enters at
  the plan checkpoint; the plan should be required to restate the full call-site
  list from `grep`, not from the spec.
- **The call-site list is the single point of failure for the whole feature.** Every
  milestone count is only as good as the set of places `record_milestone` is
  called, and there is no mechanism proposed that would notice a missing one. A
  new `Player(...)` or `Agent(...)` site added later silently degrades the page. A
  guard test asserting "these N files are the only modules constructing
  `Player`/`Agent`/`User`" would convert a silent drift into a failing test.
- **`is_internal` has no ongoing enforcement.** The backfill is one-time and the
  toggle is manual. Six months of new internal accounts will need someone to
  remember. Not blocking; worth a line in the plan.
- **The privacy obligation will be decided under merge pressure.** Even with the
  flag fix in HIGH 7, the decision arrives at the moment someone wants the feature
  live. Deciding the disclosure wording *before* implementation starts costs
  almost nothing now and a lot later.
- **Unverified by this lens:** the actual dev-DB numbers quoted in D5 and D10
  (4,614/20,276 submissions; 500 of 646 player rows internal) were not re-counted;
  the exact behaviour of `SessionMiddleware`'s default `max_age` was taken from
  D8's claim, not re-derived from Starlette; and no migration was written or run,
  so AC20's clean upgrade/downgrade on SQLite remains an assertion.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "Human path's real code sites are absent from the spec, so AC4 fails as written", "detail": "The human Agent is created in app/engine/human_player.py:98 and the human Player row in app/routes/web_play.py:313, neither of which the spec mentions — it names agents_create.py and web_join.py instead, so the default new-user path records no milestones."}, {"severity": "HIGH", "title": "AC7's denominator cannot be built and has no test", "detail": "set_up_a_way_to_play stores no agent kind, and the only other source (agents.kind='ai') is hard-deleted for agents with no play history at agents_lifecycle.py:157-169, so the ai_connected share silently inflates and nothing tests it."}, {"severity": "HIGH", "title": "ai_connected misses connection_activity.mark_seen, the real first-connect choke point", "detail": "first_connected_at is stamped in app/engine/connection_activity.py:95 for every connection method (runner, MCP, direct API) as well as in mcp_connection.py, but the spec's call-site list names only mcp_connection.py, so connector and direct-API users never record the milestone."}, {"severity": "HIGH", "title": "returned needs a first-play date the milestone table never defines, and the fallback rows are deleted", "detail": "The table is specified only as (user_id, milestone) with no timestamp column, so the recorder must query TurnSubmission — which app/engine/match_deletion.py:62,69 hard-deletes, breaking the deletion-proof guarantee the redesign is built on."}, {"severity": "HIGH", "title": "AC2 and AC16 contradict each other: a write-once row cannot honour a read-time viewer timezone", "detail": "returned is written once at submission time via an MCP/API call that has no browser or session, so it can never reflect the page window's browser-defaulted timezone that AC16 requires, and the two-UTC-day test is unimplementable."}, {"severity": "HIGH", "title": "Summary numbers 2 and 3 require a read-time genuine-turn filter that D5 says will not exist, and autopilot is unfilterable", "detail": "Window-scoped turn counts cannot come from first-time-only milestones, and autopilot rows are written by the same record_submission with was_defaulted=False and a real submitted_at, while Player.autopilot_at over-excludes the genuine turns played before the human left."}, {"severity": "HIGH", "title": "D8's open obligation has no gate that can hold it because merging the PR is shipping to production", "detail": "docs/deploy-railway.md:87 confirms push to main auto-deploys, so 'raised again at the PR' and 'resolved before production' are the same moment, and there is no flag, criterion, or test — a default-off first_touch_capture_enabled setting would make the obligation actionable."}, {"severity": "MEDIUM", "title": "The claim that every criterion has a matching test is false; round 2's coverage gap is still open", "detail": "AC7, AC14 and AC18 have no test-plan item at all, and AC1, AC9, AC16 and AC21 are only partly covered, while two test items assert behaviour no criterion states."}, {"severity": "MEDIUM", "title": "The three summary numbers have no criterion, no test, no stated population, and no stated internal filter", "detail": "Round 2 raised this for four numbers, revision 3 dropped one and added nothing; 'users who played a genuine turn in the window' never says whether the population is the signup cohort or all users, and none of the three restates is_internal."}, {"severity": "MEDIUM", "title": "For humans, set_up_a_way_to_play and joined_match are always written in the same request", "detail": "app/routes/web_play.py:302-313 creates the human agent and the Player row in one click, so the difference between those two steps is structurally zero for the majority path and the page carries no note saying so."}, {"severity": "MEDIUM", "title": "The internal-domain config key does not exist and the 'one shared predicate' rule is self-contradictory", "detail": "No 'internal' setting exists in app/config.py, and D10 gives the creation rule a configured list while giving the backfill a hardcoded seed list, so on prod with an unset env var the backfill would flag nothing."}, {"severity": "MEDIUM", "title": "dev@localhost is an email address inside a list of domains", "detail": "app/routes/dev_login.py:30 sets _DEV_USER_EMAIL = 'dev@localhost' whose domain is 'localhost', so a domain-matching predicate misses it and round 2's finding survives its own fix."}, {"severity": "MEDIUM", "title": "The test plan asserts an internal-referrer rule that no design decision or criterion states", "detail": "'Internal referrer treated as direct' appears only in the test plan; D6 and D9 never mention it, and 'internal' is ambiguous because CanonicalHostMiddleware exists precisely because the site answers on more than one host."}, {"severity": "MEDIUM", "title": "'Length-capped' appears three times and never carries a number", "detail": "No cap value and no column widths are given for the five source columns or first_source_channel, so the test 'over-long values capped at capture' passes at any limit and D8's 4KB cookie-budget argument cannot be checked."}, {"severity": "MEDIUM", "title": "The small-numbers tension is unresolved; the rationale was deleted rather than answered", "detail": "Non-goal 5 is now bare, but the page still shows neighbour differences, a share, and period-over-period comparisons on 10-20 users with no precision note, while D4 and D9 each earned a rendered caveat for smaller inaccuracies."}, {"severity": "MEDIUM", "title": "AC9's 'everywhere' mixes a testable invariant with an untestable claim", "detail": "Every-query-applies-the-filter is testable and worth pinning, but every-internal-human-is-flagged is false by D10's own admission, and the fourth creation path it blames on scripts/ does not exist in the repo."}, {"severity": "LOW", "title": "D3 points the implementer at D8 for the user-creation sites, which now live in D10", "detail": "D8 is the cookie-obligation section in this revision and contains no creation table, so the cross-reference sends the reader to the wrong place."}, {"severity": "LOW", "title": "AC1's '403s for a non-admin' is wrong for the anonymous case", "detail": "app/deps.py:38 raises 401 NOT_SIGNED_IN for a signed-out visitor and only a signed-in non-admin gets 403, so the test '403 non-admin' leaves the signed-out case untested on a page that renders user emails."}, {"severity": "LOW", "title": "was_defaulted now carries two meanings, so D5's rationale and its 4,614-row figure are incomplete", "detail": "app/games/hoard_hurt_help/game.py:220-222 reuses was_defaulted for connector fallbacks as well as missed deadlines, so the quoted dev-DB count mixes two populations even though the exclusion outcome is unchanged."}]}
```

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Round 3. 19 findings (7 HIGH), all code-confirmed. Decisive: D8's privacy obligation was honest but had NO GATE - docs/deploy-railway.md:87 confirms push to main auto-deploys, so 'raised at the PR' and 'shipped to production' are the same instant. Fixed by shipping first-touch capture behind FIRST_TOUCH_CAPTURE_ENABLED, default false, with AC0 and a test asserting no cookie and no storage when off. Also fixed: the AC2-vs-AC16 contradiction (write-once-at-event-time vs read-time timezone) resolved by splitting durable setup milestones from read-time-derived play metrics (D1); AC1 corrected to 401-anonymous / 403-non-admin; summary numbers given their own criterion and tests; small-numbers rule added suppressing shares below 20 users, which the earlier non-goal implied but never applied.
