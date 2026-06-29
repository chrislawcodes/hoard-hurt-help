"""Pytest bootstrap for the vendored Feature Factory engine tests.

These test modules import their sibling scripts by bare module name (e.g.
``import factory_state``), so the ``scripts`` directory must be on ``sys.path``
before collection. Historically that only worked when the caller exported
``PYTHONPATH`` or when an earlier test happened to insert the path first — which
made the suite order-dependent and is why CI never ran it. This conftest makes
the path setup explicit and deterministic, and makes git operations in the tests
hermetic so a host's global ``commit.gpgsign`` (which may point at a signing
server the CI box can't reach) can't break throwaway-repo commits.

It also isolates the engine's feature-run output root so the suite can never
write scratch run-dirs into the live repo tree (see the autouse fixture below).
This conftest lives at the ``feature-factory`` package root on purpose: the suite
has two sibling test trees (``tests/`` and ``scripts/tests/``), and the
pollution actually originated in ``tests/`` — so the isolation must apply to
both, which only a shared parent conftest guarantees.
"""
from __future__ import annotations

import gc
import os
import sys
import tempfile
import types
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

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


# ---------------------------------------------------------------------------
# Feature-run output isolation
# ---------------------------------------------------------------------------
#
# ``factory_state`` resolves two roots **at import time** and stores them as
# module-level constants:
#
#   * ``REPO_ROOT``         — the repo checkout (git root, or ``$FF_REPO_ROOT``)
#   * ``FACTORY_RUNS_ROOT`` — ``REPO_ROOT/docs/workflow/feature-runs`` (or
#                             ``$FF_FACTORY_RUNS_ROOT``)
#
# Every write path the engine produces for a feature run (``state.json``,
# ``scope.json``, reviews, …) is derived from ``FACTORY_RUNS_ROOT`` via
# ``workflow_dir`` / ``factory_state_path`` / ``reviews_dir`` — and those helpers
# read the *module-level* constant at call time. So any test that drives a real
# write through ``factory_state`` without first repointing those constants writes
# straight into the live repo tree under ``docs/workflow/feature-runs/``. That is
# exactly how dead scratch run-dirs (``test-slug/``, ``feature-workflow-repair/``,
# ``feature-workflow-discovery-shaping/`` …) kept getting created and committed.
#
# The leak is order-dependent: no single test pollutes when run alone. Several
# tests ``importlib.reload`` / ``exec_module`` ``factory_state`` (creating extra,
# independent module objects), and command modules resolve ``factory_state``
# lazily via ``sys.modules`` at call time, so a write can land through a stale,
# unpatched instance. ``check_workflow_isolation.py`` (the CI gate) documents the
# required fix in its own failure message: patch BOTH ``factory_state.REPO_ROOT``
# and ``factory_state.FACTORY_RUNS_ROOT`` (both computed at import time) to a
# tempdir before the call.
#
# This is a **test-setup** change only. Production runs are untouched: the env
# hooks and import-time resolution in ``factory_state`` are unchanged, so real
# (non-test) invocations still write to ``docs/workflow/feature-runs/``.


def _live_factory_state_modules() -> list[types.ModuleType]:
    """Every ``factory_state`` module object currently alive in the interpreter.

    importlib reloads / ``exec_module`` (used by a handful of engine tests)
    create independent module objects, and several command modules resolve
    ``factory_state`` lazily via ``sys.modules`` at call time. Patching only the
    canonical import would leave those stale instances pointed at the live repo,
    so we sweep all of them — the same approach the per-test setups in this suite
    already use (``gc.get_objects()``).
    """
    modules: list[types.ModuleType] = []
    for obj in gc.get_objects():
        if not isinstance(obj, types.ModuleType):
            continue
        if getattr(obj, "__name__", "") != "factory_state":
            continue
        modules.append(obj)
    return modules


@pytest.fixture(autouse=True)
def _isolate_factory_runs_root() -> Iterator[Path]:
    """Repoint factory_state's import-time roots at a per-test temp dir.

    Runs for every test (including ``unittest.TestCase`` methods). The temp dir
    is seeded with the ``docs/workflow/feature-runs/`` layout so repo-relative
    resolution keeps working. Yields the temp repo root for tests that want it;
    most never need it — the point is simply that no engine write escapes into
    the real ``docs/workflow/feature-runs/`` tree.

    Tests that already manage their own patching are unaffected: this fixture
    runs first, so a test's own explicit patch in ``setUp``/the test body takes
    precedence during the test and unwinds back onto the temp dir afterward
    (never back onto the live repo). Tests that assert on the *relative* path
    string ``"docs/workflow/feature-runs/..."`` use hardcoded literals, not the
    constant, so they are unaffected as well.
    """
    with tempfile.TemporaryDirectory(prefix="ff-test-runs-") as tmp:
        repo_root = Path(tmp)
        runs_root = repo_root / "docs" / "workflow" / "feature-runs"
        runs_root.mkdir(parents=True, exist_ok=True)

        patches: list[mock._patch] = []
        for module in _live_factory_state_modules():
            if hasattr(module, "REPO_ROOT"):
                repo_patch = mock.patch.object(module, "REPO_ROOT", repo_root)
                repo_patch.start()
                patches.append(repo_patch)
            if hasattr(module, "FACTORY_RUNS_ROOT"):
                runs_patch = mock.patch.object(module, "FACTORY_RUNS_ROOT", runs_root)
                runs_patch.start()
                patches.append(runs_patch)

        try:
            yield repo_root
        finally:
            for started in reversed(patches):
                started.stop()
