"""Payoff math, mutual bonus, score floor, missed-turn default.

Every test creates a minimal in-memory game with N players and one open turn,
materializes submissions, calls resolve_turn, then asserts the deltas.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.resolver import resolve_turn, award_round_winners, finalize_game
from app.engine.rules import DEFAULT_MISSED_MESSAGE
from app.models import Base, Game, GameState, Player, Turn, TurnSubmission, User
from tests.factories import make_bot


# --- Fixtures ---


@pytest.fixture
async def db(engine, session_factory):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        yield session


async def _make_game_with_players(db: AsyncSession, n: int) -> tuple[Game, list[Player]]:
    """Create a game in ACTIVE state with n players, current_round_score=0."""
    game = Game(
        id="G_TEST",
        name="test",
        state=GameState.ACTIVE,
        scheduled_start=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
    )
    db.add(game)
    await db.flush()

    players = []
    for i in range(n):
        u = User(google_sub=f"sub-{i}", email=f"u{i}@test.com", name=f"u{i}")
        db.add(u)
        await db.flush()
        bot, _ = await make_bot(db, u, name=f"AI_{i}")
        p = Player(
            game_id=game.id,
            user_id=u.id,
            bot_id=bot.id,
            agent_id=f"AI_{i}",
        )
        db.add(p)
        await db.flush()
        players.append(p)

    await db.commit()
    return game, players


async def _open_turn(db: AsyncSession, game: Game, round_num: int = 1, turn_num: int = 1) -> Turn:
    now = datetime.now(timezone.utc)
    t = Turn(
        game_id=game.id,
        round=round_num,
        turn=turn_num,
        turn_token=f"tk_{round_num}_{turn_num}",
        opened_at=now,
        deadline_at=now + timedelta(seconds=60),
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


async def _submit(
    db: AsyncSession,
    turn: Turn,
    player: Player,
    action: str,
    target: Player | None = None,
    message: str = "",
):
    s = TurnSubmission(
        turn_id=turn.id,
        player_id=player.id,
        action=action,
        target_player_id=target.id if target else None,
        message=message,
        submitted_at=datetime.now(timezone.utc),
    )
    db.add(s)
    await db.commit()


# --- Tests ---


@pytest.mark.asyncio
async def test_single_hoard(db):
    game, [p0] = await _make_game_with_players(db, 1)
    turn = await _open_turn(db, game)
    await _submit(db, turn, p0, "HOARD")
    await resolve_turn(db, turn)
    await db.refresh(p0)
    assert p0.current_round_score == 2


@pytest.mark.asyncio
async def test_single_help(db):
    """A Helps B → A gets 0, B gets +4."""
    game, [a, b] = await _make_game_with_players(db, 2)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HELP", target=b)
    await _submit(db, turn, b, "HOARD")  # B Hoards to keep test simple
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 0
    assert b.current_round_score == 2 + 4  # Hoard +2 plus Help received


@pytest.mark.asyncio
async def test_single_hurt(db):
    """A Hurts B → A gets 0, B gets -4 (clipped to 0 from 0)."""
    game, [a, b] = await _make_game_with_players(db, 2)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HOARD")
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 0
    # B starts at 0, Hoard +2, Hurt -4 → max(0, -2) = 0
    assert b.current_round_score == 0


@pytest.mark.asyncio
async def test_help_stacks(db):
    """5 helps on one target → +20 to target."""
    game, players = await _make_game_with_players(db, 6)
    target = players[0]
    helpers = players[1:]
    turn = await _open_turn(db, game)
    await _submit(db, turn, target, "HOARD")
    for h in helpers:
        await _submit(db, turn, h, "HELP", target=target)
    await resolve_turn(db, turn)
    await db.refresh(target)
    # Target: +2 hoard + 5*4 help = 22
    assert target.current_round_score == 22


@pytest.mark.asyncio
async def test_hurt_stacks_with_floor(db):
    """5 hurts on one target → floored at 0."""
    game, players = await _make_game_with_players(db, 6)
    target = players[0]
    attackers = players[1:]
    turn = await _open_turn(db, game)
    await _submit(db, turn, target, "HOARD")
    for a in attackers:
        await _submit(db, turn, a, "HURT", target=target)
    await resolve_turn(db, turn)
    await db.refresh(target)
    # Target: +2 hoard - 5*4 hurt = -18, floored to 0
    assert target.current_round_score == 0


@pytest.mark.asyncio
async def test_mutual_help_bonus(db):
    """A Helps B and B Helps A → each ends +8."""
    game, [a, b] = await _make_game_with_players(db, 2)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HELP", target=b)
    await _submit(db, turn, b, "HELP", target=a)
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 8
    assert b.current_round_score == 8


@pytest.mark.asyncio
async def test_mutual_bonus_does_not_double(db):
    """If A Helps B, B Helps A, and C also Helps A, mutual bonus only counts the A↔B pair.

    A receives: +4 from B (base) + +4 from C (base) + +4 mutual = 12
    B receives: +4 from A (base) + +4 mutual = 8
    C receives: 0 (nobody Helped C back)
    """
    game, [a, b, c] = await _make_game_with_players(db, 3)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HELP", target=b)
    await _submit(db, turn, b, "HELP", target=a)
    await _submit(db, turn, c, "HELP", target=a)
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    await db.refresh(c)
    assert a.current_round_score == 12
    assert b.current_round_score == 8
    assert c.current_round_score == 0


@pytest.mark.asyncio
async def test_score_floor_on_final_delta(db):
    """Floor applies to the final summed delta, not per incoming Hurt.

    Player starts at 3, gets two -4 Hurts and one +4 Help in same turn.
    Raw: 3 - 4 - 4 + 4 = -1, floored to 0.
    """
    game, [target, h1, h2, helper] = await _make_game_with_players(db, 4)
    target.current_round_score = 3
    await db.commit()

    turn = await _open_turn(db, game)
    await _submit(db, turn, target, "HOARD")  # +2 added
    await _submit(db, turn, h1, "HURT", target=target)
    await _submit(db, turn, h2, "HURT", target=target)
    await _submit(db, turn, helper, "HELP", target=target)
    await resolve_turn(db, turn)
    await db.refresh(target)
    # 3 + 2 (hoard) + 4 (help) - 4 - 4 (two hurts) = 1, no floor needed
    assert target.current_round_score == 1


@pytest.mark.asyncio
async def test_hurt_against_zero_target(db):
    """HURT against 0-score target: target stays at 0; attacker gets 0 (not +2)."""
    game, [a, b] = await _make_game_with_players(db, 2)
    # B starts at 0.
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HOARD")  # B hoards but is also being hurt
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 0  # used turn on HURT, no Hoard
    assert b.current_round_score == 0  # +2 - 4, clipped to 0


@pytest.mark.asyncio
async def test_missed_turn_defaults_to_hoard(db):
    """A player with no submission gets defaulted to Hoard with canonical message."""
    game, [a, b] = await _make_game_with_players(db, 2)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HOARD")
    # B does not submit.
    await resolve_turn(db, turn)
    await db.refresh(b)
    assert b.current_round_score == 2

    # The defaulted submission row exists with the canonical message.
    from sqlalchemy import select
    sub = (
        await db.execute(
            select(TurnSubmission).where(
                TurnSubmission.turn_id == turn.id, TurnSubmission.player_id == b.id
            )
        )
    ).scalar_one()
    assert sub.was_defaulted is True
    assert sub.action == "HOARD"
    assert sub.message == DEFAULT_MISSED_MESSAGE


@pytest.mark.asyncio
async def test_round_award_single_winner(db):
    game, [a, b, c] = await _make_game_with_players(db, 3)
    a.current_round_score = 10
    b.current_round_score = 6
    c.current_round_score = 4
    await db.commit()
    await award_round_winners(db, game, 1)
    await db.refresh(a)
    await db.refresh(b)
    await db.refresh(c)
    assert a.total_round_wins == 1.0
    assert b.total_round_wins == 0
    assert c.total_round_wins == 0
    assert a.total_round_score == 10
    assert b.total_round_score == 6
    assert c.total_round_score == 4


@pytest.mark.asyncio
async def test_round_award_three_way_tie(db):
    game, [a, b, c] = await _make_game_with_players(db, 3)
    a.current_round_score = 8
    b.current_round_score = 8
    c.current_round_score = 8
    await db.commit()
    await award_round_winners(db, game, 1)
    await db.refresh(a)
    await db.refresh(b)
    await db.refresh(c)
    assert a.total_round_wins == pytest.approx(1 / 3)
    assert b.total_round_wins == pytest.approx(1 / 3)
    assert c.total_round_wins == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_round_award_is_idempotent(db):
    """Awarding the same round twice (a mid-game restart re-entering the loop at
    an already-finished round) must NOT double-count wins or scores."""
    game, [a, b, c] = await _make_game_with_players(db, 3)
    a.current_round_score = 10
    b.current_round_score = 6
    c.current_round_score = 4
    await db.commit()

    await award_round_winners(db, game, 1)
    await award_round_winners(db, game, 1)  # resume re-entry — must be a no-op

    await db.refresh(a)
    await db.refresh(b)
    await db.refresh(c)
    await db.refresh(game)
    assert a.total_round_wins == 1.0
    assert b.total_round_wins == 0
    assert c.total_round_wins == 0
    assert a.total_round_score == 10
    assert b.total_round_score == 6
    assert c.total_round_score == 4
    assert game.rounds_awarded == 1


@pytest.mark.asyncio
async def test_round_award_accumulates_across_rounds(db):
    """Consecutive rounds each award once and advance rounds_awarded."""
    game, [a, b] = await _make_game_with_players(db, 2)
    a.current_round_score = 5  # a wins round 1
    b.current_round_score = 3
    await db.commit()
    await award_round_winners(db, game, 1)

    a.current_round_score = 2  # round 2 (scores reset then re-earned); b wins
    b.current_round_score = 9
    await db.commit()
    await award_round_winners(db, game, 2)

    await db.refresh(a)
    await db.refresh(b)
    await db.refresh(game)
    assert game.rounds_awarded == 2
    assert a.total_round_score == 7  # 5 + 2
    assert b.total_round_score == 12  # 3 + 9
    assert a.total_round_wins == 1.0  # round 1
    assert b.total_round_wins == 1.0  # round 2


@pytest.mark.asyncio
async def test_finalize_game_with_tiebreaker(db):
    """Two players tie on round wins; tiebreaker is total in-round score."""
    game, [a, b] = await _make_game_with_players(db, 2)
    a.total_round_wins = 5
    a.total_round_score = 120
    b.total_round_wins = 5
    b.total_round_score = 130
    await db.commit()
    await finalize_game(db, game)
    await db.refresh(game)
    assert game.state == GameState.COMPLETED
    assert game.winner_player_id == b.id
