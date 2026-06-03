# Sims Tech Spec

**Status:** draft
**Created:** 2026-06-03

## Purpose

This document turns the Sims spec and architecture into implementation
contracts.

It covers:

- how Sims are stored
- how they decide talk and action
- how trust and talk signals are computed
- how the scheduler auto-submits Sims
- how presets and fixtures are represented

It does not cover UI polish, voice styling, or the later batch simulator.

## Core Design Choice

Sims should be represented as **platform-managed Bots**.

That keeps the existing `Player -> Bot -> User` shape intact and avoids a
parallel participation model.

### Rule

- `Bot.kind = external` means a normal user-managed bot that connects through
  the existing agent API.
- `Bot.kind = sim` means a deterministic platform bot that the scheduler runs
  internally.

### Why this shape

- `Player` already points at `bot_id`.
- Existing exports, joins, and game history already understand `Bot`.
- The platform can keep one persistent Sim record per participant.

### Ownership

`Bot.user_id` stays required.

For Sims, that user should be a dedicated platform-owned account that is not
shown as a normal product user.

## Data Model

### `games` table

No new table is needed for Sims. The existing game row stays the source of
truth for the match container.

The one rule this feature adds to the game model is a hard ceiling of `20` on
`max_players` for Hoard-Hurt-Help games. The existing `max_players` column can
stay in place, but creation and validation must not allow a value above 20.

### `bots` table

Add Sim fields to the existing `bots` table.

| Field | Type | Notes |
|---|---|---|
| `kind` | enum string | `external` or `sim` |
| `sim_strategy` | string | Required when `kind = sim` |
| `sim_truthfulness` | integer | `0..100`, required when `kind = sim` |
| `sim_trust_model` | string | Required when `kind = sim` |
| `sim_seed` | integer | Required when `kind = sim` |
| `sim_version` | string | Version tag for the preset bundle used to create the Sim |
| `sim_fixture_pack` | string, nullable | Optional hidden fixture lane label |

### Validation

- `kind = external` keeps current bot behavior.
- `kind = sim` requires the Sim fields above.
- `provider` and `model` stay nullable and should normally be `NULL` for Sims.
- Sim bots should not expose or use their auth key in product flows.
- Sim bots should still be normal `players` rows when they enter a game.

### `players` table

No new player table is needed. A Sim occupies the same seat model as any other
participant:

- one `Player` row per game seat
- `player.bot_id` points at the Sim bot
- `player.agent_id` remains the visible in-game agent label

### Derived runtime state

The following should not be persisted as first-class tables in v1:

- per-opponent trust map
- talk signal summaries
- phrase choice state
- per-turn Sim decision state

Those values are recomputed from:

- the Sim bot row
- the current game/turn
- resolved public history
- current public talk
- the Sim seed

### What does not get a new table in v1

- trust state
- talk signals
- phrase library
- preset packs

Those are derived from code and game history, not persisted as separate rows.

## Runtime Types

The Sims engine should live in a focused package, for example:

- `app/engine/sims/presets.py`
- `app/engine/sims/signals.py`
- `app/engine/sims/trust.py`
- `app/engine/sims/strategies.py`
- `app/engine/sims/phrases.py`
- `app/engine/sims/runtime.py`

Suggested core types:

```python
@dataclass(frozen=True)
class SimProfile:
    strategy: str
    truthfulness: int
    trust_model: str
    seed: int
    version: str

@dataclass(frozen=True)
class SimContext:
    game_id: str
    round: int
    turn: int
    phase: str
    your_agent_id: str
    all_agent_ids: list[str]
    history: list[HistoryTurn]
    scoreboard: list[ScoreboardRow]
    current_talk_messages: list[TalkMessage]
```

### Suggested runtime return types

Keep the runtime small and explicit:

```python
@dataclass(frozen=True)
class SimTalkDecision:
    intent: str
    message: str
    thinking: str

@dataclass(frozen=True)
class SimActionDecision:
    intent: str
    move: dict[str, str | None]
    thinking: str
```

The `move` dict should match the existing game-module contract:

```python
{"action": "HELP", "target_id": "AI_07"}
```

## Module Contracts

The following helpers should stay pure where possible and be easy to test in
isolation:

| Module | Contract |
|---|---|
| `presets.py` | Expand a pack id into one or more Sim profiles |
| `signals.py` | Extract typed talk signals from messages |
| `trust.py` | Build a pairwise trust map from game history and talk signals |
| `strategies.py` | Choose talk intent and action intent for one Sim |
| `phrases.py` | Render one canonical phrase for a talk intent + truth mode |
| `runtime.py` | Orchestrate the full talk or action decision for a Sim |

Suggested top-level functions:

```python
build_sim_profile(bot: Bot) -> SimProfile
extract_talk_signals(messages: list[TalkMessage], *, all_agent_ids: list[str]) -> list[Signal]
compute_trust_map(history: list[HistoryTurn], signals: list[Signal], profile: SimProfile) -> dict[str, int]
choose_talk_decision(context: SimContext, profile: SimProfile) -> SimTalkDecision
choose_action_decision(context: SimContext, profile: SimProfile) -> SimActionDecision
render_phrase(intent: str, truth_mode: str, *, seed: int) -> str
```

### Implementation rule

The strategy function should return an intent, not perform persistence.
Persistence belongs in the scheduler or the game module record helpers.

## Deterministic Seed Rule

Every Sim decision should be repeatable from:

- the Sim seed
- game id
- round
- turn
- phase
- agent id

Use one deterministic hash-to-RNG conversion and reuse it everywhere. Do not
use lexicographic agent-id ordering as the primary tie-break.

Recommended seed input:

```text
seed + game_id + round + turn + phase + agent_id
```

That seed should drive:

- truth mode selection within the truthfulness band
- tie-breaks among equal candidates
- any candidate partner/target selection
- any future phrase variation

### Tie-break rule

If two candidates are equally good after trust and strategy filtering, use the
seeded hash result as the final tie-break. Do not use `agent_id` ordering as
the first choice. Deterministic is the goal; bias is not.

## Talk Phase Pipeline

Talk happens before action, so Sims need a provisional talk pass.

### Talk flow

1. Load resolved history up to the previous turn.
2. Recompute trust from resolved history.
3. Let the strategy choose a talk intent.
4. Pick the truth mode from the Sim's truthfulness band.
5. Render one canonical phrase from the phrase library.
6. Store the public text in `turn_messages`.
7. Store a short structured `thinking` string for debugging.

### Talk output

The `thinking` field should be concise and structured, not a free-form chain of
thought.

Example:

```text
strategy=grudger intent=warn_attacker target=AI_07 trust=-72 seed=42
```

## Action Phase Pipeline

After all talk is revealed, Sims choose their final action.

### Action flow

1. Load resolved history plus the current turn's public talk.
2. Extract typed talk signals.
3. Recompute trust using action history first, talk second.
4. Let the strategy choose an action intent.
5. Convert the action intent to a legal game move.
6. Validate the move through the game module.
7. If the move is illegal or mechanically useless, fall back to the next
   action intent in the strategy order.
8. If no valid intent remains, submit `HOARD`.

### Mechanical uselessness

The first Sim release should treat `HURT` on a zero-score target as a failed
candidate, not a final choice. That move is legal in the game rules but
mechanically pointless, so the Sim should try its next fallback instead of
wasting the turn when another valid intent exists.

## Trust Model

Trust should be derived from game history, not stored in a separate table.

### Rule

- Start every pairwise trust score at `0`.
- Apply action evidence first.
- Apply talk evidence second.
- Clamp after every update to `[-100, 100]`.

### Current v1 evidence

Use the exact trust deltas from the spec.

| Evidence | Delta |
|---|---:|
| Helped me last turn | +4 |
| Helped me earlier this round | +2 |
| Mutual help succeeded | +5 |
| Hurt me last turn | -6 |
| Hurt me earlier this round | -3 |
| Broke expected mutual help | -4 |
| Hurt my current partner | -2 |
| Helped my current partner | +1 |
| Offered cooperation in talk | +1 |
| Mentioned me positively | +1 |
| Threatened me in talk | -1 |
| Apologized / asked for truce | +1, capped |

### Trust model presets

Implement the v1 preset table from the spec exactly.

The preset changes how much help, hurt, and talk matter. Do not invent a new
weighting system.

## Talk Signal Reader

Talk should be read as simple typed signals, not as free text.

### Signal extraction rules

Use deterministic keyword matching over recent public messages.

Suggested signals:

| Signal | Trigger |
|---|---|
| Direct mention | Exact agent id token appears in the message |
| Cooperation offer | Id plus `help`, `partner`, `ally`, `mutual`, `pact`, `lane`, `pair` |
| Loyalty claim | Id plus `stay`, `stick`, `loyal`, `continue`, `keep` |
| Threat | `hurt`, `hit`, `punish`, `retaliate`, `attack`, `target`, `coming for` |
| Apology / repair | `sorry`, `truce`, `repair`, `reset`, `forgive` |
| Leader warning | Current leader id plus `leader`, `ahead`, `runaway`, `top score`, `too far ahead` |

### Deduping

- Count at most one signal of each type per speaker per turn.
- Exact agent-id matching must use token boundaries so `AI_1` does not match
  `AI_10`.
- The reader should only emit typed signals; strategy code should not parse raw
  chat again.

## Strategy Runtime

Each strategy should remain a small priority list.

### Contract

- one talk intent per talk phase
- one action intent per action phase
- one legal move per action intent

### Candidate handling

The strategy runtime should evaluate candidates in order and stop at the first
valid one.

Validity means:

- legal for the game
- not self-targeting
- not mechanically useless when a better fallback exists

If candidate ties remain, use the deterministic seed hash, not agent-id order.

## Scheduler Integration

Sims should be auto-submitted by the scheduler, not through the public agent
HTTP endpoints.

### Talk phase

When the talk phase opens:

1. The scheduler identifies active players whose bots have `kind = sim`.
2. The Sims engine computes and records their messages.
3. Those messages count toward the normal talk-complete check.
4. The scheduler then waits for the remaining external players or the deadline.

### Action phase

When the action phase opens:

1. The scheduler identifies active players whose bots have `kind = sim`.
2. The Sims engine computes and records their moves through the game module.
3. Those submissions count toward the normal action-complete check.
4. The scheduler then waits for the remaining external players or the deadline.

### Important constraint

The scheduler should call shared internal helpers, not fabricate HTTP requests.
The public agent routes remain the path for external bots only.

### Concrete hook point

The least invasive shape is to add a small Sim helper in the scheduler that:

- loads the active players for the open turn
- filters to `bot.kind = sim`
- computes decisions in memory
- persists through the same `record_message` / `record_submission` module
  helpers the public API already uses

That keeps Sim moves on the normal storage path and avoids a second code path
for turn records.

## Presets And Fixtures

Preset bundles should be code constants with version tags.

### Public presets

These are the normal packs the host can choose from in private/admin games.
They are safe for player-facing use because they stay inside the versioned,
documented Sim roster.

### Hidden fixtures

These are internal-only packs used for mechanical edge cases and regression
tests. They should not appear in normal admin UI.

### Versioning rule

Preset packs must be versioned so a past run can be recreated with the same
bundle later.

### Pack shape

A preset pack should be a small code-defined bundle, not an editable runtime
blob in v1.

```python
@dataclass(frozen=True)
class SimPackEntry:
    strategy: str
    truthfulness: int
    trust_model: str
    seed_offset: int

@dataclass(frozen=True)
class SimPack:
    id: str
    version: str
    name: str
    hidden: bool
    entries: list[SimPackEntry]
```

The pack registry should support:

- public packs for normal host use
- hidden packs for admin-only mechanical fixtures

## Migration Notes

The first schema migration for Sims should be additive and narrow:

- add the `Bot.kind` field
- add the Sim trait columns to `bots`
- keep all current `players`, `turns`, and history tables intact
- update Hoard-Hurt-Help config defaults so `max_players = 20`

The migration should not split state into a new Sim table in v1.
The point is to keep the feature close to the existing bot/player model and
avoid a second participation graph.

## Game Module Contract Update

Hoard-Hurt-Help itself needs one small module-level update to match the new
cap:

- `config_defaults().max_players` should become `20`
- admin create-game validation should reject values above `20`
- any UI copy that assumes the old larger cap should be updated with the same
  rule

No scoring logic changes are required for Sims beyond the existing game rules.

## Normal Game And Bot Flow

We do not need a separate admin-only Sim creation flow in v1.
Instead, Sims should fit into the normal game/bot lifecycle:

1. A user creates a game as usual.
2. The user creates or adds bots as usual.
3. When a bot is meant to be a Sim, the creator chooses a public Sim pack.
4. The system stores the Sim traits on the bot row and seats it as a normal
   `Player`.

### Suggested creation contract

The existing bot creation path should accept an optional Sim preset choice:

```python
create_bot(
    db,
    *,
    user_id: int,
    name: str,
    kind: str = "external",
    sim_pack_id: str | None = None,
    seed: int | None = None,
) -> Bot
```

When `kind = "sim"`:

- `sim_pack_id` is required
- the pack expands into the Sim trait fields
- the bot is marked as a Sim on creation
- the bot can later be seated into any eligible game through the existing join
  / add-player flow

### Behavioural rules

- stay within the 20-player cap
- allow public Sim packs through the normal bot path
- keep hidden fixture packs internal-only
- create one persistent Sim bot per seat
- apply the resolved preset traits to the bot row
- create the matching `Player` row through the existing join/add-player flow
- use deterministic seeds for seat assignment

## Tests

The first test pass should prove:

- same seed + same context = same decision
- tie-breaks do not prefer low agent ids
- `HURT` on a zero-score target falls back cleanly
- trust clamping stays within bounds
- talk signals are extracted deterministically
- talk phase completes before action phase for Sims
- mixed games still let external bots use the existing routes
- Sims can be created through the normal bot/game flow without a separate
  admin-only creation screen

## Out Of Scope For This Doc

- voice presets
- batch simulator CLI
- public-game Sims
- player-facing customization of individual phrase wording
- new game rules
