"""Tests for creation-time bot profile validation.

Covers:
- validate_bot_profile_fields rejects missing or unknown fields.
- build_bot_profile rejects the same.
- add_bots_to_game (seating) rejects a preset that would produce an invalid
  profile, rather than silently skipping at play-time.
- A valid bot passes all checks end-to-end.
- All system-defined presets (BOT_PRESETS and BOT_PACKS) produce valid profiles.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.engine.bot_presets import BOT_PRESETS
from app.engine.bots import validate_bot_profile_fields
from app.engine.bots.presets import BOT_PACKS, resolve_profile_choice
from app.engine.bots.runtime import build_bot_profile
from app.engine.bots.seating import BotSeatingError, add_bots_to_game
from app.engine.bots.strategies import VALID_STRATEGIES
from app.models.agent import Agent, AgentKind
from app.models.match import GameState, Match


# ---------------------------------------------------------------------------
# validate_bot_profile_fields — unit tests (no DB needed)
# ---------------------------------------------------------------------------


def _valid_kwargs() -> dict:
    return {
        "kind": AgentKind.BOT,
        "bot_strategy": "grudger",
        "bot_truthfulness": 80,
        "bot_trust_model": "bitter",
        "bot_seed": 42,
        "bot_version": "v1",
    }


def test_validate_bot_profile_fields_passes_for_valid_input() -> None:
    validate_bot_profile_fields(**_valid_kwargs())


@pytest.mark.parametrize(
    "field",
    ["bot_strategy", "bot_truthfulness", "bot_trust_model", "bot_seed", "bot_version"],
)
def test_validate_bot_profile_fields_rejects_null_field(field: str) -> None:
    kwargs = _valid_kwargs()
    kwargs[field] = None
    with pytest.raises(ValueError, match=field):
        validate_bot_profile_fields(**kwargs)


def test_validate_bot_profile_fields_rejects_non_bot_kind() -> None:
    kwargs = _valid_kwargs()
    kwargs["kind"] = AgentKind.AI
    with pytest.raises(ValueError, match="not a bot"):
        validate_bot_profile_fields(**kwargs)


def test_validate_bot_profile_fields_rejects_unknown_strategy() -> None:
    kwargs = _valid_kwargs()
    kwargs["bot_strategy"] = "galaxy_brain"
    with pytest.raises(ValueError, match="unknown bot strategy"):
        validate_bot_profile_fields(**kwargs)


def test_validate_bot_profile_fields_accepts_all_known_strategies() -> None:
    for strategy in VALID_STRATEGIES:
        kwargs = _valid_kwargs()
        kwargs["bot_strategy"] = strategy
        validate_bot_profile_fields(**kwargs)


# ---------------------------------------------------------------------------
# build_bot_profile — integration tests via in-memory DB
# (build_bot_profile delegates to validate_bot_profile_fields so the same
# logic is exercised; using a real persisted Agent avoids ORM init quirks)
# ---------------------------------------------------------------------------


async def test_build_bot_profile_succeeds_for_valid_agent(reset_db) -> None:
    async with reset_db() as db:
        match = await _seed_match(db, "M_bp")
        created = await add_bots_to_game(db, match, [("Caesar", "grudger")])
        player = created[0]
        from sqlalchemy import select

        agent = (
            await db.execute(select(Agent).where(Agent.id == player.agent_id))
        ).scalar_one()
        profile = build_bot_profile(agent)
        assert profile.strategy == "grudger"
        assert profile.truthfulness == 92  # Long Memory preset
        assert profile.seed == agent.bot_seed


async def test_build_bot_profile_rejects_missing_strategy(reset_db) -> None:
    async with reset_db() as db:
        match = await _seed_match(db, "M_bp2")
        created = await add_bots_to_game(db, match, [("Caesar", "grudger")])
        player = created[0]
        from sqlalchemy import select

        agent = (
            await db.execute(select(Agent).where(Agent.id == player.agent_id))
        ).scalar_one()
        agent.bot_strategy = None
        with pytest.raises(ValueError, match="bot_strategy"):
            build_bot_profile(agent)


async def test_build_bot_profile_rejects_unknown_strategy(reset_db) -> None:
    async with reset_db() as db:
        match = await _seed_match(db, "M_bp3")
        created = await add_bots_to_game(db, match, [("Caesar", "grudger")])
        player = created[0]
        from sqlalchemy import select

        agent = (
            await db.execute(select(Agent).where(Agent.id == player.agent_id))
        ).scalar_one()
        agent.bot_strategy = "galaxy_brain"
        with pytest.raises(ValueError, match="unknown bot strategy"):
            build_bot_profile(agent)


# ---------------------------------------------------------------------------
# Preset coverage — all system-defined presets must produce valid profiles
# ---------------------------------------------------------------------------


def test_all_bot_presets_produce_valid_profiles() -> None:
    """Every preset in BOT_PRESETS must pass validation with a non-None seed."""
    for preset in BOT_PRESETS:
        validate_bot_profile_fields(
            kind=AgentKind.BOT,
            bot_strategy=preset.strategy,
            bot_truthfulness=preset.truthfulness,
            bot_trust_model=preset.trust_model,
            bot_seed=preset.seed_offset,  # positive int; seating replaces with agent.id
            bot_version="v1",
        )


def test_all_bot_pack_choices_produce_valid_profiles() -> None:
    """Every bot-pack profile choice must yield a valid BotProfile."""
    for pack_id, pack in BOT_PACKS.items():
        for index in range(len(pack.entries)):
            choice_id = f"{pack_id}:{index}"
            profile = resolve_profile_choice(choice_id, seed_base=100)
            # resolve_profile_choice uses VALID_STRATEGIES indirectly via the
            # BotProfile dataclass — we also run the explicit validation path.
            validate_bot_profile_fields(
                kind=AgentKind.BOT,
                bot_strategy=profile.strategy,
                bot_truthfulness=profile.truthfulness,
                bot_trust_model=profile.trust_model,
                bot_seed=profile.seed,
                bot_version=profile.version,
            )


# ---------------------------------------------------------------------------
# add_bots_to_game — integration tests (need in-memory DB)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    from app.db import make_engine
    from app.models import Base

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)

    yield test_factory
    await test_engine.dispose()


async def _seed_match(db, match_id: str = "M_test") -> Match:
    match = Match(
        id=match_id,
        name="Test Match",
        state=GameState.REGISTERING,
        scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        max_players=20,
        game="hoard-hurt-help",
    )
    db.add(match)
    await db.flush()
    return match


async def test_add_bots_to_game_succeeds_for_valid_preset(reset_db) -> None:
    async with reset_db() as db:
        match = await _seed_match(db)
        created = await add_bots_to_game(db, match, [("Caesar", "grudger")])
        assert len(created) == 1
        assert created[0].seat_name == "Caesar"
        from sqlalchemy import select

        agent = (
            await db.execute(select(Agent).where(Agent.id == created[0].agent_id))
        ).scalar_one()
        assert agent.name == f"{match.id}:Caesar"


async def test_add_bots_to_game_rejects_unknown_personality(reset_db) -> None:
    async with reset_db() as db:
        match = await _seed_match(db)
        with pytest.raises(BotSeatingError, match="Unknown personality"):
            await add_bots_to_game(db, match, [("Caesar", "galaxy_brain")])
