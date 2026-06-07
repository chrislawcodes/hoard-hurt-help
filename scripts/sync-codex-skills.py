#!/usr/bin/env python3
"""Vendored-fork marker for the Feature Factory / review-lens engine.

The engine under ``docs/workflow/operations/codex-skills/`` is a VENDORED FORK of
the ValueRank project's workflow engine. There is no automated upstream sync:
changes flow ValueRank -> here by manual port, and this repo's copy is the source
of truth for hoard-hurt-help. See ``docs/workflow/FEATURE_FACTORY_DESIGN.md`` (Section 11).

This script stays a deliberate no-op so the engine's ``ensure_sync()`` hook keeps
working without coupling to a sync server. The engine calls it with
``--sync-if-needed``, where it is silent so it does not spam every runner command;
run it directly (no flag) to print the fork status.
"""
import sys

_NOTICE = (
    "Feature Factory engine is a vendored fork of ValueRank — there is no automated sync.\n"
    "This repo's copy under docs/workflow/operations/codex-skills/ is authoritative; port\n"
    "upstream changes by hand. See docs/workflow/FEATURE_FACTORY_DESIGN.md (Section 11)."
)


def main(argv: list[str]) -> int:
    # The engine hook passes --sync-if-needed; stay silent there. A human running
    # this directly gets the honest fork status instead of a misleading "synced".
    if "--sync-if-needed" not in argv:
        print(_NOTICE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
