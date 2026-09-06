"""The One Home ratchet: every function-name duplicate needs a recorded verdict.

scripts/find_duplicate_rules.py finds candidates; one_home_verdicts.toml is where
a human's judgment on each one lives. These tests keep the two in sync — a new
duplicate can't ship unrecorded, and a recorded verdict can't quietly go stale.
"""

from __future__ import annotations

import pathlib
from typing import Any

from scripts.find_duplicate_rules import (
    _function_definitions,
    _load_verdicts,
    duplicate_function_names,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL_DOC = REPO_ROOT / ".claude" / "skills" / "failure-archaeology" / "SKILL.md"


def all_definitions(root: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    """Every module-level function name and every file that defines it, count >= 1.

    duplicate_function_names() filters this down to names defined in 2+ files —
    that is the right bar for a *new* duplicate. But a "merged" verdict is a
    claim that a name is gone for good, and a name back in exactly ONE file is
    invisible to that 2+ filter. Checking "merged" against this wider, unfiltered
    set is what catches it.
    """
    out: dict[str, list[pathlib.Path]] = {}
    for name, entries in _function_definitions(root).items():
        if name.startswith("__") or name.startswith("test_"):
            continue
        out[name] = sorted({path for path, _ in entries})
    return out


def _merged_name_problems(
    root: pathlib.Path, entries: list[dict[str, Any]], everywhere: dict[str, list[pathlib.Path]]
) -> list[str]:
    """For every entry with verdict "merged", fail if the name shows up anywhere
    at all — not just in 2+ files."""
    problems: list[str] = []
    for entry in entries:
        if entry["verdict"] != "merged":
            continue
        name = entry["name"]
        back = sorted(str(p.relative_to(root)) for p in everywhere.get(name, []))
        if back:
            problems.append(
                f"{name}() was merged in {entry['pr']}; a copy is back in {back[0]}. "
                f"Import it from {entry['home']}."
            )
    return problems


def test_every_duplicate_name_has_a_verdict_entry() -> None:
    """A same-name function newly defined in 2+ files must get an entry before
    it can ship quietly — this is the ratchet."""
    verdicts = _load_verdicts(REPO_ROOT)
    entries_by_name = {entry["name"]: entry for entry in verdicts["function_names"]}
    for name, paths in duplicate_function_names(REPO_ROOT).items():
        files = sorted(str(p.relative_to(REPO_ROOT)) for p in paths)
        entry = entries_by_name.get(name)
        assert entry is not None and entry["files"] == files, (
            f"{name}() is defined in {files} with no matching one_home_verdicts.toml "
            "entry — merge it, or add an entry with a verdict and a note"
        )


def test_recorded_verdicts_have_not_gone_stale() -> None:
    """A verdict is a claim about the code at the time it was written. If the
    code moved on, the entry has to move with it."""
    verdicts = _load_verdicts(REPO_ROOT)
    live = duplicate_function_names(REPO_ROOT)
    everywhere = all_definitions(REPO_ROOT)

    problems = _merged_name_problems(REPO_ROOT, verdicts["function_names"], everywhere)
    assert not problems, "\n".join(problems)

    for entry in verdicts["function_names"]:
        if entry["verdict"] == "merged":
            continue
        name = entry["name"]
        live_files = sorted(str(p.relative_to(REPO_ROOT)) for p in live.get(name, []))
        assert live_files == entry["files"], (
            f"one_home_verdicts.toml says {name}() is in {entry['files']}, code "
            f"now shows {live_files} — stale entry: delete it or set verdict = merged"
        )


def test_adjudicated_entries_match_failure_archaeology() -> None:
    """[[adjudicated]] mirrors the failure-archaeology "Refactors adjudicated"
    table so a re-proposed refactor gets caught in one place, not two."""
    lines = SKILL_DOC.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## Refactors adjudicated")) + 1
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("## "))
    table_rows = [line for line in lines[start:end] if line.startswith("| ")]
    candidates_in_doc = {row.split("|")[1].strip() for row in table_rows[1:]}

    verdicts = _load_verdicts(REPO_ROOT)
    candidates_in_toml = {entry["candidate"] for entry in verdicts["adjudicated"]}
    assert candidates_in_toml == candidates_in_doc


def test_merged_verdict_catches_a_name_back_in_a_single_file(tmp_path: pathlib.Path) -> None:
    """Pins the bug this ratchet exists to catch: a merged name reappearing in
    exactly ONE file is invisible to duplicate_function_names' 2+ filter, but
    must not be invisible to the merged-verdict check.

    Proven on main before this fix: `def _game_display_name(g): return g` in a
    single new file passed test_one_home_ratchet.py even though its verdict is
    "merged".
    """
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "_zz.py").write_text("def _game_display_name(g):\n    return g\n")
    (tmp_path / "mcp_server").mkdir()

    # The 2+ filter alone misses it — that's the bug.
    assert "_game_display_name" not in duplicate_function_names(tmp_path)

    entries = [
        {
            "name": "_game_display_name",
            "verdict": "merged",
            "pr": "PR H",
            "home": "app/games/__init__.py",
        },
        {
            "name": "_now",
            "verdict": "merged",
            "pr": "PR A",
            "home": "app/engine/turn_clock.py",
        },
    ]
    everywhere = all_definitions(tmp_path)
    problems = _merged_name_problems(tmp_path, entries, everywhere)
    assert problems == [
        "_game_display_name() was merged in PR H; a copy is back in app/_zz.py. "
        "Import it from app/games/__init__.py."
    ]
