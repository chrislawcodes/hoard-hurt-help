"""SequentialDriver conformance: a sequential, hidden-state game plays end to end.

Proves the new platform seams work without PD: single-actor turns driven by
`next_actor`, a round that ends when `next_actor` returns None, per-match and
per-player generic state (`match_state` / `player_state`) read and written by the
module, private state via `private_state_for`, and the round/match hooks
(`on_round_start`, `award_round`, `is_match_over`, `finalize`). The driver is
game-agnostic — this stub touches only its own module + the generic tables.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.engine.turn_drivers import SequentialDriver
from app.games.base import BaseGameModule, GameConfig
from app.models import Base, Match, GameState, MatchState, PlayerState, Player
from app.models.agent import AgentKind
from app.models.turn import TurnSubmission
from tests.factories import make_bot, make_user, seat_player


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _SeqStub(BaseGameModule):
    """One hand: each still-in player acts once in seat order; each move scores +1.

    Exercises the generic state store: match_state holds the turn order + cursor;
    player_state holds a per-player 'secret' (a stand-in for hidden dice).
    """

    game_type = "seq-stub"

    def config_defaults(self) -> GameConfig:
        return GameConfig(
            total_rounds=1,
            turns_per_round=16,
            per_turn_deadline_seconds=30,
            min_players=2,
            max_players=6,
            simultaneous=False,
        )

    async def _state(self, db: Any, match: Match) -> MatchState:
        ms = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one_or_none()
        if ms is None:
            ms = MatchState(match_id=match.id, state_json={})
            db.add(ms)
            await db.flush()
        return ms

    async def on_round_start(self, db: Any, match: Match, round_num: int) -> None:
        players = (
            (await db.execute(select(Player).where(Player.match_id == match.id)))
            .scalars()
            .all()
        )
        order = sorted(p.seat_name for p in players)
        ms = await self._state(db, match)
        ms.state_json = {"order": order, "idx": 0, "hands_played": ms.state_json.get("hands_played", 0)}
        # Deal a private "secret" to each player (stand-in for hidden dice).
        for i, p in enumerate(players):
            db.add(PlayerState(match_id=match.id, player_id=p.id, state_json={"secret": i + 1}))
        await db.commit()

    async def next_actor(self, db: Any, match: Match) -> str | None:
        ms = await self._state(db, match)
        order = ms.state_json["order"]
        idx = ms.state_json["idx"]
        return order[idx] if idx < len(order) else None

    async def default_move(self, db: Any, match: Match, player: Player) -> dict[str, Any]:
        return {"action": "MOVE"}

    async def record_submission(
        self, db: Any, turn: Any, player: Player, move: dict[str, Any], *,
        existing: Any = None, is_connector_fallback: bool = False,
    ) -> None:
        db.add(TurnSubmission(
            turn_id=turn.id, player_id=player.id, action="MOVE", submitted_at=_now(),
        ))
        ms = (await db.execute(select(MatchState).where(MatchState.match_id == turn.match_id))).scalar_one()
        ms.state_json["idx"] = ms.state_json["idx"] + 1
        player.current_round_score += 1
        player.total_round_score += 1
        await db.flush()

    async def resolve_turn(self, db: Any, turn: Any) -> None:
        turn.resolved_at = _now()
        await db.commit()

    async def award_round(self, db: Any, match: Match, round_num: int) -> None:
        ms = await self._state(db, match)
        ms.state_json["hands_played"] = ms.state_json.get("hands_played", 0) + 1
        await db.commit()

    async def is_match_over(self, db: Any, match: Match) -> bool:
        ms = await self._state(db, match)
        return ms.state_json.get("hands_played", 0) >= 1

    async def finalize(self, db: Any, match: Match) -> None:
        players = (
            (await db.execute(select(Player).where(Player.match_id == match.id)))
            .scalars()
            .all()
        )
        match.state = GameState.COMPLETED
        match.completed_at = _now()
        if players:
            match.winner_player_id = max(players, key=lambda p: p.total_round_score).id
        await db.commit()


async def test_sequential_driver_plays_a_hidden_state_match() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    module = _SeqStub()

    async with factory() as db:
        match = Match(
            id="M_SEQ",
            name="seq",
            game="seq-stub",
            state=GameState.ACTIVE,
            scheduled_start=_now(),
            total_rounds=1,
            turns_per_round=16,
            # 0s deadline: these are AI-agent seats with no live submitter, so each
            # turn falls straight through to the missed-turn default (no waiting).
            per_turn_deadline_seconds=0,
            current_round=0,
            current_turn=0,
        )
        db.add(match)
        await db.flush()
        for i in range(3):
            await seat_player(db, match.id, f"S{i}", i=i)
        await db.commit()

        await SequentialDriver().run_match(db, match, module)

        refreshed = (
            (await db.execute(select(Player).where(Player.match_id == match.id)))
            .scalars()
            .all()
        )
        # Each of the 3 players acted exactly once → +1 each.
        assert sorted(p.total_round_score for p in refreshed) == [1, 1, 1]

        m = (await db.execute(select(Match).where(Match.id == "M_SEQ"))).scalar_one()
        assert m.state == GameState.COMPLETED
        assert m.winner_player_id in {p.id for p in refreshed}

        # The hand was awarded, and private per-player state was written.
        ms = (await db.execute(select(MatchState).where(MatchState.match_id == "M_SEQ"))).scalar_one()
        assert ms.state_json["hands_played"] == 1
        ps_rows = (await db.execute(select(PlayerState).where(PlayerState.match_id == "M_SEQ"))).scalars().all()
        assert len(ps_rows) == 3
        assert all("secret" in ps.state_json for ps in ps_rows)

    await engine.dispose()


async def _seat_bot(db: Any, match_id: str, seat_name: str, i: int) -> Player:
    """Seat a scripted bot (kind=bot) player — no connection, no live submitter."""
    user = await make_user(db, 100 + i)
    agent, _ = await make_bot(db, user, name=seat_name, kind=AgentKind.BOT)
    player = Player(match_id=match_id, user_id=user.id, agent_id=agent.id, seat_name=seat_name)
    db.add(player)
    await db.flush()
    return player


async def test_sequential_driver_bots_auto_submit_without_waiting() -> None:
    """Bot actors are submitted on the platform's behalf — no deadline wait.

    The deadline is long (300s); if the driver were waiting on a live submission
    the match would hang. Completing instantly proves the bot path does not wait.
    """
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    module = _SeqStub()

    async with factory() as db:
        match = Match(
            id="M_SEQ_BOT", name="seqbot", game="seq-stub", state=GameState.ACTIVE,
            scheduled_start=_now(), total_rounds=1, turns_per_round=16,
            per_turn_deadline_seconds=300, current_round=0, current_turn=0,
        )
        db.add(match)
        await db.flush()
        for i in range(3):
            await _seat_bot(db, match.id, f"B{i}", i)
        await db.commit()

        await SequentialDriver().run_match(db, match, module)

        refreshed = (
            (await db.execute(select(Player).where(Player.match_id == match.id)))
            .scalars()
            .all()
        )
        assert sorted(p.total_round_score for p in refreshed) == [1, 1, 1]
        m = (await db.execute(select(Match).where(Match.id == "M_SEQ_BOT"))).scalar_one()
        assert m.state == GameState.COMPLETED

    await engine.dispose()
