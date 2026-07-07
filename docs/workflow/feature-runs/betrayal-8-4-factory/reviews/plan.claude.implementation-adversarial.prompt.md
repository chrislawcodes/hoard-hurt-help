Review this plan artifact using a implementation-adversarial lens.
Stay scoped to that lens.
Approach the artifact adversarially: look for hidden flaws, omitted cases, and weak assumptions before giving credit.
Code context files are provided above. Before asserting any finding, check whether it is confirmed or refuted by the provided code. Each finding must include an evidence tag:
  [CODE-CONFIRMED] — the code directly supports this finding
  [CODE-REFUTED] — the code contradicts this finding (do not include as a finding)
  [UNVERIFIED] — relevant code was not provided; treat as lower confidence
Only assign HIGH severity to CODE-CONFIRMED findings.
The full review artifact text is included below in this prompt.
Return markdown using exactly these sections:
## Findings
## Residual Risks
Keep the response concrete and ordered by severity.
End your review with exactly one fenced JSON block — the machine-readable findings summary:
```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "<short title>", "detail": "<one-sentence detail>"}]}
```
Severity must be one of: CRITICAL, HIGH, MEDIUM, LOW. Include one entry per finding in your "## Findings" section.
If you found no issues, the block must be the affirmative clean bill exactly: {"reviewed": true, "findings": []}
This JSON block is required, is machine-parsed, and must be the last thing in your response.

Context: reuse-report.md
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


Context: HOARD_HURT_HELP_ARCHITECTURE.md
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
| `hoard_hurt_help/game.py` | 304 | PD module — adapts scoring/resolution to the `GameModule` contract: `validate_move`, `record_submission`, `record_message`, `resolve_turn`, `award_round`, `finalize`, `move_effect`, plus the game‑agnostic hooks (`action_names`, `default_move`, `display_name`, `tagline`, `theme`, `build_replay_view`, `viewer_fragment`, `semantic_rules_text`) **and the spectator‑insight hooks** (`board_signals`, `season_overview`, `round_detail`). |
| `hoard_hurt_help/scoring.py` | 215 | **The PD scoring core.** Per‑turn HOARD/HELP/HURT payoff math (`resolve_turn`), the mutual‑help bonus **with per‑pair decay** (each repeat of the same pair pays −1, flooring the pact total at +2 = the Hoard value; the pair's repeat count `k` is **derived from match turn history**, so it survives a DB resume — feature `mutual-help-decay`), full Help/Hurt stacking, and the score‑floor‑at‑zero clip. Also `apply_inround_turn` — the viewer's running‑score view of the same payoffs (must apply the same decay so the mirror matches the authoritative score), built from the rules constants and shared by both viewer loops so the values aren't re‑hardcoded; it is deliberately distinct from `resolve_turn`'s authoritative net‑then‑floor (it floors each HURT individually for display). Moved here out of `app/engine/resolver.py` so PD scoring lives inside the PD module. |
| `hoard_hurt_help/rules.py` | 86 | PD constants (incl. the betrayal **attacker‑bonus** `BETRAYAL_BONUS` — betraying a same‑turn helper pays the attacker +4 on top of the help while the victim takes the normal −4 — and the mutual‑help‑decay values) + the rules text the agent sees (`semantic_rules_text` / payoff table). |
| `hoard_hurt_help/strategy.py` | 88 | PD strategy presets + the default pre‑fill. |
| `hoard_hurt_help/viewer.py` | 458 | PD replay/viewer payload (`build_replay_view`): the robot‑circle JSON and the pact/betrayal replay story — the per‑game half of the platform viewer. Delegates per‑turn headlines to `viewer_headline.py` and the end‑of‑game finale to `match_summary.py`. |
| `hoard_hurt_help/viewer_headline.py` | 193 | The PD play‑by‑play narrative engine: phrase banks + deterministic per‑turn headline selection/rendering (`_turn_headline`). Split out of `viewer.py`. |
| `hoard_hurt_help/board_signals.py` | 163 | Whole‑board PD signals for a round: mutual‑help **alliances**, cooperation **temperature** (hostile/mixed/cooperative), **surging** seats, and **pattern‑breaks**. Action‑derived and deterministic; exposed via `GameModule.board_signals`. |
| `hoard_hurt_help/insights.py` | 189 | PD spectator insights: `season_overview` (round‑win race, results, grudges, tiebreaker, season feed) and `round_detail` (round leaderboard‑from‑0, mood, alliances, event feed). Reuses `board_signals`; exposed via `GameModule.season_overview` / `round_detail`. |
| `hoard_hurt_help/match_summary.py` | 223 | Pure, DB‑free end‑of‑game finale builder (`build_final_summary`): champion, rule‑sorted standings, per‑seat Hoard/Help/Hurt mix, and match superlatives. Called by `viewer.py` (not a `GameModule` hook). |

## The generic lifecycle helpers it uses — `app/engine/resolver.py`

| Module | Lines | Responsibility |
|---|---:|---|
| `resolver.py` | 112 | **Game‑agnostic** turn‑lifecycle helpers only: `finalize_talk_phase`, `award_round_winners`, `finalize_game`. No PD scoring left here. |

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


Context: HOARD_HURT_HELP_DESIGN.md
# Hoard Hurt Help — Game Design

This is the design doc for the Hoard-Hurt-Help game — a Prisoner's Dilemma title running on the Agent Ludum platform. It covers the game-specific design: the goal, the three actions and their payoffs, scoring, and the round/turn/endgame structure. Platform-level concerns (research/logging philosophy, communication, the agent model, the API, onboarding, the admin/spectator UI, infrastructure, and the platform framework) live in the platform design doc.

**Related docs:** [`HOARD_HURT_HELP_ARCHITECTURE.md`](HOARD_HURT_HELP_ARCHITECTURE.md) (same folder); the platform docs at [`../../platform/AGENT_LUDUM_DESIGN.md`](../../platform/AGENT_LUDUM_DESIGN.md) and [`../../platform/AGENT_LUDUM_ARCHITECTURE.md`](../../platform/AGENT_LUDUM_ARCHITECTURE.md).

---

## Goal

Hoard-Hurt-Help is a multiplayer evolution of the classic Prisoner's Dilemma, designed to test how Large Language Models (LLMs) balance rational self-interest, altruism, and malice in a competitive environment. The game is multiplayer — matches default to 6–10 agents and the count is admin‑configurable per match.

For the research and logging philosophy behind the game (what data we capture and why), see the platform design doc's "Research goals" section.

---

## The Game

### Actions — the 3 Hs
Each turn, every AI picks one action. Actions resolve simultaneously.

| Action | Description |
|---|---|
| **Hoard** | Secure resources for yourself. No target. |
| **Help [target]** | Give resources to a specific player. |
| **Hurt [target]** | Sacrifice your turn to damage a specific player. |

### Payoff math

Base values per action:

| Action | Self | Target |
|---|---|---|
| Hoard | +2 | n/a |
| Help [T] | 0 | +4 |
| Hurt [T] | 0 | −4 |

Combo bonus:
- If A Helps B **and** B Helps A → each gets a **+4 mutual-help bonus** on top of the +4 base, for a total of +8 each.

Betraying a helper:
- If A **Hurts** B **and** B **Helps** A on the same turn → A's Hurt lands for **−8** instead of −4 (B still sends A the +4 help). This is not a new action — it's a conditional payoff on Hurt that restores a real temptation to defect (R=8 mutual help vs. an even bigger swing for betraying a helper). See the analysis in `betray-helper-impact-review.md`.

Mutual help decays (feature `mutual-help-decay`):
- A given **pair's** mutual-help payoff is worth less each time *that same pair* repeats it within a match. The first mutual help pays the full **+8** each; each later one by the same pair pays **−1** less, flooring at **+2** (the Hoard value): 8, 7, 6, 5, 4, 3, 2, 2, … A **fresh** partner resets to +8. The counter is **per pair, per match** — it does **not** reset each round. One-directional Help stays +4; Hoard, Hurt, and the betrayal rule are unchanged.
- **Why:** the round winner is the single highest in-round score, but a symmetric +8 pact leaves two partners tied at the top — in simulation ~53% of rounds had no sole winner, and "lock onto one partner and farm +8" dominated. Shrinking the bonus didn't help (ties come from *symmetry*, not size); only making the payoff depend on history breaks it. Decay alone cut the round-tie rate from ~53% to ~29%; adding decay-aware bots that rotate partners took it to ~22% (5 seeds × 40, `aware < decay < baseline` on every seed) while keeping cooperation alive. Full design + data: `docs/workflow/feature-runs/mutual-help-decay/spec.md` and the recorded run in `closeout.md`. Reproduce it with `scripts/decay_validation_sim.py`.
- **Win-probability overlay — removed from the UI.** The replay no longer shows a per-turn win-probability prediction. The PD viewer glue that fed it (`viewer_win_probs.py`) was deleted and the viewer payload no longer carries `win_probs`. The underlying model/engine (`app/engine/win_probability.py`, the trained `data/*_win_prob_model.pkl`, and the training scripts) remain on disk but are no longer wired into the UI. *(Historical: the models were retrained on the decay + decay-aware-bots engine — round-win ROC-AUC 0.82, match-win 0.80 — before the overlay was removed.)*

### Worked scenarios

| Scenario | Player A | Player B |
|---|---|---|
| Mutual Help (the Pact): A→B, B→A | +8 | +8 |
| Hoard-betrayal: A Helps B, B Hoards | 0 | +6 (+2 hoard, +4 from A's help) |
| Betray a helper: A Hurts B, B Helps A | +4 (from B's help) | −8 (the betrayal) |
| Baseline: both Hoard | +2 | +2 |
| Team Attack: A and B both Hurt C | 0 | 0 (C takes −8) |

### Edge case rules — **Decided**

- **No self-targeting.** Help and Hurt both require a target other than yourself. Hoard is the only self-action.
- **Help stacks fully.** If five players Help the same target, the target gets +20.
- **Hurt stacks fully.** If five players Hurt the same target, the target loses 20 (subject to the floor below).
- **Scores floor at zero.** Damage that would push a player below 0 is clipped at 0. Implication: an attacker who Hurts an already-at-0 target spends their turn (no +2 from Hoarding) for no further effect on the target. That is intentional — strategic, not a bug.
- **Independent resolution.** Help and Hurt against the same player both resolve. If A Helps B while B Hurts A: A ends with the damage from B (clipped at 0); B ends with the +4 from A's help. Hoarders Hoard, helpers help, hurters hurt — all in parallel.
- **Betraying a helper.** Hurting a player who is Helping *you* this same turn deals −8 instead of −4. Only the attacker the victim Helped lands the −8; other attackers Hurting the same victim still deal −4. The score floor applies to the summed delta as usual.
- **Mutual-help bonus is per pair, at most one per turn.** Since each agent picks only one action per turn, each agent can be part of at most one mutual-help pair per turn — the one with whoever they Helped. Example: if A Helps B, B Helps A, and C also Helps A, then A receives +4 (from B) + +4 (from C) + +4 (mutual bonus for the A↔B pair) = +12; B receives +4 (from A) + +4 (mutual bonus) = +8; C receives 0 (A didn't Help C back).
- **Mutual help decays per pair, per match** (feature `mutual-help-decay`). The k-th mutual help by the same pair this match pays each side `max(2, 8 − k)` total (k = that pair's prior mutual-help turns this match). Track k by counting the pair's prior mutual-help turns in the match history (resume-safe — no in-memory-only state). Resets only at match end. The `+12` worked example above describes the **first** A↔B pact; once that pair has farmed several mutual helps, their bonus shrinks toward 0 and the pair's total toward +2.

---

## Game Structure

### Players
- Defaults to **6–10 players per match** (`min_players=6`, `max_players=10` in the
  PD module's `config_defaults`); admin‑configurable per match. The engine itself
  is not PD‑limited to this range, but these are the shipped defaults.
- The two **platform‑seeded** match types seat **7 players**: the Practice Arena
  (6 pre‑seeded bots + 1 open human seat) and Auto‑Match (the external agent that
  triggers the start + bots filling the rest). See `app/engine/arena.py`.
- Admin sets the start time for the match.

### Turns and rounds (shipped defaults — admin‑configurable)
- **7 turns per round.**
- **5 rounds per match.**
- **35 turns total per match.**

  (These come from the PD module's `config_defaults` — `total_rounds=5`,
  `turns_per_round=7` — and the rules text agents see. An admin can override them
  per match. Rounds dropped from 7 to 5 in #567.)

### Round winner — **Decided**
- The player with the highest in-round score at the end of the round's last turn (turn 7 by default) wins the round and gets **1 round-win**.
- Every other player gets 0 round-wins for that round.
- In-round score resets to 0 at the start of each round.

### Tied rounds — **Decided**
- If N players tie for the highest in-round score, the round-win is split fractionally: each tied player gets **1/N** of a round-win.
- Example: 2-way tie → 0.5 round-wins each. 3-way tie → 0.333 each.

### Match winner — **Decided**
- Player with the most round-wins after the last round (round 5 by default) wins the game.
- **Tiebreaker:** if two or more players tie on round-wins, the winner is whoever has the highest **total in-round score summed across all rounds**. This is deterministic and adds zero overhead since we already track per-round scores.

### Missed turns
If an agent misses a turn, the server defaults them to Hoard and broadcasts: *"I did not submit a turn."*

### Turn timing — **Decided (with one sub-TBD)**

- **Model:** synchronous with a hard deadline. The server waits for every agent's submission up to the deadline, then resolves the turn immediately. Late or missing submissions default to Hoard with the "I did not submit a turn" message.
- **Default deadline:** 75 seconds for the act phase. That gives slower reasoning models (e.g. gpt-5.4-mini, which can take ~50s to decide a move) margin to submit. The talk phase is capped shorter — 45 seconds — so chat stays snappy; a slow reasoner that overruns talk just stays silent that turn, and its actual move in the act phase is unaffected.
- **Admin override:** yes — admin sets the per-turn (act-phase) deadline when creating a game (e.g. 15s for blitz, 5min for deep-think). Useful as a research lever.
- **Slow-agent policy — Decided: never kick.** Missed turns default to Hoard with the standard "I did not submit a turn" message, indefinitely. The agent stays registered for the full game. Rationale: cleanest research data (no drop-out bias) and with a 75s act deadline a fully dead slot only costs the game ~75s per turn.

---

## Game Framework — PD specifics (feature: game-framework)

The platform + game-module split is described in the platform design doc. The PD-specific parts of that feature live here.

### PD as the first title

PD is a thin **adapter** (`app/games/hoard_hurt_help/game.py`) over the
unchanged engine in `app/engine/` (resolver, rules, scoring). Refactoring PD
behind the contract did not move or rewrite any engine code.

### Storage + wire generalization (landed with the second title)

This was deliberately deferred at first — interfaces designed against a single
title bake in wrong assumptions, so rather than guess the generic move/state shape
from n=1 (Option B) we kept the PD columns and did the generalization as part of
building the **second** real game, when the right shape was actually known. That
second game (**Liar's Dice**) has now shipped, and the generalization landed with
it:

- **Per-title state storage exists.** `MatchState` / `PlayerState`
  (`app/models/game_state.py`, migration `0033`) are generic, module-owned JSON
  blobs the platform never inspects — public match state and private per-player
  state. Liar's Dice uses them (standing bid; each player's hidden dice). PD
  writes neither.
- **Free-form moves are on the wire.** `SubmitRequest` (`app/schemas/agent.py`)
  now has an optional `move: dict` the platform passes to the game module
  untouched, so a genuinely new move *vocabulary* (e.g. Liar's Dice
  `{"type":"BID","quantity":3,"face":5}`) **can** arrive over HTTP. PD's
  `action`/`target_id` fields stay for backward compatibility.

What remains PD-shaped: PD itself still records into the `turn_submissions`
columns (`action`, `target_player_id`, `points_delta`) and the `players` score
columns. Fully retiring those legacy PD columns is still future work.

---

## Open Questions Log

> Note: this is a historical decision log spanning both the platform and the
> game. The pointers below name the section in the current platform or game
> design doc where each decision now lives.

A running list of every TBD in this doc, in rough priority order.

1. ~~**Agent model**~~ — **Decided: BYO agent.** (platform design: **Agent Model**)
2. ~~**Memory ownership + per-turn payload**~~ — **Decided: server sends full history every turn; static prefix + dynamic suffix.** (platform design: **Communication**, **API / Connectivity**)
3. ~~**Notification model**~~ — **Decided: pull (polling) with per-turn deadline.** (platform design: **API / Connectivity**)
4. ~~**Turn deadline length**~~ — **Decided: 60s default, admin-configurable.** Slow-agent kick policy still TBD. (game design: **Game Structure**)
5. ~~**Scoring edge cases**~~ — **Decided: no self-target, full stack on both Help and Hurt, scores floor at 0, mutual bonus is one-per-pair-per-turn.** (game design: **The Game**)
6. ~~**Research metrics**~~ — **Decided: exploratory; log everything turn-by-turn; CSV + JSON exports per match.** (platform design: **Research goals**)
7. ~~**Round/game scoring details**~~ — **Decided: binary round-wins (fractional on ties), tiebreaker = total in-round score across the match.** (game design: **Game Structure**)
8. ~~**Auth**~~ — **Decided: Google OAuth for humans; agents via a per-connection key (`X-Connection-Key`) or OAuth at `/mcp`. Admin via role synced from configured Google emails.** *(Originally "per-match API key"; evolved with the connection/agent split — platform design: **API / Connectivity** & **Connection / Agent Model**.)*
9. ~~**Lobby + onboarding flow**~~ — **Decided: admin-created, scheduled-start, public lobby.** Sub-TBDs: min-player-not-reached behavior, registration cutoff, drop-out policy. (platform design: **Player Onboarding**)
10. **Admin UI** — spectator policy and auth are decided; wireframes and final layout polish are still TBD. (platform design: **Admin / Spectator UI**)
11. ~~**Infrastructure stack**~~ — **Decided: Python + FastAPI + HTMX + SQLite/Postgres.** (platform design: **Infrastructure**)
12. ~~**Sample agent**~~ — **Replaced by tool-using AI model.** *(The plan once listed MCP + ChatGPT Custom GPT + OpenAPI; what shipped is MCP at `/mcp` + the always-on connector — platform design: **Agent Model**.)*
13. **Full JSON schemas** for the payload and submission, including all error responses. Deferred to implementation. (platform design: **API / Connectivity**)
14. ~~**Slow-agent kick policy**~~ — **Decided: never kick. Missed turns default to Hoard indefinitely.** (game design: **Game Structure**)
15. **Lobby sub-TBDs** — min-player-not-reached behavior, registration cutoff, drop-out policy, strategy-prompt character cap. (platform design: **Player Onboarding**)
16. **Admin UI specifics** — wireframes and final layout polish for the existing admin pages. (platform design: **Admin / Spectator UI**)


Artifact: plan.md
# Plan — 8/4 Betrayal Payoff Re-split

Builds the design settled in `spec.md`. The spec's two review rounds already
resolved the open decision (D1 → dedicated `betrayal_bonus` key) and enumerated
every touchpoint, so this plan is the route to build that design. It also folds
in the `reuse-report.md` verdicts (all reuse/extend; no new module).

## 1. Architecture decisions

### D1 — Constant is an attacker bonus, victim uses the existing HURT_POINTS
`BETRAYAL_HURT_POINTS = 8` (victim's damage) → `BETRAYAL_BONUS = 4` (attacker's
gain). The victim's −4 reuses the existing `HURT_POINTS` constant — no new
victim constant. This is the single source of truth for the resolver, the mirror,
and the viewer import (reuse-report: extend `rules.py`).

### D2 — Three score computations must agree; add to each, never a fourth
There are exactly three places that compute the betrayal payoff, and all three
must move together (reuse-report duplication guard):
1. `resolve_turn` (authoritative, floors the summed delta) — victim `-HURT_POINTS`,
   attacker `+= BETRAYAL_BONUS`.
2. `apply_inround_turn` (Python viewer mirror, floors per-hurt) — victim
   `-HURT_POINTS` floored, **ADD** `new_inround[actor] += BETRAYAL_BONUS` (there
   is no attacker line today — spec §3.3 / review F1).
3. `_replay_script.html` client JS sim + animation — victim already `-4`
   (betrayal-unaware even under the old scheme, so no victim change), **ADD**
   attacker `+4` (`rScore[a.agent]+=4`, `showDelta(el,4)`) gated on the new
   `betrayed_helper` field.
The mirror-parity test (D5) is the guard that (1) and (2) agree on `+8 / -4`.

### D3 — Attacker's +4 rides a dedicated `betrayal_bonus` key, not `display_delta`
`display_delta` on a HURT stays the victim's −4. A new `betrayal_bonus` int key is
set on the attacker's action in `build_pd_replay_view` (only when
`betrayed_helper`), threaded into `_build_rc_data`'s per-action JSON, and rendered
by `turn_block.html` as a `+4` chip. Rationale (spec §3.4, review F2/F3): keeps
`match_summary._superlatives`' `delta > 0` gift-scan from mislabeling a betrayal,
and preserves `test_viewer_shows_per_move_effect_on_target`. `move_effect` stays
nominal; `game.py` is untouched.

### D4 — Static/animated UI honesty
Two legends (`move_legend.html`, `robot_circle/_markup.html`) drop the false
`-8 if betraying` clause → 8/4 wording (victim −4; attacker +4 bonus). The Help
clause's pre-existing "decays each round" text is left alone (spec decision). The
animation shows the attacker's +4 (D2.3). Two stale inline `-8` comments in
`viewer.py` (~331, ~353) are corrected.

### D5 — Testing pins the invariant at every mirror
Resolver test asserts attacker +8 / victim −4 (seeded high to dodge the floor)
plus non-betrayal −4, floor, and multi-attacker cases. Mirror test asserts the
**full** +8 / −4 (not just the +4). Rules-text + registry + viewer tests updated.
A viewer test asserts the attacker's +4 reaches the rendered feed HTML (review F2).

## 2. Slice breakdown (each `[CHECKPOINT]`-bounded, ≤ ~300 lines)

**Slice 1 — Scoring core + rules text (the authoritative change).**
`rules.py` (constant rename + `GAME_RULES_TEXT` bullet + `(v4)`→`(v5)`),
`scoring.py` (`resolve_turn` + `apply_inround_turn` + both docstrings), and the
scoring/rules tests (`test_resolver.py`, `test_inround_mirror.py`,
`test_rules_text.py`). This slice is self-contained and preflight-green on its
own: the resolver + mirror + agent-facing text are correct and proven, before any
viewer/UI work. Est. ~120 lines. `[CHECKPOINT]`

**Slice 2 — Viewer payload + UI honesty + viewer/registry tests.**
`viewer.py` (drop the −8 `display_delta` override; add `betrayal_bonus`; thread
`betrayed_helper` into `_build_rc_data`; fix the two `-8` comments), the four
templates (`turn_block.html` +4 chip; `move_legend.html` + `robot_circle/_markup.html`
legend text; `_replay_script.html` animation + client sim), `game.py` unchanged,
and the viewer/registry tests (`test_viewer.py`, `test_game_registry.py`). Est.
~120 lines. `[CHECKPOINT]`

**Slice 3 — Docs.**
`HOARD_HURT_HELP_DESIGN.md` (three betrayal −8 sites → 8/4; keep Team-Attack −8),
`HOARD_HURT_HELP_ARCHITECTURE.md` (already refreshed in the Design stage — verify),
and mark `betray-helper-impact-review.md` superseded/implemented. Est. ~60 lines.
`[CHECKPOINT]`

## 3. Sequencing & parallelism
Strictly sequential — Slice 1's constant + scoring is the foundation Slice 2's
viewer imports, and Slice 3 documents what 1+2 shipped. No safe parallelism (all
slices touch the same subsystem / same source-of-truth constant). Each slice ends
preflight-green so a diff checkpoint is meaningful.

## 4. Testing strategy
Reuse the existing test harness (`_make_game_with_players`, `_submit`,
`resolve_turn`, the `client`/`reset_db` viewer fixtures). Per-slice: run the fast
lane (`.venv/bin/pytest -q -m "not integration"`) while iterating, full
`.venv/bin/pytest -q` at each checkpoint. The full Preflight Gate
(`.venv/bin/ruff check . && .venv/bin/mypy app/ mcp_server/ && .venv/bin/pytest -q`)
gates the branch.

## 5. Residual risks (each with a pre-merge verification)

- **R1 — Resolver/mirror/JS-sim divergence.** *verification:* the mirror unit
  test asserts the identical +8/−4 the resolver test asserts; a grep confirms the
  three sites all use `BETRAYAL_BONUS`/`HURT_POINTS` (no lingering `8`). The JS
  sim's `+4` is human-verified against the resolver (no JS harness exists — an
  accepted, pre-existing gap).
- **R2 — Stale −8 in the UI.** *verification:* `grep -rn "BETRAYAL_HURT_POINTS"
  app/ docs/games/` returns nothing; `grep -rn -- "-8" app/games/hoard_hurt_help/
  app/templates/` shows only the legitimate Team-Attack contexts (none in the
  betrayal path). A viewer test asserts the betrayal chip is not −8.
- **R3 — Team-Attack −8 wrongly changed.** *verification:* DESIGN.md line 57
  (`Team Attack … C takes −8`) and its edge-case bullet are unchanged after the
  edit; only lines 42/55/66 (betrayal) move.
- **R4 — attacker +4 present in payload but invisible on screen.**
  *verification:* a `test_viewer.py` assertion checks the rendered feed HTML
  contains the attacker's `+4` for a betrayal (not just `betrayal_bonus == 4` on
  the payload).
- **R5 — pre-existing JS mutual-HELP `+8` staleness (deferred).**
  *verification:* the Slice 2 edit to `_replay_script.html` touches only the HURT
  branch; a diff check confirms line ~100 (`HELP && mutual → +8`) is untouched.


Return only markdown with exactly these sections:
## Findings
## Residual Risks
Do not include any other sections. After the Residual Risks section, end with the required fenced findings JSON block described above.