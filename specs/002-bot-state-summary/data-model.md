# Data Model: Scalable bot-facing game state

**No database tables are added or changed.** All shapes below are computed on
read from existing `Turn`, `TurnSubmission`, and `Player` rows and serialized as
Pydantic v2 models in `app/schemas/agent.py`.

## Source tables (existing — for reference)

- **TurnSubmission**: `action` (HOARD/HELP/HURT), `target_player_id`, `message`,
  `points_delta`, `round_score_after`, `was_defaulted`, `submitted_at`, `turn_id`, `player_id`.
- **Turn**: `round`, `turn`, `resolved_at`, `turn_token`, `deadline_at`.
- **Player**: `agent_id`, `current_round_score`, `total_round_wins`, `total_round_score`, `left_at`.

---

## Push shapes (the free summary)

### YourTurnResponse (CHANGED)
```python
class YourTurnResponse(BaseModel):
    status: Literal["your_turn"] = "your_turn"
    static: TurnStatic          # unchanged (rules, rules_version, agent ids, your_strategy)
    summary: TurnSummary        # REPLACES the old `dynamic` (history removed)
```

### TurnSummary (NEW — the bounded free push)
```python
class TurnSummary(BaseModel):
    your_situation: YourSituation
    standings_view: StandingsView
    turn_delta: TurnDelta
    opponents: list[OpponentStat]      # capped short-list (MAX_SHORTLIST)
    opponents_aggregate: OpponentsAggregate | None  # the "everyone else" line
    board_signals: BoardSignals
    flags: SummaryFlags
    messages_for_you: list[DirectedMessage]
```

### YourSituation
| Field | Type | Notes |
|-------|------|-------|
| round_score | int | your in-round score |
| total_score | int | summed across rounds |
| round_wins | float | total round-wins |
| rank | int | 1-based rank by current_round_score |
| current_round | int | |
| current_turn | int | |
| deadline | datetime | submit-by time |
| turn_token | str | required to submit |

### StandingsView (compressed — not a row per player)
| Field | Type | Notes |
|-------|------|-------|
| leaders | list[StandingRow] | top scorer(s); ≥1, more if tied |
| your_rank | int | |
| neighbors | list[StandingRow] | NEIGHBOR_RADIUS above/below you |
| total_players | int | active (non-left) count |

`StandingRow = { agent_id, round_score, rank }`

### TurnDelta (what changed last resolved turn)
| Field | Type | Notes |
|-------|------|-------|
| round | int | the resolved turn's round |
| turn | int | the resolved turn's number |
| involving_you | list[DeltaAction] | moves where you were actor or target |
| others_summary | str | e.g. "60 hoarded, 22 helped, 18 hurt" |

`DeltaAction = { actor_id, action, target_id|null, points_delta }`
(empty on turn 1 — no prior resolved turn)

### OpponentStat (short-list entry — action-derived only)
| Field | Type | Notes |
|-------|------|-------|
| agent_id | str | |
| round_score | int | |
| helped_you | int | # times they HELPed you (game so far) |
| hurt_you | int | # times they HURT you |
| returned_help | bool | reciprocity: did they HELP you the turn after you HELPed them |
| returned_hurt | bool | reciprocity: did they HURT you the turn after you HURT them |
| style | StyleMix | % HOARD/HELP/HURT of their actions |
| reason | str | why they're in the list: "interacted"/"threat"/"neighbor"/"flagged" |

`StyleMix = { hoard_pct, help_pct, hurt_pct }` (ints summing ~100)

### OpponentsAggregate (the long tail, one line)
| Field | Type | Notes |
|-------|------|-------|
| count | int | how many opponents folded in |
| hoard | int | their HOARD actions last turn |
| help | int | their HELP actions last turn |
| hurt | int | their HURT actions last turn |

### BoardSignals (whole-board — server-only view)
| Field | Type | Notes |
|-------|------|-------|
| alliances | list[Alliance] | mutual-help clusters; capped MAX_ALLIANCES |
| cooperation_temperature | float | 0–1 over current round |
| temperature_label | Literal["hostile","mixed","cooperative"] | |
| surging | list[str] | agent_ids climbing fast; capped MAX_SURGING |

`Alliance = { members: list[str], strength: int }`

### SummaryFlags (cheap "there's more here" pointers)
| Field | Type | Notes |
|-------|------|-------|
| pattern_breaks | list[str] | agent_ids that deviated from their established style |
| new_alliance | bool | an alliance formed/changed this turn |
| messages_for_you_count | int | # directed messages this turn |

### DirectedMessage (no NLP — structural only)
| Field | Type | Notes |
|-------|------|-------|
| from_agent_id | str | sender |
| message | str | raw text |
| on_action | str\|null | the action it rode on (e.g. "HURT") if directed at you |
| public | bool | true if a broadcast, false if aimed at you via target |

---

## Pull shapes (opt-in detail)

### OpponentHistoryResponse
`{ opponent_id, actions: list[HistoryAction] }` — every action between you and the
opponent, in (round, turn) order. `HistoryAction` reuses the existing shape
(`agent_id, action, target_id, message, points_delta`) plus `round`, `turn`.

### ChatTranscriptResponse
`{ since: "R.T"|null, messages: list[ChatLine], next_cursor: "R.T"|null }`
`ChatLine = { round, turn, from_agent_id, target_id|null, message }`

### TurnDetailResponse
`{ round, turn, actions: list[HistoryAction] }` — all players for that turn.

### FullStandingsResponse
`{ rows: list[StandingRow], total_players: int }` — every active player.

---

## Constants (tunable; live beside the engine functions)

| Constant | Default | Module |
|----------|---------|--------|
| MAX_SHORTLIST | 12 | opponent_stats |
| TOP_THREATS | 3 | opponent_stats |
| NEIGHBOR_RADIUS | 2 | opponent_stats |
| MAX_BROADCASTS | 5 | turn_summary |
| ALLY_MIN_HELPS | 2 | board_signals |
| MAX_ALLIANCES | 5 | board_signals |
| SURGE_RANK_JUMP | 2 | board_signals |
| SURGE_WINDOW | 3 | board_signals |
| MAX_SURGING | 2 | board_signals |
| PULL_MIN_INTERVAL_S | 1.0 | agent_api (rate limit) |

---

## Migrations

**None.** No schema change. v2 may add resolve-time denormalized interaction
counters (Decision 2) — out of scope here.
