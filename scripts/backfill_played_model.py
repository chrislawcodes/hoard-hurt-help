#!/usr/bin/env python3
"""Write the model each seat played onto matches that ran before it was recorded.

`Player.played_model` was added in migration 0056, and the export now reads it
instead of the agent's live setting. That fixed a real bug — editing an agent
used to rewrite what every past match claimed it played — but it also blanked
the eleven matches already run, because nothing had stamped them.

Those models are not unknown. Seven were downloaded into local export files
before the change and still carry the value; the four earliest predate the
export field and were confirmed by hand as claude-haiku-4-5. This writes those
known values down so the record says again what it used to say, and cannot drift
again.

WHAT IT WILL NOT DO:

  It never overwrites a value. Only rows where `played_model IS NULL` are
  touched, so a seat that recorded its own model at play time always wins over
  anything asserted here.

  It never invents one. A seat whose export carries no model is skipped unless
  you name one on the command line with --default-model, so the assumption is
  visible in the command someone typed rather than buried in this file.

  It changes nothing without --apply. The default is a dry run that prints every
  write it would make.

usage:
  backfill_played_model.py --exports ~/.agentludum/exports            # dry run
  backfill_played_model.py --exports ~/.agentludum/exports \
      --match M_7344 --match M_7444 --default-model claude-haiku-4-5  # dry run
  backfill_played_model.py --exports ~/.agentludum/exports --apply    # writes
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_exports(directory: str, only: list[str]) -> list[dict[str, Any]]:
    """Every export in a directory, or just the named matches."""
    found = []
    for path in sorted(glob.glob(os.path.join(os.path.expanduser(directory), "*.json"))):
        with open(path) as fh:
            export = json.load(fh)
        match_id = (export.get("game") or {}).get("id")
        if not match_id:
            print(f"  skipping {os.path.basename(path)}: no match id", file=sys.stderr)
            continue
        if only and match_id not in only:
            continue
        found.append(export)
    return found


def planned_writes(
    export: dict[str, Any], default_model: str | None
) -> tuple[str, list[tuple[int, str]], int]:
    """(match_id, [(agent_id, model)], how many seats had no model to write).

    The export's `players` block keys by NUMERIC agent id, which is exactly
    `Player.agent_id` — so this joins on a real key and never has to guess from
    a seat name.
    """
    match_id = export["game"]["id"]
    writes: list[tuple[int, str]] = []
    unknown = 0
    for player in export.get("players", []):
        model = player.get("model") or default_model
        if not model:
            unknown += 1
            continue
        try:
            agent_id = int(player["agent_id"])
        except (KeyError, TypeError, ValueError):
            unknown += 1
            continue
        writes.append((agent_id, model))
    return match_id, writes, unknown


async def apply_writes(plans: list[tuple[str, list[tuple[int, str]], int]]) -> int:
    """Stamp the planned models. Returns how many rows actually changed."""
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models.player import Player

    changed = 0
    async with SessionLocal() as db:
        for match_id, writes, _ in plans:
            for agent_id, model in writes:
                result = await db.execute(
                    update(Player)
                    .where(
                        Player.match_id == match_id,
                        Player.agent_id == agent_id,
                        # Never overwrite: a seat that recorded its own model at
                        # play time is the better source than anything asserted
                        # here, and this must be safe to run twice.
                        Player.played_model.is_(None),
                    )
                    .values(played_model=model)
                )
                changed += result.rowcount or 0
        await db.commit()
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exports", required=True, help="directory of match export .json files")
    ap.add_argument("--match", action="append", default=[], help="only this match id (repeatable)")
    ap.add_argument(
        "--default-model",
        help="model for seats whose export carries none. Stated here on purpose: "
        "an assumption belongs in the command someone typed, not in the script.",
    )
    ap.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
    args = ap.parse_args()

    exports = load_exports(args.exports, args.match)
    if not exports:
        raise SystemExit(f"no exports found in {args.exports}")

    plans = [planned_writes(e, args.default_model) for e in exports]
    total = sum(len(w) for _, w, _ in plans)
    unknown = sum(u for _, _, u in plans)

    print(f"{'match':<10}{'seats to stamp':>15}   models")
    for match_id, writes, missing in plans:
        models = sorted({m for _, m in writes})
        note = f"   ({missing} seat(s) with no model — skipped)" if missing else ""
        print(f"{match_id:<10}{len(writes):>15}   {', '.join(models) or '—'}{note}")
    print(f"\n{total} seat(s) would be stamped; {unknown} skipped for having no model.")

    if not args.apply:
        print("\nDRY RUN — nothing was written. Re-run with --apply to write.")
        return 0
    if not os.environ.get("DATABASE_URL"):
        raise SystemExit("--apply needs DATABASE_URL pointing at the database to write")

    changed = asyncio.run(apply_writes(plans))
    print(f"\nwrote {changed} row(s).")
    if changed != total:
        # Not an error: rows already stamped are skipped by design, and this is
        # safe to run twice. Said out loud so the difference is never a mystery.
        print(f"({total - changed} were already stamped, or no such seat exists.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
