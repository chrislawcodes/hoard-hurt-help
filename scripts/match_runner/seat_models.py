#!/usr/bin/env python3
"""Print each seat's agent id and the model the server says it plays.

Reads a match export on stdin, writes `agent_id<TAB>model` lines. Used by
run_all.sh to launch every agent on the model the SERVER chose, rather than on
one value set in the shell.

It is a file rather than a line of inline python inside the shell script because
the quoting for that was genuinely unreadable, and an unreadable launch path is
how a whole match ends up running on the wrong model without anyone noticing.

A seat with no model resolves to an empty string, and the caller falls back —
loudly. Never guess a model here: the point of this file is that one place
decides, and that place is the server.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        export = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"seat_models: could not read the export: {exc}", file=sys.stderr)
        return 1
    players = export.get("players")
    if not players:
        print("seat_models: the export lists no players", file=sys.stderr)
        return 1
    for player in players:
        print(f"{player.get('agent_id', '')}\t{player.get('model') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
