"""Conformance proof (SC-002): a brand-new game is added by implementing the
GameModule contract and registering it — touching ONLY the module + one
registration line. No platform, engine, or PD code changes.

The stub uses a novel move vocabulary ("MOVE") and a trivial rule (+1 per move),
and runs through the SAME generic storage (Turn/TurnSubmission/Player) and the
SAME registry the platform uses. The rest of the suite proves the platform's
scheduler/submit/viewer call only the module (PD behaves identically), so a
passing stub here means the framework is genuinely game-agnostic.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.games as registry
from app.db import make_engine
from app.engine.tokens import generate_turn_token
from app.games.base import GameConfig, GameError, StrategyPreset
from app.models import Base, Match, GameState, Player, Turn, TurnSubmission
from tests.factories import seat_player

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StubGame:
    """A trivial conformance game: every legal move scores its actor +1."""

    game_type = "stub"

    def display_name(self) -> str:
        return "Stub Showdown"

    def tagline(self) -> str:
        return "A trivial conformance game."

    def config_defaults(self) -> GameConfig:
        return GameConfig(
            total_rounds=1,
            turns_per_round=2,
            per_turn_deadline_seconds=30,
            min_players=2,
            max_players=4,
        )

    def rules_text(self, total_rounds: int = 10, turns_per_round: int = 10) -> str:
        return "Stub game: submit MOVE; each move scores you +1 point."

    def strategy_presets(self) -> list[StrategyPreset]:
        return []

    def default_strategy(self) -> str:
        return "Stub strategy: submit MOVE every turn."

    def agent_base_prompt(
        self,
        *,
        your_agent_id: str,
        all_agent_ids: list[str],
        total_rounds: int = 10,
        turns_per_round: int = 10,
    ) -> str:
        return f"You are {your_agent_id}. {self.rules_text(total_rounds, turns_per_round)}"

    def validate_move(
        self, move: dict[str, Any], *, your_agent_id: str, all_agent_ids: list[str]
    ) -> None:
        if str(move.get("action", "")).upper() != "MOVE":
            raise GameError("INVALID_MOVE", "Stub accepts only action=MOVE.")

    async def record_submission(
        self,
        db: AsyncSession,
        turn: Turn,
        player: Player,
        move: dict[str, Any],
        *,
        existing: TurnSubmission | None,
    ) -> None:
        if existing is not None:
            existing.action = "MOVE"
            existing.was_defaulted = False
            existing.submitted_at = _now()
        else:
            db.add(
                TurnSubmission(
                    turn_id=turn.id,
                    player_id=player.id,
                    action="MOVE",
                    message=str(move.get("message", "")),
                    submitted_at=_now(),
                )
            )

    async def resolve_turn(self, db: AsyncSession, turn: Turn) -> None:
        subs = (
            (
                await db.execute(
                    select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)
                )
            )
            .scalars()
            .all()
        )
        for s in subs:
            p = (
                await db.execute(select(Player).where(Player.id == s.player_id))
            ).scalar_one()
            p.current_round_score += 1
            p.total_round_score += 1
            s.points_delta = 1
            s.round_score_after = p.current_round_score
        turn.resolved_at = _now()
        await db.commit()

    async def award_round(self, db: AsyncSession, game: Match, round_num: int) -> None:
        players = (
            (await db.execute(select(Player).where(Player.match_id == game.id)))
            .scalars()
            .all()
        )
        if not players:
            return
        top = max(p.current_round_score for p in players)
        for p in players:
            if p.current_round_score == top:
                p.total_round_wins += 1
        await db.commit()

    async def finalize(self, db: AsyncSession, game: Match) -> None:
        players = (
            (await db.execute(select(Player).where(Player.match_id == game.id)))
            .scalars()
            .all()
        )
        game.state = GameState.COMPLETED
        game.completed_at = _now()
        if players:
            game.winner_player_id = max(players, key=lambda p: p.total_round_score).id
        await db.commit()

    def move_effect(self, action: str) -> tuple[int, int | None]:
        return (1, None) if action.upper() == "MOVE" else (0, None)


@pytest.fixture(autouse=True)
def _register_stub_game() -> Iterator[None]:
    """Register the stub game for this module's tests, then remove it.

    A real game registers itself on import — exactly how PD does in
    app/games/__init__.py. The stub does it in a fixture instead so it never
    leaks into other test modules' view of the registry.
    """
    registry.register(StubGame())
    try:
        yield
    finally:
        registry.unregister("stub")


async def test_stub_registers_without_touching_pd() -> None:
    assert "stub" in registry.known_types()
    assert "hoard-hurt-help" in registry.known_types()  # PD still registered
    module = registry.get("stub")
    assert module.game_type == "stub"
    assert module.config_defaults().turns_per_round == 2


async def test_stub_rejects_illegal_move() -> None:
    module = registry.get("stub")
    with pytest.raises(GameError):
        module.validate_move(
            {"action": "HOARD"}, your_agent_id="A", all_agent_ids=["A", "B"]
        )


def test_catalog_and_leaderboard_read_display_from_module() -> None:
    """The catalog and leaderboard get a game's title/tagline from its module,
    not a game_type branch. A brand-new game (the stub) declares its own strings
    and both surfaces pick them up with zero platform edits."""
    from app.read_models.leaderboard import (
        _game_display_name as leaderboard_display_name,
    )
    from app.routes.web_games_catalog import (
        _game_display_name as catalog_display_name,
    )
    from app.routes.web_games_catalog import _game_tagline as catalog_tagline

    assert catalog_display_name("stub") == "Stub Showdown"
    assert catalog_tagline("stub") == "A trivial conformance game."
    assert leaderboard_display_name("stub") == "Stub Showdown"

    # PD's stylized title still comes through unchanged.
    assert catalog_display_name("hoard-hurt-help") == "Hoard · Hurt · Help"
    assert leaderboard_display_name("hoard-hurt-help") == "Hoard · Hurt · Help"

    # An unregistered legacy type falls back to the humanized game_type.
    assert catalog_display_name("ancient-game") == "Ancient Game"
    assert catalog_tagline("ancient-game") == ""
    assert leaderboard_display_name("ancient-game") == "Ancient Game"


async def test_stub_game_plays_resolves_and_scores() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    module = registry.get("stub")
    cfg = module.config_defaults()

    async with factory() as db:
        game = Match(
            id="G_STUB",
            name="stub",
            game="stub",
            state=GameState.ACTIVE,
            scheduled_start=_now(),
            total_rounds=cfg.total_rounds,
            turns_per_round=cfg.turns_per_round,
            min_players=cfg.min_players,
            max_players=cfg.max_players,
            per_turn_deadline_seconds=cfg.per_turn_deadline_seconds,
        )
        db.add(game)
        await db.flush()
        players = [await seat_player(db, game.id, f"S_{i}", i=i) for i in range(2)]
        agent_ids = [p.agent_id for p in players]
        await db.commit()

        # One turn: every player submits the novel "MOVE".
        turn = Turn(
            match_id=game.id,
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=_now(),
            deadline_at=_now() + timedelta(seconds=30),
        )
        db.add(turn)
        await db.flush()

        move = {"action": "MOVE", "message": ""}
        for p in players:
            module.validate_move(
                move, your_agent_id=p.agent_id, all_agent_ids=agent_ids
            )
            await module.record_submission(db, turn, p, move, existing=None)
        await db.commit()

        await module.resolve_turn(db, turn)
        await module.award_round(db, game, 1)
        await module.finalize(db, game)

        refreshed = (
            (await db.execute(select(Player).where(Player.match_id == game.id)))
            .scalars()
            .all()
        )
        assert all(p.total_round_score == 1 for p in refreshed)
        assert all(p.current_round_score == 1 for p in refreshed)
        g = (
            await db.execute(select(Match).where(Match.id == game.id))
        ).scalar_one()
        assert g.state == GameState.COMPLETED
        assert g.winner_player_id in {p.id for p in refreshed}

    await engine.dispose()
