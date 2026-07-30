# Tasks — Mutual-Help Decay Switch (Thin arm)

## Slicing decision: ONE slice (one commit)

Per the feature-thin skill's "Keep Diffs Scoped" criteria, slice only for ordered
steps, a diff clearly over ~300 lines of INDEPENDENT work, or data-critical gates.
This feature is the opposite of independent: the brief REQUIRES scoring, rules
text, agent signal, and viewer display to change together in the SAME commit
("never let the rules shown drift from what the engine scores"). Splitting would
create an intermediate state where the engine and the shown numbers disagree —
exactly the invariant the feature protects. The one data-critical piece (the
additive migration) is low-risk (ADD COLUMN, default ON). So: single slice, single
commit, whole-diff review fan at the end.

## Build order (within the one slice)

1. **Model + migration** — `app/models/match.py` column; `migrations/versions/0047_match_mutual_help_decay.py` (add col default ON, batch downgrade); `tests/test_migrations.py:187` head `0046`→`0047`.
2. **create_match wrapper** — `app/engine/match_creation.py` (`create_match`, `create_match_with_state` kwarg → `Match(...)`).
3. **Scoring** — `app/games/hoard_hurt_help/scoring.py` (`resolve_turn` reads match flag, `prior_counts` guarded; `current_pact_values` OFF early-return flat 8).
4. **Rules text** — `app/games/hoard_hurt_help/rules.py` (ON/OFF builder; `make_game_rules_text`/`make_rules_text` decay kwarg; `GAME_RULES_TEXT` byte-identical).
5. **Contract seam** — `app/games/base.py` (Protocol + BaseGameModule `*_for_match` defaults; `rules_text`/`agent_base_prompt` NotImplementedError stubs).
6. **PD module** — `app/games/hoard_hurt_help/game.py` (rules methods take flag; `*_for_match` overrides; `private_state_for` pact_values + OFF note).
7. **AI-facing callers** — `app/engine/agent_play_reads.py` (`build_turn_static_dict`), `mcp_server/mcp_tools.py` (`_format_instruction_sections`) → `*_for_match(match)`.
8. **Bot logic** — `app/engine/bots/types.py` (BotContext field), `trust.py` (gate PARTNER_FATIGUE), `runtime.py` (pass flag both sites), `service.py` (build with `game.mutual_help_decay`).
9. **Viewer value** — `app/games/hoard_hurt_help/viewer.py` (pact value flat under OFF; `is not False`).
10. **Replay JS** — `app/templates/fragments/robot_circle/_replay_script.html` (three `8`→`a.delta` sites).
11. **Legend** — `app/templates/fragments/robot_circle/_markup.html` (conditional) + `app/templates/game.html` (pass `rc_mutual_help_decay`).
12. **Tests** — `tests/test_mutual_help_decay_switch.py` (the AC6 table in plan.md) + the legend test.
13. **Preflight** — ruff, mypy app/ mcp_server/, pytest. Fix root causes.

## Definition of done
- All 15 planned tests pass; existing suites stay green.
- Preflight clean (no suppressions).
- Liar's Dice and other games untouched; no CLAUDE.md/MEMORY.md/AGENTS.md edits.
- Committed on `exp-thin/decay-switch` (no push, no PR).
