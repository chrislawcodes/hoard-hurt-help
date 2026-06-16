"""Liar's Dice module."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from random import Random
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.games.base import BaseGameModule, GameConfig, GameError, GameTheme, StrategyPreset
from app.games.liars_dice.engine import (
    Bid,
    BidMove,
    ChallengeMove,
    is_legal_raise,
    min_legal_raise,
    parse_move,
    resolve_showdown,
    roll,
)
from app.games.liars_dice.rules_text import make_game_rules_text, make_rules_text
from app.games.liars_dice.strategy import LD_DEFAULT_STRATEGY, LD_STRATEGY_PRESETS
from app.models.game_state import MatchState, PlayerState
from app.models.match import GameState, Match
from app.models.player import Player
from app.models.turn import TurnSubmission

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_config() -> dict[str, Any]:
    return {"wild_ones": True, "dice_per_player": 5}


def _state_template() -> dict[str, Any]:
    return {
        "config": _default_config(),
        "hand": 0,
        "seat_order": [],
        "standing_bid": None,
        "active_actor": None,
        "challenge_pending": False,
        "challenger": None,
        "next_leader": None,
        "last_showdown": None,
        "showdowns": [],
        "bid_history": [],
        "elimination_order": [],
        "showdown_resolved_hand": 0,
    }


def _load_config(state_json: dict[str, Any]) -> dict[str, Any]:
    config = _default_config()
    raw = state_json.get("config")
    if isinstance(raw, dict):
        if isinstance(raw.get("wild_ones"), bool):
            config["wild_ones"] = raw["wild_ones"]
        dice = raw.get("dice_per_player")
        if isinstance(dice, int) and not isinstance(dice, bool) and dice > 0:
            config["dice_per_player"] = dice
    return config


def _ensure_state_defaults(state_json: dict[str, Any]) -> dict[str, Any]:
    template = _state_template()
    for key, value in template.items():
        if key not in state_json:
            state_json[key] = copy.deepcopy(value)
    state_json["config"] = _load_config(state_json)
    return state_json


async def _load_match(db: AsyncSession, match_id: str) -> Match:
    return (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()


async def _load_state(
    db: AsyncSession, match_id: str, *, create: bool = False
) -> MatchState | None:
    state = (
        await db.execute(select(MatchState).where(MatchState.match_id == match_id))
    ).scalar_one_or_none()
    if state is None and create:
        state = MatchState(match_id=match_id, state_json=_ensure_state_defaults(_state_template()))
        db.add(state)
        await db.flush()
    elif state is not None:
        _ensure_state_defaults(state.state_json)
    return state


async def _players(db: AsyncSession, match_id: str) -> list[Player]:
    return list(
        (
            await db.execute(
                select(Player).where(Player.match_id == match_id).order_by(Player.seat_name)
            )
        )
        .scalars()
        .all()
    )


async def _player_state_map(db: AsyncSession, match_id: str) -> dict[int, PlayerState]:
    rows = (
        (
            await db.execute(select(PlayerState).where(PlayerState.match_id == match_id))
        )
        .scalars()
        .all()
    )
    return {row.player_id: row for row in rows}


def _dice_count(state: PlayerState | None) -> int:
    if state is None:
        return 0
    count = state.state_json.get("dice_count")
    if isinstance(count, int) and not isinstance(count, bool):
        return max(0, count)
    dice = state.state_json.get("dice")
    if isinstance(dice, list):
        return sum(1 for die in dice if isinstance(die, int))
    return 0


def _dice_list(state: PlayerState | None) -> list[int]:
    if state is None:
        return []
    dice = state.state_json.get("dice")
    if not isinstance(dice, list):
        return []
    return [die for die in dice if isinstance(die, int)]


def _public_dice_counts(players: list[Player], states: dict[int, PlayerState]) -> dict[str, int]:
    return {
        player.seat_name: _dice_count(states.get(player.id))
        for player in players
        if player.left_at is None
    }


def _alive_players(players: list[Player], states: dict[int, PlayerState]) -> list[Player]:
    return [
        player
        for player in players
        if player.left_at is None and _dice_count(states.get(player.id)) > 0
    ]


def _seat_order(players: list[Player]) -> list[str]:
    return [player.seat_name for player in players]


def _next_alive_seat(
    seat_order: list[str], counts: dict[str, int], current_seat: str | None
) -> str | None:
    if not seat_order:
        return None
    if current_seat not in seat_order:
        for seat in seat_order:
            if counts.get(seat, 0) > 0:
                return seat
        return None
    start = seat_order.index(current_seat)
    for offset in range(1, len(seat_order) + 1):
        seat = seat_order[(start + offset) % len(seat_order)]
        if counts.get(seat, 0) > 0:
            return seat
    return None


def _standing_bid(raw: Any) -> Bid | None:
    if not isinstance(raw, dict):
        return None
    quantity = raw.get("quantity")
    face = raw.get("face")
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        return None
    if not isinstance(face, int) or isinstance(face, bool):
        return None
    return Bid(quantity=quantity, face=face)


def _challenger_name(state_json: dict[str, Any]) -> str | None:
    challenger = state_json.get("challenger")
    return challenger if isinstance(challenger, str) else None


class LiarsDice(BaseGameModule):
    game_type = "liars-dice"

    def config_defaults(self) -> GameConfig:
        return GameConfig(
            total_rounds=64,
            turns_per_round=256,
            per_turn_deadline_seconds=30,
            min_players=3,
            max_players=6,
            simultaneous=False,
            admin_only=True,
        )

    def action_names(self) -> tuple[str, ...]:
        return ("BID", "CHALLENGE")

    def strategy_presets(self) -> list[StrategyPreset]:
        return LD_STRATEGY_PRESETS

    def default_strategy(self) -> str:
        return LD_DEFAULT_STRATEGY

    def rules_text(self, total_rounds: int = 7, turns_per_round: int = 7) -> str:
        cfg = self.config_defaults()
        return make_rules_text(
            wild_ones=True,
            dice_per_player=_default_config()["dice_per_player"],
            min_players=cfg.min_players,
            max_players=cfg.max_players,
            total_rounds=total_rounds,
            turns_per_round=turns_per_round,
        )

    def agent_base_prompt(
        self,
        *,
        your_agent_id: str,
        all_agent_ids: list[str],
        total_rounds: int = 7,
        turns_per_round: int = 7,
    ) -> str:
        cfg = self.config_defaults()
        rules = make_game_rules_text(
            wild_ones=True,
            dice_per_player=_default_config()["dice_per_player"],
            min_players=cfg.min_players,
            max_players=cfg.max_players,
            total_rounds=total_rounds,
            turns_per_round=turns_per_round,
        )
        targets = [seat for seat in all_agent_ids if seat != your_agent_id]
        return (
            f'You are playing Liar\'s Dice as agent "{your_agent_id}". '
            "Read your_private_state for your hidden dice. Read public_state for the current bid, whose turn it is, "
            "how many dice each player has left, and all past bids and showdowns in this round.\n\n"
            f"RULES:\n{rules.rstrip()}\n\n"
            f"All agents at the table: {json.dumps(all_agent_ids)}\n"
            f"Other agents: {json.dumps(targets)}\n\n"
            "RESPONSE FORMAT:\n"
            "Return exactly one JSON object with no prose:\n"
            '{"move": {"type": "BID", "quantity": N, "face": F}} or {"move": {"type": "CHALLENGE"}}\n'
            "\nBids must strictly raise the standing bid per the rules above. Do not invent illegal bids."
        )

    async def validation_snapshot(
        self,
        db: AsyncSession,
        match: Match,
        player: Player,
    ) -> dict[str, Any]:
        state = await _load_state(db, match.id)
        if state is None:
            state_json = _state_template()
        else:
            state_json = state.state_json
        players = await _players(db, match.id)
        states = await _player_state_map(db, match.id)
        config = _load_config(state_json)
        return {
            "standing_bid": state_json.get("standing_bid"),
            "dice_counts": _public_dice_counts(players, states),
            "active_actor": state_json.get("active_actor"),
            "total_dice": sum(_dice_count(states.get(p.id)) for p in _alive_players(players, states)),
            "wild": config["wild_ones"],
        }

    async def next_actor(self, db: AsyncSession, match: Match) -> str | None:
        state = await _load_state(db, match.id)
        if state is None:
            return None
        state_json = state.state_json
        if state_json.get("challenge_pending"):
            return None
        players = await _players(db, match.id)
        states = await _player_state_map(db, match.id)
        counts = _public_dice_counts(players, states)
        seat_order = state_json.get("seat_order")
        if not isinstance(seat_order, list) or not seat_order:
            seat_order = _seat_order(players)
        active_actor = state_json.get("active_actor")
        if isinstance(active_actor, str) and counts.get(active_actor, 0) > 0:
            return active_actor
        return _next_alive_seat(
            seat_order,
            counts,
            active_actor if isinstance(active_actor, str) else None,
        )

    def validate_move(
        self, move: dict[str, Any], *, your_agent_id: str, all_agent_ids: list[str]
    ) -> None:
        parsed = parse_move(move)
        active_actor = move.get("active_actor")
        if active_actor is not None and active_actor != your_agent_id:
            raise GameError("NOT_YOUR_TURN", "It is not your turn.")
        standing_bid = _standing_bid(move.get("standing_bid"))
        wild = bool(move.get("wild", True))
        if isinstance(parsed, ChallengeMove):
            if standing_bid is None:
                raise GameError("NOTHING_TO_CHALLENGE", "There is no standing bid yet.")
            return
        if not isinstance(parsed, BidMove):
            raise GameError("MALFORMED_MOVE", "Unsupported move type.")
        if parsed.quantity < 1:
            raise GameError("MALFORMED_MOVE", "quantity must be positive.")
        if parsed.face < 1 or parsed.face > 6:
            raise GameError("BAD_FACE", "face must be between 1 and 6.")
        total_dice = move.get("total_dice")
        if isinstance(total_dice, int) and not isinstance(total_dice, bool) and parsed.quantity > total_dice:
            raise GameError("BID_TOO_LARGE", "Bid quantity exceeds the dice in play.")
        if not is_legal_raise(standing_bid, Bid(parsed.quantity, parsed.face), wild=wild):
            raise GameError("ILLEGAL_RAISE", "Bid must strictly raise the standing bid.")

    async def record_submission(
        self,
        db: AsyncSession,
        turn: Any,
        player: Player,
        move: dict[str, Any],
        *,
        existing: TurnSubmission | None,
        is_connector_fallback: bool = False,
    ) -> None:
        game = await _load_match(db, turn.match_id)
        state = await _load_state(db, game.id, create=True)
        assert state is not None
        players = await _players(db, game.id)
        player_states = await _player_state_map(db, game.id)
        counts = _public_dice_counts(players, player_states)
        seat_order = state.state_json.get("seat_order")
        if not isinstance(seat_order, list) or not seat_order:
            seat_order = _seat_order(players)
            state.state_json["seat_order"] = seat_order

        parsed = parse_move(move)
        action = "CHALLENGE" if isinstance(parsed, ChallengeMove) else "BID"
        standing = _standing_bid(state.state_json.get("standing_bid"))
        if isinstance(parsed, BidMove):
            state.state_json["standing_bid"] = {
                "by": player.seat_name,
                "quantity": parsed.quantity,
                "face": parsed.face,
            }
            state.state_json["challenge_pending"] = False
            state.state_json["challenger"] = None
            state.state_json["active_actor"] = _next_alive_seat(seat_order, counts, player.seat_name)
            state.state_json.setdefault("bid_history", []).append(
                {
                    "hand": state.state_json.get("hand", 0),
                    "by": player.seat_name,
                    "quantity": parsed.quantity,
                    "face": parsed.face,
                    "message": str(move.get("message", "")),
                }
            )
        else:
            state.state_json["challenge_pending"] = True
            state.state_json["challenger"] = player.seat_name
            state.state_json["active_actor"] = player.seat_name

        target_player_id = None
        if isinstance(parsed, ChallengeMove) and standing is not None:
            bidder = next(
                (
                    p
                    for p in players
                    if p.seat_name == state.state_json["standing_bid"]["by"]
                ),
                None,
            )
            target_player_id = bidder.id if bidder is not None else None

        if existing is not None:
            existing.action = action
            existing.target_player_id = target_player_id
            existing.quantity = parsed.quantity if isinstance(parsed, BidMove) else None
            existing.face = parsed.face if isinstance(parsed, BidMove) else None
            existing.message = str(move.get("message", ""))
            existing.thinking = str(move.get("thinking", ""))
            existing.was_defaulted = is_connector_fallback
            existing.submitted_at = _now()
        else:
            db.add(
                TurnSubmission(
                    turn_id=turn.id,
                    player_id=player.id,
                    action=action,
                    target_player_id=target_player_id,
                    quantity=parsed.quantity if isinstance(parsed, BidMove) else None,
                    face=parsed.face if isinstance(parsed, BidMove) else None,
                    message=str(move.get("message", "")),
                    thinking=str(move.get("thinking", "")),
                    was_defaulted=is_connector_fallback,
                    submitted_at=_now(),
                )
            )

        await db.commit()

    async def resolve_turn(self, db: AsyncSession, turn: Any) -> None:
        if turn.resolved_at is None:
            turn.resolved_at = _now()
            await db.commit()

    async def award_round(self, db: AsyncSession, game: Match, round_num: int) -> None:
        state = await _load_state(db, game.id)
        if state is None:
            return
        state_json = state.state_json
        if state_json.get("showdown_resolved_hand") == round_num:
            return
        standing = _standing_bid(state_json.get("standing_bid"))
        if standing is None:
            return
        players = await _players(db, game.id)
        player_states = await _player_state_map(db, game.id)
        config = _load_config(state_json)

        all_dice: list[int] = []
        revealed: dict[str, list[int]] = {}
        counts: dict[str, int] = {}
        for player in players:
            dice = _dice_list(player_states.get(player.id))
            revealed[player.seat_name] = list(dice)
            counts[player.seat_name] = len(dice)
            all_dice.extend(dice)

        bid_holds, actual_count = resolve_showdown(standing, all_dice, wild=config["wild_ones"])
        challenger = _challenger_name(state_json)
        bidder = standing and state_json.get("standing_bid", {}).get("by")
        loser_seat = bidder if not bid_holds else challenger
        winner_seat = challenger if not bid_holds else bidder
        loser_player = next((player for player in players if player.seat_name == loser_seat), None)
        winner_player = next((player for player in players if player.seat_name == winner_seat), None)

        if loser_player is not None:
            loser_state = player_states.get(loser_player.id)
            if loser_state is not None:
                dice = _dice_list(loser_state)
                if dice:
                    dice.pop()
                loser_state.state_json["dice"] = dice
                loser_state.state_json["dice_count"] = len(dice)
                counts[loser_player.seat_name] = len(dice)
                loser_player.current_round_score = len(dice)
                if len(dice) == 0 and loser_player.seat_name not in state_json["elimination_order"]:
                    state_json["elimination_order"].append(loser_player.seat_name)

        for player in players:
            player.current_round_score = _dice_count(player_states.get(player.id))

        if winner_player is not None:
            winner_player.total_round_wins += 1

        next_leader = None
        if loser_player is not None:
            if counts.get(loser_player.seat_name, 0) > 0:
                next_leader = loser_player.seat_name
            else:
                next_leader = _next_alive_seat(_seat_order(players), counts, loser_player.seat_name)

        state_json["last_showdown"] = {
            "hand": round_num,
            "bid": {"by": bidder, "quantity": standing.quantity, "face": standing.face},
            "actual_count": actual_count,
            "bid_holds": bid_holds,
            "challenger": challenger,
            "winner": winner_seat,
            "loser": loser_seat,
            "revealed": revealed,
        }
        state_json.setdefault("showdowns", []).append(state_json["last_showdown"])
        state_json["standing_bid"] = None
        state_json["challenge_pending"] = False
        state_json["challenger"] = None
        state_json["next_leader"] = next_leader
        state_json["active_actor"] = None
        state_json["showdown_resolved_hand"] = round_num
        state_json["hand"] = round_num
        await db.commit()

    async def is_match_over(self, db: AsyncSession, match: Match) -> bool:
        players = await _players(db, match.id)
        states = await _player_state_map(db, match.id)
        return len(_alive_players(players, states)) == 1

    async def finalize(self, db: AsyncSession, game: Match) -> None:
        placement = await self.final_placement(db, game)
        players = await _players(db, game.id)
        total_players = len(players)
        placement_points = {
            player_id: total_players - index
            for index, player_id in enumerate(placement)
        }
        for player in players:
            player.total_round_score = placement_points.get(player.id, 0)
            player.current_round_score = placement_points.get(player.id, 0)
        game.state = GameState.COMPLETED
        game.completed_at = _now()
        game.winner_player_id = placement[0] if placement else None
        await db.commit()

    async def final_placement(self, db: AsyncSession, match: Match) -> list[int]:
        players = await _players(db, match.id)
        state = await _load_state(db, match.id)
        if state is None:
            return [player.id for player in players]
        elimination_order = [
            seat for seat in state.state_json.get("elimination_order", []) if isinstance(seat, str)
        ]
        by_seat = {player.seat_name: player.id for player in players}
        winner = [
            player.id
            for player in players
            if player.seat_name not in elimination_order and player.left_at is None
        ]
        winner_id = winner[0] if winner else None
        losers = [by_seat[seat] for seat in reversed(elimination_order) if seat in by_seat]
        return ([winner_id] if winner_id is not None else []) + losers

    async def default_move(
        self, db: AsyncSession, match: Match, player: Player
    ) -> dict[str, Any]:
        state = await _load_state(db, match.id)
        state_json = state.state_json if state is not None else _state_template()
        standing = _standing_bid(state_json.get("standing_bid"))
        wild = _load_config(state_json)["wild_ones"]
        total_dice = sum(
            _dice_count(state_row)
            for state_row in (await _player_state_map(db, match.id)).values()
        )
        nxt = min_legal_raise(standing, total_dice, wild=wild)
        if nxt is None:
            return {"type": "CHALLENGE"}
        return {"type": "BID", "quantity": nxt.quantity, "face": nxt.face}

    async def bot_move(self, db: AsyncSession, match: Match, player: Player) -> dict[str, Any]:
        from app.games.liars_dice.sims import decide

        public_state = await self.public_state_for(db, match, player)
        private_state = await self.private_state_for(db, match, player)
        seed_material = (
            f"{match.id}:{public_state.get('hand', 0)}:{player.seat_name}:"
            f"{len(public_state.get('bid_history', []))}"
        )
        seed = int.from_bytes(hashlib.sha256(seed_material.encode()).digest()[:8], "big")
        return decide(public_state, list(private_state.get("dice", [])), seed=seed)

    async def private_state_for(
        self, db: AsyncSession, match: Match, player: Player
    ) -> dict[str, Any]:
        states = await _player_state_map(db, match.id)
        dice = _dice_list(states.get(player.id))
        return {"dice": dice, "dice_count": len(dice)}

    async def public_state_for(
        self, db: AsyncSession, match: Match, viewer: Player | None
    ) -> dict[str, Any]:
        state = await _load_state(db, match.id)
        if state is None:
            return {}
        players = await _players(db, match.id)
        states = await _player_state_map(db, match.id)
        config = _load_config(state.state_json)
        return {
            "hand": state.state_json.get("hand", 0),
            "wild_ones": config["wild_ones"],
            "standing_bid": state.state_json.get("standing_bid"),
            "active_actor": state.state_json.get("active_actor"),
            "dice_counts": _public_dice_counts(players, states),
            "bid_history": list(state.state_json.get("bid_history", [])),
            "showdowns": list(state.state_json.get("showdowns", [])),
            "last_showdown": state.state_json.get("last_showdown"),
            "next_leader": state.state_json.get("next_leader"),
        }

    async def on_round_start(self, db: AsyncSession, match: Match, round_num: int) -> None:
        state = await _load_state(db, match.id, create=True)
        assert state is not None
        players = await _players(db, match.id)
        states = await _player_state_map(db, match.id)
        config = _load_config(state.state_json)
        if round_num == 1:
            state.state_json["config"] = config
            state.state_json["seat_order"] = _seat_order(players)
        seat_order = list(state.state_json.get("seat_order") or _seat_order(players))
        state.state_json["hand"] = round_num
        state.state_json["standing_bid"] = None
        state.state_json["challenge_pending"] = False
        state.state_json["challenger"] = None
        state.state_json["showdown_resolved_hand"] = 0
        if round_num == 1:
            leader = seat_order[0] if seat_order else None
        else:
            leader = state.state_json.get("next_leader")
            if not isinstance(leader, str):
                leader = None

        for player in players:
            player_state = states.get(player.id)
            if player_state is None:
                player_state = PlayerState(match_id=match.id, player_id=player.id, state_json={})
                db.add(player_state)
            existing = _dice_count(player_state)
            should_roll = round_num == 1 or existing > 0
            if player.left_at is not None or not should_roll:
                player_state.state_json["dice"] = []
                player_state.state_json["dice_count"] = 0
                player.current_round_score = 0
                continue
            rng_seed = int.from_bytes(
                hashlib.sha256(
                    f"{match.id}:{round_num}:{player.seat_name}:{config['dice_per_player']}".encode()
                ).digest()[:8],
                "big",
            )
            dice = roll(config["dice_per_player"], Random(rng_seed))
            player_state.state_json["dice"] = dice
            player_state.state_json["dice_count"] = len(dice)
            player.current_round_score = len(dice)
        if leader is None:
            leader = _next_alive_seat(seat_order, _public_dice_counts(players, states), None)
        state.state_json["active_actor"] = leader
        state.state_json["next_leader"] = None
        await db.commit()

    def match_placement_key(
        self, *, round_wins: float, total_score: int
    ) -> tuple[float, ...]:
        return (float(total_score), round_wins)

    def theme(self) -> GameTheme:
        return GameTheme(
            key=self.game_type,
            vars={
                "--brand": "#0f6c7a",
                "--brand-2": "#103b52",
                "--accent": "#e2a84b",
                "--on-brand": "#f5fbfc",
                "--surface": "#f4fbfc",
                "--surface-2": "#e7f3f6",
            },
        )
