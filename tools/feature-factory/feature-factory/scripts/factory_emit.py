#!/usr/bin/env python3
"""Emit next-action banners after state-advancing commands."""
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    BLOCKED_KEY,
    load_workflow_state,
)

from factory_stages import (  # noqa: E402
    CHECKPOINT_STAGES,
    NEXT_ACTION_LABELS,
    stage_manifest_state,
    reconciliation_state,
)

from factory_next_action import recommended_next_action  # noqa: E402


def _emit_next_action(slug: str, completed: str) -> None:
    """Print a step-complete banner with the recommended next action.

    Called after every state-advancing command so both Claude and Codex
    orchestrators always know what to do next without running status manually.
    Never raises — if the banner fails, the parent command already succeeded.
    """
    try:
        state = load_workflow_state(slug)
        stages = {s: stage_manifest_state(slug, s) for s in CHECKPOINT_STAGES}
        recon_ok, _ = reconciliation_state(slug)
        action = recommended_next_action(slug, state, stages, recon_ok)
        label = NEXT_ACTION_LABELS.get(action, action)
        print(f"\n[workflow] ✓ {completed}")
        print(f"[workflow] → next: {action} — {label}")
        if action == "mark_blocked":
            blocked = state.get(BLOCKED_KEY, {})
            reason = str(blocked.get("reason", "")).strip()
            if reason:
                print(f"[workflow]   reason: {reason}")
        print()
    except Exception:
        pass  # Never fail the parent command because the banner failed
