#!/usr/bin/env python3
"""Validation oracle for decaying mutual help — the A/B that justified the feature.

Runs three conditions on the REAL engine, with deterministic scripted bots (no
LLM, no network), and reports the per-round tie-rate that motivated the change:

  baseline  flat mutual help (+8 each, no decay)  — the pre-feature rule, pinned
            here so the comparison stays valid even though the shipped engine now
            decays.
  decay     the SHIPPED rule: a pair's mutual help pays max(2, 8-k), k = that
            pair's prior mutual helps this match. Uses the real resolve_turn, so
            this also smoke-checks app/games/hoard_hurt_help/scoring.py.
  aware     decay + decay-aware partner rotation — the SHIPPED Slice 4 bots. Uses
            the real trust.PARTNER_FATIGUE so a farmed partner's trust erodes toward
            neutral and bots seek a fresh ally. baseline/decay pin PARTNER_FATIGUE
            to 0 to isolate the lever, so this also exercises app/engine/bots/trust.py.

The headline result (5 seeds × 40 matches): mean tie-rate baseline ~0.53 →
decay ~0.27 → aware ~0.19, with aware < decay < baseline on every seed. See
docs/workflow/feature-runs/mutual-help-decay/closeout.md for the recorded run.

    uv run python scripts/decay_validation_sim.py --n 40 --seeds 42 99 7 13 23
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from offline_db import bootstrap_file_db  # noqa: E402 - needs sys.path setup above

STRATEGIES = (
    "coalition_seeker", "pragmatist", "loyal_partner", "grudger", "leader_pressure",
    "opportunist", "endgame_sniper", "diplomat", "crowd_follower",
)
PLAYERS_PER_MATCH = 10


def make_flat_resolve(scoring):
    """Pre-feature resolve: flat +8 mutual help, no decay. Pins the baseline.

    Identical to the shipped resolver except the mutual bonus is a constant +4
    (per side, on top of the base +4 help) instead of decaying — so the only
    lever that differs between baseline and decay is the decay itself.
    """
    from sqlalchemy import select

    from app.models.player import Player
    from app.models.turn import TurnSubmission

    async def resolve_turn(db, turn):
        players = list(
            (await db.execute(select(Player).where(Player.match_id == turn.match_id)))
            .scalars().all()
        )
        subs = list(
            (await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)))
            .scalars().all()
        )
        submitted = {s.player_id for s in subs}
        for p in players:
            if p.id not in submitted:
                d = TurnSubmission(
                    turn_id=turn.id, player_id=p.id, action="HOARD", target_player_id=None,
                    message=scoring.DEFAULT_MISSED_MESSAGE, was_defaulted=True, submitted_at=None,
                )
                db.add(d)
                subs.append(d)
        await db.flush()
        delta = {p.id: 0 for p in players}
        help_targets = {s.player_id: s.target_player_id for s in subs if s.action == "HELP"}
        for s in subs:
            if s.action == "HOARD":
                delta[s.player_id] += 2
            elif s.action == "HELP" and s.target_player_id in delta:
                delta[s.target_player_id] += 4
            elif s.action == "HURT" and s.target_player_id in delta:
                betrayed = help_targets.get(s.target_player_id) == s.player_id
                delta[s.target_player_id] -= 8 if betrayed else 4
        seen = set()
        for a, b in help_targets.items():
            if b is None or help_targets.get(b) != a:
                continue
            pair = frozenset({a, b})
            if pair in seen:
                continue
            seen.add(pair)
            delta[a] += 4  # flat bonus — no decay
            delta[b] += 4
        for p in players:
            new = max(0, p.current_round_score + delta[p.id])
            s = next(x for x in subs if x.player_id == p.id)
            s.points_delta = new - p.current_round_score
            s.round_score_after = new
            p.current_round_score = new
        turn.resolved_at = datetime.now(timezone.utc)
        await db.commit()

    return resolve_turn


async def run_condition(
    mode: str, n_matches: int, seed: int, db_path: str, fatigue_override: int | None = None
):
    # Fresh module state per condition so a per-mode patch never leaks across
    # modes. Must happen BEFORE bootstrap_file_db so its (fresh) import of
    # app.db binds to this condition's DATABASE_URL, not a stale cached one.
    for m in [k for k in list(sys.modules) if k.startswith("app.")]:
        del sys.modules[m]
    SessionLocal = await bootstrap_file_db(db_path, mkdir=False)

    import app.games.hoard_hurt_help.scoring as scoring
    if mode == "baseline":
        scoring.resolve_turn = make_flat_resolve(scoring)
    # decay/aware use the real (shipped) resolve_turn — no patch.

    # Decay-aware bots are the shipped trust.PARTNER_FATIGUE. baseline/decay pin it
    # to 0 to isolate the scoring lever; aware leaves the real value in place (or a
    # `--fatigue` override, for tuning sweeps).
    import app.engine.bots.trust as trust
    if mode != "aware":
        trust.PARTNER_FATIGUE = 0
    elif fatigue_override is not None:
        trust.PARTNER_FATIGUE = fatigue_override

    from sqlalchemy import func, select

    from app.engine.bots.seating import add_bots_to_game
    from app.engine.scheduler import _run_game
    from app.engine.state_machine import assert_transition
    from app.engine.tokens import generate_match_id
    from app.models.match import GameState, Match

    rng = random.Random(seed)
    rosters = [
        [rng.choice(STRATEGIES) for _ in range(PLAYERS_PER_MATCH)]
        for _ in range(n_matches)
    ]
    match_ids = []
    for idx, strategies in enumerate(rosters):
        async with SessionLocal() as db:
            cnt = await db.scalar(select(func.count()).select_from(Match)) or 0
            mid = generate_match_id(cnt + 1)
            now = datetime.now(timezone.utc)
            mm = Match(
                id=mid, name=f"d-{idx}", game="hoard-hurt-help", state=GameState.REGISTERING,
                scheduled_start=now - timedelta(seconds=1), per_turn_deadline_seconds=0,
                total_rounds=7, turns_per_round=7, min_players=3, max_players=100,
            )
            db.add(mm)
            await db.flush()
            seats, counts = [], {}
            for s in strategies:
                counts[s] = counts.get(s, 0) + 1
                seats.append((f"{s.replace('_', ' ')[:26]} {counts[s]}", s))
            await add_bots_to_game(db, mm, seats)
            assert_transition(mm.state, GameState.ACTIVE)
            mm.state = GameState.ACTIVE
            mm.started_at = now
            await db.commit()
        await _run_game(mid)
        match_ids.append(mid)
    return await summarize(SessionLocal, match_ids, mode)


async def summarize(SessionLocal, match_ids, mode):
    from sqlalchemy import select

    from app.models.agent import Agent
    from app.models.match import Match
    from app.models.player import Player
    from app.models.turn import Turn, TurnSubmission

    async with SessionLocal() as db:
        rows = (await db.execute(
            select(Player, Agent).join(Agent, Agent.id == Player.agent_id)
            .where(Player.match_id.in_(match_ids))
        )).all()
        matches = (await db.execute(
            select(Match).where(Match.id.in_(match_ids))
        )).scalars().all()
        winners = {m.winner_player_id for m in matches if m.winner_player_id}
        pids = [p.id for p, _ in rows]
        turns = (await db.execute(
            select(Turn).where(Turn.match_id.in_(match_ids))
        )).scalars().all()
        subs = (await db.execute(
            select(TurnSubmission).where(TurnSubmission.player_id.in_(pids))
        )).scalars().all()

    strat = {p.id: (a.bot_strategy or "?") for p, a in rows}
    by = collections.defaultdict(lambda: dict(app=0, win=0, HOARD=0, HELP=0, HURT=0))
    for p, a in rows:
        s = a.bot_strategy or "?"
        by[s]["app"] += 1
        if p.id in winners:
            by[s]["win"] += 1
    by_turn = collections.defaultdict(list)
    for sub in subs:
        s = strat.get(sub.player_id, "?")
        if sub.action in ("HOARD", "HELP", "HURT"):
            by[s][sub.action] += 1
        by_turn[sub.turn_id].append(sub)
    mutual = betrayals = 0
    for ss in by_turn.values():
        bypid = {x.player_id: x for x in ss}
        for x in ss:
            t = bypid.get(x.target_player_id)
            if x.action == "HELP" and t and t.action == "HELP" and t.target_player_id == x.player_id:
                mutual += 1
            if x.action == "HURT" and t and t.action == "HELP" and t.target_player_id == x.player_id:
                betrayals += 1
    turn_round = {t.id: (t.match_id, t.round, t.turn) for t in turns}
    last = {}
    for t in turns:
        last[(t.match_id, t.round)] = max(last.get((t.match_id, t.round), 0), t.turn)
    rs = collections.defaultdict(dict)
    for sub in subs:
        mid, rnd, trn = turn_round[sub.turn_id]
        if trn == last[(mid, rnd)]:
            rs[(mid, rnd)][sub.player_id] = sub.round_score_after
    ties = tot = 0
    for sc in rs.values():
        v = sorted(sc.values(), reverse=True)
        if len(v) >= 2:
            tot += 1
            ties += 1 if v[0] == v[1] else 0

    return dict(
        mode=mode, n=len(match_ids), tie=ties / tot if tot else 0,
        mutual=mutual // 2, betrayals=betrayals,
        strat={s: dict(win=by[s]["win"], app=by[s]["app"]) for s in by},
    )


def main():
    import statistics
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 99, 7, 13, 23])
    ap.add_argument("--modes", nargs="+", default=["baseline", "decay", "aware"])
    ap.add_argument("--fatigue", type=int, default=None,
                    help="override trust.PARTNER_FATIGUE for the aware mode (tuning sweeps)")
    a = ap.parse_args()
    modes = a.modes
    runs = {m: [] for m in modes}
    print(
        f"Decaying mutual help (8->2, floor 2) + decay-aware partner rotation — "
        f"{len(a.seeds)} seeds x {a.n} matches\n"
    )
    for seed in a.seeds:
        for mode in modes:
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tf:
                db_path = tf.name
            runs[mode].append(
                asyncio.run(run_condition(mode, a.n, seed, db_path, a.fatigue))
            )
            os.unlink(db_path)

    print("Round tie-rate per seed:")
    print("  " + f"{'seed':>5} " + " ".join(f"{m:>9s}" for m in modes))
    for i, seed in enumerate(a.seeds):
        print("  " + f"{seed:>5} " + " ".join(f"{runs[m][i]['tie']:>9.3f}" for m in modes))
    print("  " + f"{'MEAN':>5} " + " ".join(
        f"{statistics.mean(x['tie'] for x in runs[m]):>9.3f}" for m in modes
    ))

    print("\nmutual-pairs (mean): " + "  ".join(
        f"{m}={statistics.mean(x['mutual'] for x in runs[m]):.0f}" for m in modes
    ))
    print("betrayals   (mean): " + "  ".join(
        f"{m}={statistics.mean(x['betrayals'] for x in runs[m]):.0f}" for m in modes
    ))

    print("\nMean win rate by strategy (pooled across seeds):")
    print("  " + f"{'strategy':18s} " + " ".join(f"{m:>9s}" for m in modes))
    for s in STRATEGIES:
        cells = []
        for m in modes:
            w = sum(r["strat"].get(s, {}).get("win", 0) for r in runs[m])
            ap_ = sum(r["strat"].get(s, {}).get("app", 0) for r in runs[m])
            cells.append(f"{(w / ap_ if ap_ else 0):>9.3f}")
        print("  " + f"{s:18s} " + " ".join(cells))
    print(f"\n(seeds={a.seeds})")


if __name__ == "__main__":
    main()
