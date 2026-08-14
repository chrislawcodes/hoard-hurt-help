"""Route-level bot form validation.

Covers that the admin "Add Bots" form rejects invalid bot strategies with a
clear user-facing error before seating, and that valid strategies are accepted.
Only bot-kind agents go through this validation path — non-bot (AI) agent
creation and editing are untouched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.main import app
from app.models import Base, GameState, Match, Player, User
from app.models.user import UserRole
from tests.factories import make_match
from tests.conftest import signed_in_cookies as _cookies


# Bespoke: also seeds admin_emails for this file's admin-gate tests, so it can't
# delegate to tests/conftest.py's shared reset_db.
@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    from app.db import make_engine

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    monkeypatch.setattr(settings, "admin_emails", "admin@test.com")

    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_admin(reset_db) -> User:
    async with reset_db() as db:
        u = User(
            google_sub="sub-admin",
            email="admin@test.com",
            name="Admin",
            role=UserRole.ADMIN,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


async def _seed_game(
    reset_db,
    *,
    state: GameState = GameState.REGISTERING,
    max_players: int = 20,
    match_id: str = "G_bfv",
) -> Match:
    async with reset_db() as db:
        g = await make_match(
            db,
            match_id,
            state=state,
            name="Bot Form Validation Test",
            max_players=max_players,
        )
        await db.commit()
        await db.refresh(g)
        return g


def _roster(*pairs: tuple[str, str]) -> dict[str, list[str]]:
    """Build the parallel-array form body matching the add-bots form encoding."""
    return {
        "seat_name": [name for name, _ in pairs],
        "seat_strategy": [strategy for _, strategy in pairs],
    }


# ---------------------------------------------------------------------------
# Invalid strategy — rejected at the route level with a user-facing error
# ---------------------------------------------------------------------------


async def test_invalid_strategy_rejected_with_form_error(client, reset_db) -> None:
    """Submitting an unknown strategy returns 400 with a legible error message."""
    admin = await _seed_admin(reset_db)
    await _seed_game(reset_db)

    r = await client.post(
        "/games/hoard-hurt-help/admin/matches/G_bfv/bots",
        data=_roster(("Caesar", "galaxy_brain")),
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )

    assert r.status_code == 400
    assert "galaxy_brain" in r.text

    # No players should have been created.
    async with reset_db() as db:
        count = len(
            (await db.execute(select(Player).where(Player.match_id == "G_bfv")))
            .scalars()
            .all()
        )
    assert count == 0


async def test_invalid_strategy_in_mixed_roster_rejected(client, reset_db) -> None:
    """A single invalid strategy in a multi-bot roster still rejects the whole form."""
    admin = await _seed_admin(reset_db)
    await _seed_game(reset_db)

    r = await client.post(
        "/games/hoard-hurt-help/admin/matches/G_bfv/bots",
        data=_roster(("Caesar", "grudger"), ("Nero", "invalid_strat")),
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )

    assert r.status_code == 400
    assert "invalid_strat" in r.text

    async with reset_db() as db:
        count = len(
            (await db.execute(select(Player).where(Player.match_id == "G_bfv")))
            .scalars()
            .all()
        )
    assert count == 0


# ---------------------------------------------------------------------------
# Valid strategy — accepted and bots are seated
# ---------------------------------------------------------------------------


async def test_valid_strategy_accepted_and_bots_seated(client, reset_db) -> None:
    """Submitting a valid strategy creates the bots and redirects."""
    admin = await _seed_admin(reset_db)
    await _seed_game(reset_db)

    r = await client.post(
        "/games/hoard-hurt-help/admin/matches/G_bfv/bots",
        data=_roster(("Caesar", "grudger"), ("Augustus", "diplomat")),
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert "added=2" in r.headers["location"]

    async with reset_db() as db:
        players = (
            (await db.execute(select(Player).where(Player.match_id == "G_bfv")))
            .scalars()
            .all()
        )
    assert sorted(p.seat_name for p in players) == ["Augustus", "Caesar"]


async def test_all_valid_strategies_accepted(client, reset_db) -> None:
    """Every known personality ID passes route-level validation."""
    from app.engine.bot_presets import BOT_PRESETS

    admin = await _seed_admin(reset_db)

    # One bot per preset, each in its own isolated game so IDs don't clash.
    for index, preset in enumerate(BOT_PRESETS):
        match_id = f"G_bfv_{index}"
        async with reset_db() as db:
            g = Match(
                id=match_id,
                name=f"Test {preset.id}",
                state=GameState.REGISTERING,
                scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
                max_players=20,
            )
            db.add(g)
            await db.commit()

        r = await client.post(
            f"/games/hoard-hurt-help/admin/matches/{match_id}/bots",
            data=_roster(("TestBot", preset.id)),
            cookies=_cookies(admin.id),
            follow_redirects=False,
        )
        assert r.status_code == 303, (
            f"Strategy {preset.id!r} was unexpectedly rejected: {r.status_code}"
        )


# ---------------------------------------------------------------------------
# Non-bot paths are unchanged — AI agent creation is not affected
# ---------------------------------------------------------------------------


async def test_ai_agent_creation_is_unaffected(client, reset_db) -> None:
    """The /me/agents/new route for AI agents is not gated by bot validation."""
    from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
    from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key

    async with reset_db() as db:
        u = User(google_sub="sub-ai", email="ai@test.com", name="AI User", handle="aiuser", handle_key="aiuser")
        db.add(u)
        await db.flush()
        plain_key = generate_connection_key()
        conn = Connection(
            user_id=u.id,
            provider=ConnectionProvider.CLAUDE,
            key_lookup=bot_key_lookup(plain_key),
            key_hint=bot_key_hint(plain_key),
            status=ConnectionStatus.ACTIVE,
            mcp_connected_at=datetime.now(timezone.utc),  # set up (MCP-recent)
        )
        db.add(conn)
        await db.flush()
        from app.models.connection_provider import ConnectionProvider as _CPRow

        db.add(_CPRow(connection_id=conn.id, provider=ConnectionProvider.CLAUDE, enabled=True, detected=False))
        await db.commit()
        uid = u.id

    cookies = _cookies(uid)

    r = await client.post(
        "/me/agents/new",
        data={
            "name": "My AI Agent",
            "strategy_text": "Play to win.",
        },
        cookies=cookies,
        follow_redirects=False,
    )
    # 303 redirect to the lobby means creation succeeded (no bot-validation gate).
    assert r.status_code == 303
    assert r.headers["location"] == "/games/hoard-hurt-help"
