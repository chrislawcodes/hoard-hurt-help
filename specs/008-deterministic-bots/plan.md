# Sims Plan

**Status:** planned
**Created:** 2026-06-03

## Goal

Add Sims: programmatic, non-LLM players that can run through real
Hoard-Hurt-Help games. Sims should make repeatable choices, help test larger
games without API cost, and keep research data clearly separated from LLM
behavior.

Current planning assumption: Hoard-Hurt-Help games cap at **20 players**.

## Recommended Shape

Use this model:

```text
Sim preset -> Sim instance -> Player in Game
```

- **Sim preset:** a reusable trait bundle.
- **Sim instance:** a concrete generated participant with traits, name, and seed.
- **Player in game:** the existing `players` row that participates in one game.

Sims should submit internally. They do not need API keys, external polling, or
an LLM provider.

## Sim Traits

A Sim is defined by:

```text
strategy + truthfulness + trust model
```

| Trait | What It Controls |
|---|---|
| Strategy | The Sim's game goal and action pattern |
| Truthfulness | How closely talk matches actual intent |
| Trust model | How the Sim gains and loses trust in other players |

## Phases

| Phase | Goal | Build | Done When |
|---|---|---|---|
| 1 | Spec and trait model | Strategy roster, truthfulness presets, trust presets, 20-player cap decision | We agree on the trait model before implementation |
| 2 | Core Sim engine | Pure strategy/trust/talk modules with seeded decisions | Unit tests prove stable actions and talk modes |
| 3 | Auto-submit | Scheduler submits Sim talk/action phases | A game with Sims completes without external agents |
| 4 | Admin controls | Admin can fill a game with Sims or a preset pack | Admin can run a 20-player Sim game from the UI |
| 5 | Player private games | Private/sandbox games can add Sim packs | Players can test LLM bots against Sims |
| 6 | Analysis and batch runs | Metadata, exports, headless repeated runs | Results can compare seeded Sim games across runs |

## Phase 2 Details

Create DB-free modules:

| Module | Responsibility |
|---|---|
| Strategy | Choose one talk intent in talk phase and one action intent in action phase |
| Intent | Map talk intents to talk topics and action intents to legal actions |
| Trust | Score other players from action history and talk signals |
| Talk signals | Extract basic typed signals from recent public messages |
| Talk generation | Generate honest, vague, or misleading talk from traits and intent |

Initial strategies:

| Strategy | Behavior |
|---|---|
| `coalition_seeker` | Looks for a reliable partner, proposes cooperation, and helps that partner when trust is good |
| `loyal_partner` | Picks or is assigned a partner and sticks with them unless betrayed badly |
| `grudger` | Cooperates early, but punishes repeat attackers or betrayers |
| `leader_pressure` | Uses `HURT` against the current round leader only when they are meaningfully ahead |
| `opportunist` | Cooperates while behind, but hoards or defects when ahead |
| `endgame_sniper` | Plays cooperative early, then uses `HURT` against leaders on turns 8-10 |
| `diplomat` | Avoids hurt unless attacked and tries to maintain mutual-help pairs |
| `crowd_follower` | Copies the most successful common behavior from recent turns |

Acceptance criteria:

- Same context and seed always produce the same decision.
- Every action is valid for the game rules.
- No Sim can self-target.
- Strategy logic is DB-free and unit-testable.
- Sims choose provisional talk intents before current-turn talk is revealed.
- Sims choose final action intents after reading current-turn talk signals.
- Each strategy has a deterministic priority order for talk intents and action intents.
- Phrase library starts with one canonical phrase per talk intent and truth mode.
- Trust uses a per-Sim `-100` to `100` score map for every other player.
- Trust scores weight action history more heavily than talk.
- Truthfulness affects talk, not action choice, using honest / partial / quiet / misdirecting / false modes.
- Reading is basic and deterministic: Sims extract simple signals from recent public talk.
- Hoard-Hurt-Help max players is capped at 20 in defaults and admin creation.

## Phase 3 Details

Add an internal auto-submit hook in the scheduler:

1. When talk phase opens, Sims choose talk intents and submit messages.
2. Wait normally. If human/LLM players are also present, the normal deadline still applies.
3. When act phase opens, Sims read current talk signals, choose action intents, and submit actions.
4. Resolve through the existing game module and resolver.

This keeps viewer, exports, scoring, and history on the normal path.

## Phase 4 Details

Add admin controls:

- Fill empty seats with Sims.
- Add a chosen number of each preset.
- Add preset packs.
- Keep clear labels in the UI.

Suggested packs:

| Pack | Mix |
|---|---|
| Calm Table | Mediators, Builders, Bonded Sims |
| Chaos Table | Grudges, Balancers, Closers |
| Coalition Table | Bonded Sims, Builders, Mediators |
| Mixed 20 | Balanced default Sim table |

## Phase 5 Details

Expose Sim packs only for private/sandbox games at first. Do not allow them in
public competitive games until the research and abuse impact is clear.

## Phase 6 Details

Add metadata and batch tooling:

```json
{
  "agent_kind": "sim",
  "strategy": "grudger",
  "truthfulness": 80,
  "trust_model": "grudging",
  "seed": 42
}
```

Later, add a command like:

```bash
python -m scripts.run_sim_games --pack mixed-20 --games 100
```

## Big Questions

| Question | Current Recommendation |
|---|---|
| Public games? | No Sims in public games at first |
| User-owned? | No, start as platform-provided test players |
| API keys? | No, submit internally |
| Same history as LLMs? | Yes, use the same resolved public history |
| Seeds? | Yes, traits, talk variation, and tie-breakers should be seeded |
| Truthfulness? | A 0-100 trait that controls talk only |
| Trust? | A preset model that scores players from actions first, talk second |
| Customization? | Preset packs first, custom tuning later |
| Simulator first? | No, real game loop first; batch simulator later |

## Data Integrity Notes

Sims must be clearly labeled in UI and exports. They are test fixtures, not LLM
behavior. Any future analysis should be able to filter them out or group them by
strategy, truthfulness, trust model, and seed.
