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

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.engine.action_vocab import pd_action_names
from app.engine.arena import _default_turn_deadline
from app.games.hoard_hurt_help.game import HoardHurtHelp, act_deadline_seconds
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


def test_act_deadline_default_is_pinned(monkeypatch) -> None:
    """No env override means the shipped 75s window.

    The knob exists so an experiment can widen the act phase, but that must never
    quietly become the game real players get. This pins the default so an override
    left behind in the environment can't ship by accident.
    """
    monkeypatch.delenv("HHH_ACT_DEADLINE_SECONDS", raising=False)
    assert act_deadline_seconds() == 75
    assert HoardHurtHelp().config_defaults().per_turn_deadline_seconds == 75


def test_act_deadline_can_be_overridden_by_env(monkeypatch) -> None:
    """HHH_ACT_DEADLINE_SECONDS widens (or narrows) the act window for new matches."""
    monkeypatch.setenv("HHH_ACT_DEADLINE_SECONDS", "180")
    assert act_deadline_seconds() == 180
    assert HoardHurtHelp().config_defaults().per_turn_deadline_seconds == 180


def test_arena_matches_track_the_same_act_deadline(monkeypatch) -> None:
    """Arena and auto-scheduled matches follow the knob too.

    They used to repeat the literal 75, so widening the window silently skipped every
    match the platform starts on its own — the ones most likely to be running when an
    experiment is on.
    """
    monkeypatch.setenv("HHH_ACT_DEADLINE_SECONDS", "180")
    assert _default_turn_deadline() == 180


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
            # Stated rather than inherited: this test is about PD's hooks, not
            # about which rule new matches default to, and the pact numbers below
            # are decay's. Riding the platform default made it fail the day that
            # default moved.
            mutual_help_mode="decay",
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
