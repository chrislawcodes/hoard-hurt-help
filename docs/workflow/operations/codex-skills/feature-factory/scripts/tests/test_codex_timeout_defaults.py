"""Regression pin for the codex review timeout knobs.

Three postmortems (unified-connections, user-roles, strategy-first-onboarding)
asked for a ``--codex-timeout-seconds`` flag on ``checkpoint`` (parity with
``--gemini-timeout-seconds``) and higher spec/plan defaults (observed need
300–540s). This was ALREADY FIXED after PR #789's 120s ceiling proved wrong:
``checkpoint`` exposes the flag with a 540s default (all stages, so spec/plan
are covered) plus a 90s idle watchdog, and forwards both into the repair
runner (factory_cmd_checkpoint), which passes them down to run_codex_review's
own 540s backstop.

These tests pin the flags and defaults so a refactor can't silently regress
to the old ceiling and re-trigger the recurring postmortem item.
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import run_factory as RUN_FACTORY  # noqa: E402

RUN_CODEX_REVIEW = (
    SCRIPTS_DIR.parents[1] / "review-lens" / "scripts" / "run_codex_review.py"
)


class CodexTimeoutDefaultsTests(unittest.TestCase):
    def _parse(self, argv: list[str]):
        return RUN_FACTORY.build_parser().parse_args(argv)

    def test_checkpoint_exposes_codex_timeout_with_540s_default(self) -> None:
        args = self._parse(["checkpoint", "--slug", "x", "--stage", "spec"])
        self.assertEqual(args.codex_timeout_seconds, 540)
        # The postmortems' observed need was 300–540s; never regress below it.
        self.assertGreaterEqual(args.codex_timeout_seconds, 420)

    def test_checkpoint_codex_timeout_default_covers_plan_stage(self) -> None:
        args = self._parse(["checkpoint", "--slug", "x", "--stage", "plan"])
        self.assertEqual(args.codex_timeout_seconds, 540)

    def test_checkpoint_codex_idle_timeout_default_is_90(self) -> None:
        args = self._parse(["checkpoint", "--slug", "x", "--stage", "spec"])
        self.assertEqual(args.codex_idle_timeout_seconds, 90)

    def test_explicit_codex_timeout_overrides_default(self) -> None:
        args = self._parse([
            "checkpoint", "--slug", "x", "--stage", "spec",
            "--codex-timeout-seconds", "300",
        ])
        self.assertEqual(args.codex_timeout_seconds, 300)

    def test_repair_wrapper_default_contains_codex_ceiling(self) -> None:
        """The outer repair timeout must exceed the codex ceiling + gemini pass.

        780 = (540 + 30) codex + (120 + 30) gemini + 60 headroom; if this ever
        drops below the codex ceiling the wrapper kills healthy long reviews —
        the exact failure mode the postmortems reported.
        """
        args = self._parse(["checkpoint", "--slug", "x", "--stage", "plan"])
        self.assertGreaterEqual(
            args.repair_timeout_seconds, args.codex_timeout_seconds + 150
        )

    def test_run_codex_review_flag_parses_and_help_lists_it(self) -> None:
        """The end-of-chain script still accepts --timeout-seconds."""
        result = subprocess.run(
            [sys.executable, str(RUN_CODEX_REVIEW), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("--timeout-seconds", result.stdout)
        self.assertIn("--idle-timeout-seconds", result.stdout)

    def test_run_codex_review_backstop_default_is_540(self) -> None:
        """run_codex_review's own --timeout-seconds backstop stays at 540s.

        The parser is built inside main() and the argument carries no help
        string (so --help omits its default); pin the source literal instead.
        This is the line PR #789 once wrongly set to 120.
        """
        source = RUN_CODEX_REVIEW.read_text(encoding="utf-8")
        match = re.search(
            r'"--timeout-seconds",\s*type=int,\s*default=(\d+)', source
        )
        self.assertIsNotNone(
            match, msg="--timeout-seconds argument not found in run_codex_review.py"
        )
        assert match is not None  # narrow for the type checker
        self.assertEqual(int(match.group(1)), 540)


if __name__ == "__main__":
    unittest.main()
