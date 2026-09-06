#!/usr/bin/env python3
"""Test-isolation gate for feature-factory workflow files."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_mutating import readonly_command  # noqa: E402
from factory_io import read_text  # noqa: E402
from factory_state import REPO_ROOT  # noqa: E402

WORKFLOW_PATH = "docs/workflow/feature-runs/"


def _capture() -> set[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain", "-uall", "--", WORKFLOW_PATH],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return set(result.stdout.splitlines())


@readonly_command("check-isolation")
def command_check_workflow_isolation(args: argparse.Namespace) -> int:
    paths = _capture()
    if args.capture_baseline:
        args.capture_baseline.write_text(json.dumps(sorted(paths)), encoding="utf-8")
        return 0

    if not args.baseline:
        print("--check requires --baseline <path>", file=sys.stderr)
        return 2

    pre = set(json.loads(read_text(args.baseline)))
    added = paths - pre
    removed = pre - paths
    if not added and not removed:
        return 0

    lines = [
        "[isolation-fail] tests modified workflow state under docs/workflow/feature-runs/:",
    ]
    if added:
        lines.append("  Files dirtied (or newly modified) by the suite:")
        for path in sorted(added):
            lines.append(f"    {path}")
    if removed:
        lines.append("  Files DELETED or restored by the suite:")
        for path in sorted(removed):
            lines.append(f"    {path}")
    lines.append("")
    lines.append(
        "If your test calls factory_state.update_workflow_state, you MUST patch BOTH "
        "factory_state.REPO_ROOT and factory_state.FACTORY_RUNS_ROOT (both computed at "
        "import time) to a tempdir before the call."
    )
    print("\n".join(lines), file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture-baseline", type=Path)
    group.add_argument("--check", action="store_true")
    parser.add_argument("--baseline", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return command_check_workflow_isolation(args)


if __name__ == "__main__":
    raise SystemExit(main())
