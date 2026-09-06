"""Pytest bootstrap for the vendored Feature Factory engine tests.

These test modules import their sibling scripts by bare module name (e.g.
``import factory_state``), so the ``scripts`` directory must be on ``sys.path``
before collection. Historically that only worked when the caller exported
``PYTHONPATH`` or when an earlier test happened to insert the path first — which
made the suite order-dependent and is why CI never ran it. This conftest makes
the path setup explicit and deterministic, and makes git operations in the tests
hermetic so a host's global ``commit.gpgsign`` (which may point at a signing
server the CI box can't reach) can't break throwaway-repo commits.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Insert whichever sibling "scripts" directory actually holds the engine
# modules. Covers tests under <pkg>/scripts/tests (scripts is the parent) and
# under <pkg>/tests or the <pkg> root (scripts is a child).
_MARKERS = ("factory_state.py", "run_factory.py", "workflow_utils.py", "verify_reconciliation.py")
for _candidate in (_HERE, _HERE.parent, _HERE / "scripts", _HERE.parent / "scripts"):
    if any((_candidate / marker).exists() for marker in _MARKERS):
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))

# Hermetic git: GIT_CONFIG_* injects highest-precedence config into every git
# subprocess these tests spawn, so the host's global commit.gpgsign never fails
# a commit in a throwaway test repo. On a clean CI box this is a harmless no-op.
if "GIT_CONFIG_COUNT" not in os.environ:
    os.environ["GIT_CONFIG_COUNT"] = "1"
    os.environ["GIT_CONFIG_KEY_0"] = "commit.gpgsign"
    os.environ["GIT_CONFIG_VALUE_0"] = "false"
