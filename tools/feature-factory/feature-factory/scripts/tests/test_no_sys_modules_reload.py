"""Guardrail: no Feature Factory test file may reload modules into sys.modules.

These tests must import their sibling engine scripts ONCE (plain `import
factory_x`) so the whole suite is order-independent and runs in a single
process. Assigning a fresh module into the shared table —
`sys.modules["factory_x"] = ...` — swaps the module out from under other test
files and makes the suite order-dependent (passes alone, fails together).

If you genuinely need a fake module for one test, use the auto-restoring
`mock.patch.dict(sys.modules, {...})` (note the comma — it patches the dict, it
does not assign to a `sys.modules[key]` slot). If you need to test import-time
behavior, load a private copy under a UNIQUE name and do NOT register it in
sys.modules (see test_factory_state.py's REPO_ROOT test).
"""
import re
import unittest
from pathlib import Path

# .../codex-skills/feature-factory/scripts/tests/this_file.py
_CODEX_SKILLS_ROOT = Path(__file__).resolve().parents[3]
_THIS_FILE = Path(__file__).resolve()

# A bare assignment into the shared module table: sys.modules[<anything>] = ...
# (but not `==`). mock.patch.dict(sys.modules, {...}) uses a comma, not `[`, so
# it never matches.
_BARE_ASSIGN = re.compile(r"sys\.modules\[[^\]]*\]\s*=(?!=)")


class NoSysModulesReloadGuardrail(unittest.TestCase):
    def test_no_test_file_assigns_into_sys_modules(self) -> None:
        offenders: list[str] = []
        for path in sorted(_CODEX_SKILLS_ROOT.rglob("test_*.py")):
            if path == _THIS_FILE:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _BARE_ASSIGN.search(line):
                    rel = path.relative_to(_CODEX_SKILLS_ROOT)
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "Test files must not reload modules into sys.modules (use import-once; "
            "for fakes use mock.patch.dict). Offenders:\n" + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
