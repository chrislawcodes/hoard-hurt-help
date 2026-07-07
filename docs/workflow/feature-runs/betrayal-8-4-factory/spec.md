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

### 3.4 Viewer honesty (`viewer.py`, `game.py → move_effect`)

- Under 8/4 the victim's nominal per-move loss is **−4**, which now equals
  `move_effect("HURT")`'s nominal target delta. So the stale
  `-BETRAYAL_HURT_POINTS` (−8) override on the HURT chip's `display_delta` must
  go — the victim chip shows −4 via the ordinary `target_delta`.
- The **attacker's +4 betrayal bonus** must be surfaced so the viewer is honest
  about who gains. `move_effect(action)` only receives the action *string* and
  has no turn context, so it cannot itself know a HURT is a betrayal. The
  attacker's bonus is a **turn-context** fact known in `build_pd_replay_view`
  (the `betrayed_helper` tag). Surface the +4 there, on the attacker's action,
  not by widening `move_effect`.
- The existing cross-turn `betrayal` flag (HURT on *last* turn's pact partner)
  is a different signal and is left as-is. The same-turn `betrayed_helper` tag
  is the one that carries the payoff.

## 4. Scope

### In scope

- `app/games/hoard_hurt_help/rules.py` — constant rename + `GAME_RULES_TEXT`
  "Betraying a helper" bullet rewrite (attacker +4 / victim −4) + header
  `(v4)` → `(v5)`.
- `app/games/hoard_hurt_help/scoring.py` — `resolve_turn` betrayal branch and
  `apply_inround_turn` betrayal branch + their docstrings.
- `app/games/hoard_hurt_help/viewer.py` — HURT `display_delta` (drop the −8
  override) and surface the attacker's +4 bonus honestly.
- `app/games/hoard_hurt_help/game.py` — only if the honest-attacker approach
  needs it; prefer NOT widening `move_effect`.
- Tests: `tests/test_resolver.py`, `tests/test_inround_mirror.py`,
  `tests/test_viewer.py`, `tests/test_game_registry.py`,
  `tests/test_rules_text.py`.
- Docs: `docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md`,
  `docs/games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md`,
  `docs/games/hoard-hurt-help/betray-helper-impact-review.md` (mark
  superseded/implemented).

### Out of scope (non-goals)

- Changing the 12-point relative swing magnitude.
- The **Team Attack** case (two players each HURT one victim for independent −4s,
  summing to −8). That −8 is not a betrayal and must stay.
- Retraining the win-probability models or adding an "exploiter" bot.
- Widening `move_effect`'s contract across other games (`liars_dice`) — avoid
  unless strictly necessary.
- A migration/schema change. `points_delta` already stores the actual delta.

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
  victim = **−4**. Proven by a resolver test.
- AC2. Non-betrayal HURT unchanged: victim −4, attacker +0.
- AC3. Score floor still applied to the **FINAL per-player delta**, not per-hurt
  (resolver). The mirror keeps its per-hurt floor (unchanged divergence).
- AC4. Agent-facing `GAME_RULES_TEXT` states attacker +4 / victim −4; header
  bumped `(v4)` → `(v5)`.
- AC5. Viewer shows the betrayal honestly (victim −4, attacker +4). No stale −8
  anywhere in the UI (chip, caption, groups).
- AC6. No stale `BETRAYAL_HURT_POINTS` references remain anywhere in the repo.
- AC7. Preflight green:
  `.venv/bin/ruff check . && .venv/bin/mypy app/ mcp_server/ && .venv/bin/pytest -q`.

## 6. Open decisions (resolve in plan)

- **D1 — How to surface the attacker's +4.** Two honest options:
  (a) add the attacker's `+BETRAYAL_BONUS` into the attacker action's
  `display_delta` in `build_pd_replay_view` (turn-context aware, no `move_effect`
  change); (b) widen `move_effect` to take turn context (ripples into
  `base.py` + `liars_dice`). Recommendation: **(a)** — contained, honest, keeps
  `move_effect` nominal and `test_game_registry` valid.

## 7. Dependencies & sequencing

`rules.py` constant + text is the source of truth; `scoring.py` imports the
constant; `viewer.py` imports it too. Change the constant and both scoring
branches together (they must stay consistent), then the viewer, then tests, then
docs. No external deps.

## 8. Validation plan

- Unit: resolver betrayal (attacker +8 / victim −4), non-betrayal HURT (−4),
  betrayal + floor (victim near 0), one-attacker-betrays-while-third-hurts.
- Unit: `apply_inround_turn` mirror betrayal (victim −4 floored, attacker +4).
- Unit: `GAME_RULES_TEXT` states the new split + `(v5)`.
- Unit: viewer per-move chip + registry `move_effect` assertions.
- Full Preflight Gate from the worktree root using `.venv/bin/`.

## 9. Risks

- **R1 — Silent scoring divergence (the reason this is full-factory).** The
  resolver and the mirror can drift. *verification:* a mirror unit test asserts
  the same betrayal numbers the resolver test asserts (victim −4, attacker +4).
- **R2 — Stale −8 left in the viewer.** A −8 could linger in a caption, group
  delta, or chip. *verification:* grep the repo for `BETRAYAL_HURT_POINTS` and
  for a literal `-8`/`−8` in the game module + design doc, distinguishing the
  legitimate Team-Attack −8 from a betrayal −8; a viewer test asserts the
  betrayal chip is not −8.
- **R3 — Team-Attack −8 wrongly changed.** *verification:* the design doc's
  "Team Attack: A and B both Hurt C → C takes −8" row and its edge-case bullet
  stay −8; the impact-review edit is limited to the betrayal rows.
