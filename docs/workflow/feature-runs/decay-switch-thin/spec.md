# Spec — Mutual-Help Decay Switch (Thin arm)

## Summary

Add a per-match boolean `mutual_help_decay` that turns the mutual-help decay rule
ON or OFF. ON (the default, and today's behavior) keeps the sliding
+8/+7/+6…→+2 decay per repeating pair. OFF makes every mutual help pay a flat +8
to each side, every time, with no decay and no floor logic.

The hard invariant: the engine's scoring, everything the game *tells* a player
about a mutual help's worth, and everything the replay/watch view *shows* for a
mutual-help move must all agree with the match's setting. If the engine stops
decaying, nothing anywhere may still show or promise a decayed number.

## Background — what "decay" is in the code today

- Base help: `HELP_POINTS = 4` to the target. Mutual-help bonus:
  `MUTUAL_HELP_BONUS = 4` extra to each side. Floor: `MUTUAL_HELP_FLOOR = 2`.
- Per-side total for a pair's mutual help at decay counter `k` (how many prior
  turns that same unordered pair mutually helped this match):
  `max(MUTUAL_HELP_FLOOR, HELP_POINTS + MUTUAL_HELP_BONUS - k)` = `max(2, 8-k)`.
  So 8, 7, 6, 5, 4, 3, 2, 2… (floors at 2). Fresh pair (`k=0`) = 8.
- ON = that formula (today). OFF = always `HELP_POINTS + MUTUAL_HELP_BONUS` = 8,
  regardless of `k`.

## User stories

- **P1 — Operator runs the A/B.** As the operator, I can create a match with
  decay OFF so I can compare game dynamics against the default ON matches. The
  switch lives on the match row and is set through the shared `create_match`
  helper; default matches stay ON and unchanged.
- **P1 — Honest agents.** As a competing agent (real AI or built-in bot), the
  rules and the live "what a mutual help pays now" signal I receive match how the
  engine will actually score my moves for THIS match — never a decayed promise in
  a flat match, never a flat promise in a decaying one.
- **P1 — Honest viewers.** As a spectator, the replay/watch view shows the same
  per-move mutual-help number the engine scored, and the legend describes the
  rule that actually applies to the match I'm watching.

## Functional requirements

1. **FR1 — Setting.** `Match` gains `mutual_help_decay: bool`, non-null, default
   `True` (ON), with a DB `server_default` so existing rows read ON. It survives a
   DB round-trip. `create_match` and `create_match_with_state` accept
   `mutual_help_decay: bool = True` and persist it. Existing callers that omit it
   get ON.
2. **FR2 — Scoring.** The authoritative `resolve_turn` scores a mutual help at the
   flat per-side total (8) for every repeat when the match is OFF, and at the
   decayed `max(2, 8-k)` when ON. The floor and the per-pair `k` count are used
   ONLY when ON. `current_pact_values` (the live per-pair value the agent reads)
   returns a flat 8 when OFF and the decayed value when ON.
3. **FR3 — Rules text.** The rules text delivered for a match reflects its
   setting. ON: describes the decay (as today). OFF: describes the flat "+8 every
   time" and contains NO decay/floor/reset language *in the mutual-help section*.
   This covers every AI-facing rules surface: the connector `rules` payload, the
   MCP `semantic_rules_text`, and the `agent_base_prompt` (which embeds the rules).
   The default (no match / catalog) rules stay ON.
4. **FR4 — Agent "current worth" signal.** The per-turn private payload's
   `pact_values` are flat 8 under OFF (decayed under ON), and the accompanying
   `pact_values_note` describes the OFF rule with NO decay/floor language under OFF.
5. **FR5 — Bot logic parity.** The built-in bots' decay-aware partner rotation
   (`trust.PARTNER_FATIGUE`, which today erodes a farmed partner's trust to mirror
   the scoring decay) is applied only when the match is ON. Under OFF the bots do
   not penalize a repeated partner, because there is no shrinking reward to rotate
   away from. This is the bot-facing equivalent of "the rules match the setting":
   the bot's model of the payoff matches what the engine scores.
6. **FR6 — Viewer per-move display.** The replay's per-pact per-side value
   (`mutual_value` / `display_delta` / the compact pact-chip delta / the
   robot-circle caption) is the flat 8 under OFF and the decayed value under ON.
   The robot-circle running-score rail and animation credit the same per-move
   value the engine scored (read from the action's authoritative `delta`), not a
   hardcoded 8 — so they are correct under BOTH settings.
7. **FR7 — Watch-page legend.** The robot-circle legend shown on a match's watch
   page describes the rule that applies to that match: "bonus decays each round"
   only when ON; a flat "+8 each, every time" when OFF. Marketing/demo pages (no
   specific match) keep the default ON legend — the template var must default to
   `true` (Jinja `| default(true)`) so an undefined var on match-less pages reads
   ON, not OFF.
8. **FR8 — Migration.** A migration adds the column with a default of ON so every
   existing row keeps today's behavior, and it runs cleanly on the SQLite dev DB
   (up and down). The migration round-trip / head-revision test is updated to the
   new head.
9. **FR9 — Cross-game safety.** The change must not alter the shared `GameModule`
   contract in a way that forces edits to other games (Liar's Dice). Other games
   keep their exact behavior and are not touched.

## Out of scope (explicit non-goals)

- A human-facing create-match UI checkbox. The switch is settable via the
  `create_match` helper (used by scripts, tests, and any future caller); wiring a
  form field into the admin/user create pages is deferred (not required by any
  acceptance criterion). Default remains ON everywhere.
- Changing `apply_inround_turn`'s logic: it already credits the caller-supplied
  `mutual_value`; the viewer simply supplies a flat 8 under OFF.
- Any change to other games, `scripts/decay_validation_sim.py` (it monkeypatches
  its own flat resolver and still works), or the offline win-probability models
  (they read the per-action value, which is already correct).
- Retuning the decay numbers, floor, or bot personalities.

## Acceptance criteria (map to the brief's 6)

- **AC1 (FR1, FR8):** `mutual_help_decay` exists per match, defaults ON, persists
  across a DB round-trip; existing/default matches behave exactly as before.
- **AC2 (FR2):** With OFF, a pair mutually helping on many turns of one match
  scores +8 each EVERY time, verified through `resolve_turn` (the real scoring
  path). With ON, the same sequence still decays 8,7,6,…→2.
- **AC3 (FR3, FR5):** The rules text matches the setting — ON describes decay, OFF
  describes flat +8 with no decay/floor/reset language in the mutual-help section —
  for BOTH the real-AI-facing rules and the built-in bots (bots via FR5: their
  decay-aware logic is off under OFF).
- **AC4 (FR2, FR4, FR6, FR7):** No lying. The agent's `pact_values` +
  `pact_values_note`, and the replay/watch per-move display + legend, all match
  what `resolve_turn` scores under the setting. Under OFF, nothing shows or
  promises a decayed number.
- **AC5 (FR8):** The migration adds the column with default ON; existing rows keep
  today's behavior; it runs cleanly on SQLite (up and down).
- **AC6:** Tests cover OFF-stays-flat (repeated +8 via `resolve_turn`),
  ON-still-decays, `current_pact_values` OFF vs ON, rules text differs by setting,
  the pact note wording, the migration round-trip, the `create_match` round-trip,
  the bot partner-fatigue gating, and the viewer's flat-under-OFF value. Full
  preflight (`ruff` + `mypy app/ mcp_server/` + `pytest`) passes.

## Consumer enumeration (every code path that reads/reflects the value)

This is the completeness contract for the plan — the value must be traced to each.

| # | Consumer | File | Change |
|---|----------|------|--------|
| 1 | Model column | `app/models/match.py` | add `mutual_help_decay` bool, default True, server_default "1" |
| 2 | Migration | `migrations/versions/0047_*.py` | add column default ON; downgrade drops it **inside `op.batch_alter_table`** (repo SQLite convention) |
| 3 | Migration head assert | `tests/test_migrations.py` | head `0046`→`0047` (the `[("0046",)]` assertion) |
| 4 | Create wrapper | `app/engine/match_creation.py` | `create_match` + `create_match_with_state` accept + persist flag |
| 5 | Authoritative scoring | `app/games/hoard_hurt_help/scoring.py` | `resolve_turn` reads match flag; `current_pact_values` flag kwarg |
| 6 | Rules builder | `app/games/hoard_hurt_help/rules.py` | refactor `GAME_RULES_TEXT` into an ON/OFF-aware builder; `make_game_rules_text` / `make_rules_text` decay param |
| 7 | PD module | `app/games/hoard_hurt_help/game.py` | rules methods take flag; `*_for_match` overrides; `private_state_for` note + pact_values |
| 8 | Contract | `app/games/base.py` | add `*_for_match` methods to **BOTH the `GameModule` Protocol AND `BaseGameModule`** (default bodies delegate to the count-based methods → Liar's Dice inherits, untouched; declaring on the Protocol is required or the typed callers fail mypy) |
| 9 | AI-facing callers | `app/engine/agent_play_reads.py`, `mcp_server/mcp_tools.py` | call `*_for_match(match)` |
| 10 | Bot logic | `app/engine/bots/trust.py` + `types.py` + `runtime.py` + `service.py` | gate PARTNER_FATIGUE on the flag |
| 11 | Viewer value | `app/games/hoard_hurt_help/viewer.py` | per-pact value flat under OFF (`match` is in scope at the pact-value loop) |
| 12 | Replay JS | `app/templates/fragments/robot_circle/_replay_script.html` | use `a.delta` for mutual at **all THREE hardcoded `8` sites** (lines ~100 running-score sim, ~866 `showDelta`, ~867 `rScore`) |
| 13 | Watch legend | `app/templates/fragments/robot_circle/_markup.html` + `app/templates/game.html` | legend conditional on `rc_mutual_help_decay | default(true)` |

Verified NON-consumers (left unchanged, with reason):
- `app/templates/fragments/move_legend.html` — marketing-only (`home.html`); shows the default ON game.
- `app/templates/fragments/play_panel.html` "+8 mutual" — nominal headline value; no decay language; true under both settings.
- `app/games/hoard_hurt_help/viewer_headline.py` "+8 each/apiece" pact headlines — a hardcoded flat 8; correct under OFF and unchanged from today under ON (today it already prints +8 regardless of decay), so it never shows a *decayed* number. Left as-is to keep ON byte-for-byte; noted here because it is a mutual-help "worth" string.
- `app/games/hoard_hurt_help/match_summary.py`, `app/templates/fragments/turn_block.html`, `app/engine/bots/board_signals.py` — reference mutual help but show only the FR6-corrected `display_delta` or a pact/alliance *count*, never an independent worth number.
- `app/routes/web_games_catalog.py:103` — the agent-instructions catalog page calls `agent_base_prompt` with no match; correctly stays ON (default).
- `app/games/hoard_hurt_help/strategy.py` presets — no decay language.
- `app/engine/bots/phrases.py` "+8" chat lines — bot talk flavor; no decay/floor/reset language; true under both.
- `scripts/decay_validation_sim.py` — monkeypatches its own flat resolver and pins PARTNER_FATIGUE; still valid after the change (its "decay" arm uses a default-ON match).
- `app/engine/win_probability.py` / `win_prob_features.py` — the model's mutual-help input is a **boolean** `got_mutual_help` (0/1), which is decay-invariant; the point value is never read.

## Risks / invariants to hold

- Same-commit rule: scoring, rules text, agent signal, and viewer display change
  together — never let a shown/promised number drift from what the engine scores.
- ON path must be byte-for-byte today's behavior (default kwargs = ON), so every
  existing test stays green without edits.
- Do not add `mutual_help_decay` to `BotContext.seed_basis()` — that would perturb
  every bot's deterministic seed and break existing bot tests. It gates behavior
  only.
- "No floor/reset language" in the OFF rules is scoped to the mutual-help section:
  the separate "## Score floor" (round scores clipped at 0) and "scores reset to
  0" (round reset) text is a different rule and stays in both ON and OFF.
