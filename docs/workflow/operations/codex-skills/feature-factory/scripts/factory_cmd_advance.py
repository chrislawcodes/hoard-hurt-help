#!/usr/bin/env python3
"""command_advance implementation.

Manual escape hatch when the runner gets wedged on manifest drift after
the 3-round adversarial cap. Sets stages[stage].judge_next_action = "advance"
and appends to annotations[]. Reason is eager-validated to >= 20 chars
before any state mutation.
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import REPO_ROOT, update_workflow_state  # noqa: E402
from factory_mutating import mutates_state  # noqa: E402


_VALID_STAGES = ("spec", "plan", "tasks", "implementation")
_MIN_REASON_LEN = 20


def _eager_validate(args: argparse.Namespace) -> str:
    """Validate reason length BEFORE any state mutation. Return cleaned reason."""
    reason = (args.reason or "").strip()
    if len(reason) < _MIN_REASON_LEN:
        print(
            f"--reason must be at least {_MIN_REASON_LEN} characters after strip "
            f"(got {len(reason)}). Provide a meaningful explanation.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return reason


def _git_head_sha() -> str:
    """Return current HEAD sha or empty string if git could not resolve it."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


@mutates_state("advance")
def command_advance(args: argparse.Namespace) -> int:
    # Eager validation — must run before any state mutation.
    reason = _eager_validate(args)

    head_sha = _git_head_sha()
    if not head_sha:
        # An empty head_sha in annotations[] is forensically useless and would
        # be confused with "advance ran on the empty tree." Refuse to write.
        print(
            "could not resolve HEAD via 'git rev-parse'; aborting before "
            "state mutation. Re-run from a clean git checkout.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S_%fZ")

    def mutate(state: dict) -> None:
        stages = state.setdefault("stages", {})
        stage_blob = stages.setdefault(args.stage, {})
        stage_blob["judge_next_action"] = "advance"
        annotations = state.setdefault("annotations", [])
        annotations.append({
            "stage": args.stage,
            "ts": ts,
            "reason": reason,
            "head_sha": head_sha,
        })

    update_workflow_state(args.slug, mutate)
    print(f"[workflow] ✓ advance ({args.stage})")
    return 0
