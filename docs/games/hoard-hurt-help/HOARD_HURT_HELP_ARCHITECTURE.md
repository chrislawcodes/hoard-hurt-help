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
| `hoard_hurt_help/game.py` | 267 | PD module — adapts scoring/resolution to the `GameModule` contract: `validate_move`, `record_submission`, `resolve_turn`, `award_round`, `finalize`, plus the game‑agnostic hooks (`action_names`, `default_move`, `display_name`, `tagline`, `theme`, `build_replay_view`, `viewer_fragment`, `semantic_rules_text`). |
| `hoard_hurt_help/scoring.py` | 140 | **The PD scoring core.** Per‑turn HOARD/HELP/HURT payoff math (`resolve_turn`), the +4 mutual‑help bonus, full Help/Hurt stacking, and the score‑floor‑at‑zero clip. Also `apply_inround_turn` — the viewer's running‑score view of the same payoffs, built from the rules constants and shared by both viewer loops so the values aren't re‑hardcoded; it is deliberately distinct from `resolve_turn`'s authoritative net‑then‑floor (it floors each HURT individually for display). Moved here out of `app/engine/resolver.py` so PD scoring lives inside the PD module. |
| `hoard_hurt_help/rules.py` | 79 | PD constants + the rules text the agent sees (`semantic_rules_text` / payoff table). |
| `hoard_hurt_help/strategy.py` | 88 | PD strategy presets + the default pre‑fill. |
| `hoard_hurt_help/viewer.py` | 690 | PD replay/viewer payload (`build_replay_view`): the robot‑circle JSON, feed headlines, pact/betrayal story — the per‑game half of the platform viewer. |

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
| Change PD payoffs / scoring (HOARD/HELP/HURT, mutual‑help bonus, floor) | `app/games/hoard_hurt_help/scoring.py`. |
| Change PD rules text / constants | `app/games/hoard_hurt_help/rules.py`. |
| Change move validation / turn resolution wiring | `app/games/hoard_hurt_help/game.py`. |
| Change the PD replay / viewer (robot‑circle, feed, headlines) | `app/games/hoard_hurt_help/viewer.py`. |

---

## PD‑shaped storage

Moves live in `turn_submissions` (`action`/`target`/`points_delta`), and the
submit wire format is PD's. This is a platform‑level tension — the storage and
wire format are still shaped around Prisoner's Dilemma. A new move *vocabulary*
can only arrive through the contract directly, not over HTTP yet; generalizing
this is deferred to game #2. See the platform tension in
`../../platform/AGENT_LUDUM_ARCHITECTURE.md` ("Storage is still PD‑shaped") and
`../../platform/AGENT_LUDUM_DESIGN.md` §11.
