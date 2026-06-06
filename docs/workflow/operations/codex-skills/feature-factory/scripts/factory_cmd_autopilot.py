#!/usr/bin/env python3
"""command_autopilot: auto-advance through mechanical Feature Factory steps.

Reads the current next-action and either runs it (deterministic mechanical
step) or stops with a structured JSON result at decision points — authoring,
open review findings, failures, or delivery.  Never reconciles, delivers, or
merges automatically.  Resumable: run again after the orchestrator handles
the yielded action.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    BLOCKED_KEY,
    REPO_ROOT,
    load_workflow_state,
    parse_review_frontmatter,
    reviews_dir,
)
from factory_stages import (  # noqa: E402
    CHECKPOINT_STAGES,
    reconciliation_state,
    stage_manifest_state,
)
from factory_next_action import recommended_next_action  # noqa: E402
from factory_cmd_checkpoint import command_checkpoint  # noqa: E402
from factory_cmd_implement import command_implement  # noqa: E402
from factory_mutating import mutates_state  # noqa: E402
from factory_runlock import acquire_run_lock, release_run_lock  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAX_ITERATIONS: int = 30

# next-action strings → checkpoint stage they map to
_CHECKPOINT_NEXT_ACTIONS: dict[str, str] = {
    "run_spec_checkpoint": "spec",
    "run_plan_checkpoint": "plan",
    "run_tasks_checkpoint": "tasks",
    "run_diff_checkpoint": "diff",
    "run_closeout_checkpoint": "closeout",
}

# next-action strings that require human/AI authoring — never auto-run
_AUTHORING_NEXT_ACTIONS: frozenset[str] = frozenset(
    {
        "discover",
        "author_spec",
        "author_plan",
        "author_tasks",
        "record_parallel_analysis",
        "closeout",
        "write_postmortem",
        "update_status_md",
        "reconcile_arch_docs",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _current_next_action(slug: str) -> str:
    """Compute the recommended next action from live workflow state."""
    state = load_workflow_state(slug)
    stages = {s: stage_manifest_state(slug, s) for s in CHECKPOINT_STAGES}
    recon_ok, _ = reconciliation_state(slug)
    return recommended_next_action(slug, state, stages, recon_ok)


def _build_checkpoint_args(
    slug: str,
    stage: str,
    *,
    use_existing_artifact: bool,
) -> argparse.Namespace:
    """Build a minimal Namespace suitable for command_checkpoint."""
    return argparse.Namespace(
        slug=slug,
        stage=stage,
        artifact=None,
        base_ref=None,
        context=[],
        path=[],
        extra_gemini_lens=[],
        sensitive=False,
        large_structural=False,
        performance_sensitive=False,
        use_existing_artifact=use_existing_artifact,
        auto_context=False,
        no_auto_context=True,
        allow_dirty_path=[],
        max_artifact_chars=50000,
        max_context_chars=60000,
        max_total_chars=250000,
        gemini_timeout_seconds=120,
        gemini_retries=1,
        repair_timeout_seconds=300,
        allow_large_diff_rerun=False,
        fallback=False,
        json=False,
        fast=False,
        keep_intermediates=False,
    )


def _build_implement_args(slug: str) -> argparse.Namespace:
    """Build a minimal Namespace suitable for command_implement."""
    return argparse.Namespace(slug=slug, max_workers=4)


def _open_reviews_for_stage(slug: str, stage: str) -> list[dict[str, Any]]:
    """Return info for every review file with resolution_status == 'open'."""
    rev_dir = reviews_dir(slug)
    open_reviews: list[dict[str, Any]] = []
    for review_path in sorted(rev_dir.glob(f"{stage}.*.review.md")):
        try:
            data, _ = parse_review_frontmatter(review_path)
        except (ValueError, OSError):
            continue
        if str(data.get("resolution_status", "")).strip() == "open":
            open_reviews.append(
                {
                    "path": str(review_path),
                    "resolution_status": "open",
                    "reviewer": data.get("reviewer", ""),
                }
            )
    return open_reviews


def _run_preflight(repo_root: Path) -> tuple[int, str]:
    """Run the repo Preflight Gate (ruff + mypy + pytest).

    Returns (rc, combined_output).  Stops at first failure.
    """
    steps: list[list[str]] = [
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "mypy", "app/", "mcp_server/"],
        [sys.executable, "-m", "pytest", "-q"],
    ]
    combined: list[str] = []
    for step in steps:
        result = subprocess.run(
            step, cwd=str(repo_root), capture_output=True, text=True
        )
        combined.append(result.stdout + result.stderr)
        if result.returncode != 0:
            return result.returncode, "\n".join(combined)
    return 0, "\n".join(combined)


def _emit_progress(msg: str) -> None:
    print(f"[autopilot] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


@mutates_state("autopilot")
def command_autopilot(args: argparse.Namespace) -> int:
    """Auto-advance mechanical steps; yield at every decision point.

    Emits a single structured JSON object on stdout when it stops, with shape:
      { "stop_reason": str, "next_action": str, "slug": str,
        "actions_taken": [{cmd, rc, summary}], "details": {...} }

    Always returns exit code 0 — the stop_reason carries the signal.
    """
    slug: str = args.slug
    max_iterations: int = int(getattr(args, "max_iterations", DEFAULT_MAX_ITERATIONS))

    actions_taken: list[dict[str, Any]] = []

    lock_fd, lock_err = acquire_run_lock(slug, "autopilot", "autopilot")
    if lock_fd == -1:
        print(lock_err, file=sys.stderr)
        return 1
    try:

        def _stop(stop_next_action: str, reason: str, details: dict[str, Any] | None = None) -> int:
            payload: dict[str, Any] = {
                "stop_reason": reason,
                "next_action": stop_next_action,
                "slug": slug,
                "actions_taken": actions_taken,
                "details": details or {},
            }
            print(json.dumps(payload, indent=2))
            return 0

        def _run_checkpoint(
            stage: str, *, use_existing_artifact: bool
        ) -> tuple[int, list[dict[str, Any]]]:
            cp_args = _build_checkpoint_args(
                slug, stage, use_existing_artifact=use_existing_artifact
            )
            rc = command_checkpoint(cp_args)
            if rc != 0:
                return rc, []
            return rc, _open_reviews_for_stage(slug, stage)

        for _iteration in range(max_iterations):
            next_action = _current_next_action(slug)

            # ── Terminal ──────────────────────────────────────────────────────
            if next_action == "done":
                return _stop(next_action, "done")

            # ── Blocked ───────────────────────────────────────────────────────
            if next_action == "mark_blocked":
                state = load_workflow_state(slug)
                blocked = state.get(BLOCKED_KEY, {})
                block_reason = str(blocked.get("reason", "")).strip()
                return _stop(next_action, "blocked", {"reason": block_reason})

            # ── Reconcile — orchestrator must judge findings ───────────────────
            if next_action == "reconcile_reviews":
                return _stop(next_action, "needs_reconcile")

            # ── Deliver — orchestrator must approve ───────────────────────────
            if next_action == "deliver":
                return _stop(next_action, "awaiting_delivery_approval")

            # ── Authoring — orchestrator must write an artifact ───────────────
            if next_action in _AUTHORING_NEXT_ACTIONS:
                return _stop(next_action, "needs_authoring", {"action": next_action})

            # ── Mechanical: run/repair an existing-artifact checkpoint ─────────
            if next_action in _CHECKPOINT_NEXT_ACTIONS:
                stage = _CHECKPOINT_NEXT_ACTIONS[next_action]
                _emit_progress(f"running {stage} checkpoint (use-existing-artifact)...")
                rc, open_reviews = _run_checkpoint(stage, use_existing_artifact=True)
                actions_taken.append(
                    {
                        "cmd": f"checkpoint --stage {stage} --use-existing-artifact",
                        "rc": rc,
                        "summary": f"{stage} checkpoint",
                    }
                )
                if rc != 0:
                    _emit_progress(f"{stage} checkpoint reviewer failed (rc={rc})")
                    return _stop(next_action, "review_runner_failed", {"stage": stage, "rc": rc})
                if open_reviews:
                    _emit_progress(
                        f"{stage} checkpoint has {len(open_reviews)} open finding(s) — needs reconcile"
                    )
                    return _stop(
                        "reconcile_reviews",
                        "needs_reconcile",
                        {"stage": stage, "open_reviews": open_reviews},
                    )
                _emit_progress(f"ran {stage} checkpoint → healthy")
                continue

            # ── Mechanical: implement next slice ──────────────────────────────
            if next_action == "dispatch_next_slice_to_codex":
                # Step 1: dispatch Codex for this slice
                _emit_progress("dispatching implement (next slice)...")
                impl_rc = command_implement(_build_implement_args(slug))
                actions_taken.append(
                    {
                        "cmd": f"implement --slug {slug}",
                        "rc": impl_rc,
                        "summary": "implement slice",
                    }
                )
                if impl_rc != 0:
                    _emit_progress(f"implement failed (rc={impl_rc})")
                    return _stop(next_action, "implement_failed", {"rc": impl_rc})

                # Step 2: preflight gate
                _emit_progress("running preflight gate (ruff + mypy + pytest)...")
                preflight_rc, preflight_output = _run_preflight(REPO_ROOT)
                actions_taken.append(
                    {
                        "cmd": "ruff check . && mypy app/ mcp_server/ && pytest -q",
                        "rc": preflight_rc,
                        "summary": "preflight gate",
                    }
                )
                if preflight_rc != 0:
                    _emit_progress("preflight gate failed")
                    return _stop(
                        next_action,
                        "preflight_failed",
                        {"rc": preflight_rc, "output": preflight_output},
                    )

                # Step 3: diff checkpoint for the new slice (regenerate diff)
                _emit_progress("running diff checkpoint for new slice...")
                diff_rc, diff_open_reviews = _run_checkpoint(
                    "diff", use_existing_artifact=False
                )
                actions_taken.append(
                    {
                        "cmd": "checkpoint --stage diff",
                        "rc": diff_rc,
                        "summary": "diff checkpoint (post-implement)",
                    }
                )
                if diff_rc != 0:
                    _emit_progress(f"diff checkpoint reviewer failed (rc={diff_rc})")
                    return _stop(
                        "run_diff_checkpoint",
                        "review_runner_failed",
                        {"stage": "diff", "rc": diff_rc},
                    )
                if diff_open_reviews:
                    _emit_progress(
                        f"diff checkpoint has {len(diff_open_reviews)} open finding(s) — needs reconcile"
                    )
                    return _stop(
                        "reconcile_reviews",
                        "needs_reconcile",
                        {"stage": "diff", "open_reviews": diff_open_reviews},
                    )
                _emit_progress("implement + diff checkpoint → clean, continuing...")
                continue

            # ── Unknown — surface it rather than silently ignoring ────────────
            _emit_progress(f"unrecognised next_action: {next_action!r}")
            return _stop(next_action, "unknown_action", {"action": next_action})

        # Exhausted the iteration budget
        final_action = _current_next_action(slug)
        _emit_progress(f"max_iterations ({max_iterations}) reached; stopping")
        return _stop(
            final_action,
            "max_iterations",
            {"max_iterations": max_iterations},
        )
    finally:
        release_run_lock(lock_fd)
