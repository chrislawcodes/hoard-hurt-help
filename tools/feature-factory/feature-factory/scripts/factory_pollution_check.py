#!/usr/bin/env python3
"""Auto-revert polluted test-only state.json files at runner startup.

Tests that call factory_state.update_workflow_state (directly or via the
@mutates_state decorator) can leave modified state.json files under
docs/workflow/feature-runs/<slug>/ when their FACTORY_RUNS_ROOT patch is
incomplete (see PR #765 / PR #ff-test-isolation-hard-block for the root
cause and the fix).

This module reverts those files at startup so the operator never has to
run ``git checkout --`` manually before committing.

Usage (from run_factory.py main()):

    from factory_pollution_check import auto_revert_polluted_state
    auto_revert_polluted_state()

Behaviour:
- Walks ``git status --porcelain docs/workflow/feature-runs/`` from the repo root.
- For every modified-but-unstaged state.json whose slug appears in
  KNOWN_TEST_ONLY_SLUGS, runs ``git checkout -- <path>``.
- Prints to stderr: ``[ff] auto-reverted N polluted test-only state.json
  files: <list>``.
- Silent if there is nothing to revert.
- Silent (skips entirely) when:
  - Not inside a git repo.
  - Env var FF_NO_POLLUTION_AUTO_CLEAN=1 is set.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Known test-only slugs whose state.json may be left dirty by the test suite.
# These slugs exist ONLY to support tests — they are not real feature runs.
#
# If you add a new test that creates a slug whose state.json leaks into the
# working tree, add it here as belt-and-suspenders while you fix the test.
# ---------------------------------------------------------------------------
KNOWN_TEST_ONLY_SLUGS: frozenset[str] = frozenset(
    {
        # test_review_gc.py — SLUG = "ff-gc-checkpoint"
        "ff-gc-checkpoint",
    }
)

_WORKFLOW_RUNS_PREFIX = "docs/workflow/feature-runs/"


def _find_repo_root() -> Path | None:
    """Return the git repo root, or None if not inside a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except FileNotFoundError:
        pass
    return None


def _dirty_state_json_slugs(repo_root: Path) -> list[tuple[str, Path]]:
    """Return (slug, abs_path) pairs for modified-but-unstaged state.json files."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", _WORKFLOW_RUNS_PREFIX],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    found: list[tuple[str, Path]] = []
    for line in result.stdout.splitlines():
        # --porcelain format: XY <path>
        # Modified-but-unstaged: " M" in columns 0-1 (index column is space)
        if len(line) < 4:
            continue
        xy = line[:2]
        # Unstaged modification: worktree column (index 1) is 'M'
        if xy[1] != "M":
            continue
        rel = line[3:]
        # Must be inside docs/workflow/feature-runs/
        if not rel.startswith(_WORKFLOW_RUNS_PREFIX):
            continue
        # Must be a state.json file
        if not rel.endswith("/state.json"):
            continue
        # Extract slug from path: docs/workflow/feature-runs/<slug>/state.json
        remainder = rel[len(_WORKFLOW_RUNS_PREFIX):]
        parts = remainder.split("/")
        if len(parts) != 2 or parts[1] != "state.json":
            continue
        slug = parts[0]
        found.append((slug, repo_root / rel))
    return found


def auto_revert_polluted_state(*, repo_root: Path | None = None) -> list[str]:
    """Revert polluted test-only state.json files.

    Returns the list of slugs that were reverted (empty when nothing to do).
    Prints a one-line diagnostic to stderr when any revert happens.
    """
    if os.environ.get("FF_NO_POLLUTION_AUTO_CLEAN") == "1":
        return []

    if repo_root is None:
        repo_root = _find_repo_root()
    if repo_root is None:
        return []

    dirty = _dirty_state_json_slugs(repo_root)
    reverted: list[str] = []

    for slug, abs_path in dirty:
        if slug not in KNOWN_TEST_ONLY_SLUGS:
            continue
        rel = abs_path.relative_to(repo_root)
        result = subprocess.run(
            ["git", "checkout", "--", str(rel)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            reverted.append(slug)
        else:
            print(
                f"[ff] warning: failed to revert {rel}: {result.stderr.strip()}",
                file=sys.stderr,
            )

    if reverted:
        print(
            f"[ff] auto-reverted {len(reverted)} polluted test-only state.json "
            f"file(s): {', '.join(reverted)}",
            file=sys.stderr,
        )

    return reverted
