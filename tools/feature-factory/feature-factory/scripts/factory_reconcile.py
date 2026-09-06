#!/usr/bin/env python3
"""Three-way reconcile helper — keeps review frontmatter, body Resolution
block, and plan.md reconciliation entry in sync.

Why this exists:
  Three prior feature runs (PR #744, #749, #750) hit `verify_review_checkpoint`
  errors of the form "resolution status does not match frontmatter" or "plan.md
  is missing reconciliation entry for reviews/foo.review.md". Every recovery
  was hand-editing one or two of three sources to match. This helper bundles
  the reconcile into a single entry point with a pre-check.

Scope (per spec FR-002):
  Pre-check + sequential write. NOT transactional. Mid-write failure can leave
  drift; idempotent re-run is the recovery path.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def reconcile_review_full(
    review_path: Path,
    plan_path: Path,
    status: str,
    note: str,
    *,
    update_review_script: Path,
    append_reconciliation_script: Path,
    python_exe: str | None = None,
) -> int:
    """Update review frontmatter + body Resolution block + plan.md entry.

    Returns 0 on success, 2 on pre-check or write failure.

    Args:
      review_path: absolute path to the .review.md file.
      plan_path: absolute path to the workflow's plan.md.
      status: one of "accepted", "rejected", "deferred", "open".
      note: free-form note. Should be non-empty.
      update_review_script: path to update_review_resolution.py (review-lens).
      append_reconciliation_script: path to append_reconciliation_entry.py.
      python_exe: Python executable. Defaults to sys.executable.
    """
    python_exe = python_exe or sys.executable

    # --- Pre-check phase: validate review file + plan dir writable ---
    # Note: plan.md may not yet exist on a first run; append_reconciliation_entry.py
    # creates it. So we only require the parent dir to be writable, not plan.md
    # itself. (Per PR #751 diff-review Codex regression MEDIUM #1.)
    pre_check_errors: list[str] = []
    if not review_path.exists():
        pre_check_errors.append(f"review file does not exist: {review_path}")
    elif not os.access(review_path, os.W_OK):
        pre_check_errors.append(f"review file not writable: {review_path}")

    if plan_path.exists():
        if not os.access(plan_path, os.W_OK):
            pre_check_errors.append(f"plan.md not writable: {plan_path}")
    else:
        # plan.md doesn't exist yet — require its parent dir to be writable
        # so append_reconciliation_entry.py can create it.
        plan_dir = plan_path.parent
        if not plan_dir.exists():
            pre_check_errors.append(f"plan.md parent dir does not exist: {plan_dir}")
        elif not os.access(plan_dir, os.W_OK):
            pre_check_errors.append(f"plan.md parent dir not writable: {plan_dir}")

    if pre_check_errors:
        for err in pre_check_errors:
            print(f"reconcile pre-check failed: {err}", file=sys.stderr)
        return 2

    # --- Write phase 1: review frontmatter + body Resolution block ---
    review_args = ["--review", str(review_path)]
    update_cmd = [
        python_exe,
        str(update_review_script),
        *review_args,
        "--status",
        status,
        "--note",
        note,
    ]
    update_result = subprocess.run(update_cmd, capture_output=True, text=True)
    if update_result.returncode != 0:
        print(
            f"reconcile failed at update_review_resolution: {update_result.stderr.strip()}",
            file=sys.stderr,
        )
        return 2

    # --- Write phase 2: plan.md entry (dedup by review path) ---
    append_cmd = [
        python_exe,
        str(append_reconciliation_script),
        "--plan",
        str(plan_path),
        *review_args,
        "--status",
        status,
        "--note",
        note,
    ]
    append_result = subprocess.run(append_cmd, capture_output=True, text=True)
    if append_result.returncode != 0:
        print(
            f"reconcile failed at append_reconciliation_entry: {append_result.stderr.strip()}\n"
            f"  WARNING: review file at {review_path} was already updated; "
            f"plan.md is now out of sync. Re-run reconcile to repair.",
            file=sys.stderr,
        )
        return 2

    return 0
