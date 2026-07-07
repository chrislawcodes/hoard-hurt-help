# Spec — 8/4 Betrayal Payoff Re-split

**Slug:** `betrayal-8-4-factory` · **Experiment arm:** thin-vs-factory (Factory) ·
**Routing:** silent-risk = yes → FULL FEATURE FACTORY.

## 1. Problem

Betraying a helper — HURTing a player who is HELPing *you* on the same turn —
today lands as an outsized punishment on the **victim**: they take **−8**
(`BETRAYAL_HURT_POINTS`) instead of the normal −4. The attacker gets no direct
bonus but still pockets the victim's +4 HELP. Net that turn: **attacker +4,
victim −8** — a 12-point relative swing.

The design intent is unchanged (betraying a same-turn helper should be a strong,
tempting play), but the *shape* is wrong: a −8 crater on the victim reads as
punitive and dominates the score floor and the viewer. We want the same 12-point
swing re-attributed so the **attacker rises** rather than the victim cratering.

## 2. Goal

Re-split the betray-a-helper payoff to **"8/4"**:

- The **victim** takes the **normal −4** (`HURT_POINTS`), not −8.
- The **attacker** gains a **new +4 bonus** (`BETRAYAL_BONUS`) on top of the +4
  HELP they receive from the victim.
- Net that turn: **attacker +8, victim −4.** The 12-point relative swing is
  unchanged — it is re-split so the attacker gains instead of the victim losing.

## 3. The mechanic (exact)

### 3.1 Constant

`app/games/hoard_hurt_help/rules.py`: replace

```python
BETRAYAL_HURT_POINTS = 8   # remove
```

with

```python
BETRAYAL_BONUS = 4         # attacker's extra gain when betraying a same-turn helper
```

The victim's damage is now just `HURT_POINTS` (= 4) — the same constant a normal
HURT already uses.

### 3.2 Authoritative resolver (`scoring.py → resolve_turn`)

In the HURT branch, when the target is HELPing the attacker this same turn
(`help_targets.get(victim) == attacker`):

- victim: `delta[victim] -= HURT_POINTS`  (−4, the normal amount — no longer −8)
- attacker: `delta[attacker] += BETRAYAL_BONUS`  (+4, the new bonus)

A non-betrayal HURT is unchanged: victim −4, attacker +0. The attacker still
receives the victim's +4 HELP through the ordinary HELP branch, so the
attacker's turn total is +4 (help) + +4 (bonus) = **+8**.

### 3.3 Running-score mirror (`scoring.py → apply_inround_turn`)

This is the **viewer's** running-score approximation (lead tracking). It floors
each HURT individually (deliberately distinct from the authoritative resolver,
which floors the summed delta — that divergence is preserved). Update its
betrayal handling to match the resolver: on a betraying HURT, the victim loses
`HURT_POINTS` (−4, floored at 0) and the attacker gains `BETRAYAL_BONUS` (+4).

**IMPORTANT (spec-review F1).** The mirror's HURT branch today modifies **only
the target** — there is no attacker-crediting statement to edit. A naive swap of
`BETRAYAL_HURT_POINTS → HURT_POINTS` therefore silently drops the attacker's +4
and re-introduces exactly the resolver/mirror divergence R1 exists to prevent.
The edit must **ADD a new statement** — `new_inround[actor] =
new_inround.get(actor, 0) + BETRAYAL_BONUS` — inside the betraying-HURT case (a
gain, not floored). The victim's own +4 HELP to the attacker is already credited
by the mirror's ordinary non-mutual HELP branch (its target is the attacker), so
the attacker's mirror **turn total** reaches +8 (+4 help + +4 bonus) — matching
the resolver. The mirror parity test (§8) asserts the full **attacker +8 /
victim −4**, not just the +4 bonus in isolation.

### 3.4 Viewer honesty (`viewer.py`, `game.py → move_effect`)

Resolves open decision **D1** → **option (a′)**: surface the attacker's +4 in a
**dedicated field**, NOT by overloading `display_delta` (spec-review F2/F3).

- Under 8/4 the victim's nominal per-move loss is **−4**, which now equals
  `move_effect("HURT")`'s nominal target delta. So the stale
  `-BETRAYAL_HURT_POINTS` (−8) override on the HURT chip's `display_delta` must
  go — the victim chip shows −4 via the ordinary `target_delta`. `display_delta`
  on a HURT stays the **victim's −4** (never positive).
- The **attacker's +4 betrayal bonus** is surfaced on the attacker's action in a
  **new key** — `betrayal_bonus` (int, present and `= BETRAYAL_BONUS` only on a
  `betrayed_helper` HURT; absent/`0` otherwise). It is NOT written into
  `display_delta`. Rationale (F2/F3): `display_delta` on a HURT already means
  "what this HURT does to its target" (−4), and `match_summary._superlatives`
  treats any **positive** `display_delta` as a "biggest gift" candidate — so a
  positive +4 in `display_delta` would (i) corrupt the −4-on-target signal
  `test_viewer_shows_per_move_effect_on_target` relies on and (ii) mislabel a
  betrayal as a gift in the finale. A separate `betrayal_bonus` key avoids both.
- **The feed-chip template must render it** (spec-review round-2 requirements
  finding, CODE-CONFIRMED). `app/templates/fragments/turn_block.html` renders the
  HURT row's delta from `a.display_delta` **only** (line ~29) — it has no
  `betrayal_bonus` reference, so without editing it the attacker's +4 would sit
  in the payload but never reach the screen. Add a small positive bonus chip on
  the attacker's HURT row that shows `+{{ a.betrayal_bonus }}` when
  `a.betrayal_bonus` is set. This is the surface AC5 means by "the feed chip …
  attacker +4 via the new `betrayal_bonus` key."
- `move_effect(action)` only receives the action *string* and has no turn
  context, so it cannot itself know a HURT is a betrayal. It stays **nominal**
  (`HURT → (0, -4)`), so `test_game_registry` and `test_viewer_shows_per_move_
  effect_on_target` remain valid. `game.py` is therefore **not** modified.
- The robot-circle payload (`_build_rc_data`) and the animation
  (`_replay_script.html`) also need the attacker's +4 to be honest (spec-review
  req-HIGH-2): thread `betrayed_helper` (and the `betrayal_bonus`) into the rc
  action JSON, and in the HURT animation show the attacker's `+4` (via
  `showDelta`) and credit the client running-score sim `+4` for a betraying
  attacker. The client sim's HURT victim line is already `-4` (it was betrayal-
  unaware even under the old −8 scheme), so the victim side needs no change —
  only the attacker credit is added.
- The existing cross-turn `betrayal` flag (HURT on *last* turn's pact partner)
  is a different signal and is left as-is. The same-turn `betrayed_helper` tag
  is the one that carries the payoff. Note (accepted, minor): the betrayal
  headline beat in `viewer_headline.py` weights by `abs(display_delta)`; since a
  betrayal's `display_delta` moves −8 → −4, a turn that is *both* a same-turn
  betrayal and last-turn's pact partner shifts headline priority slightly. This
  is cosmetic and acceptable — no code change required beyond what the −8 removal
  already does.

## 4. Scope

### In scope

- `app/games/hoard_hurt_help/rules.py` — constant rename (`BETRAYAL_HURT_POINTS`
  → `BETRAYAL_BONUS`, value 8 → 4) + `GAME_RULES_TEXT` "Betraying a helper"
  bullet rewrite (attacker +4 / victim −4) + header `(v4)` → `(v5)`.
- `app/games/hoard_hurt_help/scoring.py` — `resolve_turn` betrayal branch and
  `apply_inround_turn` betrayal branch (**add** the attacker-credit line, see
  §3.3) + their docstrings (both mention `BETRAYAL_HURT_POINTS` today).
- `app/games/hoard_hurt_help/viewer.py` — drop the `-BETRAYAL_HURT_POINTS`
  override on the HURT `display_delta`; add the `betrayal_bonus` key on the
  attacker's action; thread `betrayed_helper` into `_build_rc_data`'s per-action
  JSON; **and fix the two stale inline `-8` comments** at ~line 331 and ~line 353
  (spec-review F4 — AC6's constant-grep won't catch prose `-8`).
- `app/games/hoard_hurt_help/game.py` — **not modified.** `move_effect` stays
  nominal (see §3.4). Listed only to state explicitly it is untouched.
- **UI templates (spec-review req-HIGH-1, req-HIGH-2, round-2 req):**
  - `app/templates/fragments/turn_block.html` — the feed chip. Renders the HURT
    row delta from `a.display_delta` only; add a `+{{ a.betrayal_bonus }}` chip on
    the attacker's row so the +4 actually shows (gated on `a.betrayal_bonus`, NOT
    on the cross-turn `a.betrayal` flag).
  - `app/templates/fragments/move_legend.html` — the Hurt chip text literally
    says `-4 to another, -8 if betraying`; the `-8 if betraying` clause is now
    false (victim takes −4). Rewrite to reflect 8/4 (victim −4; attacker gains a
    +4 bonus when betraying a helper).
  - `app/templates/fragments/robot_circle/_markup.html` — same stale legend text
    (`-4 to another, -8 if betraying`). Rewrite identically.
  - `app/templates/fragments/robot_circle/_replay_script.html` — the HURT
    animation (`showDelta(T, -4)`, `rScore[...] -4`) never shows the attacker's
    gain; add the attacker `+4` on a betraying HURT and credit the client sim.
    Gate on the `betrayed_helper`/`betrayal_bonus` signal threaded into the rc
    JSON — not the existing cross-turn `betrayal` visual class.
- Tests: `tests/test_resolver.py`, `tests/test_inround_mirror.py`,
  `tests/test_viewer.py`, `tests/test_game_registry.py`,
  `tests/test_rules_text.py`.
- Docs: `docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md`,
  `docs/games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md`,
  `docs/games/hoard-hurt-help/betray-helper-impact-review.md` (mark
  superseded/implemented).

**Rename fan-out (spec-review req-5) — old → new symbol, every call site:**
`BETRAYAL_HURT_POINTS` → `BETRAYAL_BONUS` (semantics change: it is now the
attacker's bonus, not the victim's damage). Edit at: `rules.py` (definition +
`GAME_RULES_TEXT`), `scoring.py` (import + `resolve_turn` + `apply_inround_turn`
+ two docstrings), `viewer.py` (import + `display_delta` override), and the tests
below. In `tests/test_rules_text.py` the `f"-{BETRAYAL_HURT_POINTS}"` assertion
and the `BETRAYAL_HURT_POINTS != HURT_POINTS` assertion both become invalid
(the new bonus *equals* `HURT_POINTS`) — rewrite them to assert the new +4/−4
wording and the `(v5)` header. `tests/test_inround_mirror.py`'s module docstring
also names the constant (cosmetic; update).

### Out of scope (non-goals)

- Changing the 12-point relative swing magnitude.
- The **Team Attack** case (two players each HURT one victim for independent −4s,
  summing to −8). That −8 is not a betrayal and must stay.
- Retraining the win-probability models or adding an "exploiter" bot.
- Widening `move_effect`'s contract across other games (`liars_dice`) — avoid
  unless strictly necessary. (Resolved: NOT widened.)
- A migration/schema change. `points_delta` already stores the actual delta.
- **`app/games/hoard_hurt_help/match_summary.py` — confirmed UNAFFECTED** by the
  §3.4 decision. Because the attacker's +4 goes in the new `betrayal_bonus` key
  and `display_delta` on a HURT stays negative (−4), `_superlatives`' `delta > 0`
  gift-detection never sees a betrayal, so the finale summary is unchanged. (This
  is why D1 was resolved to a separate field rather than overloading
  `display_delta` — see §3.4.)
- **The client sim's stale mutual-HELP `+8`** (`_replay_script.html:100` credits
  a flat +8, ignoring mutual-help decay). This is a **pre-existing** bug
  unrelated to the betrayal change and is **deferred** — this feature only fixes
  the HURT/betrayal path in that file, not the mutual-help decay path.
- **The legends' stale "bonus decays each round" clause** (spec-review round-2
  LOW). `move_legend.html` and `robot_circle/_markup.html` both say `mutual +8
  each, bonus decays each round`; the decay is actually per-pair, per-**match**,
  not per round. This is **pre-existing** and unrelated to the betrayal payoff.
  **Decision: leave it** — the legend edit for this feature touches only the
  Hurt clause (the betrayal text); do NOT also rewrite the Help clause here, to
  keep the diff scoped to the betrayal change. (A separate cleanup can fix the
  "each round" wording.)

### Reality-check assumptions carried in (from discovery)

- **`viewer_win_probs.py` no longer exists.** It was deleted when the win-prob
  overlay was removed (see DESIGN.md §2 "Win-probability overlay — removed").
  The only running-score mirror is `apply_inround_turn` in `scoring.py`. The
  brief's reference to updating `viewer_win_probs.py` maps to that function.
- **`.claude/skills/game-design/references/boardgame-design-patterns.md` does
  not exist** in this checkout. That doc touchpoint has no file to edit; it is
  logged in the friction/notes rather than force-created.

## 5. Acceptance criteria

- AC1. Betraying a helper: attacker turn delta = **+8** (= +4 help + +4 bonus),
  victim's **raw** damage = **−4** (pre-floor). Proven by a resolver test that
  seeds the victim high enough that the floor does not trigger (spec-review
  req-3). The score floor still applies to the summed per-player delta as usual
  (AC3) — a betrayed victim already near 0 ends at 0 with a smaller persisted
  `points_delta`; AC1 is about the raw −4 attribution, not the floored delta.
- AC2. Non-betrayal HURT unchanged: victim −4, attacker +0.
- AC3. Score floor still applied to the **FINAL per-player delta**, not per-hurt
  (resolver). The mirror keeps its per-hurt floor (unchanged divergence).
- AC4. Agent-facing `GAME_RULES_TEXT` states attacker +4 / victim −4; header
  bumped `(v4)` → `(v5)`.
- AC5. Viewer shows the betrayal honestly across **every** surface: the feed chip
  (victim −4 via `display_delta`; attacker +4 via the new `betrayal_bonus` key),
  the robot-circle animation (attacker `+4` shown, client sim credited), and the
  two static legends (`move_legend.html`, `robot_circle/_markup.html`). **No
  stale −8 anywhere in the UI** — chip, caption, groups, animation, or legend
  text.
- AC6. No stale `BETRAYAL_HURT_POINTS` references remain in **shipping code, the
  UI templates, or the design/architecture docs** (spec-review req-7: scoped this
  way rather than "anywhere in the repo", because the token legitimately survives
  in this run's own `feature-runs/` artifacts and as a historical quote inside the
  now-superseded `betray-helper-impact-review.md`). Additionally, no stale literal
  `-8`/`−8` describing a *betrayal* (as opposed to Team-Attack) remains in the
  game module or the UI templates.
- AC7. Preflight green:
  `.venv/bin/ruff check . && .venv/bin/mypy app/ mcp_server/ && .venv/bin/pytest -q`.

## 6. Open decisions — RESOLVED

- **D1 — How to surface the attacker's +4.** *(Resolved by the spec-review
  round — see §3.4.)* Neither of the two originally-floated options is used
  verbatim; the chosen path is **(a′)**: a **dedicated `betrayal_bonus` key** on
  the attacker's action, `move_effect` left nominal, `game.py` untouched. This
  keeps `display_delta` semantically "what the HURT does to its target" (−4),
  avoids the `match_summary._superlatives` gift-mislabel (F3), and keeps
  `test_game_registry` / `test_viewer_shows_per_move_effect_on_target` valid.

## 7. Dependencies & sequencing

`rules.py` constant + text is the source of truth; `scoring.py` imports the
constant; `viewer.py` imports it too. Change the constant and both scoring
branches together (they must stay consistent), then the viewer payload + the UI
templates/animation, then tests, then docs. No external deps.

## 8. Validation plan

- Unit (`test_resolver.py`): resolver betrayal (attacker +8 / victim −4),
  non-betrayal HURT (−4), betrayal + floor (victim seeded low → ends 0, summed-
  delta floor), one-attacker-betrays-while-a-third-hurts (only the helped
  attacker gets the bonus; the third stays −4).
- Unit (`test_inround_mirror.py`): `apply_inround_turn` betrayal asserts the full
  **attacker +8 / victim −4** (not just the +4 bonus — spec-review req-4), so the
  mirror is proven equivalent to the resolver on the betrayal case. Existing
  `test_mirror_betraying_a_helper_is_eight` (currently `{"A":4,"B":2}`) is
  rewritten to the new expectation.
- Unit (`test_rules_text.py`): `GAME_RULES_TEXT` states attacker +4 / victim −4
  and carries `(v5)`; the old `-{BETRAYAL_HURT_POINTS}` and `!= HURT_POINTS`
  assertions are replaced.
- Unit (`test_viewer.py`, `test_game_registry.py`): the per-move chip shows the
  victim's −4 on the target; `move_effect("HURT") == (0, -4)` still holds; a
  betrayal exposes the attacker's `betrayal_bonus == 4` on the payload **and**
  the rendered feed HTML shows the attacker's `+4` (spec-review round-2 LOW F2 —
  assert the +4 actually reaches the screen, so the suite can't go green with the
  bonus present in the payload but invisible in the feed).
- Full Preflight Gate from the worktree root using `.venv/bin/`.

## 9. Risks

- **R1 — Silent scoring divergence (the reason this is full-factory).** The
  resolver, the Python mirror (`apply_inround_turn`), and the JS client sim can
  drift. *verification:* the mirror unit test asserts the **same** betrayal
  numbers the resolver test asserts (attacker +8 / victim −4); the JS sim's
  victim line is already −4 and the added attacker +4 mirrors the resolver.
- **R2 — Stale −8 left in the UI.** A −8 could linger in a caption, group delta,
  chip, animation, or **static legend text** (the two HIGH findings). *verification:*
  grep for `BETRAYAL_HURT_POINTS` across `app/` + `docs/games/`; grep for a literal
  `-8`/`−8` across the game module **and `app/templates/`**, distinguishing the
  legitimate Team-Attack −8 from a betrayal −8; a viewer test asserts the betrayal
  chip is not −8.
- **R3 — Team-Attack −8 wrongly changed.** *verification:* the design doc's
  "Team Attack: A and B both Hurt C → C takes −8" row and its edge-case bullet
  stay −8; the impact-review edit is limited to the betrayal rows. The three
  distinct betrayal `−8` sites in DESIGN.md (payoff-math bullet, worked-scenarios
  table row, edge-case bullet — spec-review F5) all change; the mutual-help
  `+8`/`8−k` lines stay.
- **R4 — Client sim mutual-HELP `+8` staleness is pre-existing and deferred.**
  `_replay_script.html:100` credits a flat +8 for mutual HELP, ignoring decay.
  Not introduced by this feature; explicitly out of scope. *verification:* the
  betrayal-path edit in that file does not touch the mutual-HELP line.
