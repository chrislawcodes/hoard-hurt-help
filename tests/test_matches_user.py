"""Tests for the user-facing match creation and ownership flow."""

from datetime import datetime, timedelta, timezone
import base64
import json

import pytest
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.match_creation import create_match
from app.models import GameState, Match, User
from app.models.user import UserRole
from tests.factories import make_user, seat_player


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _seed_user(
    reset_db: async_sessionmaker,
    *,
    i: int = 0,
    role: UserRole = UserRole.USER,
) -> User:
    async with reset_db() as db:
        user = await make_user(db, i)
        user.role = role
        await db.commit()
        await db.refresh(user)
        return user


@pytest.mark.asyncio
async def test_lobby_shows_create_match_action_for_signed_in_users(client, reset_db):
    user = await _seed_user(reset_db, i=1)
    r = await client.get(
        "/games/hoard-hurt-help",
        cookies=_cookies(user.id),
    )
    assert r.status_code == 200
    assert 'href="/games/hoard-hurt-help/matches/new"' in r.text


@pytest.mark.asyncio
async def test_create_match_form_is_available(client, reset_db):
    user = await _seed_user(reset_db, i=2)
    r = await client.get(
        "/games/hoard-hurt-help/matches/new",
        cookies=_cookies(user.id),
    )
    assert r.status_code == 200
    assert "Create match" in r.text


@pytest.mark.asyncio
async def test_user_create_flow_records_creator(client, reset_db):
    user = await _seed_user(reset_db, i=3)
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    r = await client.post(
        "/games/hoard-hurt-help/matches/new",
        data={"name": "User Created Match", "scheduled_start": when},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert r.headers["location"] == "/me/matches"

    async with reset_db() as db:
        match = (
            await db.execute(
                select(Match).where(Match.name == "User Created Match")
            )
        ).scalar_one()
        assert match.created_by_user_id == user.id
        assert match.state == GameState.REGISTERING


@pytest.mark.asyncio
async def test_user_create_flow_rejects_at_active_match_cap(client, reset_db):
    user = await _seed_user(reset_db, i=4)
    when = datetime.now(timezone.utc) + timedelta(hours=1)

    async with reset_db() as db:
        for index, state in enumerate(
            [GameState.SCHEDULED, GameState.REGISTERING, GameState.ACTIVE], start=1
        ):
            await create_match(
                db,
                game="hoard-hurt-help",
                name=f"Busy {index}",
                scheduled_start=when,
                min_players=6,
                max_players=20,
                per_turn_deadline_seconds=60,
                total_rounds=7,
                turns_per_round=7,
                state=state,
                created_by_user_id=user.id,
            )

    r = await client.post(
        "/games/hoard-hurt-help/matches/new",
        data={"name": "Over Limit", "scheduled_start": (when + timedelta(minutes=5)).isoformat()},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )

    assert r.status_code == 409
    assert "active matches at once" in r.text


@pytest.mark.asyncio
async def test_my_matches_includes_owned_unjoined_matches_and_owner_controls(
    client, reset_db
):
    user = await _seed_user(reset_db, i=5)
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    async with reset_db() as db:
        db_user = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        owned_only = Match(
            id="M_OWNED",
            name="Owned Only",
            game="hoard-hurt-help",
            state=GameState.REGISTERING,
            scheduled_start=future,
            per_turn_deadline_seconds=60,
            created_by_user_id=db_user.id,
        )
        owned_and_joined = Match(
            id="M_JOINED",
            name="Owned + Joined",
            game="hoard-hurt-help",
            state=GameState.REGISTERING,
            scheduled_start=future,
            per_turn_deadline_seconds=60,
            created_by_user_id=db_user.id,
        )
        db.add_all([owned_only, owned_and_joined])
        await db.flush()
        joined_player = await seat_player(db, owned_and_joined.id, "Joined Seat", user=db_user)
        joined_seat_name = joined_player.seat_name
        await db.commit()

    r = await client.get("/me/matches", cookies=_cookies(user.id))
    assert r.status_code == 200
    assert "Created by you" in r.text
    assert 'action="/matches/M_OWNED/delete"' in r.text
    assert 'action="/matches/M_JOINED/delete"' in r.text
    assert f"Playing as {joined_seat_name}" in r.text


@pytest.mark.asyncio
async def test_owner_delete_pre_start_succeeds(client, reset_db):
    user = await _seed_user(reset_db, i=6)
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    async with reset_db() as db:
        match = Match(
            id="M_PRE",
            name="Pre Start",
            game="hoard-hurt-help",
            state=GameState.REGISTERING,
            scheduled_start=future,
            per_turn_deadline_seconds=60,
            created_by_user_id=user.id,
        )
        db.add(match)
        await db.commit()

    r = await client.post(
        "/matches/M_PRE/delete",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303

    async with reset_db() as db:
        assert await db.get(Match, "M_PRE") is None


@pytest.mark.asyncio
async def test_owner_delete_active_match_is_rejected(client, reset_db):
    user = await _seed_user(reset_db, i=7)
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    async with reset_db() as db:
        match = Match(
            id="M_ACTIVE",
            name="Active Match",
            game="hoard-hurt-help",
            state=GameState.ACTIVE,
            scheduled_start=future,
            per_turn_deadline_seconds=60,
            created_by_user_id=user.id,
        )
        db.add(match)
        await db.commit()

    r = await client.post(
        "/matches/M_ACTIVE/delete",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "MATCH_ALREADY_STARTED"


@pytest.mark.asyncio
async def test_non_owner_delete_is_rejected(client, reset_db):
    owner = await _seed_user(reset_db, i=8)
    other = await _seed_user(reset_db, i=9)
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    async with reset_db() as db:
        match = Match(
            id="M_OTHER",
            name="Someone Else's Match",
            game="hoard-hurt-help",
            state=GameState.REGISTERING,
            scheduled_start=future,
            per_turn_deadline_seconds=60,
            created_by_user_id=owner.id,
        )
        db.add(match)
        await db.commit()

    r = await client.post(
        "/matches/M_OTHER/delete",
        cookies=_cookies(other.id),
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "NOT_MATCH_OWNER"


@pytest.mark.asyncio
async def test_admin_can_delete_any_match(client, reset_db):
    owner = await _seed_user(reset_db, i=10)
    admin = await _seed_user(reset_db, i=11, role=UserRole.ADMIN)
    future = datetime.now(timezone.utc) + timedelta(hours=1)

    async with reset_db() as db:
        match = Match(
            id="M_ADMIN",
            name="Admin Target",
            game="hoard-hurt-help",
            state=GameState.ACTIVE,
            scheduled_start=future,
            per_turn_deadline_seconds=60,
            created_by_user_id=owner.id,
        )
        db.add(match)
        await db.commit()

    r = await client.post(
        "/matches/M_ADMIN/delete",
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 303

    async with reset_db() as db:
        assert await db.get(Match, "M_ADMIN") is None


async def _seed_match(reset_db, *, match_id, owner_id, state):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    async with reset_db() as db:
        db.add(
            Match(
                id=match_id,
                name=match_id,
                game="hoard-hurt-help",
                state=state,
                scheduled_start=future,
                per_turn_deadline_seconds=60,
                created_by_user_id=owner_id,
            )
        )
        await db.commit()


# Cancel is admin-only (admins are the "organizers"). Regular users — even the
# match owner — cannot cancel; they can only delete their own match pre-start.


@pytest.mark.asyncio
async def test_owner_cannot_cancel_their_match(client, reset_db):
    user = await _seed_user(reset_db, i=20)
    await _seed_match(reset_db, match_id="M_C1", owner_id=user.id, state=GameState.REGISTERING)

    r = await client.post(
        "/matches/M_C1/cancel", cookies=_cookies(user.id), follow_redirects=False
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"]["code"] == "NOT_PLATFORM_ADMIN"
    async with reset_db() as db:
        assert (await db.get(Match, "M_C1")).state == GameState.REGISTERING  # unchanged


@pytest.mark.asyncio
async def test_signed_out_cannot_cancel(client, reset_db):
    owner = await _seed_user(reset_db, i=21)
    await _seed_match(reset_db, match_id="M_C2", owner_id=owner.id, state=GameState.ACTIVE)

    r = await client.post("/matches/M_C2/cancel", follow_redirects=False)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_admin_can_cancel_pre_start_and_active(client, reset_db):
    admin = await _seed_user(reset_db, i=22, role=UserRole.ADMIN)
    owner = await _seed_user(reset_db, i=23)
    await _seed_match(reset_db, match_id="M_C3", owner_id=owner.id, state=GameState.REGISTERING)
    await _seed_match(reset_db, match_id="M_C4", owner_id=owner.id, state=GameState.ACTIVE)

    for mid in ("M_C3", "M_C4"):
        r = await client.post(
            f"/matches/{mid}/cancel", cookies=_cookies(admin.id), follow_redirects=False
        )
        assert r.status_code == 303
        async with reset_db() as db:
            assert (await db.get(Match, mid)).state == GameState.CANCELLED


@pytest.mark.asyncio
async def test_admin_cancel_already_ended_match_is_rejected(client, reset_db):
    admin = await _seed_user(reset_db, i=24, role=UserRole.ADMIN)
    await _seed_match(reset_db, match_id="M_C5", owner_id=admin.id, state=GameState.COMPLETED)

    r = await client.post(
        "/matches/M_C5/cancel", cookies=_cookies(admin.id), follow_redirects=False
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"]["code"] == "MATCH_ALREADY_ENDED"
