# Tasks — 8/4 Betrayal Payoff Re-split

Executable slices from `plan.md` §2 (already hardened by the plan review — the
slice boundary was corrected so each slice is preflight-green). Three
`[CHECKPOINT]` boundaries. Verification per slice uses the worktree venv.

Preflight (worktree root): `.venv/bin/ruff check . && .venv/bin/mypy app/ mcp_server/ && .venv/bin/pytest -q`.

---

## Slice 1 — All Python + its unit tests (atomic, preflight-green) — est. ~150 lines

- [x] T1.1 `rules.py`: renamed `BETRAYAL_HURT_POINTS = 8` → `BETRAYAL_BONUS = 4`.
- [x] T1.2 `rules.py`: rewrote the "Betraying a helper" bullet → attacker nets +8
  (+4 help + +4 bonus) / victim −4; bumped header `(v4)` → `(v5)`.
- [x] T1.3 `scoring.py → resolve_turn`: victim `-= HURT_POINTS`; attacker
  `+= BETRAYAL_BONUS` on a betrayal. Import + comment updated.
- [x] T1.4 `scoring.py → apply_inround_turn`: victim always `HURT_POINTS` (ternary
  removed); ADD `new_inround[actor] += BETRAYAL_BONUS` on a betrayal (not floored).
  Docstring updated.
- [x] T1.5 `viewer.py`: import → `BETRAYAL_BONUS`; dropped the `-BETRAYAL_HURT_POINTS`
  `display_delta` override (HURT shows victim −4); set `betrayal_bonus` on the
  attacker action when `betrayed_helper`; added `betrayed_helper` + `betrayal_bonus`
  to `_build_rc_data` (via `.get()` for tolerance); fixed the two stale `-8`
  comments. `game.py` untouched.
- [x] T1.6 `test_resolver.py`: rewrote the three betrayal tests to attacker +8 /
  victim −4 (and every HURT −4); added `test_betrayal_victim_floored_at_zero`.
- [x] T1.7 `test_inround_mirror.py`: rewrote the mirror betrayal test to the
  explicit dict `{"A":8,"B":6}`; added a floored mirror case (5 → 1); added
  `test_rc_data_threads_betrayed_helper_and_bonus`; refreshed the docstring.
- [x] T1.8 `test_rules_text.py`: replaced the old assertions with the +8/−4 wording;
  `(v4)` → `(v5)`.
- [x] T1.9 `test_game_registry.py`: `move_effect("HURT") == (0, -4)` still holds —
  no edit needed (verified). The **rendered-HTML** feed +4 assertion moves to Slice 2
  (T2.5) since it needs the `turn_block.html` edit; the Slice-1 payload guard is
  `test_rc_data_threads_betrayed_helper_and_bonus`.
- [x] T1.10 Full preflight GREEN: ruff clean, mypy clean (195 files), 1438 passed. **[CHECKPOINT]**

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
  betrayal turn's rail total + round-win end at attacker +8.
- [ ] T2.5 `test_viewer.py`: add a betrayal case asserting the **rendered feed HTML**
  shows the attacker's `+4` (R4 guard — present-in-payload-but-invisible). This test
  lands with the `turn_block.html` edit so it can assert the real rendered chip. Run
  preflight (green). **[CHECKPOINT]**

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
