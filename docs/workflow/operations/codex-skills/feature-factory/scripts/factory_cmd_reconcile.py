#!/usr/bin/env python3
"""command_reconcile implementation."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_mutating import mutates_state  # noqa: E402
from factory_state import REPO_ROOT, update_workflow_state, workflow_dir  # noqa: E402
from factory_state import load_workflow_state  # noqa: E402
from factory_reconcile import reconcile_review_full  # noqa: E402
from factory_review import APPEND_RECONCILIATION, UPDATE_REVIEW  # noqa: E402
from factory_emit import _emit_next_action  # noqa: E402
from factory_stages import VERIFY_RECONCILIATION  # noqa: E402


def _capture_head_and_dirty() -> tuple[str | None, set[str]]:
    try:
        head_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        head_at_start = head_result.stdout.strip() if head_result.returncode == 0 else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        head_at_start = None
    try:
        dirty_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        dirty_at_start = set(dirty_result.stdout.splitlines())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        dirty_at_start = set()
    return head_at_start, dirty_at_start


def _git_dir() -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    git_dir = result.stdout.strip()
    if not git_dir:
        return None
    git_dir_path = Path(git_dir)
    if not git_dir_path.is_absolute():
        git_dir_path = REPO_ROOT / git_dir_path
    return git_dir_path


def _git_sentinels_present() -> bool:
    git_dir = _git_dir()
    if git_dir is None:
        return True
    sentinels = [
        "MERGE_HEAD",
        "REBASE_HEAD",
        "rebase-merge",
        "rebase-apply",
        "BISECT_LOG",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
    ]
    return any((git_dir / sentinel).exists() for sentinel in sentinels)


def _update_diff_review_budget(slug: str, head_sha: str) -> None:
    def mutate(state: dict) -> None:
        review_budget = state.get("diff_review_budget")
        if not isinstance(review_budget, dict):
            review_budget = {}
            state["diff_review_budget"] = review_budget
        review_budget["recorded_head"] = head_sha
        review_budget["last_review_only_advance_at"] = int(time.time())

    update_workflow_state(slug, mutate)


@mutates_state("reconcile")
def command_reconcile(args: argparse.Namespace) -> int:
    head_at_start, dirty_at_start = _capture_head_and_dirty()
    from factory_git import ensure_sync  # noqa: E402

    ensure_sync()
    plan_path = workflow_dir(args.slug) / "plan.md"

    for review in args.review:
        review_path = Path(review).resolve()
        rc = reconcile_review_full(
            review_path=review_path,
            plan_path=plan_path,
            status=args.status,
            note=args.note,
            update_review_script=UPDATE_REVIEW,
            append_reconciliation_script=APPEND_RECONCILIATION,
        )
        if rc != 0:
            return rc

    review_args: list[str] = []
    for review in args.review:
        review_args.extend(["--review", str(Path(review).resolve())])
    subprocess.run([sys.executable, str(VERIFY_RECONCILIATION), "--plan", str(plan_path), *review_args], check=True, text=True)

    head_at_end, dirty_at_end = _capture_head_and_dirty()
    if head_at_start and head_at_end and head_at_start != head_at_end and dirty_at_start == dirty_at_end and not _git_sentinels_present():
        changed_paths_result = subprocess.run(
            ["git", "diff", "--name-only", head_at_start, head_at_end],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        changed_paths = [line.strip() for line in changed_paths_result.stdout.splitlines() if line.strip()]
        if changed_paths and all(
            path.startswith(f"docs/workflow/feature-runs/{args.slug}/reviews/")
            or path == f"docs/workflow/feature-runs/{args.slug}/plan.md"
            for path in changed_paths
        ):
            _update_diff_review_budget(args.slug, head_at_end)

    _emit_next_action(args.slug, f"reconcile ({args.status})")
    return 0
