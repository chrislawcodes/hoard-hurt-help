#!/usr/bin/env python3
"""command_arch_docs implementation.

Records the architecture-doc decision for a run. The `done` gate
(`arch_docs_resolved`) is satisfied when ARCHITECTURE.md/DESIGN.md were modified
since init, OR when the orchestrator explicitly acks "no architecture change
needed" here. This command is only needed for the no-change case; if the docs
were actually edited, the git-diff check resolves the gate on its own.
"""
import argparse
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    ARCH_DOCS_KEY,
    update_workflow_state,
)

from factory_git import ensure_sync  # noqa: E402
from factory_mutating import mutates_state  # noqa: E402


@mutates_state("arch-docs")
def command_arch_docs(args: argparse.Namespace) -> int:
    ensure_sync()
    if not args.reset and not args.no_change_needed:
        raise SystemExit(
            "arch-docs requires --no-change-needed (with --reason) to ack that "
            "this feature needs no ARCHITECTURE.md/DESIGN.md change, or --reset "
            "to clear a prior ack. If the docs DID change, just edit them — the "
            "done gate detects the change automatically."
        )
    if args.no_change_needed and not args.reason:
        raise SystemExit("arch-docs --no-change-needed requires --reason explaining why no doc change is needed")

    def mutate(state: dict) -> None:
        if args.reset:
            state[ARCH_DOCS_KEY] = {"no_change_acked": False, "reason": "", "updated_at": int(time.time())}
            return
        state[ARCH_DOCS_KEY] = {
            "no_change_acked": True,
            "reason": args.reason,
            "updated_at": int(time.time()),
        }

    state = update_workflow_state(args.slug, mutate)
    record = state.get(ARCH_DOCS_KEY, {})
    if record.get("no_change_acked"):
        print(f"arch-docs: acked no change needed — {record.get('reason', '')}")
    else:
        print("arch-docs: ack cleared")
    return 0
