# Reuse Audit — `mutual-help-decay`

Capabilities the feature needs, mapped to existing code. Verdict per row: **reuse** / **extend** / **justified-new**.

| Capability | Existing module(s) | Verdict | Note |
|---|---|---|---|
| Per-turn mutual-pair detection (who reciprocated this turn) | `scoring.py:resolve_turn` (`help_targets` + reciprocity check); also `viewer.py` (`helps` dict + `mutual` flag) | **extend** | The decay hooks into the *authoritative* detection in `resolve_turn`. Don't add new detection — add the `k`-lookup + decayed bonus where mutual pairs are already found. |
| **Count a pair's prior mutual-help turns this match** (the `k` for `max(2, 8−k)`) | `trust.py:_mutual_help_partners(history)` (by-turn grouping over history); `board_signals.py` mutual-strength clusters (`frozenset` pair → weight) | **justified-new (one canonical helper), reusing the pattern** | Neither returns "for unordered pair {A,B}, how many prior turns did they mutually help, up to now." Mutual-pair detection already lives in **4** spots — add **one** shared counter (e.g. `mutual_help_counts(prior_submissions) -> dict[frozenset, int]`) and reuse it in `resolve_turn`, `apply_inround_turn`, and the bot fatigue. Adding a 5th ad-hoc scan is the thing to avoid. |
| Viewer running-score mirror | `scoring.py:apply_inround_turn` | **extend** | Apply the same decay so the mirror matches the authoritative score (acceptance #2). |
| Per-move display value | `viewer.py` `display_delta`; `game.py:move_effect` | **extend (viewer only)** | `move_effect(action: str)` is action-string-only — it *cannot* know `k` (same hard limit as the betrayal sting). Show the decayed value in `viewer.py` where the turn context exists; leave `move_effect` nominal. |
| Agent rules text | `rules.py:GAME_RULES_TEXT` / `make_game_rules_text` | **extend** | Add the decay sentence + bump version; existing `test_rules_text.py` pattern guards it. |
| Bot partner selection / trust | `trust.py:compute_trust_map`; `strategies.py:_best_partner`, `_recent_helper` | **extend (trust map only)** | Add per-pair "fatigue" to the **trust map** so the *existing* selection logic rotates. No new selection code — same approach the validated sim used (erode a farmed partner's trust toward neutral). |
| Per-match persistent state (storage fallback) | `MatchState`/`PlayerState` (`app/models/game_state.py`, migration 0033) | **reuse (fallback only)** | Only if derive-from-history proves too costly. No new migration needed if used. Primary recommendation remains derive-from-history. |
| Reading prior turns' submissions in `resolve_turn` | `resolve_turn` currently loads only the current turn's submissions + players | **extend** | To derive `k`, `resolve_turn` (or its caller) must load this match's prior **resolved** `TurnSubmission`s grouped by turn. Bounded O(≤49 turns × ≤10 players). |

**Duplication flag:** mutual-pair detection is the one real duplication hazard. The plan must route every decay/fatigue computation through a single shared counter helper.
