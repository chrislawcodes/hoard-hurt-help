"""Resuming a killed match: the position must carry over, and it must play on.

A match that dies partway cannot be rewound. Once a turn's deadline passes the
missing moves are recorded as defaults, and a default scores as a HOARD, so the
fabricated moves sit in the results looking like decisions. M_7558 lost rounds
five through seven that way. The admin resume builds a NEW match holding the
position that was really played, ready to be finished by the ordinary path.

The two properties worth pinning are here: the carried position is EXACTLY the
recorded one (not a rescored version of it), and the new match runs to
completion from the resume point through the real turn loop.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.engine.bots.seating import add_bots_to_game
from app.engine.match_resume import ResumeError, ResumePoint, resume_match_from
from app.engine.state_machine import assert_transition
from app.engine.tokens import generate_match_id
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission
from app.models.user import User, UserRole

SEATS = ["Alpha", "Bravo", "Charlie"]


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch, tmp_path):
    """A FILE-backed database with the scheduler pointed at it.

    These tests run real matches, and the turn loop opens its own session
    through `scheduler.SessionLocal`. The suite's default in-memory database is
    per-connection, so that session finds no tables at all — and the failure
    ("no such table: matches") looks nothing like the cause. Same shape as
    tests/test_bots_scheduler.py, which runs games for the same reason.
    """
    import app.db as app_db
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    from app.db import make_engine
    from app.engine import scheduler
    from app.models import Base

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'match_resume.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = _factory(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    monkeypatch.setattr("app.db.engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", factory)
    monkeypatch.setattr(scheduler, "SessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _quiet_publish(monkeypatch):
    """Swallow the loop's broadcast events; nothing here is listening."""
    from app.engine import scheduler

    async def noop(channel: str, event_type: str, payload: dict) -> None:
        return None

    monkeypatch.setattr(scheduler, "publish", noop)


async def _seed(reset_db, email: str, *, admin: bool) -> User:
    """Seed a signed-in user. Role is set here rather than inferred from settings,
    so a test says plainly which door it is knocking on."""
    async with reset_db() as db:
        user = User(
            google_sub=f"sub-{email}",
            email=email,
            name=email,
            role=UserRole.ADMIN if admin else UserRole.USER,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def _played_match(reset_db, *, rounds: int = 3, turns: int = 2) -> str:
    """A finished match, run by the real loop, to resume from."""
    from app.engine import scheduler

    async with reset_db() as db:
        match = Match(
            id=generate_match_id(1),
            name="source",
            game="hoard-hurt-help",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) - timedelta(seconds=1),
            per_turn_deadline_seconds=0,
            total_rounds=rounds,
            turns_per_round=turns,
            min_players=3,
            max_players=10,
        )
        db.add(match)
        await db.flush()
        await add_bots_to_game(db, match, [(s, "pragmatist") for s in SEATS])
        assert_transition(match.state, GameState.ACTIVE)
        match.state = GameState.ACTIVE
        match.started_at = datetime.now(timezone.utc)
        await db.commit()
        mid = match.id
    await scheduler._run_game(mid)
    return mid


async def _position(db, match_id: str, before: tuple[int, int]) -> dict:
    """Every recorded move before a turn, keyed so two matches can be compared."""
    rows = (
        await db.execute(
            select(Turn.round, Turn.turn, Player.seat_name, TurnSubmission.action,
                   TurnSubmission.points_delta, TurnSubmission.round_score_after)
            .join(TurnSubmission, TurnSubmission.turn_id == Turn.id)
            .join(Player, Player.id == TurnSubmission.player_id)
            .where(Turn.match_id == match_id)
        )
    ).all()
    return {
        (r, t, seat): (action, delta, after)
        for r, t, seat, action, delta, after in rows
        if (r, t) < before
    }


async def test_the_carried_position_is_exactly_the_recorded_one(reset_db):
    """Every seeded move keeps its own action, delta and running score.

    Re-scoring the history with today's resolver would silently restate the
    position whenever the payoffs have moved since the match was played — and
    the whole point of a resume is to pick up from the position that existed.
    """
    source_id = await _played_match(reset_db)
    cut = (2, 1)

    async with reset_db() as db:
        source = (await db.execute(select(Match).where(Match.id == source_id))).scalar_one()
        before = await _position(db, source_id, cut)
        new_match, seeded, _ = await resume_match_from(
            db, source, ResumePoint(round=cut[0], turn=cut[1])
        )
        await db.commit()
        after = await _position(db, new_match.id, cut)

    assert seeded == 2, "one round of two turns should have been carried"
    assert after == before, "the carried position differs from the recorded one"


async def test_the_source_match_is_never_touched(reset_db):
    """A recovery must not be able to destroy the evidence of what went wrong."""
    source_id = await _played_match(reset_db)

    async with reset_db() as db:
        source = (await db.execute(select(Match).where(Match.id == source_id))).scalar_one()
        was = (source.state, source.current_round, source.current_turn, source.rounds_awarded)
        turn_count = len(
            (await db.execute(select(Turn).where(Turn.match_id == source_id))).scalars().all()
        )
        await resume_match_from(db, source, ResumePoint(round=2, turn=1))
        await db.commit()

    async with reset_db() as db:
        again = (await db.execute(select(Match).where(Match.id == source_id))).scalar_one()
        still = len(
            (await db.execute(select(Turn).where(Turn.match_id == source_id))).scalars().all()
        )
    assert (again.state, again.current_round, again.current_turn, again.rounds_awarded) == was
    assert still == turn_count


async def test_the_resumed_match_plays_on_to_the_end(reset_db):
    """The point of the whole thing: it must finish, through the real loop.

    Not a detail — the resume writes `current_round`, `current_turn` and
    `rounds_awarded`, and getting any of them wrong either replays a round that
    already happened or awards one twice.
    """
    from app.engine import scheduler

    source_id = await _played_match(reset_db, rounds=3, turns=2)

    async with reset_db() as db:
        source = (await db.execute(select(Match).where(Match.id == source_id))).scalar_one()
        new_match, _, _ = await resume_match_from(db, source, ResumePoint(round=2, turn=1))
        new_match.state = GameState.ACTIVE
        new_match.started_at = datetime.now(timezone.utc)
        await db.commit()
        new_id = new_match.id

    await scheduler._run_game(new_id)

    async with reset_db() as db:
        finished = (await db.execute(select(Match).where(Match.id == new_id))).scalar_one()
        turns = (await db.execute(select(Turn).where(Turn.match_id == new_id))).scalars().all()
        wins = (
            await db.execute(select(Player.total_round_wins).where(Player.match_id == new_id))
        ).scalars().all()

    assert finished.state == GameState.COMPLETED
    assert len(turns) == 6, "three rounds of two turns, seeded plus played"
    assert all(t.resolved_at is not None for t in turns)
    # Three rounds, one win each, split on ties — never more, which is what a
    # double-award would produce.
    assert sum(wins) == pytest.approx(3.0)


async def test_it_refuses_a_cut_with_nothing_before_it(reset_db):
    """Resuming at the very first turn carries no position, so it is a mistake."""
    source_id = await _played_match(reset_db)

    async with reset_db() as db:
        source = (await db.execute(select(Match).where(Match.id == source_id))).scalar_one()
        with pytest.raises(ResumeError, match="nothing to carry over"):
            await resume_match_from(db, source, ResumePoint(round=1, turn=1))


async def test_it_refuses_a_cut_outside_the_match(reset_db):
    """A turn the match never had cannot be resumed from."""
    source_id = await _played_match(reset_db, rounds=3, turns=2)

    async with reset_db() as db:
        source = (await db.execute(select(Match).where(Match.id == source_id))).scalar_one()
        with pytest.raises(ResumeError, match="outside a 3x2 match"):
            await resume_match_from(db, source, ResumePoint(round=9, turn=1))


async def test_a_mid_round_cut_carries_that_round_s_score(reset_db):
    """Resuming partway through a round must keep the score already earned in it.

    The turn loop only zeroes scores when it starts a round at turn 1, so a
    mid-round resume that carried nothing would play the rest of the round from
    zero — a position nobody was ever in.
    """
    source_id = await _played_match(reset_db, rounds=3, turns=4)

    async with reset_db() as db:
        source = (await db.execute(select(Match).where(Match.id == source_id))).scalar_one()
        # What round 2 looked like after its second turn, in the source.
        recorded = dict(
            (
                await db.execute(
                    select(Player.seat_name, TurnSubmission.round_score_after)
                    .join(TurnSubmission, TurnSubmission.player_id == Player.id)
                    .join(Turn, Turn.id == TurnSubmission.turn_id)
                    .where(Turn.match_id == source_id, Turn.round == 2, Turn.turn == 2)
                )
            ).all()
        )
        new_match, seeded, _ = await resume_match_from(db, source, ResumePoint(round=2, turn=3))
        await db.commit()
        carried = dict(
            (
                await db.execute(
                    select(Player.seat_name, Player.current_round_score)
                    .where(Player.match_id == new_match.id)
                )
            ).all()
        )
        awarded = new_match.rounds_awarded

    assert seeded == 6, "all of round 1 plus the first two turns of round 2"
    assert carried == recorded, "round 2's part-scored position did not carry"
    assert awarded == 1, "only round 1 finished before the cut"


async def test_the_endpoint_builds_the_match_and_reports_the_defaults(reset_db, client):
    """The admin route returns the new match and the one number worth checking."""
    from tests.test_admin import _cookies

    admin = await _seed(reset_db, "admin@test.com", admin=True)
    source_id = await _played_match(reset_db, rounds=3, turns=2)

    r = await client.post(
        f"/api/admin/matches/{source_id}/resume-from",
        json={"round": 2, "turn": 1},
        cookies=_cookies(admin.id),
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source_match_id"] == source_id
    assert body["match_id"].startswith("M_") and body["match_id"] != source_id
    assert body["resumes_at"] == "R2T1"
    assert body["seeded_turns"] == 2
    # These bots always answer, so nothing was fabricated. The field exists for
    # the case that is not true, which is the usual reason to resume at all.
    assert body["seeded_defaulted_moves"] == 0


async def test_the_endpoint_explains_a_cut_it_cannot_use(reset_db, client):
    """A refusal must say why, not just fail."""
    from tests.test_admin import _cookies

    admin = await _seed(reset_db, "admin@test.com", admin=True)
    source_id = await _played_match(reset_db, rounds=3, turns=2)

    r = await client.post(
        f"/api/admin/matches/{source_id}/resume-from",
        json={"round": 1, "turn": 1},
        cookies=_cookies(admin.id),
    )

    assert r.status_code == 422, r.text
    assert "nothing to carry over" in r.text


async def test_a_plain_user_cannot_resume_a_match(reset_db, client):
    """Session-only and admin-only, like create and cancel.

    Spawning matches is not something a leaked credential should be able to do,
    which is why this is not on the wider connection-key door the exports use.
    """
    from tests.test_admin import _cookies

    user = await _seed(reset_db, "player@test.com", admin=False)
    source_id = await _played_match(reset_db, rounds=3, turns=2)

    r = await client.post(
        f"/api/admin/matches/{source_id}/resume-from",
        json={"round": 2, "turn": 1},
        cookies=_cookies(user.id),
    )

    assert r.status_code in (401, 403), r.text
