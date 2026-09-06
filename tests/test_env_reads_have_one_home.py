"""Reading an environment variable has exactly one home: app/config.py.

Ten call sites used to read `os.environ`/`os.getenv` directly, scattered
across app/main.py, app/engine/scheduler_turn_loop.py, and
app/games/hoard_hurt_help/game.py — the same kind of rule-written-twice risk
as tests/test_clamp_has_one_home.py guards against, just for "what does this
environment variable say" instead of "keep a number inside a range". All ten
were moved into named properties on `Settings` (app/config.py); every other
module reads those properties instead of the environment directly.

Like that file, this is a **structural** guard: it reads the source and fails
if `os.environ` or `os.getenv` appears anywhere in `app/` or `mcp_server/`
outside the one home. It does not matter how the result is used (`.get(...)`,
subscript, or a direct call) — reading the environment at all, outside
app/config.py, is the thing this test catches.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The one home. Anything else reading os.environ/os.getenv is a second copy.
CANONICAL_FILE = "app/config.py"

SEARCH_ROOTS = ("app", "mcp_server")


def _is_os_env_read(node: ast.Attribute) -> bool:
    """True for `os.environ` or `os.getenv`, however the result is then used
    (`.get(...)`, `[...]`, or called directly) — the attribute access itself
    is the environment read."""
    return (
        isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr in {"environ", "getenv"}
    )


def _find_env_reads(root: Path) -> list[tuple[str, int]]:
    """[(file, line)] for every os.environ/os.getenv read outside the one home."""
    found: list[tuple[str, int]] = []
    for top in SEARCH_ROOTS:
        top_dir = root / top
        if not top_dir.is_dir():
            continue
        for path in sorted(top_dir.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            rel = str(path.relative_to(root))
            if rel == CANONICAL_FILE:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and _is_os_env_read(node):
                    found.append((rel, node.lineno))
    return found


def test_no_env_read_outside_the_one_home() -> None:
    hits = _find_env_reads(_REPO_ROOT)
    assert not hits, "\n".join(
        f"{f}:{n}: read this variable via a Settings property in {CANONICAL_FILE} instead"
        for f, n in hits
    )


def test_the_canonical_config_is_where_this_test_thinks_it_is() -> None:
    """Guard the guard: if config.py moves or is renamed, fail loudly here
    rather than silently passing because the scan found nothing to compare
    against."""
    path = _REPO_ROOT / CANONICAL_FILE
    assert path.is_file(), (
        f"expected {CANONICAL_FILE} to exist; if it moved, update CANONICAL_FILE here"
    )


def test_the_scan_catches_a_planted_getenv(tmp_path: Path) -> None:
    """Pins the bug this file exists to catch: a fresh `os.getenv` call,
    anywhere in app/ or mcp_server/, outside the one home."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "import os\n\n\ndef f():\n    return os.getenv('X')\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_env_reads(tmp_path) == [("app/_zz.py", 5)]


def test_the_scan_catches_a_planted_environ_get(tmp_path: Path) -> None:
    """The `os.environ.get(...)` / `os.environ[...]` shapes, not just `getenv`."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "_zz.py").write_text(
        "import os\n\nTIMEOUT = int(os.environ.get('TIMEOUT', '5'))\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_env_reads(tmp_path) == [("app/_zz.py", 3)]


def test_the_scan_ignores_the_canonical_file(tmp_path: Path) -> None:
    """A read inside app/config.py itself is the one home, not a copy."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "config.py").write_text(
        "import os\n\ndef f():\n    return os.getenv('X')\n"
    )
    (tmp_path / "mcp_server").mkdir()
    assert _find_env_reads(tmp_path) == []
