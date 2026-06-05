# Tech Spec — Liar's Dice (game title #2)

The build contract for Liar's Dice: exact interface changes, data model and
migration, the pure rules engine, wire schemas, payload building, hidden-info
enforcement, Sims, and the test plan. Read `design.md` (decisions D-1…D-11) and
`architecture.md` (modules, flows, the decoupling line) first — this spec assumes
both.

Standards: `from __future__ import annotations`; full type annotations; `async def`
for all DB paths; specific exceptions; no suppressions (CLAUDE.md). Preflight
(`ruff`, `mypy app/ mcp_server/`, `pytest -q`) must pass.

---

## 1. Scope

In scope:
- A new game module `app/games/liars_dice/` (pure engine + thin DB module + rules
  text + strategy + Sims).
- Four additive, gated platform seams: the `TurnDriver` split, the private/public
  payload hooks, the free-form move on the wire, and the generic per-title state
  store.
- A per-game `final_placement` consumed by records/Elo.
- Admin create-match fields for the per-match toggles (wild on/off, dice count).
- A minimal Liar's Dice viewer.

Out of scope (tracked elsewhere):
- Relocating PD's engine out of `app/engine/` (HHH tech spec).
- Spot-on call, per-hand table-talk round, dice-table animation (design deferrals).

Hard constraint: **PD behavior is byte-identical after this change.** The PD test
suite and `tests/test_stub_game.py` stay green unmodified. This is the merge gate.

---

## 2. Contract changes — `app/games/base.py`

New members on the `GameModule` protocol. Every one ships a **default** that
reproduces PD, so PD opts out by inheriting defaults and LD overrides.

```python
# Loop progression (used only when config.simultaneous is False)
async def next_actor(self, db: AsyncSession, match: Match) -> str | None:
    """agent_id of the single player to act now, or None when the round (hand)
    is over. Default: raise NotImplementedError — simultaneous games never call it."""

async def on_round_start(self, db: AsyncSession, match: Match, round_num: int) -> None:
    """Set up a new round/hand (LD: roll every still-in player's dice, clear the
    standing bid). Default: no-op."""

async def is_match_over(self, db: AsyncSession, match: Match) -> bool:
    """True when the match should finalize. Default: rounds_awarded >= total_rounds
    (PD's fixed-grid end)."""

async def default_move(self, db: AsyncSession, match: Match, player: Player) -> dict[str, Any]:
    """The move to record when a player misses their deadline. Default:
    {"action": "HOARD"} (PD). LD: smallest legal raise / opening / challenge."""

# Player-facing payload (the contract finally owns 'what you see')
async def private_state_for(self, db: AsyncSession, match: Match, player: Player) -> dict[str, Any]:
    """Per-player secret state for the turn payload. Default: {} (PD has none)."""

async def public_state_for(self, db: AsyncSession, match: Match, viewer: Player | None) -> dict[str, Any]:
    """Game-rendered public state block for the turn payload / spectator.
    Default: {} (PD keeps its existing history/summary path — see HHH tech spec)."""

# Records / Elo
async def final_placement(self, db: AsyncSession, match: Match) -> list[int]:
    """player_ids ranked best→worst for a completed match. Default: order by
    (total_round_wins desc, total_round_score desc) — PD's existing tiebreaker."""
```

Unchanged members keep working: `config_defaults`, `rules_text`,
`strategy_presets`, `default_strategy`, `validate_move`, `record_submission`,
`record_message`, `resolve_turn`, `award_round`, `finalize`, `move_effect`,
`theme`.

`move_effect` stays PD-oriented; for LD it returns `(0, None)` (the viewer reads
bids/showdowns from state, not from a numeric per-move delta).

---

## 3. The turn-loop split — `app/engine/scheduler.py` + new driver module

### 3.1 `TurnDriver` interface (new, e.g. `app/engine/turn_drivers.py`)

```python
class GameLoopContext:
    session_factory: async_sessionmaker
    match_id: str
    module: GameModule
    publish: Callable[..., Awaitable[None]]
    # shared helpers extracted from today's scheduler:
    open_turn: ...          # get-or-create a Turn row (resume-safe)
    wait_for_turn: ...      # block until the expected submitter(s) submit or deadline
    wait_for_messages: ...  # talk-phase wait (simultaneous games only)
    begin_act_phase: ...

class TurnDriver(Protocol):
    async def run_match(self, ctx: GameLoopContext) -> None:
        """Drive a whole match to completion. Owns its own resume logic by reading
        persisted match/turn state."""
```

### 3.2 Two implementations

- **`SimultaneousDriver`** — today's `_run_game` body, **moved verbatim** (round
  reset → per-turn talk→act → resolve all → `award_round` → `finalize`). PD uses it.
- **`SequentialDriver`** — Liar's Dice:

```text
resume: read match_state (hand, active_actor, standing_bid). Continue there.
loop:
  actor = await module.next_actor(db, match)
  if actor is None:                      # hand over (a challenge was made)
      await module.award_round(db, match, hand)     # showdown, dock a die
      await publish("round_ended", showdown)
      if await module.is_match_over(db, match):
          await module.finalize(db, match)
          await publish("game_completed", winner)
          return
      hand += 1
      await module.on_round_start(db, match, hand)   # re-roll, clear bid
      continue
  turn = await ctx.open_turn(round=hand, turn=bid_index, actor=actor)
  await publish("turn_opened", {actor, deadline, public_state})
  await maybe_auto_submit_sim(actor)     # if actor is a Sim
  await ctx.wait_for_turn(turn, expected={actor})
  if not submitted_by_deadline(actor):
      mv = await module.default_move(db, match, actor_player)
      await module.record_submission(db, turn, actor_player, mv, existing=None, defaulted=True)
  await module.resolve_turn(db, turn)    # bid: no-op; challenge: marks hand over via next_actor
  await publish("turn_resolved", ...)
```

### 3.3 Scheduler skeleton (stays game-agnostic)

`SchedulerRegistry`, the due-game poller, `start_game`, and
`resume_active_games_on_startup` stay. The only change: select the driver and run
it.

```python
driver = SimultaneousDriver() if cfg.simultaneous else SequentialDriver()
await driver.run_match(ctx)
```

`cfg.simultaneous` is read from the match's game module config — the existing,
currently-unused `GameConfig.simultaneous` flag.

### 3.4 Single-actor quorum

`wait_for_turn` takes an `expected` set. For PD it's all active players (today's
behavior). For LD it's the single `{actor}`. The "resolve early when everyone
submitted" logic keys off `expected`, so a sequential turn resolves the instant
the one actor submits.

---

## 4. Data model & migration

### 4.1 New tables (`app/models/game_state.py`)

Generic, opaque to the platform, reusable by future hidden-info games.

```python
class MatchState(Base):
    __tablename__ = "match_state"
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    state_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), ...)

class PlayerState(Base):
    __tablename__ = "player_state"
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    state_json: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), ...)
```

- Use SQLAlchemy `JSON` (works on SQLite + Postgres). Wrap in
  `MutableDict.as_mutable(JSON)` **or** reassign the whole dict on write so the ORM
  marks the column dirty — a silent in-place mutation that doesn't persist is the
  classic bug here; a test must assert a round-trip.

### 4.2 New columns on `turn_submissions` (D-3)

```python
quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
face:     Mapped[int | None] = mapped_column(Integer, nullable=True)
```

### 4.3 Per-match game settings

Per-match toggles (wild on/off, dice per player) are seeded into
`match_state.state_json["config"]` at match start by `on_round_start(hand=1)`,
read from the admin create form (§9). `config_defaults()` supplies the defaults if
the form is left untouched.

### 4.4 Migration

One Alembic migration: create `match_state`, `player_state`; add `quantity`,
`face` to `turn_submissions`. **Additive and nullable** — no backfill, PD ignores
all of it. Covered by `tests/test_migrations.py`.

---

## 5. Pure rules engine — `app/games/liars_dice/engine.py`

No DB, no async. Fully unit-tested. Shared by the module **and** the Sims.

```python
@dataclass(frozen=True)
class Bid:
    quantity: int            # >= 1
    face: int                # 1..6 (1 = aces)

@dataclass(frozen=True)
class BidMove: quantity: int; face: int
@dataclass(frozen=True)
class ChallengeMove: pass
Move = BidMove | ChallengeMove

def parse_move(raw: dict) -> Move:
    """Raise GameError('MALFORMED_MOVE', ...) on a bad shape/type."""

def count_for(face: int, all_dice: list[int], *, wild: bool) -> int:
    """Dice showing `face`, plus all 1s when wild and face != 1."""

def resolve_showdown(bid: Bid, all_dice: list[int], *, wild: bool) -> tuple[bool, int]:
    """(bid_holds, actual_count). bid_holds = actual_count >= bid.quantity."""

def is_legal_raise(prev: Bid | None, nxt: Bid, *, wild: bool) -> bool:
    """Strictly-higher rule + ace switching rules (design §3.3-3.4).
    prev is None only for the opening bid (any valid Bid with face 2..6, q>=1)."""

def min_legal_raise(prev: Bid | None, total_dice: int, *, wild: bool) -> Bid | None:
    """Smallest strictly-higher legal bid. None when at the ceiling
    (no higher bid exists). prev None -> the minimum opening bid Bid(1, 2)."""

def roll(n: int, rng: random.Random) -> list[int]:
    """n dice in 1..6 from a seeded RNG (deterministic for Sims/tests)."""
```

Ace rules inside `is_legal_raise` / `min_legal_raise` (wild on):
- normal→aces: `nxt.quantity >= ceil(prev.quantity / 2)`.
- aces→normal: `nxt.quantity >= 2 * prev.quantity + 1`.
- aces→aces / normal→normal: standard strictly-higher.

`min_legal_raise` algorithm (wild on, prev is a normal face): try
`Bid(q, f+1)` if `f < 6`; else `Bid(q+1, 2)`; if `q == total_dice` and `f == 6`,
return `None`. (Ace branches handled explicitly; full table in tests.)

---

## 6. The module — `app/games/liars_dice/game.py`

```python
class LiarsDice:
    game_type = "liars-dice"

    def config_defaults(self) -> GameConfig:
        return GameConfig(
            total_rounds=64,         # safe upper bound; SequentialDriver ignores it
            turns_per_round=256,     # vestigial for a variable-length game
            per_turn_deadline_seconds=30,
            min_players=3, max_players=6,
            simultaneous=False,      # <-- selects SequentialDriver
        )
```

Per method:

- **`rules_text(...)`** — reads `match_state.config` (wild on/off, dice count, table
  size) and renders the matching ruleset, including the exact submit JSON.
- **`validate_move(move, *, your_agent_id, all_agent_ids)`** — pure. `parse_move`,
  then: not your turn → `NOT_YOUR_TURN`; challenge with no standing bid →
  `NOTHING_TO_CHALLENGE`; illegal raise → `ILLEGAL_RAISE`; quantity > total dice →
  `BID_TOO_LARGE`; face∉1..6 → `BAD_FACE`. (Standing bid + dice totals come from
  `match_state`; `validate_move` is pure, so the route passes the needed snapshot
  in `move` or a sibling read — see §7.)
- **`record_submission(...)`** — write `TurnSubmission(action="BID"|"CHALLENGE",
  quantity, face, message, thinking)`; on a BID update `match_state` (standing bid,
  advance `active_actor` to the next still-in seat). On CHALLENGE, set a flag so
  `next_actor` returns `None`.
- **`record_message(...)`** — unused (no separate talk phase); message rides with
  the move. Keep the default/no-op to satisfy the protocol.
- **`next_actor(...)`** — if a challenge is pending → `None`; else the
  `active_actor` from `match_state` (skipping eliminated players).
- **`on_round_start(...)`** — roll each still-in player's dice into `player_state`,
  clear the standing bid, set the hand leader (first hand: seat 0; later: last
  die-loser or their left if eliminated). On hand 1, seed `match_state.config`.
- **`resolve_turn(...)`** — BID: mark `resolved_at` only. CHALLENGE: mark
  `resolved_at`; the showdown itself happens in `award_round`.
- **`award_round(...)`** — the **showdown**: read all `player_state` dice,
  `resolve_showdown`, dock one die from the loser, write `last_showdown` (revealed
  dice now public) into `match_state`, update display fields on `players`
  (hands-won, placement points).
- **`is_match_over(...)`** — True when exactly one player has `dice_count > 0`.
- **`finalize(...)`** — set `winner_player_id` (last standing); write final
  placement points / hands-won to `players` for the leaderboard.
- **`final_placement(...)`** — elimination order: winner first, then players in
  reverse order of elimination.
- **`default_move(...)`** — `min_legal_raise` of the standing bid; opening →
  `Bid(1,2)`; ceiling (`None`) → `{"type": "CHALLENGE"}`.
- **`private_state_for(...)`** — `{"dice": [...], "dice_count": n}` for that player.
- **`public_state_for(...)`** — the `public_state` block in §7.2.
- **`move_effect(action)`** — `(0, None)`.
- **`theme()`** — LD color identity (its own `--brand`, dice/bid/challenge colors).

---

## 7. Wire format & payload — `app/schemas/agent.py`, `app/routes/agent_api.py`

### 7.1 Submit (extended, back-compat)

```python
class SubmitRequest(BaseModel):
    turn_token: str
    action: Action | None = None              # PD
    target_id: str | None = None              # PD
    move: dict[str, Any] | None = None        # free-form (LD: {"type","quantity","face"})
    message: str = Field(default="", max_length=200)
    thinking: str = Field(default="", max_length=200)
```

Submit route stays game-agnostic: if `move` is present, pass it through; else build
`{"action","target_id"}` from the PD fields. The route then attaches the live
standing-bid/dice-totals snapshot needed for `validate_move` (so the validator
stays pure), calls `module.validate_move(...)`, then `record_submission(...)`.

### 7.2 Turn payload (extended)

```python
class YourTurnResponse(BaseModel):
    status: Literal["your_turn"] = "your_turn"
    static: TurnStatic
    history: list[HistoryTurn]                 # empty for LD
    scoreboard: list[ScoreboardRow]            # dice counts for LD
    current: CurrentTurn
    your_private_state: dict | None = None     # LD: your dice; PD: null
    public_state: dict | None = None           # LD: game state block; PD: null
```

LD `public_state` shape:
```json
{ "hand": 6, "wild_ones": true,
  "standing_bid": {"by":"P2","quantity":4,"face":5},
  "active_actor": "P3",
  "dice_counts": {"P1":3,"P2":2,"P3":4,"P4":1},
  "bid_history": [{"by":"P1","quantity":3,"face":4,"message":"..."}],
  "showdowns": [{"hand":5,"actual_count":4,"loser":"P4","revealed":{...}}] }
```

Add `"not_your_turn"` to the `WaitingResponse` / `NextTurnWaiting` reason literals;
the waiting payload carries `public_state` so non-active AIs can plan ahead.

---

## 8. Hidden-information enforcement

Rule: a player's dice never reach another player's channel before the showdown.
Mirrors feature 007's `thinking` segregation.

- `player_state.dice` appears **only** in that player's own `your_private_state`.
- The agent API, next-turn, MCP tools, and spectator JSON expose **dice counts
  only** for other players, never faces — until a showdown writes
  `match_state.last_showdown.revealed`, which is public thereafter.
- **Test (SC-HD):** drive a match to a pre-showdown state; assert that across the
  agent API, every MCP tool, and the spectator JSON, no other player's dice faces
  appear; then after a showdown, assert the revealed dice *do* appear.

---

## 9. Admin create-match additions

The create-match form/route (`admin_web.py` / `admin_api.py`) gains two
LD-specific fields, shown only when game = `liars-dice`:
- **Wild ones** (on/off, default on).
- **Dice per player** (default 5).

Stored into `match_state.config` at start. Table size uses the existing min/max
fields (defaults 3/6 from `config_defaults`). Deadline uses the existing field
(default 30).

---

## 10. Sims — `app/games/liars_dice/sims.py`

- Decision fn: `decide(public_state, my_dice, *, seed) -> move_dict`. Deterministic
  given seed + state. Uses the pure engine: estimate P(standing bid holds) from my
  dice + count of unknown dice; bid up when confident, challenge when the standing
  bid is improbable; bluff occasionally per personality.
- Integration: `app/engine/sims/service.py` gains an `auto_submit_active(match,
  actor)` path the `SequentialDriver` calls for the single active Sim (contrast
  PD's per-phase all-Sims submit).
- Seeding/seating reuse `arena.py` / `sims/seating.py`. A small LD Sim roster +
  canned taunts for `message`.
- Sims must play correctly in both wild and no-wild modes (read `public_state.wild_ones`).

---

## 11. Edge cases

- **Challenge with no standing bid** → rejected (`NOTHING_TO_CHALLENGE`); the
  opening actor can only bid.
- **Bid at/over the ceiling** → `BID_TOO_LARGE`; missed-turn default at ceiling →
  challenge.
- **Malformed / wrong-phase / stale-token / duplicate move** → rejected; first
  valid move per turn wins.
- **Player leaves mid-match** → treated as eliminated for turn order; their dice
  removed from the table on their next would-be deal.
- **One die lost per hand** → no simultaneous elimination; placement is a strict
  order.
- **Mid-game restart** → `SequentialDriver` resumes from `match_state`
  (hand/active_actor/standing_bid) and the open unresolved `Turn` row.
- **Tie at the very end** is impossible (exactly one player ends with dice).

---

## 12. Test plan

| Layer | Tests |
|---|---|
| Pure engine | `count_for` with/without wild; `resolve_showdown` boundary (count == quantity holds); `is_legal_raise` full ace table; `min_legal_raise` incl. ceiling → None; `roll` determinism by seed. |
| Module | `validate_move` rejects each illegal case; `record_submission` advances state; `award_round` showdown docks the right player; `is_match_over` / `finalize` / `final_placement` ordering. |
| Driver | `SequentialDriver` plays a seeded 3-player match to completion; hand/turn counts variable; winner = last standing. |
| Security | SC-HD multi-channel dice-leak test (pre- vs post-showdown). |
| State | `match_state` / `player_state` JSON round-trips (dirty-tracking guard). |
| Regression | PD suite + `tests/test_stub_game.py` unchanged and green (the gate). |
| Migration | `tests/test_migrations.py` passes with the new tables/columns. |

---

## 13. Rollout

- Additive migration; deploy with no ACTIVE games (repo operational assumption),
  so no in-flight LD match to migrate; PD is untouched.
- Ship in the `design.md` §11 phase order: **Phase A** (PD parity refactor) merges
  first; **Phase B** (the new seams — `SequentialDriver`, private/public payload,
  generic state — validated by a sequential/hidden stub) second; **Phase C** (this
  spec's engine → module → Sims → viewer) last. Each phase is its own branch + PR,
  so a regression is isolated to the layer that introduced it.

## 14. Open items

- **TBD-3 — resolved:** `quantity`/`face` columns (§4.2).
- **TBD-6:** viewer fidelity — minimal text + dice-count bars for v1.
