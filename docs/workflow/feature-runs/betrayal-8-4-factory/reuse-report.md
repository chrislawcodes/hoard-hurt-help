# Reuse audit — 8/4 betrayal payoff re-split

Adversarial reuse scan (Design stage). For each capability the feature needs, is
there an existing module/function that already provides it? Verdict:
reuse / extend / justified-new. This feature is a **payoff-value tweak to
existing PD scoring** — it adds no new module and no new capability; almost
everything is *reuse* or *extend*.

| Capability the feature needs | Existing module (path) | Verdict | Note |
|---|---|---|---|
| The authoritative per-turn payoff constant | `app/games/hoard_hurt_help/rules.py` (`BETRAYAL_HURT_POINTS`, `HURT_POINTS`, `HELP_POINTS`) | **extend** | Rename `BETRAYAL_HURT_POINTS` → `BETRAYAL_BONUS` (value 8 → 4), reuse existing `HURT_POINTS` for the victim's −4. No new constant kind — same "single source of truth" block. |
| Authoritative resolver betrayal branch | `app/games/hoard_hurt_help/scoring.py → resolve_turn` (existing HURT branch with `betrayed_helper` detection) | **extend** | The betrayal-detection (`help_targets.get(victim) == attacker`) already exists; only the two deltas change (victim −4, attacker +BONUS). No new resolver. |
| Viewer running-score mirror | `app/games/hoard_hurt_help/scoring.py → apply_inround_turn` | **extend** | Already the running-score mirror (the deleted `viewer_win_probs.py`'s consumer is gone; this is the sole mirror). Add the attacker-credit line. Do NOT create a second mirror. |
| Per-move nominal effect for the feed chip | `app/games/hoard_hurt_help/game.py → move_effect` + `viewer.py → _move_effect_for` | **reuse** | Stays nominal (`HURT → (0,-4)`); no change. The new `betrayal_bonus` rides on the action dict in `build_pd_replay_view`, reusing the existing per-action enrichment loop. |
| Per-action turn-context tags (mutual / betrayal / betrayed_helper) | `app/games/hoard_hurt_help/viewer.py → build_pd_replay_view` | **extend** | `betrayed_helper` is already computed here; reuse it to attach `betrayal_bonus` and to thread into `_build_rc_data`. No new tagging pass. |
| Robot-circle replay JSON | `app/games/hoard_hurt_help/viewer.py → _build_rc_data` | **extend** | Reuse the existing per-action JSON emit; add `betrayed_helper`/bonus fields. |
| Robot-circle animation + client running-score sim | `app/templates/fragments/robot_circle/_replay_script.html` | **extend** | Reuse the existing HURT animation + `showDelta` + client `sim`/`rScore`; add the attacker +4. Do NOT add a parallel animation path. |
| Static move legends | `app/templates/fragments/move_legend.html`, `app/templates/fragments/robot_circle/_markup.html` | **extend** | Reuse the existing legend markup; correct the stale `-8 if betraying` text. |
| Agent-facing rules text | `app/games/hoard_hurt_help/rules.py → GAME_RULES_TEXT` + `make_game_rules_text` | **reuse** | Reuse the existing versioned text block + interpolation helper; only the betrayal bullet + version string change. |
| Finale "biggest gift" summary | `app/games/hoard_hurt_help/match_summary.py → _superlatives` | **reuse (no change)** | Confirmed unaffected: the attacker's +4 goes in `betrayal_bonus`, not `display_delta`, so `_superlatives`' `delta > 0` gift-scan never sees a betrayal. Reused as-is; the separate-field decision (§3.4) is precisely what keeps this reuse valid. |
| Tests for scoring / mirror / rules text / viewer / registry | `tests/test_resolver.py`, `tests/test_inround_mirror.py`, `tests/test_rules_text.py`, `tests/test_viewer.py`, `tests/test_game_registry.py` | **extend** | Reuse the existing test fixtures/harness (`_make_game_with_players`, `_submit`, `resolve_turn`); update the betrayal expectations and add the mirror-parity assertion. No new test module. |

## Justified-new

**None.** This feature introduces no new module, class, or capability. The single
new *symbol* is the `betrayal_bonus` key on the viewer action dict — that is a
field on an existing dict, chosen (over reusing `display_delta`) specifically to
avoid the `match_summary` collision the spec review found; it is not a new module.

## Duplication flags

- **No new score mirror.** The one real duplication risk here is accidentally
  computing the betrayal payoff in a *fourth* place. There are already three
  score computations that must agree: the authoritative `resolve_turn`, the
  Python mirror `apply_inround_turn`, and the JS client sim in
  `_replay_script.html`. The plan must update all three consistently and MUST NOT
  add a new one. The mirror-parity test (spec §8) is the guard.
