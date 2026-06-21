"""Slice 1 — the human move-in path.

The web play routes record a human's talk/act through the same GameModule verbs
agents and bots use, guarded by session auth + seat ownership + phase/deadline.
Plus: a leaver's seat auto-Hoards immediately, and an untouched human defaults to
Hoard at resolution.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from itsdangerous import TimestampSigner
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.engine.bots.service import auto_submit_bot_phase
from app.engine.human_player import get_or_create_human_agent
from app.engine.tokens import generate_turn_token
from app.games import get as get_game_module
from app.models import Base, GameState, Match, Player, User
from app.models.turn import Turn, TurnMessage, TurnSubmission
from tests.factories import make_user, seat_player

GAME = "hoard-hurt-help"


async def make_match(db, match_id: str, *, state: GameState) -> Match:
    """Local match factory: the shared one omits the NOT NULL scheduled_start."""
    match = Match(
        id=match_id,
        name=f"Match {match_id}",
        game=GAME,
        state=state,
        scheduled_start=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
    )
    db.add(match)
    await db.flush()
    return match


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    monkeypatch.setattr("app.db.engine", engine)
    yield factory
    await engine.dispose()


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _open_turn(db, match_id: str, phase: str, *, deadline_offset: int = 60) -> Turn:
    now = datetime.now(timezone.utc)
    turn = Turn(
        match_id=match_id,
        round=1,
        turn=1,
        turn_token=generate_turn_token(),
        opened_at=now,
        deadline_at=now + timedelta(seconds=deadline_offset),
        phase=phase,
    )
    db.add(turn)
    await db.flush()
    return turn


async def _seat_human(db, match_id: str, user: User, seat_name: str) -> Player:
    agent, version = await get_or_create_human_agent(db, user, GAME)
    player = Player(
        match_id=match_id,
        user_id=user.id,
        agent_id=agent.id,
        agent_version_id=version.id,
        seat_name=seat_name,
    )
    db.add(player)
    await db.flush()
    return player


# --- HTTP route tests ------------------------------------------------------


async def test_human_act_records_help_with_target(reset_db, client) -> None:
    async with reset_db() as db:
        human_user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, human_user, "alice")
        bob = await seat_player(db, match.id, "bob", i=2)
        turn = await _open_turn(db, match.id, "act")
        await db.commit()
        bob_agent_id = bob.agent_id
        turn_id = turn.id

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/act",
        data={"action": "HELP", "target": "bob"},
        cookies=_cookies(human_user.id),
    )
    assert r.status_code == 200, r.text

    async with reset_db() as db:
        sub = (
            await db.execute(
                select(TurnSubmission).where(TurnSubmission.turn_id == turn_id)
            )
        ).scalar_one()
        assert sub.action == "HELP"
        assert sub.target_player_id is not None
        assert sub.was_defaulted is False
        # target stored as the internal player id of bob's agent's player
        bob_player = (
            await db.execute(select(Player).where(Player.agent_id == bob_agent_id))
        ).scalar_one()
        assert sub.target_player_id == bob_player.id


async def test_human_hoard_takes_no_target(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, user, "alice")
        await _open_turn(db, match.id, "act")
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/act",
        data={"action": "HOARD"},
        cookies=_cookies(user.id),
    )
    assert r.status_code == 200, r.text
    async with reset_db() as db:
        sub = (await db.execute(select(TurnSubmission))).scalar_one()
        assert sub.action == "HOARD"
        assert sub.target_player_id is None


async def test_reselect_replaces_pending_choice(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, user, "alice")
        await seat_player(db, match.id, "bob", i=2)
        await _open_turn(db, match.id, "act")
        await db.commit()

    await client.post(
        f"/games/{GAME}/matches/M_0001/play/act",
        data={"action": "HOARD"},
        cookies=_cookies(user.id),
    )
    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/act",
        data={"action": "HELP", "target": "bob"},
        cookies=_cookies(user.id),
    )
    assert r.status_code == 200, r.text
    async with reset_db() as db:
        subs = (await db.execute(select(TurnSubmission))).scalars().all()
        assert len(subs) == 1  # replaced, not appended
        assert subs[0].action == "HELP"


async def test_illegal_move_rejected_records_nothing(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, user, "alice")
        await _open_turn(db, match.id, "act")
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/act",
        data={"action": "HELP"},  # HELP with no target
        cookies=_cookies(user.id),
    )
    assert r.status_code == 400
    async with reset_db() as db:
        subs = (await db.execute(select(TurnSubmission))).scalars().all()
        assert subs == []


async def test_talk_pass_records_empty_message(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, user, "alice")
        await _open_turn(db, match.id, "talk")
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/talk",
        data={},  # Pass: no message
        cookies=_cookies(user.id),
    )
    assert r.status_code == 200, r.text
    async with reset_db() as db:
        msg = (await db.execute(select(TurnMessage))).scalar_one()
        assert msg.text == ""
        assert msg.was_defaulted is False


async def test_talk_message_recorded(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, user, "alice")
        await _open_turn(db, match.id, "talk")
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/talk",
        data={"message": "Pact?"},
        cookies=_cookies(user.id),
    )
    assert r.status_code == 200, r.text
    async with reset_db() as db:
        msg = (await db.execute(select(TurnMessage))).scalar_one()
        assert msg.text == "Pact?"


async def test_non_owner_cannot_play(reset_db, client) -> None:
    async with reset_db() as db:
        owner = await make_user(db, 1)
        intruder = await make_user(db, 99)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, owner, "alice")
        await _open_turn(db, match.id, "act")
        await db.commit()
        intruder_id = intruder.id

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/act",
        data={"action": "HOARD"},
        cookies=_cookies(intruder_id),
    )
    assert r.status_code == 403


async def test_signed_out_cannot_play(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, user, "alice")
        await _open_turn(db, match.id, "act")
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/act", data={"action": "HOARD"}
    )
    assert r.status_code == 401


async def test_post_deadline_refused(reset_db, client) -> None:
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, user, "alice")
        await _open_turn(db, match.id, "act", deadline_offset=-5)  # already passed
        await db.commit()

    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/act",
        data={"action": "HOARD"},
        cookies=_cookies(user.id),
    )
    assert r.status_code in (409, 410)
    async with reset_db() as db:
        assert (await db.execute(select(TurnSubmission))).scalars().all() == []


# --- engine tests ----------------------------------------------------------


async def test_mixed_turn_resolves_when_all_act(reset_db, client) -> None:
    """A human + a bot both act, then the turn resolves and scores both."""
    async with reset_db() as db:
        human_user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, human_user, "alice")
        # a bot seat built from a real preset so its profile is valid
        bots_user = await make_user(db, 50)
        from app.engine.bot_presets import bot_preset_by_id
        from app.models.agent import Agent, AgentKind

        preset = bot_preset_by_id("coalition_seeker")
        assert preset is not None
        bot_agent = Agent(
            user_id=bots_user.id,
            name="M_0001:carol",
            kind=AgentKind.BOT,
            game=GAME,
            bot_profile_id=preset.id,
            bot_profile_name=preset.name,
            bot_strategy=preset.strategy,
            bot_truthfulness=preset.truthfulness,
            bot_trust_model=preset.trust_model,
            bot_version="v1",
        )
        db.add(bot_agent)
        await db.flush()
        bot_agent.bot_seed = bot_agent.id
        db.add(Player(match_id=match.id, user_id=bots_user.id, agent_id=bot_agent.id, seat_name="carol"))
        turn = await _open_turn(db, match.id, "act")
        await db.commit()
        turn_id = turn.id

    # the human acts via the route
    r = await client.post(
        f"/games/{GAME}/matches/M_0001/play/act",
        data={"action": "HOARD"},
        cookies=_cookies(human_user.id),
    )
    assert r.status_code == 200, r.text

    async with reset_db() as db:
        match = (await db.execute(select(Match))).scalar_one()
        turn = (await db.execute(select(Turn).where(Turn.id == turn_id))).scalar_one()
        module = get_game_module(GAME)
        # bot auto-submits its action, then the turn resolves
        await auto_submit_bot_phase(db, match, turn, module, phase="act")
        await module.resolve_turn(db, turn)
        await db.commit()

    async with reset_db() as db:
        turn = (await db.execute(select(Turn).where(Turn.id == turn_id))).scalar_one()
        assert turn.resolved_at is not None
        subs = (await db.execute(select(TurnSubmission))).scalars().all()
        assert len(subs) == 2
        assert all(not s.was_defaulted for s in subs)


async def test_autopilot_human_auto_hoards_immediately(reset_db, client) -> None:
    """A human who left (autopilot_at set) gets an immediate Hoard, not a wait."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        player = await _seat_human(db, match.id, user, "alice")
        player.autopilot_at = datetime.now(timezone.utc)
        turn = await _open_turn(db, match.id, "act")
        await db.commit()
        turn_id = turn.id

    async with reset_db() as db:
        match = (await db.execute(select(Match))).scalar_one()
        turn = (await db.execute(select(Turn).where(Turn.id == turn_id))).scalar_one()
        module = get_game_module(GAME)
        posted = await auto_submit_bot_phase(db, match, turn, module, phase="act")
        await db.commit()
        assert posted == 1
        sub = (await db.execute(select(TurnSubmission))).scalar_one()
        assert sub.action == "HOARD"
        assert sub.was_defaulted is False  # counts as submitted -> never waited on


async def test_untouched_human_defaults_to_hoard(reset_db, client) -> None:
    """A human who does nothing is defaulted to Hoard when the turn resolves."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, user, "alice")
        turn = await _open_turn(db, match.id, "act")
        await db.commit()
        turn_id = turn.id

    async with reset_db() as db:
        turn = (await db.execute(select(Turn).where(Turn.id == turn_id))).scalar_one()
        module = get_game_module(GAME)
        await module.resolve_turn(db, turn)
        await db.commit()

    async with reset_db() as db:
        sub = (await db.execute(select(TurnSubmission))).scalar_one()
        assert sub.action == "HOARD"
        assert sub.was_defaulted is True


# --- cockpit layout (spec 018) ---------------------------------------------


async def test_player_mode_renders_cockpit(reset_db, client) -> None:
    """A seated human on an open turn gets the play cockpit: the live region is
    tagged `cockpit`, with collapsible standings and the docked play panel."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, user, "alice")
        await seat_player(db, match.id, "bob", i=2)
        await _open_turn(db, match.id, "act")
        await db.commit()

    r = await client.get(f"/games/{GAME}/matches/M_0001", cookies=_cookies(user.id))
    assert r.status_code == 200, r.text
    html = r.text
    assert "view-cards cockpit" in html
    assert 'id="play-standings"' in html
    assert 'id="play-panel"' in html


async def test_spectator_gets_no_cockpit(reset_db, client) -> None:
    """A viewer with no seat sees the normal layout, not the play cockpit."""
    async with reset_db() as db:
        user = await make_user(db, 1)
        match = await make_match(db, "M_0001", state=GameState.ACTIVE)
        await _seat_human(db, match.id, user, "alice")
        await _open_turn(db, match.id, "act")
        await db.commit()

    # signed out → not the seated human → no cockpit
    r = await client.get(f"/games/{GAME}/matches/M_0001")
    assert r.status_code == 200, r.text
    assert "view-cards cockpit" not in r.text


def test_user_created_matches_cap_at_ten() -> None:
    """Spec 018: the normal create path tops out at 10 players."""
    from app.routes.matches_user import _CREATE_DEFAULTS

    assert _CREATE_DEFAULTS["max_players"] == 10
