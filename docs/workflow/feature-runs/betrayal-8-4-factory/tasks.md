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

- [x] T2.1 `turn_block.html`: added a distinct positive `+4 betrayal` chip on the
  attacker's HURT row, gated on `a.betrayal_bonus` (NOT the cross-turn `a.betrayal`),
  visually separate from the −4-on-target chip (D4/R-C).
- [x] T2.2 `move_legend.html` + `robot_circle/_markup.html`: Hurt clause →
  `-4 to another; +4 to you if betraying a helper`. Help clause left untouched.
- [x] T2.3 `_replay_script.html`: added the attacker `+4` on a `betrayed_helper`
  HURT in BOTH loops — `computeScores` (line 106, `sim[a.agent]+=4`) and `playAction`
  (line 924, `showDelta(el,4); rScore[a.agent]+=4`). Mutual-HELP `+8` line untouched.
- [x] T2.4 R-A checklist: verified both JS loops credit the attacker +4 (lines 106
  + 924), both leave the victim at −4, and the mutual-HELP `+8` line is unchanged.
- [x] T2.5 `test_viewer.py`: added `test_viewer_shows_attacker_bonus_on_betrayal`
  asserting the rendered feed HTML shows `+4 betrayal` and the victim chip is `>-4<`,
  never `>-8<`. Full preflight GREEN: ruff clean, mypy clean, 1439 passed. **[CHECKPOINT]**

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
