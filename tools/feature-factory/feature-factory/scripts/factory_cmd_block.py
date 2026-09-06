#!/usr/bin/env python3
"""command_block implementation."""
import argparse
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    BLOCKED_KEY,
    update_workflow_state,
)

from factory_git import ensure_sync  # noqa: E402
from factory_mutating import mutates_state  # noqa: E402


@mutates_state("block")
def command_block(args: argparse.Namespace) -> int:
    ensure_sync()
    if not args.clear and not args.reason:
        raise SystemExit("block requires --reason unless --clear is used")

    def mutate(state: dict) -> None:
        if args.clear:
            state[BLOCKED_KEY] = {
                "active": False,
                "reason": "",
                "updated_at": int(time.time()),
            }
            return
        state[BLOCKED_KEY] = {
            "active": True,
            "reason": args.reason,
            "updated_at": int(time.time()),
        }

    state = update_workflow_state(args.slug, mutate)
    blocked = state.get(BLOCKED_KEY, {})
    if blocked.get("active"):
        print(f"blocked: {blocked.get('reason', '')}")
    else:
        print("unblocked")
    return 0
