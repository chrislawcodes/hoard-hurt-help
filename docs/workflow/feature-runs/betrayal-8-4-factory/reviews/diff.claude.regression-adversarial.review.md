---
reviewer: "claude"
lens: "regression-adversarial"
stage: "diff"
artifact_path: "docs/workflow/feature-runs/betrayal-8-4-factory/reviews/implementation.diff.patch"
artifact_sha256: "1d60eee90196a99626f0969483c6124e3971fb4e8d450bd64478a3d3aabb088d"
repo_root: "."
git_head_sha: "4e0aa7575366dfc5c164cba83414adbdb79314c8"
git_base_ref: "225b575df6bc43bb0f49e079f4d8333b073cf6fd"
git_base_sha: "225b575df6bc43bb0f49e079f4d8333b073cf6fd"
generation_method: "claude-subagent"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/betrayal-8-4-factory/reviews/diff.claude.regression-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: diff regression-adversarial

## Findings

**No CODE-CONFIRMED correctness or regression bugs found in the shipping code path.** I verified every load-bearing claim against the repo files under `/tmp/wt-betrayal-8-4-factory/`. The 8/4 re-split is implemented correctly and consistently across the resolver, the Python mirror, both JS score loops, and the viewer payload. The findings below are documentation-drift observations only.

### MEDIUM

**M1 — Stale `-8` betrayal comments left in the bot engine (`app/engine/bots/`) [CODE-CONFIRMED]**
Three bot files still describe the old victim-`-8` mechanic in prose, though none of them compute score from it:
- `app/engine/bots/plan_rules.py:178` — comment says the buzzer HURT "lands for the full betrayal damage (-8)".
- `app/engine/bots/trust.py:32-38` — "A betrayal … is just a much worse hurt"; and `_betrayals()` docstring at `trust.py:235` says "the attacker triggered the -8 betrayal".

These are **comments/docstrings, not logic**. The bot trust dials (`BETRAYAL_SELF_FACTOR=6`, `BETRAYAL_OTHER_FACTOR=3`) scale a personality's *memory/forgiveness reaction* to a betrayal; they never referenced `BETRAYAL_HURT_POINTS` and don't touch the payoff. `_betrayals()` detects a betrayal by the same-turn HELP condition (`trust.py:252-254`), which is unchanged. The buzzer-betrayal strategy is still correct and still profitable under 8/4 (attacker nets +8). So this is doc drift, not a behavioral regression — but the `-8` numbers are now factually wrong and could mislead the next reader. The feature's own AC6 scoped "no stale `BETRAYAL_HURT_POINTS`" to shipping code / rules text / design doc, and these files use no such symbol, so they technically pass the acceptance bar while still being stale. Worth a one-line fix.

### LOW

**L1 — `betrayal_bonus` chip is gated on truthiness, so a future `betrayal_bonus: 0` on a real betrayal would silently hide it [CODE-CONFIRMED]**
`turn_block.html:35` renders the attacker chip with `{% if a.betrayal_bonus %}`, and `_build_rc_data` defaults it to `0`. Today this is correct — a same-turn betrayal always sets `betrayal_bonus = BETRAYAL_BONUS` (4) in `viewer.py:364`, and `0` correctly suppresses the chip for non-betrayals. This is only a latent fragility: the truthy gate couples "show the chip" to "the bonus is non-zero" rather than to the `betrayed_helper` flag. If the bonus were ever configured to 0, the signal would vanish. Not a defect in the current change.

## Residual Risks

- **Verified against the resolver directly:** `resolve_turn` (`scoring.py:176-185`) produces attacker `+HELP_POINTS+BETRAYAL_BONUS` (=+8) and victim `-HURT_POINTS` (=−4); a non-betrayal HURT gives the attacker nothing and the victim −4 (`test_hurt_non_helper_stays_four` pins attacker=0). The floor is applied once to the summed per-player delta (`scoring.py:204-211`), and `test_betrayal_victim_floored_at_zero` confirms the victim floors while the attacker's +8 does not. **Confirmed correct.**
- **Mirror parity:** `apply_inround_turn` (`scoring.py:258-263`) matches the resolver's attacker credit and applies the floor per-hurt on the victim only (its documented, deliberate divergence). Confirmed by `test_mirror_betraying_a_helper_pays_the_attacker_eight` and `test_mirror_betrayed_victim_floors_per_hurt`.
- **Both JS loops credit the attacker +4 and agree:** `computeScores` (`_replay_script.html:102-106`) and `playAction` (`_replay_script.html:919-924`) both do `sim/rScore[a.agent] += 4` under `if(a.betrayed_helper)`, both after applying the victim's −4. `renderTurn` reseeds `rScore` from the `computeScores` snapshot, so they stay in step. Confirmed consistent.
- **No `match_summary` "biggest gift" leak:** the HURT chip's `display_delta` is `a["target_delta"]`, and `move_effect("HURT")` returns `(0, -HURT_POINTS)` = −4 (`game.py:267`), so `display_delta` is always negative on a HURT. The attacker's +4 rides the separate `betrayal_bonus` key, which `_superlatives` never scans (it only reads `display_delta > 0` at `match_summary.py:84-86`). Confirmed the positive-delta scan is unaffected.
- **No stale `-8` in a betrayal context in shipping code:** `grep` for `BETRAYAL_HURT_POINTS` across `app/` returns nothing (only `docs/workflow/feature-runs/…` spec/review artifacts, which are historical). The only `-8` in `app/templates` are CSS pixel offsets (`top:-8px`), not scores. The design-doc `−8` at `HOARD_HURT_HELP_DESIGN.md:57` is the **Team-Attack** line ("C takes −8"), which is correctly preserved — that −8 is two stacked −4 HURTs, not the betrayal constant. The other design-doc `−8` (line 42) is the explicit "not −8" re-split explanation. Confirmed.
- **Unverified — win-probability model retraining:** the mirror now floors the victim at −4 instead of −8, shifting some running-score boundaries (as `test_mirror_betrayed_victim_floors_per_hurt` documents). If any persisted/serialized win-probability model was trained against the old −8 mirror output, its inputs have shifted. I found no such model artifact in the changed files and did not audit the training pipeline. [UNVERIFIED] — flagged as a downstream data question, not a code defect in this diff.

```json
{"reviewed": true, "findings": [{"severity": "MEDIUM", "title": "Stale -8 betrayal comments in app/engine/bots/", "detail": "plan_rules.py:178 and trust.py:32-38/235 still describe the old victim -8 mechanic in comments/docstrings, though the bot logic computes no score from it and the strategy stays correct under 8/4."}, {"severity": "LOW", "title": "betrayal_bonus chip gated on truthiness not the betrayed_helper flag", "detail": "turn_block.html:35 uses {% if a.betrayal_bonus %}, so a future betrayal_bonus:0 on a real betrayal would silently hide the attacker chip; harmless today since a betrayal always sets it to 4."}]}
```

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: open
- note: 