"""Rebuild a match's position in a local database, then let the real loop finish it.

The design rule is that this file runs NOTHING the game already runs. It seeds
the turns that were recorded, writes down where the match stopped, and calls the
shipped `_run_game`. Everything after that — the talk phase, the per-round score
reset, awarding rounds, finishing the match — is the same code that ran the
original, because the engine already knows how to resume: `run_match` picks up
from `current_round`/`current_turn`, and `_run_turn` returns immediately on a
turn that is already resolved. That resume path exists for mid-deploy restarts
and works just as well for this.

The first version of this file did not do that. It reimplemented the turn loop,
and paid for it twice: it forgot to zero the round scores between rounds (real
match totals came out fourfold too high) and it had no talk phase at all (a
table replayed three turns forward saw a completely silent match, in a game
whose pacts are made in chat). Both were already solved a few files away. If
something here starts to look like the turn loop, that is the bug.

Import order matters and is easy to get wrong: `app.db` builds its engine at
module import time from `app.config.settings`, so DATABASE_URL must be set
before anything under `app` is imported, including transitively. Every `app.*`
import here is therefore inside a function body. See scripts/offline_db.py.

Not safe to call inside a shared process (pytest included): it repoints
DATABASE_URL and drops every loaded `app.*` module. Run it as its own process.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from offline_db import bootstrap_file_db, ensure_repo_root_on_path, set_database_url  # noqa: E402


@dataclass
class ReplayResult:
    match_id: str
    turns_seeded: int
    turns_played: int
    round_wins: dict[str, float] = field(default_factory=dict)
    totals: dict[str, int] = field(default_factory=dict)
    moves: list[dict[str, Any]] = field(default_factory=list)


async def run_replay(
    export: dict[str, Any],
    *,
    start: tuple[int, int],
    seats_play: dict[str, Any],
    db_path: str,
) -> ReplayResult:
    """Seed the position before `start`, then hand the match to the real loop.

    `seats_play` maps each seat name to how it should play from `start` onward:
    a personality id for a scripted bot, or a driver object with a `.move` for a
    model-backed seat. Seats are seated as bots either way, because a bot is the
    seat the platform plays for itself — which is what a replayed seat is.
    """
    ensure_repo_root_on_path()
    set_database_url(db_path)
    for name in [k for k in list(sys.modules) if k.startswith("app.")]:
        del sys.modules[name]
    await bootstrap_file_db(db_path)

    import app.engine.scheduler as scheduler
    from sqlalchemy import select

    from app.engine.bots.seating import add_bots_to_game
    from app.engine.state_machine import assert_transition
    from app.games import get as get_game_module
    from app.models.match import GameState, Match
    from app.models.player import Player
    from app.models.turn import Turn, TurnSubmission

    subs = export["submissions"]
    seats = sorted({s["agent_id"] for s in subs})
    total_rounds = max(s["round"] for s in subs)
    turns_per_round = max(s["turn"] for s in subs)
    now = datetime.now(timezone.utc)
    game_meta = export["game"]

    recorded: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for sub in subs:
        recorded.setdefault((sub["round"], sub["turn"]), []).append(sub)

    async with scheduler.SessionLocal() as db:
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

        # Seat names are capped at 26 characters, so keep the map back to the
        # export's full names — everything the caller reads is keyed by those.
        await add_bots_to_game(
            db, match, [(s[:26], _personality(seats_play.get(s))) for s in seats]
        )
        assert_transition(match.state, GameState.ACTIVE)
        match.state = GameState.ACTIVE
        match.started_at = now
        await db.commit()

        module = get_game_module(match.game)
        players = list(
            (await db.execute(select(Player).where(Player.match_id == match.id))).scalars().all()
        )
        full_name = {s[:26]: s for s in seats}
        pid = {p.seat_name: p.id for p in players}

        seeded = 0
        # Bounded by the match, not by `start`: a cut past the end (the way you
        # ask for "seed everything") would otherwise iterate rounds that do not
        # exist and award them.
        for rnd in range(1, min(start[0], total_rounds) + 1):
            for p in players:
                p.current_round_score = 0
            await db.commit()
            for tn in range(1, turns_per_round + 1):
                if (rnd, tn) >= start:
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
                for sub in recorded.get((rnd, tn), []):
                    target = (sub.get("target_id") or "")[:26]
                    db.add(
                        TurnSubmission(
                            turn_id=turn.id,
                            player_id=pid[sub["agent_id"][:26]],
                            action=sub["action"],
                            target_player_id=pid.get(target),
                            # Keep the chat: replayed history is read by the seats
                            # that resume, and a silent past is a different match.
                            message=sub.get("message") or "",
                            thinking=sub.get("thinking") or "",
                            submitted_at=now,
                        )
                    )
                await db.flush()
                await module.resolve_turn(db, turn)
                await db.commit()
                seeded += 1
            # Award only rounds that finished before the cut. A round the replay
            # is resuming into must stay unawarded, or the real loop will skip it.
            if (rnd, turns_per_round) < start:
                await module.award_round(db, match, rnd)
                await db.commit()

        match.current_round, match.current_turn = start
        await db.commit()

    moves: list[dict[str, Any]] = []
    _install_model_seats(seats_play, full_name, moves)

    # From here it is the shipped match loop, unmodified.
    await scheduler._run_game(match_id=game_meta["id"])

    async with scheduler.SessionLocal() as db:
        rows = (
            await db.execute(
                select(Player.seat_name, Player.total_round_wins, Player.total_round_score)
                .where(Player.match_id == game_meta["id"])
            )
        ).all()
    return ReplayResult(
        match_id=game_meta["id"],
        turns_seeded=seeded,
        turns_played=(total_rounds * turns_per_round) - seeded,
        round_wins={full_name[n]: w for n, w, _ in rows},
        totals={full_name[n]: p for n, _, p in rows},
        moves=moves,
    )


def _personality(play: Any) -> str:
    """The scripted personality this seat is seated with.

    A model-backed seat still needs one: `add_bots_to_game` validates the bot
    profile at seating time. Its decisions are replaced before the loop runs, so
    it never plays this.
    """
    return play if isinstance(play, str) else "pragmatist"


def _install_model_seats(
    seats_play: dict[str, Any], full_name: dict[str, str], moves: list[dict[str, Any]]
) -> None:
    """Point the named seats at a model instead of their scripted rules.

    The platform asks two questions per bot seat per turn — what to say, and what
    to do — through `choose_bot_talk_decision` / `choose_bot_action_decision`.
    Replacing those two for the seats that want a model puts the model inside the
    real loop, so a model seat gets the talk phase and everything else for free.
    Seats without a driver fall through to the scripted rules untouched.
    """
    drivers = {n: d for n, d in seats_play.items() if not isinstance(d, str)}
    if not drivers:
        return

    import app.engine.bots.service as service

    scripted_talk = service.choose_bot_talk_decision
    scripted_action = service.choose_bot_action_decision

    def talk(context: Any, profile: Any) -> Any:
        driver = drivers.get(full_name.get(context.your_agent_id, ""))
        if driver is None:
            return scripted_talk(context, profile)
        return driver.talk(context)

    def action(context: Any, profile: Any) -> Any:
        seat = full_name.get(context.your_agent_id, "")
        driver = drivers.get(seat)
        if driver is None:
            return scripted_action(context, profile)
        decision = driver.act(context)
        moves.append(
            {
                "round": context.round,
                "turn": context.turn,
                "seat": seat,
                "action": decision.move.get("action"),
                "target": decision.move.get("target_agent_id"),
                "by": driver.name,
            }
        )
        return decision

    service.choose_bot_talk_decision = talk
    service.choose_bot_action_decision = action
