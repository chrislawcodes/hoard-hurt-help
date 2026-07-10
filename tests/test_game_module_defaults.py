"""The BaseGameModule defaults reproduce PD's behavior.

These hooks (added so a sequential/hidden game can override them) must, by
default, behave exactly as PD always has: no extra public state, HOARD as the
missed-turn move, fixed-grid match end, and the round-wins-then-score finish
order. PD inherits all of these unchanged, EXCEPT `private_state_for`: PD
overrides it to surface each pair's current mutual-help pact value (feature
`mutual-help-pact-value`) instead of the base `{}`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.engine.action_vocab import pd_action_names
from app.games.hoard_hurt_help.game import HoardHurtHelp
from app.games.liars_dice.game import LiarsDice
from app.models import Base, Match, GameState
from tests.factories import seat_player


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_action_names_contract() -> None:
    # The read-side insight engines bucket the action log by these names in this
    # exact order (HOARD, HELP, HURT); the insight/aggregate buckets depend on it.
    assert HoardHurtHelp().action_names() == ("HOARD", "HELP", "HURT")
    assert pd_action_names() == ("HOARD", "HELP", "HURT")
    # Each game owns its own vocabulary; Liar's Dice is not PD's trio.
    assert LiarsDice().action_names() == ("BID", "CHALLENGE")


def test_validation_snapshot_keys_contract() -> None:
    """Each game declares which validation-only snapshot keys the shared submit
    path must strip before record_submission. The default is deliberately empty
    ("strip nothing"), so PD's move dict passes through untouched; Liar's Dice
    declares exactly its validation_snapshot vocabulary."""
    assert HoardHurtHelp().validation_snapshot_keys == frozenset()
    assert LiarsDice().validation_snapshot_keys == frozenset(
        {"standing_bid", "dice_counts", "active_actor", "total_dice", "wild"}
    )


async def test_active_actors_default_fails_loud_for_non_sequential_games() -> None:
    """The batched actor read is sequential-only; a simultaneous game (PD) never
    receives the call, and a sequential game that forgot to override it must
    fail loud instead of silently serving turns to the wrong seats."""
    # The default raises before touching the session, so no DB is needed here.
    with pytest.raises(NotImplementedError):
        await HoardHurtHelp().active_actors(None, [])


async def test_pd_inherits_default_hooks() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    module = HoardHurtHelp()

    async with factory() as db:
        match = Match(
            id="M_DEF",
            name="def",
            game="hoard-hurt-help",
            state=GameState.ACTIVE,
            scheduled_start=_now(),
            total_rounds=7,
            rounds_awarded=3,
        )
        db.add(match)
        await db.flush()
        p1 = await seat_player(db, match.id, "A", i=0)
        p2 = await seat_player(db, match.id, "B", i=1)
        p1.total_round_wins, p1.total_round_score = 2.0, 30
        p2.total_round_wins, p2.total_round_score = 2.0, 45  # ties wins, higher score
        await db.commit()

        assert await module.default_move(db, match, p1) == {"action": "HOARD", "target_id": None}
        # PD overrides private_state_for (pact values); a fresh pair with no
        # resolved turns shows the un-decayed HELP_POINTS + MUTUAL_HELP_BONUS value.
        assert await module.private_state_for(db, match, p1) == {
            "pact_values": {p2.seat_name: 8},
            "pact_values_note": (
                "What a mutual HELP with this agent would pay EACH side right "
                "now (decays per repeat mutual-help pair this match; floors at 2)."
            ),
        }
        assert await module.public_state_for(db, match, p1) == {}

        assert await module.is_match_over(db, match) is False  # 3 < 7
        match.rounds_awarded = 7
        assert await module.is_match_over(db, match) is True

        # Finish order: equal wins → higher total score first.
        assert await module.final_placement(db, match) == [p2.id, p1.id]

        # Placement key: (round_wins, total_score), higher = better.
        assert module.match_placement_key(round_wins=2.0, total_score=45) == (2.0, 45.0)

    await engine.dispose()
