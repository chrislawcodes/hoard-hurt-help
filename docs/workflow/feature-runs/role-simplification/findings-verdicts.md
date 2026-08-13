# Findings verdict table — whole-diff review fan

Five fresh foreground reviewers ran on the complete diff, in parallel:
`silent-failure` (S), `regression-adversarial` (R), `completeness-adversarial`
(C), `test-honesty` (T), and a **blind** reviewer (B) given only the acceptance
criteria and the diff — no spec, no plan, no author reasoning.

**Every finding from every reviewer has a row. Nothing was dropped.**
Totals: 47 findings — 26 fixed now, 4 rejected with evidence, 17 deferred with a
reason (all documentation, naming, or pre-existing issues; each named in the PR).

## Blockers

| # | Lens | Finding | Verdict | Reason |
|---|------|---------|---------|--------|
| R1 | regression | **Deleting a match created through the merged route returns 500.** `create_match_with_state` seeds a `MatchState` row; `delete_match` never cleared it, so the foreign key blocked the delete | **fix now** | **Real, reproduced.** Every Delete button on `/me/matches` answered 500. The gap pre-existed for admin-created and Liar's Dice matches; routing the one player create path through `create_match_with_state` made it everyone's. Every delete test seeds a bare `Match` row, which is why 1648 green tests missed it. `PlayerState` had the same gap and went with it |
| B1 | blind | The suite is intermittently red — ~4 failures in 8 runs | **reject** | **Not real.** The `test-honesty` reviewer was mutation-testing the same worktree at that moment; the `completeness` reviewer independently noticed the concurrent edits. The specific failures B names (a `role=USER` user getting 200, a re-added `/games/{game}/admin/matches/new` route) are literally mutations #6 and #53 from T's own table. Verified: **8/8 green** on a clean worktree, then 1658/1658 green after every fix |

## Real

| # | Lens | Finding | Verdict | Reason |
|---|------|---------|---------|--------|
| S1 | silent-failure | The hidden-game 404 guard is untested on 7 of 9 routes — `ALL_GATED_PATHS` used an unseeded match id, so the match loader produced the 404 by itself | **fix now** | Proven by deleting the guard: all 1648 tests still passed while five routes leaked the game's existence via 403 and two handed over the whole match body. The match is seeded now; deleting any one guard fails 2–3 params, deleting all four fails 9 |
| S2 | silent-failure | Nothing links an owner to the manage/bots page they were just granted — the owner half of this change was inert in production | **fix now** | Correct and important: a player who created a match could not seat bots without hand-typing a URL. `/me/matches` now carries a "Manage →" link |
| T1 | test-honesty | `delete_match`'s new `PlayerState` clear has zero coverage, and removing it is a real 500 for Liar's Dice | **fix now** | Only Liar's Dice writes `PlayerState`, so the hoard-hurt-help delete test cannot reach that branch. New test creates a Liar's Dice match through the real route |
| T2 | test-honesty | "Exports are open to every signed-in player" is unpinned — every export test uses the owner or an admin | **fix now** | Narrowing `load_exportable_match` to owner-or-admin left the suite green. New stranger test asserts 200, all prompts `null`, in-flight turn absent, resolved turn present |
| T3 | test-honesty | The owner-link test is a substring check a broken link survives | **fix now** | Verified: appending `/nope` to `manage_url` kept it green. Now asserts the exact `href` and follows it |
| T4 | test-honesty | `can_manage` is unpinned in the negative — `/me/matches` lists matches you *play in*, so a participant could be shown a link that 403s | **fix now** | Same dead-button failure the force-start row already pins. New test: a guest seated in someone else's match sees no link, and the link would indeed have 403'd |
| T5 | test-honesty | "Hidden game absent from every listing" is 2/3 vacuous — on an empty database the slug is absent for admins too | **fix now** | Split: the catalog half is meaningful as-is (an admin does see the slug) and now asserts both directions; the leaderboard and home-page halves get an injected section with an admin leg proving the injection lands |
| T6 | test-honesty | All 75 tests are excluded from the fast lane — `reset_db` was `autouse=True` | **fix now** | Dropped `autouse`; the two pure-logic tests now run in the fast lane |
| C5/B5 | completeness, blind | A validation error blanks the whole create form | **fix now** | The form went from 2 fields to 7 in this change, so one mistyped number cost a player all six others plus their start time. All 11 error paths now echo the submission back; the template binds it |
| C6/B3 | completeness, blind | `dice_per_player` is validated on games that have no dice | **fix now** | A hoard-hurt-help create could be rejected with "Dice per player must be 1 to 20." Scoped to games with module-owned config |
| B4 | blind | `state_config_for` lives in a module named for admin actions, imported by the one create path every player uses | **fix now** | The name actively misleads — the next reader would assume the create path is admin-gated, which is the confusion this change exists to remove. Moved to `app/engine/match_creation.py` |
| S3 | silent-failure | `dice_per_player` unbounded on the HTML route while the JSON API bounds it 1–20 | **fix now** | A zero-dice match is a wedged match, not a rejected request |
| R5/B10 | regression, blind | An admin creating from the per-game dashboard is dumped on `/me/matches` | **fix now** | The loop is broken — they came from the dashboard and do not get back |
| B11 | blind | The manage link is emitted in the completed and cancelled card sections, where its condition is structurally always false | **fix now** | Dead markup in two of three places |
| B10 | blind | The link says "Add bots →" but opens the manage page, needing a second click | **fix now** | Relabelled "Manage →" |
| S6/C7/R6/B6 | four lenses | The `game_not_found_status` 404 branch is unreachable and its docstring describes a deleted caller | **fix now** | Dropped the parameter |
| S4 | silent-failure | `players[].agent_id` is an internal DB key while `submissions[].agent_id` is a seat name — one document, two value spaces | **defer** | Pre-existing shape, unchanged by this diff. Real for a player consuming the export, but changing a payload key is a separate, wider change. **Follow-up recorded in the PR** |
| R3 | regression | A live `GAME_ADMIN_EMAILS__*` variable silently stops granting anything | **fix now (as a deploy step)** | Correct and important. `platform_admin_emails_set` still falls back to `ADMIN_EMAILS` and `auth.py` re-stamps the role at sign-in, so an admin listed there keeps access. The PR carries the pre-merge check |
| C1/R2/B2 | three lenses | `docs/platform/AGENT_LUDUM_ARCHITECTURE.md` (lines 108, 114–118, 399, 641–643) and `AGENT_LUDUM_DESIGN.md:336` document the deleted modules and the removed role | **defer — scope fence** | The brief explicitly forbids touching `docs/` outside this run folder. Real and important (`CLAUDE.md` makes that doc mandatory reading), so it is the top follow-up: exact line numbers in the PR, in `STATUS.md`, and in spec §8 |
| C2 | completeness | No `STATUS.md` entry, and `STATUS.md` still describes the removed role | **fix now** | Outside the Small-Change Lane, so the constitution requires it. Entry added in this worktree, and the two stale lines corrected |
| T9 | test-honesty | The per-game dashboard's `Match.game == game` filter and the prompts page's query have no content-level test | **defer** | Both queries are pre-existing and unchanged by this diff; the modules moved, the SQL did not. Worth a test, but it is not this change's gap. **Follow-up recorded in the PR** |
| B6 | blind | The removed role survives in the URL prefixes (`/games/{game}/admin`, `/api/game-admin/`) and in two page titles | **partial fix** | Titles fixed. URLs kept deliberately: they use a hyphen, never matched the definition-of-done grep, and renaming a public API path is a separate change with redirect obligations. **Stated plainly in the PR rather than left implied** |
| B3 | blind | The platform now hardcodes individual games in three new places, against `app/games/base.py`'s protocol contract | **partial fix** | The slug switch is now one function in the engine (`game_owns_match_config` / `state_config_for`) rather than scattered, and the player route asks it rather than comparing slugs itself. Pushing it onto `GameModule` is the right end state but is a game-platform refactor, not a role change. **Follow-up recorded in the PR** |

## Minor

| # | Lens | Finding | Verdict | Reason |
|---|------|---------|---------|--------|
| T7 | test-honesty | The bots-seating test asserts a status code where seating is the feature | **fix now** | Now asserts the seated `Player` row |
| T8 | test-honesty | The admin-cancel leg is status-only | **fix now** | Now asserts the match reaches `CANCELLED` |
| T10 | test-honesty | Single-value `parametrize` is dead scaffolding | **fix now** | Inlined |
| T11 | test-honesty | One assertion is structurally redundant; the git scan errors rather than skipping outside a checkout | **fix now** | Redundant assert removed; the scan now skips |
| S6 | silent-failure | The repo scan walks the working directory, so an untracked scratch file reddens the suite | **fix now** | Verified by accident during review. Walks git-tracked files now |
| B7 | blind | `_create_context` takes an unused `request` | **fix now** | Dropped |
| B8 | blind | `_deny_not_admin` / `_deny_not_owner` are typed `-> None` but always raise | **fix now** | Both typed `NoReturn` |
| C3 | completeness | `web_match_loaders.py:35` still names "game-admin" as a caller family | **fix now** | Reworded |
| C4/R4/B12 | three lenses | A bookmarked `/games/{game}/admin/matches/new` now 404s with a body about a missing match | **defer** | Reachable only by a stale bookmark; every in-app link was repointed and a test pins that. A redirect is polish, not correctness |
| B9 | blind | `_is_any_admin` keeps a name its own docstring concedes is now wrong | **defer** | ~20 call sites. A mechanical rename mixed into an auth change adds diff noise for no behaviour change; the docstring says plainly what it means |
| B13 | blind | A player posting `mutual_help_mode` gets it silently swapped rather than rejected | **reject** | Deliberate and already commented. The control is not rendered for a player, so a value there means a hand-made request; rejecting it would teach a prober that the field exists and matters. The stored value is pinned by a test |
| B14 | blind | Player-created hoard-hurt-help matches now write an unread `MatchState` row of `{"config": {}}` | **defer** | One row per match, never read. The alternative — seeding conditionally — reintroduces two creation shapes, which is what this change removed. The delete fix (R1) makes it harmless |
| B15/C2 | blind, completeness | `STATUS.md` not updated | **fix now** | Same as C2 |
| R7 | regression | Two untracked probe files from a parallel reviewer sitting in the worktree | **fix now** | Deleted before the commit; `git status` verified clean |
| S5 | silent-failure | Architecture doc and `STATUS.md` stale | **split** | `STATUS.md` fixed; docs deferred per the scope fence |
| C3 (2) | completeness | `is_game_admin` template key was already dead on `origin/main` | **note only** | Confirms the removal is complete; no action |
| B12 | blind | Unknown game slug on the create form returns raw JSON to a browser POST | **defer** | Reachable only by hand-editing the URL. The 404 is the same answer every other route gives for a game that does not exist, and a test pins it |

## Mutation results after the fixes

Every mutation that previously survived is now caught:

| Mutation | Before | After |
|----------|--------|-------|
| Export narrowed to owner-only | none caught | 1 failed |
| `PlayerState` delete removed | none caught | 1 failed |
| `can_manage` always true | none caught | 1 failed |
| `manage_url` broken | none caught | 1 failed |
| All four hidden-game guards deleted | 9 params passed on unseeded ids | 9 failed |

The `test-honesty` reviewer ran 61 further mutations against the pre-fix code and
56 were already caught, most of them by this matrix.
