# Plan — 8/4 Betrayal Payoff Re-split

Builds the design settled in `spec.md`. The spec's two review rounds already
resolved the open decision (D1 → dedicated `betrayal_bonus` key) and enumerated
every touchpoint, so this plan is the route to build that design. It also folds
in the `reuse-report.md` verdicts (all reuse/extend; no new module).

## Review Reconciliation

- review: reviews/spec.claude.feasibility-adversarial.review.md | status: accepted | note: Round 2: no HIGH/MED feasibility defects — reviewer CODE-CONFIRMED all round-1 resolutions are sound. 2 LOW: ARCHITECTURE.md has no BETRAYAL_HURT_POINTS token (redundant listing, harmless — keep for prose refresh); betrayal_bonus needs a feed-chip consumer -> already fixed by adding turn_block.html to scope + AC5 in the final revision.
- review: reviews/spec.claude.requirements-adversarial.review.md | status: accepted | note: Round 2: MED F1 (turn_block.html out of scope but AC5 needs it) -> FIXED in final revision: turn_block.html added to scope with an explicit +betrayal_bonus chip render, §3.4 + AC5 updated. LOW F2 (no feed-render test) -> §8 now asserts the +4 reaches rendered HTML. LOW F3 (stale 'decays each round' legend text) -> explicit decision: leave it (pre-existing, out of scope), edit only the Hurt clause.
- review: reviews/plan.claude.implementation-adversarial.review.md | status: accepted | note: HIGH (Slice 1 not preflight-green: viewer.py imports BETRAYAL_HURT_POINTS + test_inround_mirror.py imports viewer at module top -> Slice-1 pytest collection + mypy fail after rename) -> FIXED: restructured slices so the constant rename + ALL Python importers (rules+scoring+viewer+all Python tests) are ONE atomic green slice; alias shim rejected (AC6). MED (computeScores second JS loop under-counts betrayer +4) -> D2 now enumerates BOTH JS loops (playAction + computeScores); R-A human checklist. LOW (enumerate the -8 test flips; test_game_registry needs no edit) -> D5 + Slice 1 now name each flip and mark registry a no-op. R-B/R-C/R-D captured (superlatives sign-off; chip placement; impact-review doc body grep-clean).
- review: reviews/plan.claude.testability-adversarial.review.md | status: accepted | note: MED F1 (two JS loops) -> D2.3/D2.4 both loops. MED F2 (rc-JSON threading untested but Python-testable) -> D5 adds a Python test asserting rc_data carries betrayed_helper for a betrayal turn. MED F3 (mirror parity must be explicit dict) -> D5 pins {'A':8,'B':6} from {'A':0,'B':10}. LOW F4 (mirror floored betrayal untested) -> D5 adds a floored mirror case (victim 5->1). LOW F5 (R5 line-brittle) -> R5 anchors on code text not line number. LOW F6 (Slice-1-green needs enumerated rules-text rewrites) -> D5/Slice 1 enumerate them.
- review: reviews/diff.claude.regression-adversarial.review.md | status: accepted | note: Final regression review: NO correctness/regression bugs in the shipping code path — independently verified resolver +8/-4, mirror parity, both JS loops agree, no match_summary gift-leak, Team-Attack -8 preserved, floor correct. M1 (MEDIUM, stale -8 betrayal comments in app/engine/bots/plan_rules.py + trust.py) accepted and FIXED in commit e67dafab. L1 (LOW, chip gated on betrayal_bonus truthiness) deferred — latent-only, correct today. Applying M1's fix re-dirtied implementation.diff.patch, so the engine flags the assembled review 'stale' (hash mismatch) — logged as friction; the review's substance is complete and its findings reconciled.

## 1. Architecture decisions

### D1 — Constant is an attacker bonus, victim uses the existing HURT_POINTS
`BETRAYAL_HURT_POINTS = 8` (victim's damage) → `BETRAYAL_BONUS = 4` (attacker's
gain). The victim's −4 reuses the existing `HURT_POINTS` constant — no new
victim constant. This is the single source of truth for the resolver, the mirror,
and the viewer import (reuse-report: extend `rules.py`).

### D2 — FOUR score computations must agree; edit each, never a fifth
Plan-review testability F1 corrected the count: there are **four** places that
compute the betrayal payoff (the JS file has TWO independent loops), and all must
move together (reuse-report duplication guard):
1. `resolve_turn` (authoritative, floors the summed delta) — change the HURT
   branch: victim `-= HURT_POINTS` (was `-= BETRAYAL_HURT_POINTS`), attacker
   `+= BETRAYAL_BONUS` (new line).
2. `apply_inround_turn` (Python viewer mirror, floors per-hurt) — TWO edits (spec
   §3.3 / review F1): (a) the victim `damage` ternary
   `BETRAYAL_HURT_POINTS if betrayal else HURT_POINTS` collapses to
   `damage = HURT_POINTS` (victim always −4); (b) ADD a new line
   `new_inround[actor] = new_inround.get(actor, 0) + BETRAYAL_BONUS` in the
   betraying-HURT case (no attacker line exists today; not floored — a gain).
3. `_replay_script.html → playAction` (the live animation, ~lines 915-916) —
   victim already `-4` (betrayal-unaware even under the old scheme, so no victim
   change), ADD attacker `+4`: `rScore[a.agent]=(rScore[a.agent]||0)+4;
   showDelta(el,4);` gated on the new `betrayed_helper`/`betrayal_bonus` field.
4. `_replay_script.html → computeScores` (the SNAPSHOT simulator, ~line 102 —
   **the one the first plan draft missed, review F1**) — `renderTurn` reseeds
   `rScore` from this snapshot at each turn start (~line 547), so it MUST also
   credit the betraying attacker `+4`, or the live total gains +4 mid-turn and
   loses it at the next turn's reset. Add the attacker `+4` on a `betrayed_helper`
   HURT here too. Its victim line stays `-4`.
The mirror-parity test (D5) guards (1)≡(2). The two JS loops (3,4) have no test
harness (accepted, R1) — they are guarded by an explicit human diff-review
checklist that BOTH loops were patched and agree.

### D3 — Attacker's +4 rides a dedicated `betrayal_bonus` key, not `display_delta`
`display_delta` on a HURT stays the victim's −4. A new `betrayal_bonus` int key is
set on the attacker's action in `build_pd_replay_view` (only when
`betrayed_helper`), threaded into `_build_rc_data`'s per-action JSON, and rendered
by `turn_block.html` as a `+4` chip. Rationale (spec §3.4, review F2/F3): keeps
`match_summary._superlatives`' `delta > 0` gift-scan from mislabeling a betrayal,
and preserves `test_viewer_shows_per_move_effect_on_target`. `move_effect` stays
nominal; `game.py` is untouched.

### D4 — Static/animated UI honesty (chip placement fixed by review R-C)
Two legends (`move_legend.html`, `robot_circle/_markup.html`) drop the false
`-8 if betraying` clause → 8/4 wording (victim −4; attacker +4 bonus). The Help
clause's pre-existing "decays each round" text is left alone (spec decision). The
animation shows the attacker's +4 (D2.3/D2.4). Two stale inline `-8` comments in
`viewer.py` (~331, ~353) are corrected.

**Feed-chip placement (review R-C).** The attacker's row in the feed is a **HURT**
row whose `display_delta` is now the victim's −4. A naive `+4` chip could read as
if the attacker's row is "−4" and bury the +4, defeating the whole re-split's
visibility goal. So the `+{{ a.betrayal_bonus }}` chip must render as a **distinct,
clearly-positive** element on the attacker's row (its own `delta pos`-styled span
with a short "betrayal +4"-style label or the existing betrayal tag), visually
separate from the −4-on-target chip — and the `test_viewer.py` HTML assertion
checks the rendered `+4` is present on the attacker's row (review F2/R4 guards
presence; R-C is why placement must be explicit).

### D5 — Testing pins the invariant at every mirror (tightened by plan-review)
- **Resolver** (`test_resolver.py`): betrayal → attacker +8 / victim −4 (victim
  seeded high to dodge the floor); non-betrayal HURT −4; betrayal+floor (victim
  seeded low → ends 0, summed-delta floor); multi-attacker (only the helped
  attacker gets the bonus; a third HURTer stays −4).
- **Mirror** (`test_inround_mirror.py`): rewrite `test_mirror_betraying_a_helper_
  is_eight` (currently asserts the OLD `{"A":4,"B":2}`) to an **explicit end-state
  dict** that pins the victim at start−4 — from `{"A":0,"B":10}` the result is
  `{"A":8,"B":6}` (attacker +8, victim 10−4=6). Stating the exact dict (not just
  "+8/−4") is required (review F3) so a victim-still-−8 bug can't pass. Add a
  **floored** mirror betrayal case (victim seeded low, e.g. 5 → 1) since the
  changed damage moves the per-hurt floor boundary (review F4). Refresh the module
  docstring (names the old constant).
- **rc-JSON threading** (`test_inround_mirror.py`, extending the existing
  `_build_rc_data` unit test pattern): assert the `rc_data` JSON for a betrayal
  turn carries `betrayed_helper: true` (and the bonus) on the attacker action —
  this is the Python guard for review F2 (a forgotten thread would leave the feed
  chip at +4 but the animation silent, and no other test would catch it).
- **Feed chip** (`test_viewer.py`): the rendered feed HTML for a betrayal contains
  the attacker's `+4` (not merely `betrayal_bonus == 4` on the payload — review F2/R4).
- **Registry** (`test_game_registry.py`): `move_effect("HURT") == (0, -4)` still
  holds (net-new file unchanged in intent; the betrayal_bonus assertion lives in
  the viewer test).
- **Rules text** (`test_rules_text.py`): replace the old `-{BETRAYAL_HURT_POINTS}`
  and `!= HURT_POINTS` assertions with the new +4/−4 wording + `(v5)` — these are
  in Slice 1 so it stays green alone (review F6).

## 2. Slice breakdown (each `[CHECKPOINT]`-bounded, ≤ ~300 lines)

**IMPORTANT — slice boundary corrected by plan-review HIGH finding.** The first
draft put `rules.py`'s constant rename in Slice 1 but deferred `viewer.py`'s
*import + use* of that constant to Slice 2. That is NOT preflight-green in
isolation: `viewer.py:4` imports `BETRAYAL_HURT_POINTS`, and
`tests/test_inround_mirror.py:20` imports `from …viewer import _build_rc_data` at
module top — so Slice 1's own `pytest` collection (and `mypy app/`) would fail on
the missing name. AC6 forbids an alias shim. Fix: **the constant rename and every
Python importer of it live in one atomic slice.** New breakdown:

**Slice 1 — All Python + its unit tests (one atomic, preflight-green unit).**
Files: `rules.py` (rename `BETRAYAL_HURT_POINTS`→`BETRAYAL_BONUS`, value 8→4;
`GAME_RULES_TEXT` betrayal bullet → attacker +4 / victim −4; header `(v4)`→`(v5)`),
`scoring.py` (`resolve_turn` + `apply_inround_turn` per D2.1/D2.2 + both
docstrings), `viewer.py` (switch the import to `BETRAYAL_BONUS`; **drop** the
`-BETRAYAL_HURT_POINTS` `display_delta` override so a HURT shows the victim's −4;
add the `betrayal_bonus` key on the attacker's action; add `betrayed_helper` +
`betrayal_bonus` to `_build_rc_data`'s `rc_actions` dict; fix the two stale `-8`
comments ~331/~353), `game.py` **unchanged** (verify `move_effect` stays nominal).
Tests: `test_resolver.py`, `test_inround_mirror.py` (incl. the rc-JSON threading
assertion + floored case + docstring), `test_rules_text.py`, and the viewer/registry
tests `test_viewer.py` (feed-HTML +4 assertion) + `test_game_registry.py` (verify
`move_effect("HURT")==(0,-4)` still holds — **no edit needed**, review LOW-4).
This whole slice is green together — the resolver, mirror, agent text, viewer
payload, and all Python tests. Est. ~150 lines. `[CHECKPOINT]`

**Slice 2 — Static + animated templates (no Python; JS/HTML honesty).**
`turn_block.html` (a `+{{ a.betrayal_bonus }}` chip on the attacker's HURT row —
see D4 for placement so the +4 is not buried behind the −4), `move_legend.html`
and `robot_circle/_markup.html` (legend text → 8/4; edit only the Hurt clause),
`_replay_script.html` (**both** JS loops per D2.3/D2.4: `playAction` animation +4
and `computeScores` snapshot +4, gated on `betrayed_helper`; leave the mutual-HELP
line untouched). No automated tests here (no JS harness — R1); guarded by the
human diff-review checklist in R-A. Est. ~40 lines. `[CHECKPOINT]`

**Slice 3 — Docs.**
`HOARD_HURT_HELP_DESIGN.md` (three betrayal −8 sites → 8/4; **keep Team-Attack −8**
at line 57 + its edge-case bullet; keep the mutual-help `+8`/`8−k` lines),
`HOARD_HURT_HELP_ARCHITECTURE.md` (already refreshed in the Design stage — verify),
and mark `betray-helper-impact-review.md` superseded/implemented — **and update or
remove its body `BETRAYAL_HURT_POINTS = 8` references (lines ~39, ~43)** so a strict
`grep BETRAYAL_HURT_POINTS docs/games/` is clean (review R-D), not just a banner on
top. Est. ~60 lines. `[CHECKPOINT]`

## 3. Sequencing & parallelism
Strictly sequential — Slice 1 is the atomic Python+tests unit (constant + scoring
+ viewer payload + all Python tests, green together — the plan-review HIGH fix),
Slice 2 is the JS/HTML templates that consume the payload, Slice 3 documents what
1+2 shipped. No safe parallelism (all slices touch the same subsystem / same
source-of-truth constant). Slice 1 ends fully preflight-green; Slice 2 adds no
Python so it keeps preflight green (templates aren't type/lint/test-checked here)
and is guarded by the R-A human checklist; Slice 3 is docs only.

## 4. Testing strategy
Reuse the existing test harness (`_make_game_with_players`, `_submit`,
`resolve_turn`, the `client`/`reset_db` viewer fixtures). Per-slice: run the fast
lane (`.venv/bin/pytest -q -m "not integration"`) while iterating, full
`.venv/bin/pytest -q` at each checkpoint. The full Preflight Gate
(`.venv/bin/ruff check . && .venv/bin/mypy app/ mcp_server/ && .venv/bin/pytest -q`)
gates the branch.

## 5. Residual risks (each with a pre-merge verification)

- **R1 — Divergence across the FOUR score computations** (resolver,
  `apply_inround_turn`, and BOTH JS loops — count corrected by review F1).
  *verification:* the mirror unit test asserts the identical +8/−4 dict the
  resolver test asserts; a Python test asserts the rc-JSON carries
  `betrayed_helper` for a betrayal turn; `grep -rn "BETRAYAL_HURT_POINTS" app/
  docs/` returns nothing. The two JS loops have no harness — see R-A.
- **R2 — Stale −8 in the UI.** *verification:* `grep -rn "BETRAYAL_HURT_POINTS"
  app/ docs/games/` returns nothing; `grep -rn -- "-8" app/games/hoard_hurt_help/
  app/templates/` shows only the legitimate Team-Attack contexts (none in the
  betrayal path). A viewer test asserts the betrayal chip is not −8.
- **R3 — Team-Attack −8 wrongly changed.** *verification:* DESIGN.md line 57
  (`Team Attack … C takes −8`) and its edge-case bullet are unchanged after the
  edit; only the three betrayal lines (42/55/66) move.
- **R4 — attacker +4 present in payload but invisible on screen.**
  *verification:* a `test_viewer.py` assertion checks the rendered feed HTML
  contains the attacker's `+4` for a betrayal (not just `betrayal_bonus == 4` on
  the payload).
- **R-A — Both JS loops must be patched and agree (review F1, sharpened R1).**
  *verification (human, no JS harness):* a written diff-review checklist entry
  confirming BOTH `computeScores` (~line 102) AND `playAction` (~lines 915-916)
  credit the betraying attacker `+4`, and that a betrayal turn's rail total + the
  round-win credit end at attacker +8. This is the guard the automated tests
  cannot provide for JS.
- **R-B — Attacker's +8 swing is structurally excluded from the finale
  superlatives (accepted sign-off, review R-B).** Because the +4 rides
  `betrayal_bonus` not `display_delta`, `match_summary._superlatives` never
  reports a betrayal as the "biggest gift." This is intentional (it is why the
  separate field was chosen — avoids mislabeling a betrayal as a gift) and needs
  no code change; recorded so it is a conscious choice, not an oversight.
- **R-C — Feed-chip placement could bury the +4 (review R-C).** *verification:*
  the `test_viewer.py` HTML assertion checks the attacker's `+4` renders as a
  positive element on the attacker's row (D4), not merged into the −4 chip.
- **R5 — pre-existing JS mutual-HELP `+8` staleness (deferred).**
  *verification:* the Slice 2 edit to `_replay_script.html` touches only the HURT
  branches; a diff check confirms the `HELP && mutual → +8` line (in
  `computeScores`) is untouched.
