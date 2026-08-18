# Hoard Hurt Help — Game Architecture

This doc is the **code map for the Hoard‑Hurt‑Help Prisoner's Dilemma game
module** — a thin plugin that sits on top of the game‑agnostic Agent Ludum
platform. It covers the PD‑specific code: the module that adapts the engine to
the `GameModule` contract, its strategy presets, and the PD scoring core it
adapts. Everything game‑agnostic (the turn loop, agent API, viewer, storage)
lives in the platform docs.

**Related docs:** `HOARD_HURT_HELP_DESIGN.md` (game why, same folder) ·
`../../platform/AGENT_LUDUM_ARCHITECTURE.md` (platform code map) ·
`../../platform/AGENT_LUDUM_DESIGN.md` (platform why).

---

## The PD module — `app/games/hoard_hurt_help/`

The Hoard‑Hurt‑Help game is a plugin in `app/games/hoard_hurt_help/`. It
implements the platform's `GameModule` contract (`app/games/base.py`) and is
registered through the registry (`app/games/__init__.py` → `get(game_type)`).
It is a thin adapter over the scoring/resolution code in `app/engine/`.

| Module | Lines | Responsibility |
|---|---:|---|
| `hoard_hurt_help/game.py` | 423 | PD module — adapts scoring/resolution to the `GameModule` contract: `validate_move`, `record_submission`, `record_message`, `resolve_turn`, `award_round`, `finalize`, `move_effect`, plus the game‑agnostic hooks (`action_names`, `default_move`, `display_name`, `tagline`, `theme`, `build_replay_view`, `viewer_fragment`, `semantic_rules_text`) **and the spectator‑insight hooks** (`board_signals`, `season_overview`, `round_detail`). |
| `hoard_hurt_help/scoring.py` | 314 | **The PD scoring core.** Per‑turn HOARD/HELP/HURT payoff math (`resolve_turn`), the mutual‑help bonus **at whatever rule that match was created under** (never a hardcoded number — always `mutual_help_value(mode, k)`, the one payout function the resolver, the pre‑move preview, the replay legend and the rules text all share. `decay` pays −1 per repeat of the same pair, flooring at +2 = the Hoard value; the flat modes ignore `k` entirely. The pair's repeat count `k` is **derived from match turn history**, so it survives a DB resume — feature `mutual-help-decay`. Today's default for a new match is `flat_6`, so decay is one selectable mode, not the shipped rule), full Help/Hurt stacking, and the score‑floor‑at‑zero clip. Also `apply_inround_turn` — the viewer's running‑score view of the same payoffs (must apply the same decay so the mirror matches the authoritative score), built from the rules constants and shared by both viewer loops so the values aren't re‑hardcoded; it is deliberately distinct from `resolve_turn`'s authoritative net‑then‑floor (it floors each HURT individually for display). Moved here out of `app/engine/resolver.py` so PD scoring lives inside the PD module. |
| `hoard_hurt_help/rules.py` | 274 | **The single source for every PD rule**, and the rules text the agent sees, rendered from those same constants. Holds: the payoffs (incl. the betrayal **attacker‑bonus** `BETRAYAL_BONUS` — betraying a same‑turn helper pays the attacker +4 on top of the help while the victim takes the normal −4); the five `MutualHelpMode` payouts behind `mutual_help_value`; the move vocabulary `ACTIONS`; the `SCORE_FLOOR`; the shipped match length (`DEFAULT_TOTAL_ROUNDS` / `DEFAULT_TURNS_PER_ROUND`); `RULES_VERSION`; and the two mode constants that must never be collapsed — `DEFAULT_MUTUAL_HELP_MODE` (a caller named no mode → today's rule) vs `LEGACY_MUTUAL_HELP_MODE` (a match row's mode column is NULL → the rule *that* match was played under). **Every number in the rules text is interpolated**, counts included — there is no render‑then‑string‑replace step, so rewording a sentence cannot detach it from the value it quotes. Nothing outside this file may restate any of these; `tests/test_rules_single_source.py` and `tests/test_rules_docs_current.py` pin that. |
| `hoard_hurt_help/strategy.py` | 232 | PD strategy presets + the default pre‑fill, and the **design record for why the roster is what it is**. Eight presets — Tit‑for‑Tat, Loyal Partner, Buzzer‑Beater, Dealmaker, Underdog's Champion, Kingslayer, Sandbagger, Salvager — in that order, because the join UI pre‑selects the first. Every preset shares `RANK_FRAMING`, now the single line **"Prioritize round wins."** The bullets it used to carry — one action per turn, the tie split, the tiebreaker, the score floor — each restated a rule the agent reads in `base_prompt` on the same turn. One more claimed an even swap leaves every pair level; that is true of the FLAT mutual‑help modes only (under `decay` a pair's fifth swap pays 4 against a fresh pair's 8), and `decay` is `LEGACY_MUTUAL_HELP_MODE`, so a line shared by all eight presets was wrong for every pre‑switch match. The bar for adding a line back is high: this block is shared by all eight presets, so anything in it pushes all eight the same way, and the roster exists to make them behave differently. The docstring also records the arithmetic that admits or rejects a strategy — a turn pays `4N + 2` where N is how many players HELP you, `4N + 4` if you betray one, so an extra helper is worth 4 and the choice of action at most 2 — plus the routes measured and **rejected** (coercion, pure denial, two‑backer rotation) so they are not re‑attempted. Grim Trigger, Pavlov, Always Defect and Generous Tit‑for‑Tat were **removed**: measured in M_6442, each either played out as plain Tit‑for‑Tat or, for Always Defect, aimed at the highest‑scoring opponent — the player least likely to be HELPing it — so its attacks collected nothing. Removing a preset never touches agents that already exist; the prompt text is copied into the agent at creation. |
| `hoard_hurt_help/viewer.py` | 544 | PD replay/viewer payload (`build_replay_view`): the robot‑circle JSON and the pact/betrayal replay story — the per‑game half of the platform viewer. Delegates per‑turn headlines to `viewer_headline.py` and the end‑of‑game finale to `match_summary.py`. |
| `hoard_hurt_help/viewer_headline.py` | 210 | The PD play‑by‑play narrative engine: phrase banks + deterministic per‑turn headline selection/rendering (`_turn_headline`). Split out of `viewer.py`. |
| `hoard_hurt_help/board_signals.py` | 119 | Whole‑board PD signals for a round: mutual‑help **alliances**, cooperation **temperature** (hostile/mixed/cooperative), **surging** seats, and **pattern‑breaks**. Action‑derived and deterministic; exposed via `GameModule.board_signals`. |
| `hoard_hurt_help/insights.py` | 189 | PD spectator insights: `season_overview` (round‑win race, results, grudges, tiebreaker, season feed) and `round_detail` (round leaderboard‑from‑0, mood, alliances, event feed). Reuses `board_signals`; exposed via `GameModule.season_overview` / `round_detail`. |
| `hoard_hurt_help/match_summary.py` | 223 | Pure, DB‑free end‑of‑game finale builder (`build_final_summary`): champion, rule‑sorted standings, per‑seat Hoard/Help/Hurt mix, and match superlatives. Called by `viewer.py` (not a `GameModule` hook). |

## The generic lifecycle helpers it uses — `app/engine/resolver.py`

| Module | Lines | Responsibility |
|---|---:|---|
| `resolver.py` | 155 | **Game‑agnostic** turn‑lifecycle helpers only: `finalize_talk_phase`, `award_round_winners`, `finalize_game`. No PD scoring left here. |

Note: `resolver.py` lives in the platform's `app/engine/` directory and is now
fully game‑agnostic. The PD‑specific per‑turn payoffs moved to
`hoard_hurt_help/scoring.py`; `game.py` calls `scoring.py` to score a turn, then
uses `resolver.py`'s generic helpers to close the talk phase, award round winners,
and finalize the match.

---

## Where to make a change

| You want to… | Start here |
|---|---|
| Change PD payoffs / scoring (HOARD/HELP/HURT, mutual‑help bonus, **per‑pair mutual‑help decay**, floor) | `app/games/hoard_hurt_help/scoring.py` (and keep `apply_inround_turn` in sync). |
| Change PD rules text / constants | `app/games/hoard_hurt_help/rules.py`. |
| Change the match length (rounds / turns per round) | `DEFAULT_TOTAL_ROUNDS` / `DEFAULT_TURNS_PER_ROUND` in `app/games/hoard_hurt_help/rules.py` — that is the **whole** edit. `config_defaults`, the rules text agents read, the Practice Arena and Auto‑Match constants, and the admin create schema all read from there. Then update the docs the guard test lists (`tests/test_rules_docs_current.py` fails until you do), and check anything keyed to a turn *number*: bot late/buzzer thresholds already count back from the round's last turn, so they need no edit. |
| Change which mutual‑help rule new matches get | `DEFAULT_MUTUAL_HELP_MODE` in `app/games/hoard_hurt_help/rules.py`. Do **not** touch `LEGACY_MUTUAL_HELP_MODE` — that is how a NULL column on an old row is read, and changing it restates the history of finished matches. |
| Change how bots pick / rotate partners (incl. **decay‑aware** partner rotation) | `app/engine/bots/trust.py` (trust map) + `app/engine/bots/strategies.py` (partner selection) — engine‑level, platform code. See `../../platform/AGENT_LUDUM_ARCHITECTURE.md`. |
| Change move validation / turn resolution wiring | `app/games/hoard_hurt_help/game.py`. |
| Change the PD replay / viewer (robot‑circle, replay story) | `app/games/hoard_hurt_help/viewer.py`. |
| Change the per‑turn replay headlines (phrase banks) | `app/games/hoard_hurt_help/viewer_headline.py`. |
| Re‑validate the mutual‑help decay (tie‑rate A/B) | `scripts/decay_validation_sim.py` (deterministic, no LLM; baseline/decay/aware). Recorded run in `docs/workflow/feature-runs/mutual-help-decay/closeout.md`. |
| Change board signals (alliances, cooperation mood, surging) | `app/games/hoard_hurt_help/board_signals.py`. |
| Change spectator insights (season overview / round detail) | `app/games/hoard_hurt_help/insights.py`. |
| Change the end‑of‑game finale (champion, standings, superlatives) | `app/games/hoard_hurt_help/match_summary.py`. |

---

## PD‑shaped storage

PD records its moves in the PD‑shaped `turn_submissions` columns
(`action`/`target`/`points_delta`) and its scores in the existing `players`
columns — it writes no generic per‑title state. That much is unchanged. But the
once‑deferred storage/wire generalization **landed with the second game (Liar's Dice):**
a generic per‑title state store now exists (`MatchState` / `PlayerState` in
`app/models/game_state.py`, migration `0033`), and the submit wire carries a
free‑form **`move: dict`** (`app/schemas/agent.py` `SubmitRequest.move`) that a
non‑PD game uses over HTTP. So a new move *vocabulary* can now arrive over HTTP,
not only through the contract. What remains PD‑shaped is the legacy
`turn_submissions` column set itself. See the platform tension in
`../../platform/AGENT_LUDUM_ARCHITECTURE.md` ("PD's columns persist, but storage
and the wire are now partly generalized") and the platform design doc's **Game Framework** section.
