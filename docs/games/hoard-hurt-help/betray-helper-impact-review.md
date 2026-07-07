# Betraying A Helper — Impact Review (for Chris)

> **SUPERSEDED / IMPLEMENTED (2026-07-07).** This review evaluated an *earlier*
> shape of the rule (victim −8, attacker no bonus). The rule that actually shipped
> is the **"8/4" re-split**: the victim takes the **normal −4** and the **attacker
> gains a +4 bonus** (`BETRAYAL_BONUS`) on top of the +4 help — net **attacker +8 /
> victim −4**. The 12-point relative swing is unchanged; it was re-split so the
> attacker rises instead of the victim cratering. The (a)/(b) `move_effect` decision
> below was resolved to a third option — a dedicated `betrayal_bonus` key on the
> viewer action (`move_effect` stays nominal). See
> `docs/workflow/feature-runs/betrayal-8-4-factory/` for the shipped design, plan,
> and tests. Everything below is kept for historical context only — its −8 numbers
> and its references to the old betrayal-hurt constant describe the pre-8/4
> proposal, not the current code (the shipped constant is `BETRAYAL_BONUS`).

**Original proposal (pre-8/4, historical):** If you **HURT** a player who is
**HELPing you in the same turn**, that player takes **−8** instead of the normal −4.
Mutual help stays **+8 each**. The attacker gets no bonus (they still pocket the +4
from the victim's help). Net effect on the betrayal turn: **attacker +4, victim −8 →
a 12-point swing.** *(Shipped instead as 8/4 — see the banner above.)*

Reduced payoff (A vs B, "cooperate" = HELP partner):
- Both HELP (pact): **+8 / +8**
- A HURTs B while B HELPs A: **A +4 / B −8**  ← the new temptation (beats the pact gap)
- Both HOARD: +2 / +2

This restores a real Prisoner's-Dilemma temptation (betraying a cooperator can now
beat mutual cooperation) **and** makes unconditional helpers exploitable.

---

## How to read this
Each row is a place the rule reaches. **Tier 1 = the actual rule.** Tier 2 = keeping
the viewer honest. Tier 3 = words (agents + docs). Tier 4 = tests. Tier 5 = soft
downstream (won't break, may want follow-up). Tier 6 = confirmed NOT affected.

Tick the box once you've eyeballed it.

---

## Tier 1 — Authoritative behavior (the real change)

- [ ] **`app/games/hoard_hurt_help/scoring.py` → `resolve_turn`, lines ~67–92**
  The single source of truth. Today:
  ```python
  for s in submissions:
      if s.action == "HOARD":      delta[s.player_id]   += HOARD_POINTS
      elif s.action == "HELP" ...: delta[s.target_id]   += HELP_POINTS
      elif s.action == "HURT" ...: delta[s.target_id]   -= HURT_POINTS   # always 4
  # then mutual-help bonus, then floor
  ```
  Change *(pre-8/4 proposal — NOT what shipped)*: build the HELP map first, then in
  the HURT branch subtract the old betrayal-hurt value (8) when
  `help_targets.get(victim) == attacker`, else `HURT_POINTS` (4). Floor +
  mutual-bonus logic stay as-is. *(Shipped instead: victim always `HURT_POINTS`;
  the attacker gains `BETRAYAL_BONUS` — see the banner.)*

- [ ] **`app/games/hoard_hurt_help/rules.py`, lines 7–10** *(pre-8/4 proposal)*
  Add the old betrayal-hurt constant (= 8) next to the other constants. *(Shipped
  instead: `BETRAYAL_BONUS = 4` — the attacker's bonus, not the victim's damage.)*

---

## Tier 2 — Viewer / score-display consistency (decide, don't just patch)

- [ ] **`app/games/hoard_hurt_help/scoring.py` → `apply_inround_turn`, lines 110–140**
  The viewer's *running-score mirror* (separate from the authoritative resolver).
  Today it does `new[target] = max(0, new[target] - HURT_POINTS)` per HURT and has
  no idea about betrayal. To match the rule it must reconstruct the HELP edges from
  the `actions` list and apply −8 on a betraying HURT.
  Consumed by → **`viewer.py:358`** and **`viewer_win_probs.py:68`** (both pass
  `agent_id/action/target_id/mutual`, so the data is there).
  ⚠️ This helper already floors *per-hurt* (the authoritative one floors the summed
  delta). That divergence exists today — I'll preserve it, just teach it the −8.

- [ ] **`app/games/hoard_hurt_help/game.py` → `move_effect(action)`, lines 226–234**
  ⚠️ **Hard limit:** this only gets the action *string*, no turn context, so it
  **cannot** know a HURT was a betrayal. The per-move chip in the watch feed will
  label a betraying HURT as "−4" even though the victim's real score dropped 8.
  - **Option (a)** keep it nominal (−4); the running score still drops the correct 8.
    Small, contained. *(my recommendation for a first trial)*
  - **Option (b)** widen the `move_effect` contract to take turn context so the chip
    shows −8 — ripples into `app/games/base.py` and `app/games/liars_dice/game.py`.
  **← I need your (a)/(b) call here.**

- [ ] **`app/games/hoard_hurt_help/viewer.py`, lines 343–351 (`display_delta`)**
  The HURT chip uses `target_delta` (nominal −4). Same cosmetic gap as above; follows
  whatever you pick in (a)/(b).

- [ ] **`app/games/hoard_hurt_help/viewer.py`, lines 312–330 — NAMING COLLISION**
  There is **already** a `betrayal` flag here, but it means something different:
  "HURT aimed at **last turn's** pact partner" (cross-turn). Betraying a helper is a
  **same-turn** condition. Do not conflate them. If you want the viewer to visually
  mark the new betrayal, that's a *new* same-turn signal (and likely game-art work).

---

## Tier 3 — Words: agent-facing rules + docs (fairness)

- [ ] **`app/games/hoard_hurt_help/rules.py` → `GAME_RULES_TEXT`, lines 12–49**
  Agents must be **told** the rule or it's unobservable and unfair. Add a bullet under
  "Stacking and combos"; bump the header `(v2)` → `(v3)`. `make_game_rules_text` /
  `make_rules_text` (lines 60–79) just interpolate counts — no structural change.

- [ ] **`docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md` §2**
  Document betraying a helper in the payoff math + edge-case list; resolve the old
  "Payoff math — needs cleanup" note while we're in there.

---

## Tier 4 — Tests

- [ ] **`tests/test_resolver.py`** — add: hurt-a-helper → −8; hurt-a-non-helper → −4
  (unchanged, guards against regressions); betrayal + floor; one attacker betraying while a
  third player HURTs the same victim normally (only the helped-attacker's edge is −8).
- [ ] **`tests/test_game_registry.py`, lines 27–29** — asserts `move_effect("HURT")
  == (0, -4)`. **Stays valid only under option (a).** Option (b) rewrites this.
- [ ] **`tests/test_viewer.py`, line 255** (`test_viewer_shows_per_move_effect_on_target`)
  — expects the loss shown on the target via `move_effect`. Stays valid under (a).

---

## Tier 5 — Soft downstream (won't break; possible follow-ups)

- [ ] **Win-prob models** `data/win_prob_model.pkl`, `data/round_win_prob_model.pkl`
  Trained on the *old* payoff distribution (and the already-stale `baseline.csv`).
  They won't crash but will be **miscalibrated** for betrayal-heavy games. Retrain
  path if/when we care: `baseline_tournament.py` → `export_baseline_dataset.py` →
  `train_win_prob.py` / `train_round_win_prob.py`.
- [ ] **Bots** `app/engine/bots/strategies.py`
  Scripted bots never *deliberately* bait-and-hurt, so they'll rarely trigger the
  betrayal (my sim confirmed ~no movement). To actually exercise it with bots we'd add
  an "exploiter" strategy. Real LLMs will trigger it via the talk phase.
  `app/engine/bots/trust.py` keys on action *type*, not magnitude — no change needed.
- [ ] **`app/games/hoard_hurt_help/match_summary.py`, lines 75–93**
  Only looks at *positive* `display_delta` (biggest gift) and pact counts — negative
  HURT deltas don't feed it, so the bigger −8 has essentially no effect here. Listed
  for completeness.

---

## Tier 6 — Confirmed NOT affected

- [ ] **No DB migration / schema change.** `points_delta` already stores the actual
  post-betrayal delta; nothing new to persist.
- [ ] **`validate_move`** — legal moves unchanged (HURT is still HURT).
- [ ] **Round / game / `finalize` logic** — unchanged.

---

## Open decision blocking implementation
1. **(a) nominal per-move chip** vs **(b) context-aware `move_effect`** (Tier 2).
2. Want an **exploiter bot** added so we can see the betrayal fire in bot-only games,
   or are we validating with real LLM matches only? (Tier 5)
