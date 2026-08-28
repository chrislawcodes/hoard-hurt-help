#!/usr/bin/env python3
"""Replay a match from any turn, with any seat played by anyone.

Two jobs, both of which currently cost a full 35-minute live match.

RECOVER. A match killed mid-flight cannot be rewound on the server: once turns
default they are recorded, and a default scores as a HOARD, so the fabricated
moves land in the measured data. M_7558 lost rounds five through seven that way
to a bug in this runner's own alarm. Replaying from the last clean turn produces
the rest from real decisions instead.

ASK WHAT-IF. Take a real position and change one thing — a preset's wording, a
model, one seat's behaviour — and see what follows. Today that costs a whole
match; here it costs the turns you choose to replay.

WHO PLAYS IS THE FLEXIBLE PART. Every seat defaults to the move it actually
made, and any seat can be overridden: a scripted action, one of the repo's
bots, or a live model. With no overrides the replay reproduces the original
match, which is what makes it trustworthy enough to change something.

SCORING IS NOT REIMPLEMENTED. Turns are scored by the game's own resolve_turn
against a local SQLite database, rounds by the real award_round_winners. A
second copy of the payoff table would answer questions about the copy.

WHAT A REPLAY IS NOT: a match. There is no deadline pressure and no
simultaneity, and a model seat answers a reconstructed position rather than one
it played into. Read it as evidence about a decision, not as a result.

usage:
  replay_match.py <export.json> --from R3T2 --dry-run
  replay_match.py <export.json> --from R5T1 --seat "Hoarder r2=always:HOARD"
  replay_match.py <export.json> --from R5T1 --seat "TfT r2=bot:grudger"
  replay_match.py <export.json> --from R5T1 --seat "No Playbook=model:claude-sonnet-5"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from match_report import round_wins_from_submissions, total_points_from_submissions  # noqa: E402
from replay_seats import KeepDriver, Move, build_driver  # noqa: E402


def parse_turn(text: str) -> tuple[int, int]:
    """'R3T2' -> (3, 2). Rejects anything else rather than guessing."""
    t = text.strip().upper()
    if not t.startswith("R") or "T" not in t:
        raise SystemExit(f"wanted a turn like R3T2, got {text!r}")
    r, _, n = t[1:].partition("T")
    if not r.isdigit() or not n.isdigit():
        raise SystemExit(f"wanted a turn like R3T2, got {text!r}")
    return int(r), int(n)


def parse_seat(text: str) -> tuple[str, dict[str, str]]:
    """'Hoarder r2=always:HOARD' -> ('Hoarder r2', {'always': 'HOARD'})."""
    name, _, spec = text.partition("=")
    kind, _, value = spec.partition(":")
    if not name.strip() or kind not in {"always", "bot", "model"}:
        raise SystemExit(
            f"--seat wants NAME=always:ACTION | NAME=bot:STRATEGY | NAME=model:ID, got {text!r}"
        )
    return name.strip(), {kind: value.strip()}


def call_claude(model: str, prompt: str) -> str:
    """One model call through the same CLI the live match runner uses.

    Checked, not trusted: a non-zero exit or empty output is a failure, because a
    seat that silently produces nothing would be defaulted to a HOARD — the exact
    fabrication this tool exists to undo.
    """
    proc = subprocess.run(
        ["claude", "--print", "--model", model],
        input=prompt, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise SystemExit(f"claude --model {model} exited {proc.returncode}: {proc.stderr[:400]}")
    if not proc.stdout.strip():
        raise SystemExit(f"claude --model {model} returned nothing")
    return proc.stdout


def load_export(path: str) -> dict[str, Any]:
    with open(path) as fh:
        d = json.load(fh)
    if not d.get("submissions"):
        raise SystemExit(f"{path}: no submissions to replay")
    return d


def position_before(export: dict[str, Any], upto: tuple[int, int]) -> dict[str, Any]:
    """Everything that happened before the given turn, without running anything."""
    prior = [s for s in export["submissions"] if (s["round"], s["turn"]) < upto]
    seats = sorted({s["agent_id"] for s in export["submissions"]})
    scores = {a: 0 for a in seats}
    for s in prior:
        if s["round"] == upto[0]:
            scores[s["agent_id"]] = s["round_score_after"]
    return {"prior": prior, "seats": seats, "round_scores": scores}


def _seat_keys(preset: str) -> list[str]:
    """The ways a seat name can stand for this preset.

    Seat names are shortened by hand, and two shortenings show up in real
    rosters: the last word ("Underdog's Champion" seated as "Champion r2") and
    the initials ("Tit-for-Tat" seated as "TfT r2"). Both are here because both
    appear in exports we already have, not on speculation.
    """
    words = [w for w in re.split(r"[^A-Za-z]+", preset) if w]
    keys = [preset.lower().replace("-", "").replace(" ", "")]
    if len(words) > 1:
        # Initials only for multi-word presets: a one-word preset reduces to a
        # single letter, which matches half the roster and makes every seat
        # ambiguous. That over-match silently dropped three correct seats.
        keys.append(words[-1].lower())
        keys.append("".join(w[0] for w in words).lower())
    return keys


def strategy_text_by_seat(export: dict[str, Any], seats: list[str]) -> dict[str, str]:
    """Each seat's strategy text, matched by the preset its prompt names.

    The export's two halves do not join — `players` keys by numeric agent id and
    `submissions` by seat name, with nothing linking them — so a seat's strategy
    has to be recovered by matching the preset named in the prompt against the
    seat name. That is a guess, so a seat matching zero or several presets is
    left out, and a caller that needs the text must refuse rather than hand a
    model somebody else's playbook.
    """
    out: dict[str, str] = {}
    for player in export.get("players", []):
        prompt = player.get("strategy_prompt") or ""
        found = re.search(r"Strategy:\s*([^.\n]+)", prompt)
        if not found:
            continue
        keys = _seat_keys(found.group(1).strip())
        hits = [
            s for s in seats
            if any(s.lower().replace("-", "").replace(" ", "").startswith(k) for k in keys)
        ]
        if len(hits) == 1:
            out[hits[0]] = prompt
    return out


def warn_if_rules_moved(recorded_version: str | None) -> None:
    """Say loudly when the replay will score the match under different payoffs.

    A replay always uses the payoff table in today's code. Replaying a match
    recorded under older rules therefore RESCORES it, and nothing about the
    output looks unusual — measured on M_7474, a v10 match replayed under v11
    lost its attacker four to five points a round and handed two round wins to
    different seats.

    That is a legitimate question ("what would this match have scored under the
    new payoffs?") and a trap for the other one ("finish this interrupted
    match"), which is why it is stated rather than guarded: only the person
    asking knows which of the two they meant.
    """
    from app.games.hoard_hurt_help.rules import RULES_VERSION

    if recorded_version and recorded_version != RULES_VERSION:
        print(f"   NOTE: recorded under {recorded_version}, replaying under {RULES_VERSION}.")
        print("         Scores will differ from the original because the payoffs moved.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export")
    ap.add_argument("--from", dest="start", required=True, help="first turn to replay, e.g. R3T2")
    ap.add_argument("--to", dest="stop", help="last turn to replay (default: end of match)")
    ap.add_argument("--seat", action="append", default=[],
                    help="NAME=always:ACTION | NAME=bot:STRATEGY | NAME=model:ID")
    ap.add_argument("--db", default="/tmp/hhh-replay.sqlite3")
    ap.add_argument("--dry-run", action="store_true", help="show the position and the plan, run nothing")
    args = ap.parse_args()

    export = load_export(args.export)
    start = parse_turn(args.start)
    stop = parse_turn(args.stop) if args.stop else None
    overrides = dict(parse_seat(s) for s in args.seat)

    pos = position_before(export, start)
    unknown = set(overrides) - set(pos["seats"])
    if unknown:
        raise SystemExit(f"no such seat(s) in this export: {', '.join(sorted(unknown))}")

    strategy_by_seat = strategy_text_by_seat(export, pos["seats"])
    recorded: dict[str, dict[tuple[int, int], Move]] = {s: {} for s in pos["seats"]}
    for s in export["submissions"]:
        recorded[s["agent_id"]][(s["round"], s["turn"])] = Move(
            s["action"],
            s.get("target_id"),
            s.get("thinking") or "",
            # Carry the chat line too. Replayed history shows each move with what
            # its player said, so dropping it would hand a resumed agent a table
            # where everyone had been silent for the whole match.
            s.get("message") or "",
        )

    drivers = {}
    for seat in pos["seats"]:
        if seat in overrides:
            drivers[seat] = build_driver(
                overrides[seat],
                recorded=recorded[seat],
                strategy_text=strategy_by_seat.get(seat, ""),
                call=call_claude,
            )
        else:
            drivers[seat] = KeepDriver(recorded[seat])

    game = export["game"]
    played = len({(s["round"], s["turn"]) for s in export["submissions"]})
    warn_if_rules_moved(game.get("rules_version"))
    print(f"REPLAY {game['id']} — {game['name']}   rules {game.get('rules_version', '?')}")
    print(f"   from R{start[0]}T{start[1]}"
          + (f" to R{stop[0]}T{stop[1]}" if stop else " to the end")
          + f"   ({played} turns in the log)")
    print(f"   position carried in: {len(pos['prior'])} prior moves\n")
    print(f"   {'seat':<20}{'round score':>12}   plays as")
    for seat in pos["seats"]:
        print(f"   {seat:<20}{pos['round_scores'].get(seat, 0):>12}   {drivers[seat].name}")

    if args.dry_run:
        print("\n   --dry-run: nothing was run.")
        return 0

    costed = [s for s, d in drivers.items() if d.name.startswith("model:")]
    if costed:
        print(f"\n   {len(costed)} seat(s) will make real model calls.")

    frozen = [s for s, d in drivers.items() if isinstance(d, KeepDriver)]
    if overrides and frozen:
        # Worth saying every time, because the output looks the same either way.
        # A seat on "as recorded" replays the move it made in a match where the
        # change never happened, so it cannot answer the change. The result is
        # "what if this seat played differently and nobody noticed" — which is a
        # real question, but not the one most people mean.
        print(f"\n   NOTE: {len(frozen)} seat(s) replay their recorded moves and do not")
        print("         react to your change. For a table that reacts, give them")
        print("         drivers too (bot: is free, model: is not).")

    from replay_engine import run_replay

    result = asyncio.run(
        run_replay(export, start=start, stop=stop, drivers=drivers, db_path=args.db)
    )

    print(f"\n   replayed {result.turns_replayed} turns of history, "
          f"played {result.turns_played} forward\n")
    # The recorded result, counted by the match report's own rule, so a
    # difference in the "was" column is a real difference and not two ways of
    # splitting a tie.
    was = round_wins_from_submissions(export["submissions"])
    was_pts = total_points_from_submissions(export["submissions"])
    print(f"   {'seat':<20}{'wins':>7}{'points':>8}   {'was':>6}{'':>8}")
    same = True
    for seat, wins in sorted(result.round_wins.items(), key=lambda kv: -kv[1]):
        ow, op = was.get(seat, 0.0), was_pts.get(seat, 0)
        pts = result.totals.get(seat, 0)
        changed = abs(wins - ow) > 0.01 or pts != op
        same = same and not changed
        print(f"   {seat:<20}{wins:>7.1f}{pts:>8}   {ow:>6.1f}{op:>8}"
              + ("   <- changed" if changed else ""))
    if not result.moves and same:
        print("\n   reproduces the recorded result exactly.")

    if result.moves:
        print(f"\n   moves played forward ({len(result.moves)}):")
        for m in result.moves:
            tgt = f" -> {m['target']}" if m["target"] else ""
            print(f"   R{m['round']}T{m['turn']}  {m['seat']:<20}{m['action']}{tgt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
