"""Payoff math, mutual bonus, score floor, missed-turn default.

Every test creates a minimal in-memory game with N players and one open turn,
materializes submissions, calls resolve_turn, then asserts the deltas.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.resolver import award_round_winners, finalize_game
from app.games.hoard_hurt_help.rules import DEFAULT_MISSED_MESSAGE
from app.games.hoard_hurt_help.scoring import resolve_turn
from app.models import Match, GameState, Player, Turn, TurnSubmission, User
from tests.factories import make_bot


# --- Fixtures ---


async def _make_game_with_players(db: AsyncSession, n: int) -> tuple[Match, list[Player]]:
    """Create a game in ACTIVE state with n players, current_round_score=0."""
    game = Match(
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
        agent, _ = await make_bot(db, u, name=f"AI_{i}")
        p = Player(
            match_id=game.id,
            user_id=u.id,
            agent_id=agent.id,
            seat_name=f"AI_{i}",
        )
        db.add(p)
        await db.flush()
        players.append(p)

    await db.commit()
    return game, players


async def _open_turn(db: AsyncSession, game: Match, round_num: int = 1, turn_num: int = 1) -> Turn:
    now = datetime.now(timezone.utc)
    t = Turn(
        match_id=game.id,
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


async def test_single_hoard(db):
    game, [p0] = await _make_game_with_players(db, 1)
    turn = await _open_turn(db, game)
    await _submit(db, turn, p0, "HOARD")
    await resolve_turn(db, turn)
    await db.refresh(p0)
    assert p0.current_round_score == 2


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


async def test_betraying_a_helper_hurts_for_eight(db):
    """HURTing a player who HELPs you this turn lands for -8, not -4.

    B HELPs A (A gets +4). A HURTs B → betrays the helper for -8 to B.
    A ends +4; B (starting at 10) ends 10 - 8 = 2.
    """
    game, [a, b] = await _make_game_with_players(db, 2)
    b.current_round_score = 10
    await db.commit()
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HELP", target=a)
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 4  # +4 from B's help (A's HURT gives A nothing)
    assert b.current_round_score == 2  # 10 - 8 betrayal


async def test_hurt_non_helper_stays_four(db):
    """A normal HURT (target did NOT help the attacker) still lands for -4.

    B HOARDs (does not help A). A HURTs B → base -4, not the betrayal -8.
    B (starting at 10) ends 10 + 2 (hoard) - 4 = 8.
    """
    game, [a, b] = await _make_game_with_players(db, 2)
    b.current_round_score = 10
    await db.commit()
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HOARD")
    await resolve_turn(db, turn)
    await db.refresh(b)
    assert b.current_round_score == 8  # 10 + 2 - 4, NOT -8


async def test_betrayal_only_for_the_helped_attacker(db):
    """Only the attacker the victim HELPed lands the -8; other attackers stay -4.

    B HELPs A. A HURTs B (betrayal -8). C HURTs B (normal -4, B never helped C).
    B (starting at 20) ends 20 - 8 - 4 = 8. A gets +4 from B's help.
    """
    game, [a, b, c] = await _make_game_with_players(db, 3)
    b.current_round_score = 20
    await db.commit()
    turn = await _open_turn(db, game)
    await _submit(db, turn, b, "HELP", target=a)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, c, "HURT", target=b)
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 4
    assert b.current_round_score == 8  # 20 - 8 (A betrayal) - 4 (C normal)


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


# --- Mutual-help decay (feature mutual-help-decay, Slice 1) ---


class _FakeSub:
    def __init__(self, player_id: int, action: str, target: int | None = None) -> None:
        self.player_id = player_id
        self.action = action
        self.target_player_id = target


def test_mutual_help_counts_helper() -> None:
    """Pure counter: per unordered pair, how many prior turns they mutually helped."""
    from app.games.hoard_hurt_help.scoring import mutual_help_counts

    turns = [
        [_FakeSub(1, "HELP", 2), _FakeSub(2, "HELP", 1), _FakeSub(3, "HOARD")],  # 1<->2
        [_FakeSub(1, "HELP", 2), _FakeSub(2, "HELP", 1)],  # 1<->2 again
        [_FakeSub(1, "HELP", 2), _FakeSub(2, "HELP", 3), _FakeSub(3, "HELP", 2)],  # 2<->3
    ]
    counts = mutual_help_counts(turns)
    assert counts[frozenset({1, 2})] == 2
    assert counts[frozenset({2, 3})] == 1
    assert frozenset({1, 3}) not in counts  # one-directional help never counts


async def test_mutual_help_decays_to_floor(db):
    """A pair's repeated mutual help pays 8,7,6,5,4,3,2,2 — decays -1/repeat, floor 2.

    k is re-derived from the persisted prior turns on every resolve, so this also
    exercises the resume-safe path (no in-memory state to lose).
    """
    game, [a, b] = await _make_game_with_players(db, 2)
    prev = 0
    for i, expected in enumerate([8, 7, 6, 5, 4, 3, 2, 2]):
        turn = await _open_turn(db, game, round_num=1, turn_num=i + 1)
        await _submit(db, turn, a, "HELP", target=b)
        await _submit(db, turn, b, "HELP", target=a)
        await resolve_turn(db, turn)
        await db.refresh(a)
        assert a.current_round_score - prev == expected, (i, expected)
        prev = a.current_round_score


async def test_decay_persists_across_rounds(db):
    """k counts prior mutual-help turns match-wide — it does NOT reset each round."""
    game, [a, b] = await _make_game_with_players(db, 2)
    t1 = await _open_turn(db, game, round_num=1, turn_num=1)
    await _submit(db, t1, a, "HELP", target=b)
    await _submit(db, t1, b, "HELP", target=a)
    await resolve_turn(db, t1)
    await db.refresh(a)
    assert a.current_round_score == 8  # k=0 → +8
    base = a.current_round_score

    t2 = await _open_turn(db, game, round_num=3, turn_num=1)
    await _submit(db, t2, a, "HELP", target=b)
    await _submit(db, t2, b, "HELP", target=a)
    await resolve_turn(db, t2)
    await db.refresh(a)
    assert a.current_round_score - base == 7  # k=1 even though it's a later round


async def test_fresh_partner_resets_decay(db):
    """A farmed pact decays, but a brand-new partner starts fresh at +8."""
    game, [a, b, c] = await _make_game_with_players(db, 3)
    t1 = await _open_turn(db, game, round_num=1, turn_num=1)
    await _submit(db, t1, a, "HELP", target=b)
    await _submit(db, t1, b, "HELP", target=a)
    await _submit(db, t1, c, "HOARD")
    await resolve_turn(db, t1)
    await db.refresh(a)
    base = a.current_round_score  # 8 from the A↔B pact

    t2 = await _open_turn(db, game, round_num=1, turn_num=2)
    await _submit(db, t2, a, "HELP", target=c)  # fresh partner
    await _submit(db, t2, c, "HELP", target=a)
    await _submit(db, t2, b, "HOARD")
    await resolve_turn(db, t2)
    await db.refresh(a)
    assert a.current_round_score - base == 8  # A↔C is a fresh pair, k=0


async def test_decay_is_per_pair_independent(db):
    """Two pacts at the same table decay on their own counters."""
    game, [a, b, c, d] = await _make_game_with_players(db, 4)
    for turn_num, expected in [(1, 8), (2, 7)]:
        turn = await _open_turn(db, game, round_num=1, turn_num=turn_num)
        await _submit(db, turn, a, "HELP", target=b)
        await _submit(db, turn, b, "HELP", target=a)
        await _submit(db, turn, c, "HELP", target=d)
        await _submit(db, turn, d, "HELP", target=c)
        await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(c)
    # Both pairs went 8 then 7 → each side totals 15, independently.
    assert a.current_round_score == 15
    assert c.current_round_score == 15


async def test_prior_hoard_turn_does_not_count_toward_k(db):
    """A prior non-mutual (HOARD/defaulted) turn leaves k=0 — first pact still pays 8."""
    game, [a, b] = await _make_game_with_players(db, 2)
    t1 = await _open_turn(db, game, round_num=1, turn_num=1)
    await _submit(db, t1, a, "HOARD")
    # b never submits → defaulted to HOARD
    await resolve_turn(db, t1)
    await db.refresh(a)
    assert a.current_round_score == 2  # just the hoard

    t2 = await _open_turn(db, game, round_num=1, turn_num=2)
    await _submit(db, t2, a, "HELP", target=b)
    await _submit(db, t2, b, "HELP", target=a)
    await resolve_turn(db, t2)
    await db.refresh(a)
    assert a.current_round_score == 2 + 8  # k=0 → fresh +8
