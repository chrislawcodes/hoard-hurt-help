"""Admin "Add Sims" flow — the form, seating, validation, and labelling."""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner
from sqlalchemy import select

from app.config import settings
from app.engine.sims.seating import SIMS_USER_SUB
from app.main import app
from app.models import Base, Bot, BotKind, Match, GameState, Player, StrategyPrompt, User
from tests.factories import make_bot


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


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _seed_user(reset_db, email: str) -> User:
    async with reset_db() as db:
        u = User(google_sub=f"sub-{email}", email=email, name=email)
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


async def _seed_game(
    reset_db,
    *,
    state: GameState = GameState.REGISTERING,
    max_players: int = 20,
    match_id: str = "G_001",
) -> Match:
    async with reset_db() as db:
        g = Match(
            id=match_id,
            name="Friday Test",
            state=state,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            max_players=max_players,
        )
        db.add(g)
        await db.commit()
        await db.refresh(g)
        return g


def _roster(*pairs: tuple[str, str]) -> dict[str, list[str]]:
    """Build the parallel-array form body (httpx encodes dict-of-lists as
    repeated fields, preserving order)."""
    return {
        "seat_name": [name for name, _ in pairs],
        "seat_strategy": [strategy for _, strategy in pairs],
    }


@pytest.mark.asyncio
async def test_form_renders_with_personalities(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    await _seed_game(reset_db)
    r = await client.get("/admin/games/G_001/sims", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert "Add Sims" in r.text
    assert "Grudger" in r.text
    assert "Fill remaining seats" in r.text


@pytest.mark.asyncio
async def test_non_admin_blocked(client, reset_db):
    user = await _seed_user(reset_db, "regular@test.com")
    await _seed_game(reset_db)
    r = await client.get(
        "/admin/games/G_001/sims", cookies=_cookies(user.id), follow_redirects=False
    )
    assert r.status_code == 403
    r2 = await client.post(
        "/admin/games/G_001/sims",
        data=_roster(("Zeus", "grudger")),
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r2.status_code == 403


@pytest.mark.asyncio
async def test_seats_sims_as_players(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    await _seed_game(reset_db)
    r = await client.post(
        "/admin/games/G_001/sims",
        data=_roster(("Zeus", "grudger"), ("Hera", "grudger"), ("Athena", "diplomat")),
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/games/G_001?added=3"

    async with reset_db() as db:
        players = (
            (await db.execute(select(Player).where(Player.match_id == "G_001")))
            .scalars()
            .all()
        )
        assert sorted(p.agent_id for p in players) == ["Athena", "Hera", "Zeus"]

        sims_user = (
            await db.execute(select(User).where(User.google_sub == SIMS_USER_SUB))
        ).scalar_one()
        bots = {
            b.id: b
            for b in (
                await db.execute(select(Bot).where(Bot.id.in_([p.bot_id for p in players])))
            )
            .scalars()
            .all()
        }
        # Every Sim has its own backing bot, owned by the internal Sims user,
        # carrying the personality's traits and a distinct seed.
        for p in players:
            bot = bots[p.bot_id]
            assert bot.kind == BotKind.SIM
            assert bot.user_id == sims_user.id
            assert p.user_id == sims_user.id
            assert bot.sim_seed is not None
        grudgers = [b for b in bots.values() if b.sim_strategy == "grudger"]
        assert len(grudgers) == 2
        assert grudgers[0].sim_seed != grudgers[1].sim_seed  # distinct seeds

        # A strategy prompt is stored so exports/admin views can label the Sim.
        prompts = (
            (await db.execute(select(StrategyPrompt))).scalars().all()
        )
        assert len(prompts) == 3
        assert all("[Sim]" in pr.prompt_text for pr in prompts)


@pytest.mark.asyncio
async def test_rejects_over_cap(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    await _seed_game(reset_db, max_players=3)
    # One human already seated → only 2 free seats.
    async with reset_db() as db:
        u = User(google_sub="human", email="human@test.com")
        db.add(u)
        await db.flush()
        bot, _ = await make_bot(db, u, name="AI_human")
        db.add(Player(match_id="G_001", user_id=u.id, bot_id=bot.id, agent_id="Human1"))
        await db.commit()

    r = await client.post(
        "/admin/games/G_001/sims",
        data=_roster(("Zeus", "grudger"), ("Hera", "diplomat"), ("Ares", "opportunist")),
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "cap" in r.text
    async with reset_db() as db:
        count = len(
            (await db.execute(select(Player).where(Player.match_id == "G_001"))).scalars().all()
        )
    assert count == 1  # nothing seated


@pytest.mark.asyncio
async def test_rejects_duplicate_name(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    await _seed_game(reset_db)
    async with reset_db() as db:
        u = User(google_sub="human", email="human@test.com")
        db.add(u)
        await db.flush()
        bot, _ = await make_bot(db, u, name="AI_human")
        db.add(Player(match_id="G_001", user_id=u.id, bot_id=bot.id, agent_id="Zeus"))
        await db.commit()

    r = await client.post(
        "/admin/games/G_001/sims",
        data=_roster(("Zeus", "grudger")),
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "already taken" in r.text


@pytest.mark.asyncio
async def test_rejects_invalid_name(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    await _seed_game(reset_db)
    r = await client.post(
        "/admin/games/G_001/sims",
        data=_roster(("Bad Name", "grudger")),
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "valid name" in r.text


@pytest.mark.asyncio
async def test_rejects_empty_roster(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    await _seed_game(reset_db)
    r = await client.post(
        "/admin/games/G_001/sims",
        data={},
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "at least one Sim" in r.text


@pytest.mark.asyncio
async def test_cannot_add_after_start(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    await _seed_game(reset_db, state=GameState.ACTIVE)
    # The form explains it's closed.
    form = await client.get("/admin/games/G_001/sims", cookies=_cookies(admin.id))
    assert "before a game starts" in form.text
    # And the POST refuses to seat.
    r = await client.post(
        "/admin/games/G_001/sims",
        data=_roster(("Zeus", "grudger")),
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 409
    async with reset_db() as db:
        count = len(
            (await db.execute(select(Player).where(Player.match_id == "G_001"))).scalars().all()
        )
    assert count == 0


@pytest.mark.asyncio
async def test_detail_labels_sims_and_shows_banner(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    await _seed_game(reset_db)
    await client.post(
        "/admin/games/G_001/sims",
        data=_roster(("Zeus", "grudger")),
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    r = await client.get("/admin/games/G_001?added=1", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert "Added 1 Sim." in r.text
    assert "Zeus" in r.text
    assert "Grudger" in r.text  # personality column
