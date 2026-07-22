# Findings verdict table — `join-lineup`

Every finding from every reviewer gets a row. None dropped silently.

Reviewers run: spec stage — `feasibility-adversarial`, `requirements-adversarial`.
Plan stage — `implementation-adversarial`, `testability-adversarial`. Whole diff —
`regression-adversarial`, `silent-failure`, `test-honesty`, and one blind reviewer
(acceptance criteria + diff only). All foreground, all fresh, none given the
author's reasoning.

The diff-stage `test-honesty` and blind reviewers **ran real mutations** against
the full suite rather than reasoning about coverage — several rows below are
"proven vacuous", not "looks weak".

## Spec stage

| # | Lens | Finding | Verdict | Reason |
|---|---|---|---|---|
| S1 | feasibility | `AC11` was factually wrong: `String(32)` does not enforce a cap. SQLite ignores VARCHAR length, so an over-long blurb passes the whole suite and 500s on Postgres in prod | **fix now** | Real prod-only bug. Added `clean_agent_blurb`, column-derived, 400 — mirrors the existing `clean_agent_name` and its comment naming this exact trap |
| S2 | feasibility | `AC1` ("fits one screen at 1280×860") is untestable — no browser in the suite | **fix now** | Restated as DOM-assertable (row counts, absent legacy markup); pixel half moved to manual verification, recorded in the PR |
| S3 | feasibility | Agent detail page has no route that can accept a blurb, and its name input auto-submits on change — a shared form would fire a rename | **fix now** | New `POST /me/agents/{id}/set-blurb` with its own form |
| S4 | feasibility | `tests/test_migrations.py:187` pins head at `0046` | **fix now** | Bumped to `0047`, plus a column-presence + downgrade test |
| S5 | feasibility | POST-contract list was wrong three ways (missed `bot_id`, `strategy_prompt`; `display_name` isn't posted by this page; `ai_for_<id>` is UI-only) | **fix now** | Non-goal rewritten to split server-read fields from the UI-only name a test pins |
| S6 | feasibility | "Use last lineup" is hidden whenever the previous match is still running, because those seats hold those AIs | **reject** | Correct behaviour, not a bug — the lineup genuinely can't be submitted then. Recorded so it isn't "fixed" later by excluding the source match from the busy check. Moot after the feature was cut (S13) |
| S7 | feasibility | State table invented behaviour: "every AI busy → rows still tick" isn't preserved, today the whole section vanishes | **fix now** | Re-labelled as new behaviour and implemented |
| S8 | requirements | The FR-027 notification-permission hook would be silently deleted | **fix now** | Kept, with a stable hook on the manual row |
| S9 | requirements | All three links out (agents, connections, new agent) would be deleted, dead-ending a user with 2 agents and 1 AI | **fix now** | `+ New agent` and `Connect another AI` kept once each in the footer |
| S10 | requirements | Hidden-but-focusable pills would trap keyboard users | **fix now** | `hidden` + an explicit `display:none` rule (see D2) |
| S11 | requirements | Day one every agent has no blurb, so rows are *less* distinguishing than today | **accept** | Recorded as DD6. Backfilling from `version.note` gains nothing — those are empty too |
| S12 | requirements | Radio group loses its only accessible name | **fix now** | `role="radiogroup"` + `aria-label` per row |
| S13 | requirements | `_default_entry_choice` vs "Use last lineup" conflict unresolved | **fix now** | Resolved by DD4; then the whole feature was **cut** on Chris's call — least testable, not part of the request. Coverage loss recorded, follow-up spawned |

## Plan stage

| # | Lens | Finding | Verdict | Reason |
|---|---|---|---|---|
| P1 | implementation | Per-row "no free AI" is not computable server-side; the third row ticked with two free AIs would post `chosen_provider="undefined"` | **fix now** | The best catch of this stage. `refreshRows` now recomputes each checkbox's disabled state, and `setCard` refuses when nothing is free |
| P2 | implementation | `hidden` would not hide anything — no global `[hidden]` rule, and `display:flex` beats the UA default | **fix now** | Shipped `.lineup-pills[hidden] { display: none; }` |
| P3 | implementation | `setCard` doesn't check the radio, so an auto-picked AI renders unselected — which kills AC4's justification for the plain `Join` label | **fix now** | `radio.checked` moved into `setCard` |
| P4 | implementation | Naive `_default_human_choice` would pre-tick "Play manually" for everyone, letting one click seat a human | **fix now** | Fallback deliberately deleted; documented in the docstring; now pinned by a test (T1) |
| P5 | implementation | `clean_agent_blurb` must be re-exported from `agents_setup`, not imported from `agents_create` | **fix now** | Would have been an ImportError |
| P6 | implementation | `web_player.py:84` `__all__` re-export — missing it breaks app import at startup | **fix now** | Renamed |
| P7 | implementation | `style.css?v=` cache-buster not bumped — returning users get old CSS with new class names | **fix now** | Bumped to `v=102` |
| P8 | implementation | `data-last-lineup` JSON in a double-quoted attribute produces broken HTML (`tojson` doesn't escape `"`) | **reject** | Moot — feature cut (S13) |
| P9 | implementation | CSS deletion list wrong in both directions; `.pick-row`/`.agent-card-hd` sharing was a false alarm; six more orphans unlisted | **fix now** | All verified join-only and removed; re-checked against every template |
| P10 | implementation | `VersionStats` import also becomes unused (ruff F401) | **fix now** | Whole import line and the `stats` key removed |
| P11 | testability | AC7's proposed pairing test **already exists on main** and would pass with `join.html` deleted | **fix now** | Replaced with structural tests on the rendered HTML (positional pairing across rows) |
| P12 | testability | The suite never runs migrations (`create_all`), so a model/migration mismatch is invisible | **fix now** | Added the `0047` column test with a real up+down |
| P13 | testability | Existing `test_admin_stacks_multiple_agents` asserts only seat names — a green light for exactly the silent admin mis-pairing | **fix now** | Now pins `{seat_name: chosen_provider}` |
| P14 | testability | An existing test asserts the seated row's `agent_id` mirror exists — the very thing R3 says to remove; risk is it gets "fixed" by keeping the mirror | **fix now** | Re-pointed at `data-agent-name`, plus a negative assertion that seated rows post nothing |
| P15 | testability | Proposed a CSS class-reference lint test as a durable guard for the layout | **defer** | Real and worth having, but it's a repo-wide tooling change, not this feature. Follow-up spawned |

## Whole-diff stage

| # | Lens | Finding | Verdict | Reason |
|---|---|---|---|---|
| D1 | silent-failure | **Back-button restore.** The browser restores a checkbox's `checked` but not the JS state beside it, so a restored page showed a row ticked while posting nothing for it — the agent silently missed the match. Variant: the AI it appeared to hold read as free, so the next row ticked got handed it. Reproduced in a real browser | **fix now** | The most serious finding of the run. `adoptRestoredState()` re-derives every row from the DOM on load and on every `pageshow`. Fix verified in-browser on a real `back_forward` navigation |
| D2 | regression | The Join button shipped `disabled`, so with JavaScript off the page couldn't be used at all — previously the human seat worked without JS | **fix now** | Ships enabled; `sync()` disables it immediately |
| D3 | regression | A disabled Join button looked identical to a live one (no `.btn:disabled` rule): full orange, pointer cursor. Tap, nothing happens, no explanation | **fix now** | Added `.btn:disabled`. Note it's an app-wide rule, not join-scoped |
| D4 | regression | An untickable row gave no reason — its busy pills are hidden behind the tick, and the tick is disabled | **fix now** | Rows now show "No AI free" |
| D5 | regression | `_default_human_choice` dropped the "never start with nothing selected" fallback, so an AI-only returning user with all AIs busy gets an inert page | **fix differently** | Keeping the fallback is worse: it would pre-tick a human seat for every AI-only user, and accidentally entering a ranked match by hand beats an extra click. Addressed via D3 + D4 so the page explains itself instead |
| D6 | blind | `.lineup-row:last-of-type` never matches — `:last-of-type` keys on tag name and `.lineup-foot` is the last div, so the list had no bottom edge | **fix now** | Replaced with `.lineup-row + .lineup-foot` |
| D7 | blind | The absolute start time was dropped for scheduled matches — "starts in 3 min" is fine, "starts in 3 days" is not | **fix now** | Restored. The original cut was over-eager |
| D8 | blind / regression | `any_pickable_ai` is computed and passed but read by no template — dead context key | **fix now** | Removed |
| D9 | test-honesty | `assert chunk.count("disabled") >= 2` proven vacuous — the pills alone satisfy it | **fix now** | Re-anchored per mirror tag |
| D10 | test-honesty | `assert "rated matches" not in r.text` proven vacuous — `record_label` is `None` at zero rated matches, so main emitted nothing either | **fix now** | Removed the no-op; the real guard (which seeds a completed win) strengthened to catch a v-line in any format |
| D11 | test-honesty | Deleting `test_join_defaults_to_agent_when_last_entry_was_agent` left `_default_human_choice`'s False branch uncovered — mutating it to `return True` passed all 1472 tests | **fix now** | Highest-value coverage gap. Added tests for both directions; mutation now fails |
| D12 | test-honesty | The mirror tripwire passed with the entire script deleted, and with `am.disabled` flipped `false`→`true` | **fix now** | Now asserts the exact assignments in `setCard`/`clearCard`; the one-token flip mutation now fails |
| D13 | test-honesty | Within-row ordering of the two mirrors is meaningless (only cross-row order matters) and would fail on a harmless reorder | **fix now** | Assertion removed; cross-row ordering keeps its own test |
| D14 | test-honesty / blind | **The inline JavaScript has no executable coverage.** Deleting the whole script, or flipping one mirror assignment, left 1472 tests green | **accept, disclosed** | The honest ceiling: this repo has no browser or JS harness, and adding one is a repo-wide tooling decision, not part of this feature. Mitigated as far as the repo allows — structural HTML tests, the tightened source tripwire (D12), and hands-on browser verification recorded in the PR. **Stated plainly in the PR rather than implied away.** Follow-up spawned |
| D15 | test-honesty | AC16/AC17 (blurb on the agent page, agents list, create form) had zero coverage — deleting all three left the suite green | **fix now** | Three render tests added, including that the blurb form is not the auto-submitting rename form |
| D16 | test-honesty | `test_human_plus_agent_join` name and docstring still promise "both independent choices" but it now only asserts a row renders | **defer** | Cosmetic naming drift in a test that still passes for a real reason. Noted; not worth a rename in this diff |
| D17 | test-honesty | `test_strategy_workspace_pages` docstring cites `test_agent_page_shows_version_line`, which doesn't exist | **fix now** | Reworded |
| D18 | test-honesty | The `0047` migration test checks column presence but not length/nullability | **accept** | `app/sqlite_parity.py` enforces the model's length at flush time in tests, and model and migration were confirmed identical |
| D19 | silent-failure | Blurb escaping in `value="{{ agent.blurb or '' }}"` | **no change needed** | Verified through the app's own Jinja env: autoescape is on, quote-breakout is not possible |
| D20 | regression | The pill-click handler's "tick the row too" branch is unreachable while unticked rows' radios are disabled | **fix now (comment only)** | Kept as a guard, but the comment no longer claims it is load-bearing |
| D21 | silent-failure | Join POST rejections render raw JSON instead of the page's `error` slot | **defer** | Pre-existing, and this change makes the new error states unreachable rather than worse. Follow-up spawned |
