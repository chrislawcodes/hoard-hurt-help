#!/usr/bin/env python3
"""Size auto-classification for Feature Factory workflow slugs.

Exports:
    estimate_size(slug: str) -> dict

The returned dict has the shape:
    {
        "size": "trivial" | "small" | "medium" | "large",
        "recommended_path": "none" | "quick" | "full",
        "signals": {
            "scope_path_count": int,
            "summary_chars": int,
            "diff_lines": int | None,
            "changed_files": int | None,
        },
        "reasoning": "<one-line human explanation>",
    }

Size bands (ordered from smallest to largest):
  trivial — ≤2 scope paths AND <300-char summary AND <100 diff lines AND ≤3 changed files.
            These thresholds were chosen to capture single-component UI tweaks, type-cast
            fixes, and copy edits that fit in a single short Codex prompt. For features
            this small, FF runner overhead exceeds its protection value.
  small   — ≤3 scope paths AND <500-char summary AND <200 diff lines AND ≤5 changed files.
  medium  — anything between small and large.
  large   — any single signal exceeds a large threshold (≥10 paths, >1500 chars, >800 lines,
            ≥15 files).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import factory_state

# ---------------------------------------------------------------------------
# Trivial-band thresholds — tune these constants if the heuristic is too
# aggressive or too conservative.  A feature must satisfy ALL four to be
# classified as trivial (all-signals-AND logic, same as the small band).
# ---------------------------------------------------------------------------
_TRIVIAL_MAX_PATHS = 2       # ≤ 2 scope paths
_TRIVIAL_MAX_CHARS = 300     # < 300-char discovery summary
_TRIVIAL_MAX_DIFF_LINES = 100  # < 100 diff lines  (insertions + deletions)
_TRIVIAL_MAX_FILES = 3       # ≤ 3 changed files


def _read_scope_path_count(slug: str) -> int:
    """Return the number of paths in scope.json, or 0 if missing/malformed."""
    scope_path = factory_state.workflow_dir(slug) / "scope.json"
    if not scope_path.exists():
        return 0
    try:
        raw = json.loads(scope_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, dict):
        paths = raw.get("paths", raw.get("scope_paths", []))
        if isinstance(paths, list):
            return len(paths)
    return 0


def _read_summary_chars(slug: str) -> int:
    """Return the character length of discovery.summary in state.json, or 0."""
    state_path = factory_state.factory_state_path(slug)
    if not state_path.exists():
        return 0
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    discovery = raw.get("discovery", {})
    if not isinstance(discovery, dict):
        return 0
    summary = discovery.get("summary", "")
    if not isinstance(summary, str):
        return 0
    return len(summary)


def _git_diff_stats() -> tuple[int | None, int | None]:
    """Return (diff_lines, changed_files) against origin/main, or (None, None).

    Parses the last line of `git diff origin/main --stat`, which looks like:
        3 files changed, 120 insertions(+), 45 deletions(-)

    Returns None for both values if origin/main does not exist, the repo is not
    a git repo, or the stat line cannot be parsed.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "origin/main", "--stat"],
            cwd=factory_state.REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, None
    if result.returncode != 0:
        return None, None

    output = result.stdout.strip()
    if not output:
        return 0, 0

    # The summary line is the last line when there are changes, e.g.:
    # " 3 files changed, 120 insertions(+), 45 deletions(-)"
    lines = output.splitlines()
    summary_line = lines[-1].strip()

    diff_lines: int | None = None
    changed_files: int | None = None

    import re

    m_files = re.search(r"(\d+)\s+files? changed", summary_line)
    if m_files:
        changed_files = int(m_files.group(1))

    m_insert = re.search(r"(\d+)\s+insertion", summary_line)
    m_delete = re.search(r"(\d+)\s+deletion", summary_line)
    insertions = int(m_insert.group(1)) if m_insert else 0
    deletions = int(m_delete.group(1)) if m_delete else 0
    if m_files:
        diff_lines = insertions + deletions

    return diff_lines, changed_files


def _classify(
    scope_path_count: int,
    summary_chars: int,
    diff_lines: int | None,
    changed_files: int | None,
) -> str:
    """Return 'trivial', 'small', 'medium', or 'large' based on heuristics."""
    # Large: any single signal exceeds the large threshold.
    if scope_path_count >= 10:
        return "large"
    if summary_chars > 1500:
        return "large"
    if diff_lines is not None and diff_lines > 800:
        return "large"
    if changed_files is not None and changed_files >= 15:
        return "large"

    # Trivial: ALL signals within trivial bounds (AND logic).
    # Note: when diff_lines/changed_files are None (no diff yet), we treat
    # them as within bounds — trivial classification can happen early in the
    # workflow before any code is written.
    trivial_diff_ok = diff_lines is None or diff_lines < _TRIVIAL_MAX_DIFF_LINES
    trivial_files_ok = changed_files is None or changed_files <= _TRIVIAL_MAX_FILES
    if (
        scope_path_count <= _TRIVIAL_MAX_PATHS
        and summary_chars < _TRIVIAL_MAX_CHARS
        and trivial_diff_ok
        and trivial_files_ok
    ):
        return "trivial"

    # Small: all signals within small bounds.
    diff_ok = diff_lines is None or diff_lines < 200
    files_ok = changed_files is None or changed_files <= 5
    if scope_path_count <= 3 and summary_chars < 500 and diff_ok and files_ok:
        return "small"

    return "medium"


def _build_reasoning(
    size: str,
    scope_path_count: int,
    summary_chars: int,
    diff_lines: int | None,
    changed_files: int | None,
    scope_missing: bool,
) -> str:
    if scope_missing:
        return "scope.json missing — classified as medium with no scope-path signal"

    parts: list[str] = []
    if size == "trivial":
        diff_note = "no diff yet" if diff_lines is None else f"{diff_lines} diff lines"
        return (
            f"{scope_path_count} scope path{'s' if scope_path_count != 1 else ''}, "
            f"{summary_chars}-char summary, {diff_note} — all within trivial bounds"
        )
    elif size == "large":
        if scope_path_count >= 10:
            parts.append(f"{scope_path_count} scope paths (>= 10)")
        if summary_chars > 1500:
            parts.append(f"{summary_chars}-char summary (> 1500)")
        if diff_lines is not None and diff_lines > 800:
            parts.append(f"{diff_lines} diff lines (> 800)")
        if changed_files is not None and changed_files >= 15:
            parts.append(f"{changed_files} changed files (>= 15)")
        return "large signals: " + ", ".join(parts)
    elif size == "small":
        diff_note = "no diff yet" if diff_lines is None else f"{diff_lines} diff lines"
        return (
            f"{scope_path_count} scope path{'s' if scope_path_count != 1 else ''}, "
            f"{summary_chars}-char summary, {diff_note}"
        )
    else:
        # medium — explain what kept it out of small
        reasons: list[str] = []
        if scope_path_count > 3:
            reasons.append(f"{scope_path_count} scope paths (> 3)")
        if summary_chars >= 500:
            reasons.append(f"{summary_chars}-char summary (>= 500)")
        if diff_lines is not None and diff_lines >= 200:
            reasons.append(f"{diff_lines} diff lines (>= 200)")
        if changed_files is not None and changed_files > 5:
            reasons.append(f"{changed_files} changed files (> 5)")
        if reasons:
            return "medium: " + ", ".join(reasons)
        return "medium by default"


def estimate_size(slug: str) -> dict:
    """Return a size-estimate dict for the given workflow slug.

    The dict shape is:
        {
            "size": "small" | "medium" | "large",
            "recommended_path": "quick" | "full",
            "signals": {
                "scope_path_count": int,
                "summary_chars": int,
                "diff_lines": int | None,
                "changed_files": int | None,
            },
            "reasoning": "<one-line human explanation>",
        }
    """
    # Check if scope.json exists for the reasoning message.
    scope_path = factory_state.workflow_dir(slug) / "scope.json"
    scope_missing = not scope_path.exists()

    scope_path_count = _read_scope_path_count(slug)
    summary_chars = _read_summary_chars(slug)
    diff_lines, changed_files = _git_diff_stats()

    # If scope.json is missing, default to medium regardless of other signals.
    if scope_missing:
        size = "medium"
    else:
        size = _classify(scope_path_count, summary_chars, diff_lines, changed_files)

    if size == "trivial":
        recommended_path = "none"
    elif size == "small":
        recommended_path = "quick"
    else:
        recommended_path = "full"
    reasoning = _build_reasoning(
        size, scope_path_count, summary_chars, diff_lines, changed_files, scope_missing
    )

    return {
        "size": size,
        "recommended_path": recommended_path,
        "signals": {
            "scope_path_count": scope_path_count,
            "summary_chars": summary_chars,
            "diff_lines": diff_lines,
            "changed_files": changed_files,
        },
        "reasoning": reasoning,
    }
