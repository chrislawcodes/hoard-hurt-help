# Tasks — 8/4 Betrayal Payoff Re-split

Executable slices from `plan.md` §2 (already hardened by the plan review — the
slice boundary was corrected so each slice is preflight-green). Three
`[CHECKPOINT]` boundaries. Verification per slice uses the worktree venv.

Preflight (worktree root): `.venv/bin/ruff check . && .venv/bin/mypy app/ mcp_server/ && .venv/bin/pytest -q`.

---

## Slice 1 — All Python + its unit tests (atomic, preflight-green) — est. ~150 lines

- [ ] T1.1 `rules.py`: rename `BETRAYAL_HURT_POINTS = 8` → `BETRAYAL_BONUS = 4`
  (comment: attacker's extra gain when betraying a same-turn helper).
- [ ] T1.2 `rules.py`: rewrite the "Betraying a helper" bullet in `GAME_RULES_TEXT`
  to state **attacker +4 bonus (on top of the +4 help = +8) / victim −4 (normal)**;
  bump header `(v4)` → `(v5)`.
- [ ] T1.3 `scoring.py → resolve_turn`: in the HURT branch, on a betrayal
  (`help_targets.get(target) == player_id`): victim `-= HURT_POINTS` (was
  `BETRAYAL_HURT_POINTS`), attacker `delta[player_id] += BETRAYAL_BONUS`. Update
  the import + the docstring.
- [ ] T1.4 `scoring.py → apply_inround_turn`: (a) victim `damage` becomes always
  `HURT_POINTS` (remove the `BETRAYAL_HURT_POINTS` ternary branch); (b) ADD
  `new_inround[actor] = new_inround.get(actor, 0) + BETRAYAL_BONUS` on a betraying
  HURT (not floored). Update the docstring (drops the `BETRAYAL_HURT_POINTS` mention).
- [ ] T1.5 `viewer.py`: switch the import to `BETRAYAL_BONUS`; **drop** the
  `-BETRAYAL_HURT_POINTS` override on the HURT `display_delta` (victim shows −4 via
  `target_delta`); set `a["betrayal_bonus"] = BETRAYAL_BONUS` on the attacker's
  action when `betrayed_helper`; add `betrayed_helper` + `betrayal_bonus` to
  `_build_rc_data`'s `rc_actions` dict; fix the two stale `-8` inline comments
  (~331, ~353). `game.py` untouched.
- [ ] T1.6 `test_resolver.py`: rewrite `test_betraying_a_helper_hurts_for_eight`
  → attacker +8 / victim −4 (rename it); `test_hurt_non_helper_stays_four` stays;
  `test_betrayal_only_for_the_helped_attacker` → victim `20−4−4=12`; add a
  betrayal+floor case (victim seeded low → 0).
- [ ] T1.7 `test_inround_mirror.py`: rewrite `test_mirror_betraying_a_helper_is_eight`
  to assert the explicit dict `{"A":8,"B":6}` from `{"A":0,"B":10}` (rename it);
  add a floored mirror betrayal case (victim seeded low, e.g. 5 → 1); add a test
  asserting `_build_rc_data`'s JSON carries `betrayed_helper`/`betrayal_bonus` for
  a betrayal turn; refresh the module docstring.
- [ ] T1.8 `test_rules_text.py`: replace the `f"-{BETRAYAL_HURT_POINTS}"` and
  `BETRAYAL_HURT_POINTS != HURT_POINTS` assertions with the new +4/−4 wording;
  `(v4)` → `(v5)`.
- [ ] T1.9 `test_viewer.py`: add/extend a betrayal case asserting the rendered feed
  HTML shows the attacker's `+4` (and `betrayal_bonus == 4` on the payload).
  `test_game_registry.py`: verify `move_effect("HURT") == (0, -4)` still holds — no
  edit expected.
- [ ] T1.10 Run full preflight; fix root causes; must be green. **[CHECKPOINT]**

## Slice 2 — Templates + animation (JS/HTML, no Python) — est. ~40 lines

- [ ] T2.1 `turn_block.html`: render a distinct positive `+{{ a.betrayal_bonus }}`
  element on the attacker's HURT row (gated on `a.betrayal_bonus`, NOT the
  cross-turn `a.betrayal`), visually separate from the −4-on-target chip (D4/R-C).
- [ ] T2.2 `move_legend.html` + `robot_circle/_markup.html`: rewrite the Hurt
  clause `-4 to another, -8 if betraying` → 8/4 wording (victim −4; +4 to the
  attacker when betraying a helper). Leave the Help clause untouched.
- [ ] T2.3 `_replay_script.html`: add the attacker `+4` on a `betrayed_helper` HURT
  in BOTH loops — `playAction` (`rScore[a.agent]+=4; showDelta(el,4)`) and
  `computeScores` (`sim[a.agent]+=4`). Leave the mutual-HELP `+8` line untouched.
- [ ] T2.4 Human diff-review checklist (R-A): confirm BOTH JS loops credit +4 and a
  betrayal turn's rail total + round-win end at attacker +8. Run preflight (stays
  green — no Python). **[CHECKPOINT]**

## Slice 3 — Docs — est. ~60 lines

- [ ] T3.1 `HOARD_HURT_HELP_DESIGN.md`: change the three betrayal −8 sites (payoff
  bullet line ~42, worked-scenarios table row ~55, edge-case bullet ~66) → 8/4
  (attacker +8 / victim −4). **Keep** the Team-Attack `−8` (line ~57) + its
  edge-case bullet, and the mutual-help `+8`/`8−k` lines.
- [ ] T3.2 `HOARD_HURT_HELP_ARCHITECTURE.md`: verify the rules.py row (already
  refreshed to `BETRAYAL_BONUS` in the Design stage).
- [ ] T3.3 `betray-helper-impact-review.md`: mark superseded/implemented AND update
  its body `BETRAYAL_HURT_POINTS = 8` references (~39, ~43) so
  `grep BETRAYAL_HURT_POINTS docs/games/` is clean (R-D).
- [ ] T3.4 Grep sweep: `grep -rn "BETRAYAL_HURT_POINTS" app/ docs/` returns nothing;
  no stale betrayal `-8` in `app/games/hoard_hurt_help/` or `app/templates/`. Run
  full preflight. **[CHECKPOINT]**
