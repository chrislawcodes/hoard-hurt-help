"""The One Home ratchet: every function-name duplicate needs a recorded verdict.

scripts/find_duplicate_rules.py finds candidates; one_home_verdicts.toml is where
a human's judgment on each one lives. These tests keep the two in sync — a new
duplicate can't ship unrecorded, and a recorded verdict can't quietly go stale.
"""

from __future__ import annotations

import pathlib

from scripts.find_duplicate_rules import _load_verdicts, duplicate_function_names

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL_DOC = REPO_ROOT / ".claude" / "skills" / "failure-archaeology" / "SKILL.md"


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
    for entry in verdicts["function_names"]:
        name = entry["name"]
        live_files = sorted(str(p.relative_to(REPO_ROOT)) for p in live.get(name, []))
        if entry["verdict"] == "merged":
            assert name not in live, f"{name}() is defined in {live_files} again — this merge came back"
        else:
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
