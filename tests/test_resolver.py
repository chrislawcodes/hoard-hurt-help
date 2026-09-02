"""Payoff math, mutual bonus, score floor, missed-turn default.

Every test creates a minimal in-memory game with N players and one open turn,
materializes submissions, calls resolve_turn, then asserts the deltas.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.resolver import award_round_winners, finalize_game
from app.games.hoard_hurt_help.rules import (
    BETRAYAL_BONUS,
    HELP_POINTS,
    HURT_POINTS,
    HURT_TAKE_HELPER,
    HURT_TAKE_HOARDER,
    hoard_share,
)
from app.games.hoard_hurt_help.scoring import resolve_turn
from app.models import Match, GameState, Player, Turn, TurnSubmission, User
from tests.factories import make_bot

# Every betrayal expectation below is derived from this, never spelled out. The
# literals it replaced were stale from v6 (they still described a +4 bonus and a
# -4 hurt) and broke again at v8.
BETRAYAL_PAYOUT = HELP_POINTS + BETRAYAL_BONUS


# --- Fixtures ---


async def _make_game_with_players(
    db: AsyncSession, n: int, *, mutual_help_mode: str = "decay"
) -> tuple[Match, list[Player]]:
    """Create a game in ACTIVE state with n players, current_round_score=0.

    The mutual-help rule is named, not inherited from the platform default: the
    payout tests below are written against decay's sliding 8/7/6…, so a match
    that quietly arrived on another rule would fail them for the wrong reason.
    Which rule a NEW match gets is tested in tests/test_mutual_help_modes.py.
    """
    game = Match(
        id="G_TEST",
        name="test",
        state=GameState.ACTIVE,
        scheduled_start=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
        mutual_help_mode=mutual_help_mode,
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
    assert p0.current_round_score == hoard_share(1)  # sole hoarder takes the pot


async def test_single_help(db):
    """A Helps B → A gets 0, B gets the help plus the pot it hoarded alone."""
    game, [a, b] = await _make_game_with_players(db, 2)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HELP", target=b)
    await _submit(db, turn, b, "HOARD")  # B Hoards to keep test simple
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == 0
    assert b.current_round_score == hoard_share(1) + HELP_POINTS


async def test_single_hurt(db):
    """A HURTs a HOARDer → A takes the hoarder rate; B nets its pot minus the hurt.

    At v9 a HURT pays the attacker off what the TARGET was doing, so attacking a
    hoarder is no longer free of charge to nobody — it pays HURT_TAKE_HOARDER.
    """
    game, [a, b] = await _make_game_with_players(db, 2)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HOARD")
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == HURT_TAKE_HOARDER
    # B starts at 0, takes the pot alone, then the hurt lands on top.
    assert b.current_round_score == max(0, hoard_share(1) - HURT_POINTS)


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
    # Target hoards alone (whole pot) and takes five helps on top.
    assert target.current_round_score == hoard_share(1) + 5 * HELP_POINTS


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

    Player starts at 3, HOARDs alone, and takes two HURTs plus one HELP in the
    same turn. The floor only bites if the pot is small enough — with a 12-point
    pot the raw total is 3 + 12 + 4 - 16 = 3, above the floor, so this asserts the
    computed value rather than a hard-coded 0.
    """
    game, [target, h1, h2, helper] = await _make_game_with_players(db, 4)
    target.current_round_score = 3
    await db.commit()

    turn = await _open_turn(db, game)
    await _submit(db, turn, target, "HOARD")  # sole hoarder
    await _submit(db, turn, h1, "HURT", target=target)
    await _submit(db, turn, h2, "HURT", target=target)
    await _submit(db, turn, helper, "HELP", target=target)
    await resolve_turn(db, turn)
    await db.refresh(target)
    expected = max(0, 3 + hoard_share(1) + HELP_POINTS - 2 * HURT_POINTS)
    assert target.current_round_score == expected


async def test_hurt_against_zero_target(db):
    """A HURT on a target the floor will clip still PAYS the attacker.

    The victim's side is capped by the score floor, but the attacker's take is
    priced off what the target was DOING, not off how much damage actually
    landed — so a swing at someone already near zero is never wasted on your
    side. The rules text says so explicitly; this pins it.
    """
    game, [a, b] = await _make_game_with_players(db, 2)
    # B starts at 0.
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HOARD")  # B hoards but is also being hurt
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == HURT_TAKE_HOARDER  # paid off B's HOARD
    assert b.current_round_score == max(0, hoard_share(1) - HURT_POINTS)


async def test_betraying_a_helper_pays_help_plus_the_bonus(db):
    """Betraying a same-turn helper pays HELP_POINTS + BETRAYAL_BONUS.

    B HELPs A. A HURTs B → betrays the helper: A keeps B's help AND gains the
    bonus. B takes the normal HURT_POINTS off a starting 10.
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
    assert a.current_round_score == BETRAYAL_PAYOUT
    assert b.current_round_score == 10 - HURT_POINTS


async def test_hurt_non_helper_takes_the_plain_hurt(db):
    """A HURT on a non-helper lands for HURT_POINTS and pays the hoarder rate.

    B HOARDs (does not help A), so A gets no BETRAYAL_BONUS — but at v9 it is no
    longer nothing either: A takes HURT_TAKE_HOARDER, the smallest tier, because
    a hoarder had the least on the table.
    """
    game, [a, b] = await _make_game_with_players(db, 2)
    b.current_round_score = 10
    await db.commit()
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HOARD")
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == HURT_TAKE_HOARDER  # priced off B's HOARD
    assert b.current_round_score == 10 + hoard_share(1) - HURT_POINTS


async def test_betrayal_bonus_only_for_the_helped_attacker(db):
    """Only the attacker the victim HELPed gets the bonus; every HURT still lands.

    B HELPs A. A HURTs B (a betrayal). C HURTs B (normal — C gets nothing).
    B takes BOTH hurts off a starting 20.
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
    await db.refresh(c)
    assert a.current_round_score == BETRAYAL_PAYOUT
    # C HURT a player who was HELPing someone ELSE (A), so C gets the helper tier
    # and NOT the betrayal bonus. C is the only attacker taking that tier, so it
    # is not split — A's betrayal bonus is a separate, never-shared payout, which
    # is what stops a third party halving someone else's betrayal by piling on.
    assert c.current_round_score == HURT_TAKE_HELPER
    assert b.current_round_score == 20 - 2 * HURT_POINTS  # both hurts land


async def test_betrayal_victim_floored_at_zero(db):
    """The score floor still applies to the victim's summed delta on a betrayal.

    B HELPs A (A takes the betrayal payout). A HURTs B. B starts at 3, below
    HURT_POINTS, so the delta goes negative and clips. The floor is on the FINAL
    delta; the attacker's gain never floors.
    """
    game, [a, b] = await _make_game_with_players(db, 2)
    b.current_round_score = 3
    await db.commit()
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, b, "HELP", target=a)
    await resolve_turn(db, turn)
    await db.refresh(a)
    await db.refresh(b)
    assert a.current_round_score == BETRAYAL_PAYOUT  # unaffected by victim's floor
    assert b.current_round_score == 0  # 3 - HURT_POINTS clipped at 0


async def test_betrayer_bonus_is_inside_summed_floor_no_floor(db):
    """The betrayer's bonus is a real, summed-in gain (no floor hit here).

    A starts 6. B HELPs A. A HURTs B (a betrayal). C HURTs A. An implementation
    that DROPPED the bonus would leave A short by exactly BETRAYAL_BONUS, so the
    derived expectation below is what proves the bonus is summed in.
    """
    game, [a, b, c] = await _make_game_with_players(db, 3)
    a.current_round_score = 6
    await db.commit()
    turn = await _open_turn(db, game)
    await _submit(db, turn, b, "HELP", target=a)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, c, "HURT", target=a)
    await resolve_turn(db, turn)
    await db.refresh(a)
    assert a.current_round_score == 6 + BETRAYAL_PAYOUT - HURT_POINTS


async def test_betrayer_floors_on_summed_delta(db):
    """The betrayer's bonus is inside the SUMMED-delta floor, not a per-hurt floor.

    A starts 0, is HELPed by B, betrays B, and is HURT by everyone else. A per-hurt
    floor would clip A to 0 partway through and then re-add the bonus, ending
    positive; ending at exactly 0 proves the floor is applied once, to the final
    summed delta.

    The attacker count is not arbitrary: the incoming damage has to outweigh the
    betrayal payout or the scenario stops exercising the floor at all. Two
    attackers were enough while the bonus was 6 and silently stopped being enough
    at 14, so the guard below pins the premise instead of trusting it.
    """
    game, [a, b, c, d, e] = await _make_game_with_players(db, 5)
    a.current_round_score = 0
    attackers = [c, d, e]
    assert BETRAYAL_PAYOUT - HURT_POINTS * len(attackers) < 0, (
        "scenario no longer drives the delta negative — add another attacker"
    )
    await db.commit()
    turn = await _open_turn(db, game)
    await _submit(db, turn, b, "HELP", target=a)
    await _submit(db, turn, a, "HURT", target=b)
    for attacker in attackers:
        await _submit(db, turn, attacker, "HURT", target=a)
    await resolve_turn(db, turn)
    await db.refresh(a)
    assert a.current_round_score == 0  # summed delta is negative → floored once


async def test_missed_turn_defaults_to_hoard(db):
    """A player with no submission gets defaulted to Hoard with canonical message."""
    game, [a, b] = await _make_game_with_players(db, 2)
    turn = await _open_turn(db, game)
    await _submit(db, turn, a, "HOARD")
    # B does not submit.
    await resolve_turn(db, turn)
    await db.refresh(b)
    # A hoarded and B defaulted to HOARD, so the two of them split the pot.
    assert b.current_round_score == hoard_share(2)

    # The defaulted submission row exists, and says nothing.
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
    # No invented prose. `was_defaulted` above is the one home for "this seat
    # missed its turn"; a sentence here would be that same fact written twice,
    # and it used to make a silent seat read as a talking one.
    assert sub.message == ""


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


# --- One shared finish-order key (finalize_game winner == final_placement) ---


class _Standing:
    """A minimal stand-in with the two fields the finish-order sorts read."""

    def __init__(self, pid: int, wins: float, score: int) -> None:
        self.id = pid
        self.total_round_wins = wins
        self.total_round_score = score

    def __repr__(self) -> str:  # readable assertion diffs
        return f"P{self.id}(w={self.total_round_wins}, s={self.total_round_score})"


def test_finish_order_key_matches_both_old_sorts() -> None:
    """The shared key reproduces BOTH old encodings — winner pick and placement.

    Old finalize_game winner sort: ascending on (-wins, -score) — stable, so
    full ties keep input order. Old final_placement sort: (wins, score) with
    reverse=True — Python's reverse sort is also stable, so full ties keep
    input order too. The shared key must reproduce both orderings exactly,
    including tie order, for every input permutation.
    """
    from itertools import permutations

    from app.engine.resolver import finish_order_sort_key

    cases: list[list[_Standing]] = [
        # Equal round wins; score breaks the tie.
        [_Standing(1, 5.0, 120), _Standing(2, 5.0, 130), _Standing(3, 5.0, 120)],
        # Equal round wins AND equal score — a full tie (input order decides).
        [_Standing(1, 2.0, 40), _Standing(2, 2.0, 40), _Standing(3, 2.0, 40)],
        # Mixed: distinct wins, partial score ties, fractional wins.
        [
            _Standing(1, 1.5, 30),
            _Standing(2, 3.0, 10),
            _Standing(3, 1.5, 30),
            _Standing(4, 0.0, 99),
        ],
    ]
    for case in cases:
        for players in permutations(case):
            seeded = list(players)
            old_winner_order = sorted(
                seeded, key=lambda p: (-p.total_round_wins, -p.total_round_score)
            )
            old_placement_order = sorted(
                seeded,
                key=lambda p: (p.total_round_wins, p.total_round_score),
                reverse=True,
            )
            shared_order = sorted(seeded, key=finish_order_sort_key)
            assert [p.id for p in shared_order] == [p.id for p in old_winner_order]
            assert [p.id for p in shared_order] == [p.id for p in old_placement_order]


async def test_finalize_game_winner_matches_final_placement_on_full_tie(db):
    """Equal round wins AND equal score: winner == final_placement[0].

    Both paths query players the same way and sort with the same stable key, so
    on a full tie both must pick the same (first-seeded) player.
    """
    from app.games.hoard_hurt_help.game import HoardHurtHelp

    game, [a, b] = await _make_game_with_players(db, 2)
    a.total_round_wins = 3
    a.total_round_score = 50
    b.total_round_wins = 3
    b.total_round_score = 50
    await db.commit()

    placement = await HoardHurtHelp().final_placement(db, game)
    await finalize_game(db, game)
    await db.refresh(game)
    assert game.state == GameState.COMPLETED
    # Full tie: the stable sorts keep seed order, so the first-seeded player
    # wins — and the winner is exactly the head of final_placement.
    assert game.winner_player_id == a.id
    assert game.winner_player_id == placement[0]


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
    assert a.current_round_score == hoard_share(2)  # a hoarded, b defaulted to hoard

    t2 = await _open_turn(db, game, round_num=1, turn_num=2)
    await _submit(db, t2, a, "HELP", target=b)
    await _submit(db, t2, b, "HELP", target=a)
    await resolve_turn(db, t2)
    await db.refresh(a)
    # The turn-1 hoard share carries forward; the fresh pact adds its full +8.
    assert a.current_round_score == hoard_share(2) + 8  # k=0 → fresh +8


# --- current_pact_values (feature mutual-help-pact-value) ---


async def test_current_pact_values_fresh_pair_shows_8(db):
    """A pair with no resolved turns yet shows the un-decayed +8 value."""
    from app.games.hoard_hurt_help.scoring import current_pact_values

    game, [a, b] = await _make_game_with_players(db, 2)
    values = await current_pact_values(db, game.id, a.id, [b.id], mode="decay")
    assert values == {b.id: 8}


async def test_current_pact_values_after_one_mutual_help_shows_7(db):
    """After one resolved mutual help (k=1), the pair's live value drops to 7."""
    from app.games.hoard_hurt_help.scoring import current_pact_values

    game, [a, b] = await _make_game_with_players(db, 2)
    t1 = await _open_turn(db, game, round_num=1, turn_num=1)
    await _submit(db, t1, a, "HELP", target=b)
    await _submit(db, t1, b, "HELP", target=a)
    await resolve_turn(db, t1)

    values = await current_pact_values(db, game.id, a.id, [b.id], mode="decay")
    assert values == {b.id: 7}
    # Symmetric: B's live value with A is the same.
    assert await current_pact_values(db, game.id, b.id, [a.id], mode="decay") == {a.id: 7}


async def test_current_pact_values_floors_at_2(db):
    """After enough repeats the pair's live value floors at MUTUAL_HELP_FLOOR (2)."""
    from app.games.hoard_hurt_help.scoring import current_pact_values

    game, [a, b] = await _make_game_with_players(db, 2)
    for i in range(8):  # k will reach 8, well past the floor
        turn = await _open_turn(db, game, round_num=1, turn_num=i + 1)
        await _submit(db, turn, a, "HELP", target=b)
        await _submit(db, turn, b, "HELP", target=a)
        await resolve_turn(db, turn)

    assert await current_pact_values(db, game.id, a.id, [b.id], mode="decay") == {b.id: 2}


async def test_current_pact_values_unaffected_pair_stays_8(db):
    """A↔B farms their pact; C↔D's fresh pair still shows the un-decayed 8."""
    from app.games.hoard_hurt_help.scoring import current_pact_values

    game, [a, b, c, d] = await _make_game_with_players(db, 4)
    t1 = await _open_turn(db, game, round_num=1, turn_num=1)
    await _submit(db, t1, a, "HELP", target=b)
    await _submit(db, t1, b, "HELP", target=a)
    await _submit(db, t1, c, "HOARD")
    await _submit(db, t1, d, "HOARD")
    await resolve_turn(db, t1)

    assert await current_pact_values(db, game.id, a.id, [b.id], mode="decay") == {b.id: 7}
    assert await current_pact_values(db, game.id, c.id, [d.id], mode="decay") == {d.id: 8}
    # One call can look up several other players' values at once.
    assert await current_pact_values(db, game.id, a.id, [b.id, c.id, d.id], mode="decay") == {
        b.id: 7,
        c.id: 8,
        d.id: 8,
    }


async def test_hurt_pays_off_what_the_target_was_doing(db):
    """Every v9 tier, in one turn, against the real resolver.

    A HURTs B (B HELPs A)      -> betrayal: the full bonus, never split
    C HURTs D (D HELPs E)      -> the helper tier
    E HURTs F (F HOARDs)       -> the hoarder tier
    G HURTs H and H HURTs G    -> blocked: no damage, no take, either way
    """
    game, players = await _make_game_with_players(db, 8)
    a, b, c, d, e, f, g, h = players
    turn = await _open_turn(db, game)
    await _submit(db, turn, b, "HELP", target=a)
    await _submit(db, turn, a, "HURT", target=b)
    await _submit(db, turn, d, "HELP", target=e)
    await _submit(db, turn, c, "HURT", target=d)
    await _submit(db, turn, f, "HOARD")
    await _submit(db, turn, e, "HURT", target=f)
    await _submit(db, turn, g, "HURT", target=h)
    await _submit(db, turn, h, "HURT", target=g)
    await resolve_turn(db, turn)
    for p in players:
        await db.refresh(p)

    assert a.current_round_score == BETRAYAL_PAYOUT          # help + bonus
    assert c.current_round_score == HURT_TAKE_HELPER         # D was helping E
    assert e.current_round_score == HURT_TAKE_HOARDER + HELP_POINTS  # take + D's help
    assert g.current_round_score == 0                        # blocked
    assert h.current_round_score == 0                        # blocked, both ways
    assert b.current_round_score == 0                        # -HURT_POINTS, floored
    assert f.current_round_score == max(0, hoard_share(1) - HURT_POINTS)


async def test_several_attackers_split_the_take_but_not_a_betrayal(db):
    """Mobbing one target shares the take; a betrayer neither splits nor thins it.

    B HELPs A. A, C and D all HURT B. A is betraying, so A takes the full bonus
    and is excluded from the split — C and D share one helper-tier take between
    them, rather than three ways.
    """
    game, players = await _make_game_with_players(db, 4)
    a, b, c, d = players
    b.current_round_score = 40
    await db.commit()
    turn = await _open_turn(db, game)
    await _submit(db, turn, b, "HELP", target=a)
    for attacker in (a, c, d):
        await _submit(db, turn, attacker, "HURT", target=b)
    await resolve_turn(db, turn)
    for p in players:
        await db.refresh(p)

    assert a.current_round_score == BETRAYAL_PAYOUT       # NOT halved by C and D
    assert c.current_round_score == HURT_TAKE_HELPER // 2  # C and D share
    assert d.current_round_score == HURT_TAKE_HELPER // 2
    assert b.current_round_score == 40 - 3 * HURT_POINTS   # all three hurts land
