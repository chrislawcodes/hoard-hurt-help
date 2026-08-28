#!/usr/bin/env python3
"""Resume a match from any turn, with any seat played by anyone.

Seeds the turns that were recorded, tells the match where it stopped, and hands
it to the game's own loop to finish. Nothing about scoring, talk, round awards
or finishing is reimplemented here — the engine already knows how to resume a
match, because that is how it survives a mid-deploy restart.

WHY IT EXISTS. A match killed mid-flight cannot be rewound on the server: once
turns default they are recorded, and a default scores as a HOARD, so fabricated
moves land in the measured data. M_7558 lost rounds five through seven that way.
Resuming from the last clean turn produces the rest from real decisions. The
same machinery answers what-if questions — change one seat, replay from a
position, see what follows — for the price of the turns you replay rather than a
whole match.

WHO PLAYS IS THE FLEXIBLE PART. Seats resume as free scripted bots by default.
Name a seat with --seat to give it a model, or --live to put every seat on the
model and strategy it actually had.

WHAT A REPLAY IS NOT: a live match. No deadline pressure, seats answer in
sequence rather than at once, and a model reasons about a position it did not
play into. Evidence about a decision, not a result.

usage:
  replay_match.py <export.json> --from R5T1 --dry-run
  replay_match.py <export.json> --from R5T1
  replay_match.py <export.json> --from R5T1 --seat "No Playbook=model:claude-opus-5"
  replay_match.py <export.json> --from R5T1 --live
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

DEFAULT_PERSONALITY = "pragmatist"


def parse_turn(text: str) -> tuple[int, int]:
    """'R3T2' -> (3, 2). Rejects anything else rather than guessing."""
    t = text.strip().upper()
    if not t.startswith("R") or "T" not in t:
        raise SystemExit(f"wanted a turn like R3T2, got {text!r}")
    r, _, n = t[1:].partition("T")
    if not r.isdigit() or not n.isdigit():
        raise SystemExit(f"wanted a turn like R3T2, got {text!r}")
    return int(r), int(n)


def parse_seat(text: str) -> tuple[str, tuple[str, str]]:
    """'TfT r2=model:claude-opus-5' -> ('TfT r2', ('model', 'claude-opus-5'))."""
    name, _, spec = text.partition("=")
    kind, _, value = spec.partition(":")
    if not name.strip() or kind not in {"bot", "model"}:
        raise SystemExit(f"--seat wants NAME=bot:STRATEGY or NAME=model:ID, got {text!r}")
    return name.strip(), (kind, value.strip())


def call_claude(model: str, prompt: str) -> str:
    """One model call through the same CLI the live match runner uses.

    Checked, not trusted: a non-zero exit or empty output is a failure, because
    a seat that silently produces nothing would be defaulted to a HOARD.
    """
    proc = subprocess.run(
        ["claude", "--print", "--model", model],
        input=prompt, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise SystemExit(f"claude --model {model} exited {proc.returncode}: {proc.stderr[:400]}")
    if not proc.stdout.strip():
        raise SystemExit(f"claude --model {model} returned nothing")
    return proc.stdout


def _seat_keys(preset: str) -> list[str]:
    """The ways a seat name can stand for this preset.

    Seat names get shortened by hand, and two shortenings appear in real
    rosters: the last word ("Underdog's Champion" seated as "Champion r2") and
    the initials ("Tit-for-Tat" as "TfT r2"). Initials only for multi-word
    presets — a one-word preset reduces to a single letter, which matches half
    the roster and makes every seat ambiguous.
    """
    words = [w for w in re.split(r"[^A-Za-z]+", preset) if w]
    keys = [preset.lower().replace("-", "").replace(" ", "")]
    if len(words) > 1:
        keys.append(words[-1].lower())
        keys.append("".join(w[0] for w in words).lower())
    return keys


def seat_playbooks(export: dict[str, Any], seats: list[str]) -> dict[str, dict[str, str]]:
    """Each seat's strategy text and model, matched by the preset its prompt names.

    The export's two halves do not join — `players` keys by numeric agent id and
    `submissions` by seat name — so this has to match on the preset name. That is
    a guess, so a seat matching zero or several presets is simply left out, and
    anything needing the text refuses rather than handing a model the wrong
    playbook.
    """
    out: dict[str, dict[str, str]] = {}
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
            out[hits[0]] = {"strategy": prompt, "model": player.get("model") or ""}
    return out


def warn_if_rules_moved(recorded: str | None) -> None:
    """Say loudly when the resumed turns will be scored under different payoffs.

    A replay always uses the payoff table in today's code, so resuming a match
    recorded under older rules RESCORES the part it replays — and nothing about
    the output looks unusual. Measured on M_7474: a v10 match replayed under v11
    lost its attacker four to five points a round and moved two round wins.
    """
    from app.games.hoard_hurt_help.rules import RULES_VERSION

    if recorded and recorded != RULES_VERSION:
        print(f"   NOTE: recorded under {recorded}, replaying under {RULES_VERSION}.")
        print("         Scores will differ from the original because the payoffs moved.")


def position_before(export: dict[str, Any], upto: tuple[int, int]) -> dict[str, Any]:
    """Everything that happened before the given turn, without running anything."""
    prior = [s for s in export["submissions"] if (s["round"], s["turn"]) < upto]
    seats = sorted({s["agent_id"] for s in export["submissions"]})
    scores = {a: 0 for a in seats}
    defaulted = sum(1 for s in prior if s.get("was_defaulted"))
    for s in prior:
        if s["round"] == upto[0]:
            scores[s["agent_id"]] = s["round_score_after"]
    return {"prior": prior, "seats": seats, "round_scores": scores, "defaulted": defaulted}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("export")
    ap.add_argument("--from", dest="start", required=True, help="first turn to replay, e.g. R5T1")
    ap.add_argument("--seat", action="append", default=[],
                    help="NAME=bot:STRATEGY or NAME=model:ID")
    ap.add_argument("--live", action="store_true",
                    help="every seat resumes on the model and strategy it actually had")
    ap.add_argument("--db", default="/tmp/hhh-replay.sqlite3")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, run nothing")
    args = ap.parse_args()

    with open(args.export) as fh:
        export = json.load(fh)
    if not export.get("submissions"):
        raise SystemExit(f"{args.export}: no submissions to replay")

    start = parse_turn(args.start)
    overrides = dict(parse_seat(s) for s in args.seat)
    pos = position_before(export, start)
    unknown = set(overrides) - set(pos["seats"])
    if unknown:
        raise SystemExit(f"no such seat(s) in this export: {', '.join(sorted(unknown))}")

    books = seat_playbooks(export, pos["seats"])
    plays: dict[str, Any] = {}
    for seat in pos["seats"]:
        kind, value = overrides.get(seat, ("live" if args.live else "bot", DEFAULT_PERSONALITY))
        if kind == "bot":
            plays[seat] = value
            continue
        book = books.get(seat)
        if book is None:
            raise SystemExit(
                f"cannot put a model in seat {seat!r}: its strategy could not be matched "
                f"to a preset in this export. Give it a bot instead (--seat '{seat}=bot:grudger')."
            )
        model = value if kind == "model" else book["model"]
        if not model:
            raise SystemExit(f"seat {seat!r} has no recorded model; name one with model:ID")
        from replay_seats import ModelSeat

        plays[seat] = ModelSeat(model, book["strategy"], call_claude)

    game = export["game"]
    total = len({(s["round"], s["turn"]) for s in export["submissions"]})
    seeded = len({(s["round"], s["turn"]) for s in pos["prior"]})
    print(f"REPLAY {game['id']} — {game['name']}   rules {game.get('rules_version', '?')}")
    warn_if_rules_moved(game.get("rules_version"))
    print(f"   seeding {seeded} recorded turns, replaying the remaining {total - seeded}")
    if pos["defaulted"]:
        print(f"   WARNING: {pos['defaulted']} of the seeded moves were defaults, not decisions.")
        print("            Cut earlier if you want a position built only from real play.")
    print(f"\n   {'seat':<20}{'round score':>12}   resumes as")
    for seat in pos["seats"]:
        play = plays[seat]
        how = f"bot: {play}" if isinstance(play, str) else play.name
        print(f"   {seat:<20}{pos['round_scores'].get(seat, 0):>12}   {how}")

    costed = [s for s, p in plays.items() if not isinstance(p, str)]
    if costed:
        print(f"\n   {len(costed)} seat(s) will call a model twice a turn (talk, then act).")
    if args.dry_run:
        print("\n   --dry-run: nothing was run.")
        return 0

    from replay_engine import run_replay

    result = asyncio.run(
        run_replay(export, start=start, seats_play=plays, db_path=args.db)
    )

    # The recorded result, counted by the match report's own rule, so a
    # difference is a real difference and not two ways of splitting a tie.
    was = round_wins_from_submissions(export["submissions"])
    was_pts = total_points_from_submissions(export["submissions"])
    print(f"\n   replayed {result.turns_played} turns forward\n")
    print(f"   {'seat':<20}{'wins':>7}{'points':>8}   {'was':>6}{'':>8}")
    same = True
    for seat, wins in sorted(result.round_wins.items(), key=lambda kv: -kv[1]):
        ow, op = was.get(seat, 0.0), was_pts.get(seat, 0)
        pts = result.totals.get(seat, 0)
        changed = abs(wins - ow) > 0.01 or pts != op
        same = same and not changed
        print(f"   {seat:<20}{wins:>7.1f}{pts:>8}   {ow:>6.1f}{op:>8}"
              + ("   <- changed" if changed else ""))
    if same and not result.moves:
        print("\n   reproduces the recorded result exactly.")
    if result.moves:
        print(f"\n   model-played moves ({len(result.moves)}):")
        for m in result.moves:
            tgt = f" -> {m['target']}" if m["target"] else ""
            print(f"   R{m['round']}T{m['turn']}  {m['seat']:<20}{m['action']}{tgt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
