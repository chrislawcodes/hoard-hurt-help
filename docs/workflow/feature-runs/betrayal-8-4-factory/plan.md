# Plan — 8/4 Betrayal Payoff Re-split

Builds the design settled in `spec.md`. The spec's two review rounds already
resolved the open decision (D1 → dedicated `betrayal_bonus` key) and enumerated
every touchpoint, so this plan is the route to build that design. It also folds
in the `reuse-report.md` verdicts (all reuse/extend; no new module).

## Review Reconciliation

- review: reviews/spec.claude.feasibility-adversarial.review.md | status: accepted | note: Round 2: no HIGH/MED feasibility defects — reviewer CODE-CONFIRMED all round-1 resolutions are sound. 2 LOW: ARCHITECTURE.md has no BETRAYAL_HURT_POINTS token (redundant listing, harmless — keep for prose refresh); betrayal_bonus needs a feed-chip consumer -> already fixed by adding turn_block.html to scope + AC5 in the final revision.
- review: reviews/spec.claude.requirements-adversarial.review.md | status: accepted | note: Round 2: MED F1 (turn_block.html out of scope but AC5 needs it) -> FIXED in final revision: turn_block.html added to scope with an explicit +betrayal_bonus chip render, §3.4 + AC5 updated. LOW F2 (no feed-render test) -> §8 now asserts the +4 reaches rendered HTML. LOW F3 (stale 'decays each round' legend text) -> explicit decision: leave it (pre-existing, out of scope), edit only the Hurt clause.

## 1. Architecture decisions

### D1 — Constant is an attacker bonus, victim uses the existing HURT_POINTS
`BETRAYAL_HURT_POINTS = 8` (victim's damage) → `BETRAYAL_BONUS = 4` (attacker's
gain). The victim's −4 reuses the existing `HURT_POINTS` constant — no new
victim constant. This is the single source of truth for the resolver, the mirror,
and the viewer import (reuse-report: extend `rules.py`).

### D2 — Three score computations must agree; add to each, never a fourth
There are exactly three places that compute the betrayal payoff, and all three
must move together (reuse-report duplication guard):
1. `resolve_turn` (authoritative, floors the summed delta) — victim `-HURT_POINTS`,
   attacker `+= BETRAYAL_BONUS`.
2. `apply_inround_turn` (Python viewer mirror, floors per-hurt) — victim
   `-HURT_POINTS` floored, **ADD** `new_inround[actor] += BETRAYAL_BONUS` (there
   is no attacker line today — spec §3.3 / review F1).
3. `_replay_script.html` client JS sim + animation — victim already `-4`
   (betrayal-unaware even under the old scheme, so no victim change), **ADD**
   attacker `+4` (`rScore[a.agent]+=4`, `showDelta(el,4)`) gated on the new
   `betrayed_helper` field.
The mirror-parity test (D5) is the guard that (1) and (2) agree on `+8 / -4`.

### D3 — Attacker's +4 rides a dedicated `betrayal_bonus` key, not `display_delta`
`display_delta` on a HURT stays the victim's −4. A new `betrayal_bonus` int key is
set on the attacker's action in `build_pd_replay_view` (only when
`betrayed_helper`), threaded into `_build_rc_data`'s per-action JSON, and rendered
by `turn_block.html` as a `+4` chip. Rationale (spec §3.4, review F2/F3): keeps
`match_summary._superlatives`' `delta > 0` gift-scan from mislabeling a betrayal,
and preserves `test_viewer_shows_per_move_effect_on_target`. `move_effect` stays
nominal; `game.py` is untouched.

### D4 — Static/animated UI honesty
Two legends (`move_legend.html`, `robot_circle/_markup.html`) drop the false
`-8 if betraying` clause → 8/4 wording (victim −4; attacker +4 bonus). The Help
clause's pre-existing "decays each round" text is left alone (spec decision). The
animation shows the attacker's +4 (D2.3). Two stale inline `-8` comments in
`viewer.py` (~331, ~353) are corrected.

### D5 — Testing pins the invariant at every mirror
Resolver test asserts attacker +8 / victim −4 (seeded high to dodge the floor)
plus non-betrayal −4, floor, and multi-attacker cases. Mirror test asserts the
**full** +8 / −4 (not just the +4). Rules-text + registry + viewer tests updated.
A viewer test asserts the attacker's +4 reaches the rendered feed HTML (review F2).

## 2. Slice breakdown (each `[CHECKPOINT]`-bounded, ≤ ~300 lines)

**Slice 1 — Scoring core + rules text (the authoritative change).**
`rules.py` (constant rename + `GAME_RULES_TEXT` bullet + `(v4)`→`(v5)`),
`scoring.py` (`resolve_turn` + `apply_inround_turn` + both docstrings), and the
scoring/rules tests (`test_resolver.py`, `test_inround_mirror.py`,
`test_rules_text.py`). This slice is self-contained and preflight-green on its
own: the resolver + mirror + agent-facing text are correct and proven, before any
viewer/UI work. Est. ~120 lines. `[CHECKPOINT]`

**Slice 2 — Viewer payload + UI honesty + viewer/registry tests.**
`viewer.py` (drop the −8 `display_delta` override; add `betrayal_bonus`; thread
`betrayed_helper` into `_build_rc_data`; fix the two `-8` comments), the four
templates (`turn_block.html` +4 chip; `move_legend.html` + `robot_circle/_markup.html`
legend text; `_replay_script.html` animation + client sim), `game.py` unchanged,
and the viewer/registry tests (`test_viewer.py`, `test_game_registry.py`). Est.
~120 lines. `[CHECKPOINT]`

**Slice 3 — Docs.**
`HOARD_HURT_HELP_DESIGN.md` (three betrayal −8 sites → 8/4; keep Team-Attack −8),
`HOARD_HURT_HELP_ARCHITECTURE.md` (already refreshed in the Design stage — verify),
and mark `betray-helper-impact-review.md` superseded/implemented. Est. ~60 lines.
`[CHECKPOINT]`

## 3. Sequencing & parallelism
Strictly sequential — Slice 1's constant + scoring is the foundation Slice 2's
viewer imports, and Slice 3 documents what 1+2 shipped. No safe parallelism (all
slices touch the same subsystem / same source-of-truth constant). Each slice ends
preflight-green so a diff checkpoint is meaningful.

## 4. Testing strategy
Reuse the existing test harness (`_make_game_with_players`, `_submit`,
`resolve_turn`, the `client`/`reset_db` viewer fixtures). Per-slice: run the fast
lane (`.venv/bin/pytest -q -m "not integration"`) while iterating, full
`.venv/bin/pytest -q` at each checkpoint. The full Preflight Gate
(`.venv/bin/ruff check . && .venv/bin/mypy app/ mcp_server/ && .venv/bin/pytest -q`)
gates the branch.

## 5. Residual risks (each with a pre-merge verification)

- **R1 — Resolver/mirror/JS-sim divergence.** *verification:* the mirror unit
  test asserts the identical +8/−4 the resolver test asserts; a grep confirms the
  three sites all use `BETRAYAL_BONUS`/`HURT_POINTS` (no lingering `8`). The JS
  sim's `+4` is human-verified against the resolver (no JS harness exists — an
  accepted, pre-existing gap).
- **R2 — Stale −8 in the UI.** *verification:* `grep -rn "BETRAYAL_HURT_POINTS"
  app/ docs/games/` returns nothing; `grep -rn -- "-8" app/games/hoard_hurt_help/
  app/templates/` shows only the legitimate Team-Attack contexts (none in the
  betrayal path). A viewer test asserts the betrayal chip is not −8.
- **R3 — Team-Attack −8 wrongly changed.** *verification:* DESIGN.md line 57
  (`Team Attack … C takes −8`) and its edge-case bullet are unchanged after the
  edit; only lines 42/55/66 (betrayal) move.
- **R4 — attacker +4 present in payload but invisible on screen.**
  *verification:* a `test_viewer.py` assertion checks the rendered feed HTML
  contains the attacker's `+4` for a betrayal (not just `betrayal_bonus == 4` on
  the payload).
- **R5 — pre-existing JS mutual-HELP `+8` staleness (deferred).**
  *verification:* the Slice 2 edit to `_replay_script.html` touches only the HURT
  branch; a diff check confirms line ~100 (`HELP && mutual → +8`) is untouched.
