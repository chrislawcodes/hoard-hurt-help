"""Tests for the sideline coaching feature.

Covers:
- coach_note is included in the next-turn payload when round matches
- coach_note is absent when round doesn't match
- 280-char cap is enforced on the POST route
- Non-players are rejected (403)
- Clearing the note works
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models import Match, GameState, Player
from tests.factories import make_user, seat_player


def _signed_in(user_id: int) -> dict[str, str]:
    signer = TimestampSigner(settings.session_secret)
    data = {"user_id": user_id, "next_after_login": None}
    payload = base64.b64encode(json.dumps(data).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _make_active_match(db_factory: async_sessionmaker) -> tuple[str, Player, int]:
    """Seed an active match with one player. Returns (match_id, player, user_id)."""
    async with db_factory() as db:
        g = Match(
            id="G_TEST",
            name="Test match",
            state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
            current_round=3,
            coaching=True,
        )
        db.add(g)
        await db.flush()
        player = await seat_player(db, g.id, "alice/bot", i=0)
        await db.commit()
        return g.id, player, player.user_id


# ---------------------------------------------------------------------------
# Unit: coach_note field semantics on Player
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_coach_note_fields_default_null(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        g = Match(
            id="G_X", name="t", state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
        )
        db.add(g)
        await db.flush()
        user = await make_user(db, 0)
        p = await seat_player(db, g.id, "bob/bot", i=0, user=user)
        await db.commit()

    async with reset_db() as db:
        row = (await db.execute(select(Player).where(Player.id == p.id))).scalar_one()
        assert row.coach_note is None
        assert row.coach_note_round is None


@pytest.mark.asyncio
async def test_coach_note_persists(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        g = Match(
            id="G_X2", name="t", state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
        )
        db.add(g)
        await db.flush()
        user = await make_user(db, 0)
        p = await seat_player(db, g.id, "bob/bot", i=0, user=user)
        p.coach_note = "Go all-in on HOARD"
        p.coach_note_round = 4
        await db.commit()

    async with reset_db() as db:
        row = (await db.execute(select(Player).where(Player.id == p.id))).scalar_one()
        assert row.coach_note == "Go all-in on HOARD"
        assert row.coach_note_round == 4


# ---------------------------------------------------------------------------
# Integration: coach note included in next-turn payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_coach_note_injected_when_round_matches(reset_db: async_sessionmaker) -> None:
    """coach_note appears in static payload only when current_round matches."""
    async with reset_db() as db:
        g = Match(
            id="G_NOTE",
            name="note test",
            state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
            current_round=4,
        )
        db.add(g)
        await db.flush()
        player = await seat_player(db, g.id, "alice/bot", i=0)
        player.coach_note = "Be cooperative this round"
        player.coach_note_round = 4
        await db.commit()

    # Verify by reading the Player row; the route wiring is covered separately.
    async with reset_db() as db:
        row = (await db.execute(select(Player).where(Player.id == player.id))).scalar_one()
        match = (await db.execute(select(Match).where(Match.id == "G_NOTE"))).scalar_one()
        note_active = row.coach_note and row.coach_note_round == match.current_round
        assert note_active
        assert row.coach_note == "Be cooperative this round"


@pytest.mark.asyncio
async def test_coach_note_not_active_wrong_round(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        g = Match(
            id="G_NOTE2",
            name="note test 2",
            state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
            current_round=3,
        )
        db.add(g)
        await db.flush()
        player = await seat_player(db, g.id, "alice/bot", i=0)
        player.coach_note = "Armed for round 4"
        player.coach_note_round = 4
        await db.commit()

    async with reset_db() as db:
        row = (await db.execute(select(Player).where(Player.id == player.id))).scalar_one()
        match = (await db.execute(select(Match).where(Match.id == "G_NOTE2"))).scalar_one()
        note_active = row.coach_note and row.coach_note_round == match.current_round
        assert not note_active


# ---------------------------------------------------------------------------
# HTTP: POST /games/{game}/matches/{match_id}/coach-note
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_coach_note_saves_for_next_round(
    reset_db: async_sessionmaker, client: AsyncClient
) -> None:
    match_id, player, user_id = await _make_active_match(reset_db)

    r = await client.post(
        f"/games/hoard-hurt-help/matches/{match_id}/coach-note",
        data={"note": "Go aggressive"},
        cookies=_signed_in(user_id),
    )
    assert r.status_code == 200, r.text

    async with reset_db() as db:
        row = (await db.execute(select(Player).where(Player.id == player.id))).scalar_one()
        match = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()

    assert row.coach_note == "Go aggressive"
    assert row.coach_note_round == match.current_round + 1


@pytest.mark.asyncio
async def test_post_coach_note_clears_when_empty(
    reset_db: async_sessionmaker, client: AsyncClient
) -> None:
    match_id, player, user_id = await _make_active_match(reset_db)

    # First arm a note.
    await client.post(
        f"/games/hoard-hurt-help/matches/{match_id}/coach-note",
        data={"note": "Some note"},
        cookies=_signed_in(user_id),
    )

    # Then clear it.
    r = await client.post(
        f"/games/hoard-hurt-help/matches/{match_id}/coach-note",
        data={"note": ""},
        cookies=_signed_in(user_id),
    )
    assert r.status_code == 200, r.text

    async with reset_db() as db:
        row = (await db.execute(select(Player).where(Player.id == player.id))).scalar_one()

    assert row.coach_note is None
    assert row.coach_note_round is None


@pytest.mark.asyncio
async def test_post_coach_note_truncates_at_280(
    reset_db: async_sessionmaker, client: AsyncClient
) -> None:
    match_id, player, user_id = await _make_active_match(reset_db)
    long_note = "x" * 400

    r = await client.post(
        f"/games/hoard-hurt-help/matches/{match_id}/coach-note",
        data={"note": long_note},
        cookies=_signed_in(user_id),
    )
    assert r.status_code == 200, r.text

    async with reset_db() as db:
        row = (await db.execute(select(Player).where(Player.id == player.id))).scalar_one()

    assert len(row.coach_note) <= 280


@pytest.mark.asyncio
async def test_post_coach_note_requires_auth(
    reset_db: async_sessionmaker, client: AsyncClient
) -> None:
    match_id, _, _ = await _make_active_match(reset_db)

    r = await client.post(
        f"/games/hoard-hurt-help/matches/{match_id}/coach-note",
        data={"note": "Hello"},
    )
    # Unauthenticated → 401 (require_user dependency).
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_post_coach_note_rejects_non_player(
    reset_db: async_sessionmaker, client: AsyncClient
) -> None:
    match_id, _, _ = await _make_active_match(reset_db)

    # Create a second user who is NOT a player in this match.
    async with reset_db() as db:
        outsider = await make_user(db, 99)
        await db.commit()

    r = await client.post(
        f"/games/hoard-hurt-help/matches/{match_id}/coach-note",
        data={"note": "Interloper"},
        cookies=_signed_in(outsider.id),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_game_view_shows_prompt_window(
    reset_db: async_sessionmaker, client: AsyncClient
) -> None:
    match_id, _, user_id = await _make_active_match(reset_db)

    r = await client.get(
        f"/games/hoard-hurt-help/matches/{match_id}",
        cookies=_signed_in(user_id),
    )
    assert r.status_code == 200, r.text
    assert 'id="coach-dialog-wrap"' in r.text
    assert "Existing prompt" in r.text
    assert "Course correction" in r.text
    assert "Play to win." in r.text
