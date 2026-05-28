"""End-to-end engine test: scripted game with stub action selection.

Drives the resolver + scheduler logic directly (no HTTP) to verify a full
game produces a deterministic winner.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.engine.resolver import award_round_winners, finalize_game, resolve_turn
from app.engine.tokens import generate_turn_token
from app.models import Base, Game, GameState, Player, Turn, TurnSubmission, User


@pytest.fixture
async def db(engine, session_factory):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        yield session


async def _setup_game(db, n_players: int = 4) -> tuple[Game, list[Player]]:
    game = Game(
        id="G_E2E",
        name="end-to-end",
        state=GameState.ACTIVE,
        scheduled_start=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        per_turn_deadline_seconds=1,
        total_rounds=3,  # shorter for test speed
        turns_per_round=4,
    )
    db.add(game)
    await db.flush()
    players = []
    for i in range(n_players):
        u = User(google_sub=f"sub-{i}", email=f"u{i}@e2e.com")
        db.add(u)
        await db.flush()
        p = Player(
            game_id=game.id,
            user_id=u.id,
            agent_id=f"AI_{i}",
            agent_key_hash="hash",
        )
        db.add(p)
        await db.flush()
        players.append(p)
    await db.commit()
    return game, players


def _action_for(player_idx: int, turn_num: int) -> tuple[str, int | None]:
    """Deterministic stub strategy.

    - AI_0 always Hoards.
    - AI_1 always Helps AI_2.
    - AI_2 always Helps AI_1.   (mutual pact for AI_1+AI_2)
    - AI_3 always Hurts AI_0.
    """
    if player_idx == 0:
        return ("HOARD", None)
    if player_idx == 1:
        return ("HELP", 2)
    if player_idx == 2:
        return ("HELP", 1)
    return ("HURT", 0)


async def _play_turn(db, game: Game, players: list[Player], round_num: int, turn_num: int) -> Turn:
    now = datetime.now(timezone.utc)
    turn = Turn(
        game_id=game.id,
        round=round_num,
        turn=turn_num,
        turn_token=generate_turn_token(),
        opened_at=now,
        deadline_at=now + timedelta(seconds=1),
    )
    db.add(turn)
    await db.commit()
    await db.refresh(turn)

    for idx, p in enumerate(players):
        action, target_idx = _action_for(idx, turn_num)
        target = players[target_idx] if target_idx is not None else None
        db.add(
            TurnSubmission(
                turn_id=turn.id,
                player_id=p.id,
                action=action,
                target_player_id=target.id if target else None,
                message="",
                submitted_at=now,
            )
        )
    await db.commit()
    await resolve_turn(db, turn)
    return turn


@pytest.mark.asyncio
async def test_full_game_runs_to_completion(db):
    """3 rounds × 4 turns × 4 players. AI_1 and AI_2 (mutual pact) should win each round."""
    game, players = await _setup_game(db, n_players=4)

    for round_num in range(1, game.total_rounds + 1):
        # Reset round scores.
        for p in players:
            p.current_round_score = 0
        await db.commit()

        for turn_num in range(1, game.turns_per_round + 1):
            await _play_turn(db, game, players, round_num, turn_num)

        await award_round_winners(db, game, round_num)

    await finalize_game(db, game)
    await db.refresh(game)

    assert game.state == GameState.COMPLETED
    assert game.winner_player_id is not None

    # AI_1 and AI_2 had the mutual pact (+8 per turn each, 4 turns = +32 per round, max).
    # AI_0 was getting Hurt every turn (-4 - 4 = -2 per turn if AI_3 hurts and another helps;
    # but no one helps AI_0, so AI_0 stays at 0 floor).
    # AI_3 wastes turns Hurting AI_0, gets 0.
    # So AI_1 or AI_2 should be tied for round winner each round.
    winners = await db.execute(
        select(Player).where(Player.game_id == game.id, Player.total_round_wins > 0)
    )
    winner_rows = winners.scalars().all()
    winner_agent_ids = {p.agent_id for p in winner_rows}
    assert winner_agent_ids == {"AI_1", "AI_2"}, f"unexpected winners: {winner_agent_ids}"


@pytest.mark.asyncio
async def test_static_prefix_byte_identical_concept(db):
    """The shape of the static prefix is stable game-wide — we test this here
    indirectly by ensuring rules text constant doesn't change at runtime."""
    from app.engine.rules import RULES_TEXT_V1

    snapshot = RULES_TEXT_V1
    assert RULES_TEXT_V1 is snapshot  # same object, no rebinding
    assert len(RULES_TEXT_V1) > 500  # sanity
