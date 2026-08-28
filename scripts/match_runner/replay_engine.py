"""Rebuild a match in a local database and run it forward.

The whole design rests on one rule: **nothing here decides points**. Turns are
scored by the game's own `resolve_turn`, rounds are awarded by the real
`award_round_winners`, and the match is finished by the real `finalize_game`.
This module only seeds a position and decides who moves next. A replay that
scored by its own copy of the payoff table would answer questions about the
copy, and this repo has been bitten by a rule living in two places more than
once.

ONE KNOWN GAP: THE TALK PHASE. A live turn is talk then act — every player posts
a message, reads what the others posted, and only then moves. This loop opens
every turn already talk-resolved and runs the act phase alone, so replayed turns
are played by a table that cannot speak. Recorded history keeps its chat, so a
seat resuming at the cut still reads what was said before it. But nothing said
after, because nothing is said: with a two-turn history window, a table replayed
three turns forward sees a completely silent match. Measured on M_7558 — 14 of
16 remembered moves carry a chat line at the resume point, 0 of 16 two turns
later. In a game whose pacts are made in chat, that is a different game, so a
replayed continuation is evidence about a decision and not a match result.

Import order matters and is easy to get wrong: `app.db` builds its engine at
module import time from `app.config.settings`, so DATABASE_URL must be set
before anything under `app` is imported, including transitively. Every `app.*`
import in this file is therefore inside a function body. See scripts/offline_db.py.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from offline_db import bootstrap_file_db, ensure_repo_root_on_path, set_database_url  # noqa: E402

from replay_seats import BotDriver, Move  # noqa: E402

# Every seat needs a scripted personality at seating time even when a driver
# will override its every move, because add_bots_to_game validates the profile.
# A seat whose driver never yields to the bot runtime never plays this.
PLACEHOLDER_PERSONALITY = "pragmatist"


@dataclass
class ReplayResult:
    match_id: str
    turns_replayed: int
    turns_played: int
    round_scores: dict[int, dict[str, int]] = field(default_factory=dict)
    round_wins: dict[str, float] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    moves: list[dict[str, Any]] = field(default_factory=list)


async def run_replay(
    export: dict[str, Any],
    *,
    start: tuple[int, int],
    stop: tuple[int, int] | None,
    drivers: dict[str, Any],
    db_path: str,
) -> ReplayResult:
    """Seed the position before `start`, then play forward to `stop` (or the end)."""
    ensure_repo_root_on_path()
    set_database_url(db_path)
    for m in [k for k in list(sys.modules) if k.startswith("app.")]:
        del sys.modules[m]
    SessionLocal = await bootstrap_file_db(db_path)

    from sqlalchemy import select

    from app.engine.bots.seating import add_bots_to_game
    from app.engine.bots.service import auto_submit_bot_phase
    from app.engine.resolver import finalize_game
    from app.engine.state_machine import assert_transition
    from app.games import get as get_game_module
    from app.models.match import GameState, Match
    from app.models.player import Player
    from app.models.turn import Turn, TurnSubmission

    game_meta = export["game"]
    # Seat names come from the move rows, never from `players`: that block keys
    # by numeric agent id, and the two halves of the export do not join.
    seats = sorted({s["agent_id"] for s in export["submissions"]})
    # The export's game block carries no shape, so take it from the log itself
    # rather than defaulting — a wrong turns_per_round silently rewrites which
    # turn ends a round, and every round score after it.
    total_rounds = max(s["round"] for s in export["submissions"])
    turns_per_round = max(s["turn"] for s in export["submissions"])
    now = datetime.now(timezone.utc)

    async with SessionLocal() as db:
        match = Match(
            id=game_meta["id"],
            name=f"replay of {game_meta['id']}",
            game=game_meta.get("game") or "hoard-hurt-help",
            state=GameState.REGISTERING,
            scheduled_start=now - timedelta(seconds=1),
            per_turn_deadline_seconds=0,
            total_rounds=total_rounds,
            turns_per_round=turns_per_round,
            min_players=3,
            max_players=100,
            mutual_help_mode=game_meta.get("mutual_help_mode"),
        )
        db.add(match)
        await db.flush()

        roster = []
        for seat in seats:
            drv = drivers.get(seat)
            personality = drv.strategy if isinstance(drv, BotDriver) else PLACEHOLDER_PERSONALITY
            roster.append((seat[:26], personality))
        await add_bots_to_game(db, match, roster)
        assert_transition(match.state, GameState.ACTIVE)
        match.state = GameState.ACTIVE
        match.started_at = now
        await db.commit()

        module = get_game_module(match.game)
        players = list(
            (await db.execute(select(Player).where(Player.match_id == match.id))).scalars().all()
        )
        by_seat = {p.seat_name: p for p in players}
        # Seat names are truncated to 26 chars at seating; map back to the export's
        # full names so a long preset name still resolves to its player row.
        seat_of = {s[:26]: s for s in seats}
        pid_of = {seat_of[p.seat_name]: p.id for p in players}

        recorded: dict[str, dict[tuple[int, int], Move]] = {s: {} for s in seats}
        for sub in export["submissions"]:
            recorded[sub["agent_id"]][(sub["round"], sub["turn"])] = Move(
                sub["action"],
                sub.get("target_id"),
                sub.get("thinking") or "",
                # Carry the chat line too. Replayed history shows each move with what
                # its player said, so dropping it would hand a resumed agent a table
                # where everyone had been silent for the whole match.
                sub.get("message") or "",
            )
        result = ReplayResult(match_id=match.id, turns_replayed=0, turns_played=0)
        last = stop or (total_rounds, turns_per_round)

        # The shape of this loop mirrors app/engine/scheduler_turn_loop._run_round:
        # zero the round scores at the start of every round, run its turns, then
        # award through the game module. Getting the reset wrong is not subtle and
        # not visible per-turn — scores simply accumulate across rounds, and the
        # first version of this loop inflated a real match's totals fourfold.
        for rnd in range(1, total_rounds + 1):
            if (rnd, 1) > last:
                break
            for player in players:
                player.current_round_score = 0
            await db.commit()

            for tn in range(1, turns_per_round + 1):
                here = (rnd, tn)
                if here > last:
                    break
                turn = Turn(
                    match_id=match.id,
                    round=rnd,
                    turn=tn,
                    turn_token=f"rp-{match.id}-{rnd}-{tn}",
                    opened_at=now,
                    deadline_at=now + timedelta(seconds=60),
                    phase="act",
                    talk_resolved_at=now,
                )
                db.add(turn)
                await db.flush()

                is_history = here < start
                for seat in seats:
                    if is_history:
                        mv = recorded[seat].get(here)
                        if mv is None:
                            continue  # short log here; resolve_turn defaults it
                    else:
                        drv = drivers[seat]
                        if isinstance(drv, BotDriver):
                            continue  # the platform fills this seat below
                        view = await _view(
                            db, match, turn, by_seat[seat[:26]], players,
                            [p.seat_name for p in players],
                            getattr(drv, "strategy_text", None),
                        )
                        mv = drv.move(seat, view)
                        result.moves.append(
                            {"round": rnd, "turn": tn, "seat": seat,
                             "action": mv.action, "target": mv.target,
                             "by": drv.name, "thinking": mv.thinking}
                        )
                    db.add(
                        TurnSubmission(
                            turn_id=turn.id,
                            player_id=pid_of[seat],
                            action=mv.action,
                            target_player_id=pid_of.get(mv.target) if mv.target else None,
                            thinking=(mv.thinking or "")[:4000],
                            message=(mv.message or "")[:2000],
                            submitted_at=now,
                        )
                    )
                await db.flush()

                if not is_history:
                    # Scripted seats, played by the shipped bot runtime. It skips
                    # any seat that already submitted, so the drivers above win.
                    await auto_submit_bot_phase(db, match, turn, module, phase="act")

                await module.resolve_turn(db, turn)
                await db.commit()
                if is_history:
                    result.turns_replayed += 1
                else:
                    result.turns_played += 1

            result.round_scores[rnd] = {
                seat_of[p.seat_name]: p.current_round_score for p in players
            }
            await module.award_round(db, match, rnd)
            await db.commit()

        # Read the totals as columns, not through the ORM objects the loop has
        # been holding: those are the identity map's copies, and the award step
        # updates the rows behind them, so an object read reports zeros.
        final = (
            await db.execute(
                select(Player.seat_name, Player.total_round_wins, Player.total_round_score)
                .where(Player.match_id == match.id)
            )
        ).all()
        result.round_wins = {seat_of[name]: wins for name, wins, _ in final}
        result.totals = {seat_of[name]: pts for name, _, pts in final}
        if last >= (total_rounds, turns_per_round):
            await finalize_game(db, match)
            await db.commit()
    return result


async def _view(db, match, turn, player, players, all_agent_ids, strategy_text) -> dict[str, Any]:
    """What this seat can see — assembled by the live payload's own builders.

    Deliberately the same four pieces `agent_play_next_turn._build_turn_payload`
    assembles, from the same functions and the same rolling window:

      static      rules and identity, from `build_turn_static_dict`
      history     recent resolved turns, `RECENT_HISTORY_TURNS` deep
      scoreboard  the public standings
      current     this turn's talk so far

    None of it is rebuilt here. If a replayed seat saw a different board than a
    live one, every comparison drawn from the replay would be measuring the
    difference in the board rather than the difference you meant to test.

    `current` comes back empty in a replay, because the replay runs no talk
    phase — see the module docstring. That is a real gap, not a formatting
    detail: it is the one part of the live view a replay cannot fill.
    """
    from app.engine.agent_play_reads import (
        RECENT_HISTORY_TURNS,
        _build_current_turn,
        _group_into_turns,
        _load_public_action_records,
        build_public_scoreboard_dicts,
        build_turn_static_dict,
    )

    history = _group_into_turns(
        await _load_public_action_records(
            db, match.id, players, recent_turns=RECENT_HISTORY_TURNS
        )
    )
    return {
        "static": build_turn_static_dict(
            match, player, all_agent_ids=all_agent_ids, your_strategy=strategy_text
        ),
        "round": turn.round,
        "turn": turn.turn,
        "history": [_plain(h) for h in history],
        "scoreboard": build_public_scoreboard_dicts(players),
        "current": _plain(await _build_current_turn(db, turn)),
        # AlwaysDriver ranks seats off this; it is the scoreboard under the name
        # the drivers already use.
        "standings": build_public_scoreboard_dicts(players),
    }


def _plain(obj: Any) -> Any:
    """Pydantic models / dataclasses -> plain data, so the view can be JSON."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, list):
        return [_plain(o) for o in obj]
    return obj
