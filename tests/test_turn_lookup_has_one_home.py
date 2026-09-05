"""Tripwire: the turn-at-position lookup has exactly one home.

`app/engine/agent_play_reads.py::load_turn_at` is the one place allowed to
filter on the exact (Turn.match_id, Turn.round, Turn.turn) triple. One of its
three original call sites (the scheduler's turn opener) carries the
mid-deploy freeze fix's resume idempotency check (see
docs/operations/debugging-history.md, G_0012) — a second hand-rolled copy is
exactly how that fix could quietly drift out of step with itself again.
"""

from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("app", "mcp_server")
ALLOWED_FILE = REPO_ROOT / "app" / "engine" / "agent_play_reads.py"
NEEDLE = "Turn.turn =="


def test_turn_position_filter_has_one_home() -> None:
    violations: list[str] = []
    for top in SOURCE_ROOTS:
        for path in sorted((REPO_ROOT / top).rglob("*.py")):
            if path == ALLOWED_FILE or "__pycache__" in str(path):
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                if NEEDLE in line:
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert not violations, (
        "'Turn.turn ==' found outside app/engine/agent_play_reads.py — the "
        "turn-at-position lookup has one home (load_turn_at); call it instead "
        "of re-deriving the filter:\n" + "\n".join(violations)
    )
