# Plan — Mutual-Help Decay Switch (Thin arm)

Design is settled by the spec + one spec-review round. Backend value threaded
through enumerated consumers. The brief mandates scoring + rules text + viewer
display land in ONE commit, so this is a single-slice build (see tasks.md).

## Core design decisions

### D1 — ON is the default kwarg everywhere; today's behavior is byte-for-byte
Every new parameter defaults to `mutual_help_decay=True`. So all existing tests
and callers that omit it get today's decaying behavior unchanged. Only an
explicit OFF flips anything.

### D2 — `resolve_turn` reads the flag off the match (stable signature)
`scoring.resolve_turn(db, turn)` keeps its signature. It fetches the match by
`turn.match_id` (`select(Match).where(Match.id == turn.match_id)).scalar_one()` —
fails loud if absent) and reads `match.mutual_help_decay`. When ON: unchanged
(`bonus = max(MUTUAL_HELP_FLOOR - HELP_POINTS, MUTUAL_HELP_BONUS - k)`, k from the
prior-turn query). When OFF: `bonus = MUTUAL_HELP_BONUS` (flat +8 per side), and
the prior-turn `k` query is skipped. Initialize `prior_counts: dict[...] = {}`
BEFORE the `if decay:` block that runs the query, so the pair loop never
references an unbound name (ruff F821) — guard the query and the bonus on the same
flag. `Match` imports into
`scoring.py` with no cycle (match.py imports only game_types / models.base /
enum_types). Keeping the signature stable means the validation sim (which
monkeypatches `scoring.resolve_turn`) and the three production callers
(`turn_drivers`, `overdue_sweeper`, `scheduler_turn_loop`) are untouched.

### D3 — `current_pact_values` gains a keyword flag
`current_pact_values(db, match_id, player_id, other_ids, *, mutual_help_decay: bool = True)`.
OFF returns `HELP_POINTS + MUTUAL_HELP_BONUS` (8) for every pair — early-return
this flat map WITHOUT running the resolved-turn scan (correct and cheaper). ON is
today's decayed value. Its only non-test caller, `game.private_state_for`, has the
match and passes `mutual_help_decay=match.mutual_help_decay`. Existing positional
test callers get the default (ON) → stay green.

### D4 — Rules text: refactor the frozen constant into an ON/OFF builder
`rules.py`: factor the mutual-help paragraph into two constants — `_MUTUAL_HELP_ON`
(today's two bullets: "Mutual-help bonus" + "Mutual-help decays") and
`_MUTUAL_HELP_OFF` (one flat bullet, below). A private `_render_game_rules(*,
mutual_help_decay)` builds the full rules body (literal 5-round/7-turn counts) with
the chosen section. `GAME_RULES_TEXT = _render_game_rules(mutual_help_decay=True)`
— its text is unchanged, so `test_rules_text.py` stays green. Precompute
`_GAME_RULES_TEXT_FLAT = _render_game_rules(mutual_help_decay=False)`.
`make_game_rules_text(total_rounds=5, turns_per_round=7, *, mutual_help_decay=True)`
picks the ON or OFF base, then applies the existing count `.replace()`s when counts
are non-default. `make_rules_text(...)` passes the flag through.

OFF mutual-help bullet (no decay/floor/reset/farming words):
> **Mutual-help bonus.** If A HELPs B and B HELPs A in the same turn, each gets an
> extra +4 on top of the base +4 — net +8 each, every time. A pair earns the full
> +8 each on every mutual help, no matter how often they do it.

The "## Score floor" (round scores clipped at 0) and "scores reset to 0" (round
reset) text are DIFFERENT rules and stay in both ON and OFF — the "no
decay/floor/reset language" requirement is scoped to the mutual-help section.

### D5 — Cross-game seam: `*_for_match` methods (Protocol + BaseGameModule)
Add to BOTH the `GameModule` Protocol (declarations) AND `BaseGameModule`
(concrete defaults) three match-aware wrappers:
- `rules_text_for_match(self, match) -> str`
- `semantic_rules_text_for_match(self, match) -> str`
- `agent_base_prompt_for_match(self, match, *, your_agent_id, all_agent_ids) -> str`

BaseGameModule defaults delegate to the count-based methods
(`self.rules_text(match.total_rounds, match.turns_per_round)`, etc.), ignoring
decay. **Liar's Dice inherits these defaults unchanged — it is not edited.** So
Liar's Dice keeps exact behavior. To let the base defaults call `self.rules_text`
/ `self.agent_base_prompt` under mypy, add matching `NotImplementedError` abstract
stubs for `rules_text` and `agent_base_prompt` to `BaseGameModule` (mirrors the
existing `action_names` / `default_move` / `next_actor` stub pattern; PD and Liar's
Dice both already override them, so no runtime effect).

PD overrides the three `*_for_match` methods to pass
`mutual_help_decay=match.mutual_help_decay` into its own decay-aware
`rules_text` / `semantic_rules_text` / `agent_base_prompt` (each of which gains a
`*, mutual_help_decay: bool = True` keyword — an LSP-safe optional-kwarg widening
of the override).

Callers switch to the match-aware variants:
- `agent_play_reads.build_turn_static_dict` (NOT "_static_turn_payload"; the real
  function, holding the `rules`/`base_prompt` keys): `module.rules_text_for_match(match)`
  + `module.agent_base_prompt_for_match(match, your_agent_id=..., all_agent_ids=...)`.
- `mcp_tools._format_instruction_sections`: `module.semantic_rules_text_for_match(match)`.
- `web_games_catalog` stays on the count-based `agent_base_prompt` (no match → ON).

### D6 — Agent "current worth" note (game.private_state_for)
`pact_values` come from `current_pact_values(..., mutual_help_decay=match.mutual_help_decay)`
(flat 8 under OFF). `pact_values_note` branches on the flag:
- ON (today): "…would pay EACH side right now (decays per repeat mutual-help pair
  this match; floors at {MUTUAL_HELP_FLOOR})."
- OFF: "What a mutual HELP with this agent pays EACH side: +{HELP_POINTS +
  MUTUAL_HELP_BONUS} every time (no decay)."

### D7 — Bot partner-fatigue gating
`trust.compute_trust_map(..., *, mutual_help_decay: bool = True)`. The
PARTNER_FATIGUE block runs only `if PARTNER_FATIGUE and mutual_help_decay`; the
separate mutual-help TRUST BOOST (`model.mutual_help`) still fires under OFF (a
mutual help still pays). Add `mutual_help_decay: bool = True` as a trailing field
on the frozen `BotContext` dataclass — and do NOT reference it in `seed_basis()`
(would perturb the deterministic seed and reintroduce the talk→act target-drift
bug). NOTE: the existing bot determinism tests would NOT catch such a regression
(they assert self-consistency / order-independence, which hold when a constant
field is added to the seed), so a NEW direct guard test is required (see the test
plan). `runtime.compute_trust_map(...)` calls pass
`mutual_help_decay=context.mutual_help_decay` (both talk and act paths).
`service.auto_submit_bot_phase` builds `BotContext(..., mutual_help_decay=game.mutual_help_decay)`
(the `game: Match` is already in scope). ON matches keep PARTNER_FATIGUE → every
existing bot test stays green.

### D8 — Viewer per-move value (viewer.build_pd_replay_view)
At the per-pact value loop, read `decay_on = match.mutual_help_decay is not False`
— treat `None` (an unpersisted in-memory `Match` in tests, where the SQLAlchemy
`default=True` has not flushed) as ON, matching the DB default. When OFF set
`pact_value[pair] = HELP_POINTS + MUTUAL_HELP_BONUS` (flat 8); when ON keep
`max(MUTUAL_HELP_FLOOR, HELP_POINTS + MUTUAL_HELP_BONUS - k)`. `match` is the
function parameter, so it is in scope. This flows into `mutual_value`,
`display_delta`, the compact pact-chip range, the RC caption, and the RC action
`delta` — all become flat 8 under OFF automatically. (Skip the `pact_counts`
increment under OFF for tidiness — `k` is unused there.)

### D9 — Replay JS uses the authoritative per-move delta (all three sites)
`_replay_script.html` hardcodes `8` for a mutual at three places, each already
iterating action objects that carry `.delta` (= `display_delta`, the per-side pact
value). Replace the literal with the action's own delta, guarding a missing value:
- `~line 100` running-score sim: `sim[a.agent] += (a.delta != null ? a.delta : 8)`
- `~line 866` `showDelta(el, …); showDelta(T, …)`: use `(a.delta != null ? a.delta : 8)`
- `~line 867` `rScore[a.agent] += (a.delta != null ? a.delta : 8)`

Correct under BOTH settings (also fixes a latent ON over-count where the animation
showed +8 for a decayed pact). Fallback 8 keeps the bundled sample payload safe.

### D10 — Watch-page legend (robot_circle/_markup.html + game.html)
Make the "mutual +8 each, bonus decays each round" legend conditional:
`mutual +8 each{% if rc_mutual_help_decay | default(true) %}, bonus decays each
round{% else %}, every time{% endif %}`. `game.html`'s robot_circle include passes
`rc_mutual_help_decay = game.mutual_help_decay`. Match-less demo pages
(home/agent_ludum) leave it undefined → `| default(true)` → ON legend.

### D11 — Migration 0047
`op.add_column("matches", sa.Column("mutual_help_decay", sa.Boolean(),
nullable=False, server_default="1"))`. Downgrade drops it inside
`op.batch_alter_table("matches")` (repo SQLite convention).
`down_revision = "0046"`. Update `tests/test_migrations.py` head assertion
`[("0046",)]` → `[("0047",)]`.

### D12 — create_match wrapper
`create_match(..., mutual_help_decay: bool = True)` passes it into `Match(...)`.
`create_match_with_state(..., mutual_help_decay: bool = True)` forwards it. Both
after the existing `*` keyword barrier → non-breaking for all current callers.

## Test plan (AC6)

**Non-vacuity rule (applies to every OFF test):** decay only diverges from 8 on
the SECOND+ mutual help (k≥1). Every OFF/ON pair shares one setup differing ONLY
in the flag, and asserts on a FARMED turn (k≥1) where ON would read <8 — so an
accidentally-ON match cannot mask the bug. In-memory `Match(...)` objects used
without a DB flush must set `total_rounds`/`turns_per_round` explicitly (their
SQLAlchemy defaults apply only at flush; `make_game_rules_text(None, None)` would
crash).

New file `tests/test_mutual_help_decay_switch.py` (plus the one-line
`test_migrations.py` head bump):

| Test | Asserts | AC |
|------|---------|----|
| `test_off_mutual_help_stays_flat_8` | OFF match, pair mutual-helps 8 turns via `resolve_turn` → per-turn delta is +8 on turns 2-8 (not just turn 1, not the sum) | AC2 |
| `test_on_mutual_help_still_decays` | ON match, same sequence → 8,7,6,5,4,3,2,2 (guards ON unchanged) | AC2 |
| `test_current_pact_values_off_is_flat_8` | farm ≥1 mutual help, then OFF → 8 while ON → 7 (both arms asserted) | AC2/AC4 |
| `test_semantic_rules_off_drops_decay_language` | `semantic_rules_text_for_match(Match(off,5,7))`: no "Mutual-help decays"/"decays"/"down to a floor of"/"resets to +8"; has "every time"; ON has "Mutual-help decays" | AC3 |
| `test_rules_text_for_match_off_drops_decay` | `rules_text_for_match(Match(off,5,7))`: same no-decay assertions (the connector `rules` surface) | AC3/AC4 |
| `test_agent_base_prompt_off_drops_decay` | `agent_base_prompt_for_match(Match(off,5,7), your_agent_id=..., all_agent_ids=...)`: no decay language, has "every time" (the `base_prompt` surface — biggest AI-facing prompt) | AC3/AC4 |
| `test_liars_dice_rules_for_match_unchanged` | `LiarsDice().semantic_rules_text_for_match(Match(...))` returns its real LD rules (FR9: inherited default delegates correctly, LD untouched) | AC-cross-game |
| `test_pact_note_off_has_no_decay_words` | `private_state_for` on OFF match (farmed pair): pact_values flat 8; note has no "decays"/"floors"; ON note has "decays" + value 7 | AC4 |
| `test_bot_partner_fatigue_off_not_applied` | `compute_trust_map` on a farmed-pair history: decay=True erodes partner trust to 0; decay=False preserves it (>=20). BOTH arms asserted | AC3 |
| `test_bot_context_seed_basis_ignores_decay` | two `BotContext`s differing ONLY in `mutual_help_decay` have equal `seed_basis()` (guards the determinism invariant the code relies on) | AC3 |
| `test_bot_action_off_context_threads_flag` | `choose_bot_action_decision` on an OFF `BotContext` with a farmed pair matches the `PARTNER_FATIGUE=0` decision (pins service→context→runtime→trust wiring, not just the leaf) | AC3 |
| `test_viewer_off_pact_value_flat_8` | `build_pd_replay_view` (in-memory, `db=None`) OFF with a repeated pact → 2nd pact's RC `delta` (what the JS reads) is 8; ON in-memory → 7 (pins the `None→ON` default and the JS-facing field) | AC4 |
| `test_create_match_persists_flag` | `create_match(mutual_help_decay=False)` then EXPIRE + re-`select` the row → False; default omitted → True (real round-trip) | AC1 |
| `test_migration_0047_backfills_existing_row_on` | upgrade to 0046, INSERT a `matches` row, upgrade to 0047, assert that pre-existing row's `mutual_help_decay == 1` AND column is NOT NULL (proves the server_default backfill, per data-critical-waves) | AC1/AC5 |
| `test_legend_off_match_drops_decay` (in `test_viewer.py` style, `client.get`) | watch page for an OFF match shows "every time" / omits "decays each round"; a match-less/demo page shows the ON "decays" legend (guards `game.html` passing the var AND `| default(true)`) | AC4/FR7 |

Existing suites that must stay green unchanged: `test_resolver.py` (ON decay +
pact values), `test_inround_mirror.py`, `test_rules_text.py`, all `test_bot*`,
`test_match_creation.py`, `test_viewer.py`, `test_migrations.py` round-trip.

The client-side JS edit (D9) has no pytest harness; it is pinned indirectly by
`test_viewer_off_pact_value_flat_8` (the RC `delta` the JS consumes) and verified
by reading — the ON latent-fix is opportunistic, not AC-gated.

## Preflight
`ruff check .` && `mypy app/ mcp_server/` && `pytest`, from the worktree venv.
Fix root causes; no suppressions.
