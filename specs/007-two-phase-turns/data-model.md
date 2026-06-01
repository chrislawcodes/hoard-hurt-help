# Data Model: Two-Phase Turns with Private Bot Reasoning

## Entities

### Turn (modify `turns`)

Adds phase state to the existing one-row-per-(round,turn) model.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| phase | VARCHAR(8) | NOT NULL, server_default `'talk'` | Current phase: `talk` or `act` |
| talk_resolved_at | DATETIME(tz) | NULL | Set when the talk phase resolved (messages revealed). NULL = still in talk |

Unchanged: `turn_token` and `deadline_at` now describe the **current** phase (regenerated/reset at the talk→act transition). `resolved_at` keeps its meaning: the **act** phase / whole turn is resolved.

**Resume tri-state**: `resolved_at` set → done; elif `talk_resolved_at` set → resume in `act`; else → resume in `talk`.

### TurnMessage (new `turn_messages`)

One public message + private thinking per player per turn (the talk phase).

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | |
| turn_id | INTEGER | FK→turns.id, NOT NULL, INDEX | |
| player_id | INTEGER | FK→players.id, NOT NULL, INDEX | |
| text | TEXT | NOT NULL, default `''` | **Public** message (revealed to all) |
| thinking | TEXT | NOT NULL, default `''` | **PRIVATE** — spectators only, never agents |
| was_defaulted | BOOLEAN | NOT NULL, default `false` | True when the player missed the talk deadline (empty text) |
| submitted_at | DATETIME(tz) | NULL | When the player submitted; NULL for a defaulted row |

**Index/Constraint**: `UNIQUE(turn_id, player_id)` → `uq_turn_messages_turn_id_player_id` (one talk message per player per turn; powers idempotency, same pattern as `turn_submissions`).

### TurnSubmission (modify `turn_submissions`)

The act phase. One add.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| thinking | TEXT | NOT NULL, server_default `''` | **PRIVATE** — reasoning behind the move; spectators only |

Unchanged: `action`, `target_player_id`, `points_delta`, `round_score_after`, `was_defaulted`, `submitted_at`, and `message`. **`message` is retained** (not dropped) so already-completed single-phase games still render; new games leave it `''` because the public message now lives in `turn_messages`.

## Type Definitions

```python
# app/models/turn.py
class Turn(Base):
    # ...existing...
    phase: Mapped[str] = mapped_column(String(8), nullable=False, default="talk", server_default="talk")
    talk_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class TurnMessage(Base):
    __tablename__ = "turn_messages"
    __table_args__ = (
        UniqueConstraint("turn_id", "player_id", name="uq_turn_messages_turn_id_player_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    turn_id: Mapped[int] = mapped_column(ForeignKey("turns.id"), nullable=False, index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    thinking: Mapped[str] = mapped_column(Text, default="", nullable=False)
    was_defaulted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class TurnSubmission(Base):
    # ...existing...
    thinking: Mapped[str] = mapped_column(Text, default="", nullable=False, server_default="")
```

## Migration (new Alembic revision, next sequential id)

All operations are column-adds and a new table — **no constraint drop/alter, so no `op.batch_alter_table` is required**. Still must pass `tests/test_migrations.py` (upgrade head on SQLite).

```python
def upgrade() -> None:
    op.add_column("turns", sa.Column("phase", sa.String(8), nullable=False, server_default="talk"))
    op.add_column("turns", sa.Column("talk_resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("turn_submissions", sa.Column("thinking", sa.Text(), nullable=False, server_default=""))
    op.create_table(
        "turn_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("turn_id", sa.Integer(), sa.ForeignKey("turns.id"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("thinking", sa.Text(), nullable=False, server_default=""),
        sa.Column("was_defaulted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("turn_id", "player_id", name="uq_turn_messages_turn_id_player_id"),
    )
    op.create_index("ix_turn_messages_turn_id", "turn_messages", ["turn_id"])
    op.create_index("ix_turn_messages_player_id", "turn_messages", ["player_id"])

def downgrade() -> None:
    op.drop_table("turn_messages")
    op.drop_column("turn_submissions", "thinking")
    op.drop_column("turns", "talk_resolved_at")
    op.drop_column("turns", "phase")
```

## Visibility note (revised after review)

The `thinking` columns above are persisted on `turn_messages` and `turn_submissions`, but they are exposed in **exactly one place**: the server-rendered viewer/analysis HTML (read directly from the DB in `app/routes/web.py`). No JSON schema — neither the agent payloads nor the spectator JSON API — selects or returns `thinking`, so no API or MCP tool (incl. `get_game_state`) can leak it. HTML-scraping is the accepted, deferred residual risk.

## Validation Rules

- A talk message: one per (turn, player); rejected if the turn is not in the `talk` phase, the token is stale, or the deadline has passed.
- An act submission: one per (turn, player); rejected if the turn is not in the `act` phase. Action vocabulary validated by the game module as today.
- `thinking` length capped (e.g. 2000 chars) at the schema layer to bound payload size.
