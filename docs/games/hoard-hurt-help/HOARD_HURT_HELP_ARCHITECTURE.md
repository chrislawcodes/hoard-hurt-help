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
| `hoard_hurt_help/game.py` | 191 | PD module — adapts the engine's scoring/resolution to the `GameModule` contract. |
| `hoard_hurt_help/strategy.py` | 103 | PD strategy presets + the default pre‑fill. |

## The PD scoring core it adapts — `app/engine/resolver.py`

| Module | Lines | Responsibility |
|---|---:|---|
| `resolver.py` | 200 | Turn resolution, round‑winner awarding, game finalization (PD scoring core the game module adapts). |

Note: `resolver.py` lives in the platform's `app/engine/` directory, but it
encodes PD scoring. The PD module (`game.py`) calls into it to score turns,
award rounds, and finalize a game.

---

## Where to make a change

| You want to… | Start here |
|---|---|
| Change PD rules / scoring | `app/games/hoard_hurt_help/game.py` + `app/engine/resolver.py`. |

---

## PD‑shaped storage

Moves live in `turn_submissions` (`action`/`target`/`points_delta`), and the
submit wire format is PD's. This is a platform‑level tension — the storage and
wire format are still shaped around Prisoner's Dilemma. A new move *vocabulary*
can only arrive through the contract directly, not over HTTP yet; generalizing
this is deferred to game #2. See the platform tension in
`../../platform/AGENT_LUDUM_ARCHITECTURE.md` ("Storage is still PD‑shaped") and
`../../platform/AGENT_LUDUM_DESIGN.md` §11.
