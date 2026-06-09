"""Seed a fresh local dev DB with a finished 100-turn match for viewer work.

Builds M_0042: 8 bots, 10 rounds x 10 turns, with pacts, betrayals, gang-ups,
hurts and hoards plus public talk — enough variety that the feed headlines,
move chips and the animated stage all have something real to show.

    python scripts/_seed_viewer_demo.py

Drops/recreates the dev SQLite DB from the models (no Alembic needed).
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

from app.db import make_engine
from app.models import Bot, BotKind, Match, Player, Turn, TurnMessage, TurnSubmission, User
from app.models.match import GameState
from sqlalchemy.ext.asyncio import async_sessionmaker

MATCH_ID = "M_0042"
AGENTS = ["Opus", "Sonnet", "Haiku", "Mini", "Nano", "Llama", "Mistral", "Gemini"]
TALK = [
    "Let's lock in a pact, +8 for both of us.",
    "Confirmed. I've got your back this round.",
    "Banking quietly, watching the table.",
    "You broke faith last turn — this is payback.",
    "Everyone pile on the leader, now.",
    "Holding steady, nothing personal.",
    "I don't trust that alliance for a second.",
    "Big swing coming. Brace yourselves.",
]


async def main() -> None:
    # Schema is built by `alembic upgrade head` before this runs (see the runner
    # below); we only insert rows so the app's boot-time upgrade stays a no-op.
    engine = make_engine("sqlite+aiosqlite:///./hoardhurthelp.db")

    Session = async_sessionmaker(engine, expire_on_commit=False)
    rng = random.Random(42)
    now = datetime.now(timezone.utc)

    async with Session() as db:
        match = Match(
            id=MATCH_ID,
            name="Friday Night Fights",
            game="hoard-hurt-help",
            state=GameState.COMPLETED,
            scheduled_start=now - timedelta(hours=2),
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(minutes=20),
            min_players=3,
            max_players=20,
            total_rounds=7,
            turns_per_round=7,
            current_round=7,
            current_turn=7,
            rounds_awarded=7,
        )
        db.add(match)
        await db.flush()

        players: dict[str, Player] = {}
        for i, agent in enumerate(AGENTS):
            user = User(google_sub=f"sub-{i}", email=f"u{i}@t.com")
            db.add(user)
            await db.flush()
            bot = Bot(
                user_id=user.id,
                name=f"bot-{agent}",
                key_lookup=f"lookup-{i}",
                key_hint=f"sk_…{i:02d}",
                kind=BotKind.EXTERNAL,
            )
            db.add(bot)
            await db.flush()
            p = Player(match_id=MATCH_ID, user_id=user.id, bot_id=bot.id, agent_id=agent)
            db.add(p)
            await db.flush()
            players[agent] = p

        wins = {a: 0.0 for a in AGENTS}
        prev_pact: tuple[str, str] | None = None

        for rnd in range(1, 11):
            round_score = {a: 0 for a in AGENTS}
            for turn_no in range(1, 11):
                t = Turn(
                    match_id=MATCH_ID,
                    round=rnd,
                    turn=turn_no,
                    turn_token=f"tk_{rnd:02d}_{turn_no:02d}",
                    opened_at=now,
                    deadline_at=now + timedelta(seconds=60),
                    phase="done",
                    talk_resolved_at=now,
                    resolved_at=now,
                )
                db.add(t)
                await db.flush()

                # Decide this turn's shape.
                actions: dict[str, tuple[str, str | None]] = {}
                seq = (rnd - 1) * 10 + turn_no
                pair = (AGENTS[seq % 8], AGENTS[(seq + 1) % 8])
                a, b = pair
                if a == b:
                    b = AGENTS[(seq + 2) % 8]

                betrayal_turn = prev_pact is not None and seq % 7 == 0
                gangup_turn = seq % 9 == 0

                if betrayal_turn and prev_pact:
                    x, y = prev_pact
                    actions[x] = ("HURT", y)
                elif gangup_turn:
                    victim = AGENTS[seq % 8]
                    for agent in AGENTS:
                        if agent != victim and rng.random() < 0.5:
                            actions[agent] = ("HURT", victim)
                else:
                    # A fresh pact.
                    actions[a] = ("HELP", b)
                    actions[b] = ("HELP", a)
                    prev_pact = (a, b)

                # Fill the rest with a mix of hurts, one-way helps, hoards.
                for agent in AGENTS:
                    if agent in actions:
                        continue
                    roll = rng.random()
                    if roll < 0.18:
                        target = rng.choice([z for z in AGENTS if z != agent])
                        actions[agent] = ("HURT", target)
                    elif roll < 0.32:
                        target = rng.choice([z for z in AGENTS if z != agent])
                        actions[agent] = ("HELP", target)
                    else:
                        actions[agent] = ("HOARD", None)

                # Score nominally for storage (viewer recomputes its own).
                helps = {ag: tgt for ag, (act, tgt) in actions.items() if act == "HELP"}
                for agent in AGENTS:
                    act, tgt = actions[agent]
                    if act == "HOARD":
                        round_score[agent] += 2
                    elif act == "HELP" and tgt and helps.get(tgt) == agent:
                        round_score[agent] += 8
                    elif act == "HELP" and tgt:
                        round_score[tgt] += 4
                    elif act == "HURT" and tgt:
                        round_score[tgt] = max(0, round_score[tgt] - 4)

                talkers = rng.sample(AGENTS, k=rng.randint(2, 5))
                for agent in AGENTS:
                    act, tgt = actions[agent]
                    db.add(
                        TurnSubmission(
                            turn_id=t.id,
                            player_id=players[agent].id,
                            action=act,
                            target_player_id=players[tgt].id if tgt else None,
                            message=rng.choice(TALK) if agent in talkers else "",
                            points_delta=0,
                            round_score_after=round_score[agent],
                            was_defaulted=False,
                            submitted_at=now,
                        )
                    )
                    if agent in talkers:
                        db.add(
                            TurnMessage(
                                turn_id=t.id,
                                player_id=players[agent].id,
                                text=rng.choice(TALK),
                                was_defaulted=False,
                                submitted_at=now,
                            )
                        )

            # Award the round.
            best = max(round_score.values())
            winners = [ag for ag, s in round_score.items() if s == best]
            for ag in winners:
                wins[ag] += 1 / len(winners)

        for agent, p in players.items():
            p.total_round_wins = wins[agent]
            p.current_round_score = round_score[agent]
            p.total_round_score = round_score[agent]
        champ = max(players.values(), key=lambda p: p.total_round_wins)
        match.winner_player_id = champ.id

        await db.commit()
    await engine.dispose()
    print(f"Seeded {MATCH_ID}: 8 bots, 100 turns. Winner: {champ.agent_id}")


if __name__ == "__main__":
    asyncio.run(main())
