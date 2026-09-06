"""Slice 1 test — argparse defaults for checkpoint character budgets."""
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_factory as RUN_FACTORY  # noqa: E402


class CharBudgetDefaultsTests(unittest.TestCase):
    """FR-001: default --max-*-chars values match real spec sizes in this codebase.

    Previously the three budget args had no defaults, forcing operators to pass
    explicit flags on every invocation. Both PR #744 and PR #749 runs hit this.
    """

    def setUp(self) -> None:
        self.run_factory = RUN_FACTORY

    def _parse(self, argv: list[str]):
        parser = self.run_factory.build_parser()
        return parser.parse_args(argv)

    def test_default_max_artifact_chars_is_50000(self) -> None:
        args = self._parse(["checkpoint", "--slug", "x", "--stage", "spec"])
        self.assertEqual(args.max_artifact_chars, 50000)

    def test_default_max_context_chars_is_60000(self) -> None:
        args = self._parse(["checkpoint", "--slug", "x", "--stage", "spec"])
        self.assertEqual(args.max_context_chars, 60000)

    def test_default_max_total_chars_is_250000(self) -> None:
        args = self._parse(["checkpoint", "--slug", "x", "--stage", "spec"])
        self.assertEqual(args.max_total_chars, 250000)

    def test_explicit_value_overrides_default(self) -> None:
        args = self._parse([
            "checkpoint", "--slug", "x", "--stage", "spec",
            "--max-artifact-chars", "10000",
        ])
        self.assertEqual(args.max_artifact_chars, 10000)


if __name__ == "__main__":
    unittest.main()
