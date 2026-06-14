"""Unit tests for factory_cmd_quick.command_quick."""
import argparse
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_cmd_quick as FACTORY_CMD_QUICK  # noqa: E402


SLUG = "quick-test"


class QuickCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_root = Path(self._tmpdir.name)
        self.runs_root = self.repo_root / "docs" / "workflow" / "feature-runs"

        self._repo_patch = mock.patch.object(FACTORY_STATE, "REPO_ROOT", self.repo_root)
        self._runs_patch = mock.patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", self.runs_root)
        self._cmd_repo_patch = mock.patch.object(FACTORY_CMD_QUICK, "factory_state", FACTORY_STATE)
        self._repo_patch.start()
        self._runs_patch.start()
        self.addCleanup(self._repo_patch.stop)
        self.addCleanup(self._runs_patch.stop)

    def _make_slug_dir(self) -> Path:
        slug_dir = FACTORY_STATE.workflow_dir(SLUG)
        slug_dir.mkdir(parents=True, exist_ok=True)
        reviews = slug_dir / "reviews"
        reviews.mkdir(parents=True, exist_ok=True)
        return slug_dir

    def _make_args(
        self,
        slug: str = SLUG,
        prompt_path: str | None = None,
        review_lens: str = "correctness",
    ) -> argparse.Namespace:
        return argparse.Namespace(
            slug=slug,
            prompt_path=prompt_path,
            review_lens=review_lens,
            model="gpt-5.4-mini",
        )

    def _invoke(self, args: argparse.Namespace) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = FACTORY_CMD_QUICK.command_quick(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    # ------------------------------------------------------------------
    # Test 1: non-existent slug exits 2
    # ------------------------------------------------------------------

    def test_nonexistent_slug_exits_2(self) -> None:
        args = self._make_args(slug="does-not-exist")
        with self.assertRaises(SystemExit) as ctx:
            self._invoke(args)
        self.assertEqual(ctx.exception.code, 2)

    # ------------------------------------------------------------------
    # Test 2: no --prompt-path skips dispatch but still runs review
    # ------------------------------------------------------------------

    def test_no_prompt_path_skips_dispatch_runs_review(self) -> None:
        self._make_slug_dir()

        # Stub out _write_diff_artifact and _run_codex_review
        stub_diff = self.runs_root / SLUG / "reviews" / "quick.diff.txt"
        stub_review = self.runs_root / SLUG / "reviews" / "diff.codex.correctness-adversarial.review.md"

        with (
            mock.patch.object(FACTORY_CMD_QUICK, "_write_diff_artifact", return_value=stub_diff),
            mock.patch.object(
                FACTORY_CMD_QUICK,
                "_run_codex_review",
                return_value=0,
            ) as mock_review,
        ):
            stub_review.parent.mkdir(parents=True, exist_ok=True)
            stub_review.write_text(
                "---\nresolution_status: open\n---\n## Findings\nNo findings.\n",
                encoding="utf-8",
            )
            args = self._make_args(prompt_path=None)
            rc, stdout, stderr = self._invoke(args)

        self.assertEqual(rc, 0)
        mock_review.assert_called_once()
        self.assertIn("review:", stdout)

    # ------------------------------------------------------------------
    # Test 3: with --prompt-path dispatches Codex then runs review
    # ------------------------------------------------------------------

    def test_with_prompt_path_dispatches_then_reviews(self) -> None:
        self._make_slug_dir()

        # Create a dummy prompt file
        prompt_path = self.runs_root / SLUG / "prompt.txt"
        prompt_path.write_text("Fix all the bugs.", encoding="utf-8")

        stub_diff = self.runs_root / SLUG / "reviews" / "quick.diff.txt"
        stub_review = self.runs_root / SLUG / "reviews" / "diff.codex.correctness-adversarial.review.md"

        stub_review.parent.mkdir(parents=True, exist_ok=True)
        stub_review.write_text(
            "---\nresolution_status: open\n---\n## Findings\nNo findings.\n",
            encoding="utf-8",
        )

        with (
            mock.patch.object(FACTORY_CMD_QUICK, "_write_diff_artifact", return_value=stub_diff),
            mock.patch.object(FACTORY_CMD_QUICK, "_run_codex_review", return_value=0) as mock_review,
            mock.patch("factory_cmd_dispatch.command_dispatch_codex", return_value=0) as mock_dispatch,
        ):
            args = self._make_args(prompt_path=str(prompt_path))
            rc, stdout, stderr = self._invoke(args)

        self.assertEqual(rc, 0)
        mock_dispatch.assert_called_once()
        mock_review.assert_called_once()

    # ------------------------------------------------------------------
    # Test 4: severity-count summary correctly reports HIGH/MEDIUM/LOW
    # ------------------------------------------------------------------

    def test_severity_count_summary_from_fixture(self) -> None:
        self._make_slug_dir()

        review_content = (
            "---\nresolution_status: open\n---\n"
            "## Findings\n"
            "- **HIGH**: first high finding\n"
            "- **HIGH**: second high finding\n"
            "- **MEDIUM**: a medium finding\n"
            "- **LOW**: a low finding\n"
            "## Residual Risks\n"
            "None.\n"
        )
        stub_diff = self.runs_root / SLUG / "reviews" / "quick.diff.txt"
        stub_review = self.runs_root / SLUG / "reviews" / "diff.codex.correctness-adversarial.review.md"
        stub_review.parent.mkdir(parents=True, exist_ok=True)
        stub_review.write_text(review_content, encoding="utf-8")

        with (
            mock.patch.object(FACTORY_CMD_QUICK, "_write_diff_artifact", return_value=stub_diff),
            mock.patch.object(FACTORY_CMD_QUICK, "_run_codex_review", return_value=0),
        ):
            args = self._make_args()
            rc, stdout, _stderr = self._invoke(args)

        self.assertEqual(rc, 0)
        counts = FACTORY_CMD_QUICK._count_severities(stub_review)
        self.assertEqual(counts["HIGH"], 2)
        self.assertEqual(counts["MEDIUM"], 1)
        self.assertEqual(counts["LOW"], 1)
        self.assertIn("HIGH/CRITICAL: 2", stdout)
        self.assertIn("MEDIUM: 1", stdout)
        self.assertIn("LOW: 1", stdout)
        self.assertIn("address HIGH/CRITICAL findings", stdout)

    # ------------------------------------------------------------------
    # Test 5: --review-lens quality routes to gemini script
    # ------------------------------------------------------------------

    def test_review_lens_quality_routes_to_gemini(self) -> None:
        self._make_slug_dir()

        stub_diff = self.runs_root / SLUG / "reviews" / "quick.diff.txt"
        stub_review = self.runs_root / SLUG / "reviews" / "diff.gemini.quality-adversarial.review.md"
        stub_review.parent.mkdir(parents=True, exist_ok=True)
        stub_review.write_text(
            "---\nresolution_status: open\n---\n## Findings\nNo findings.\n",
            encoding="utf-8",
        )

        with (
            mock.patch.object(FACTORY_CMD_QUICK, "_write_diff_artifact", return_value=stub_diff),
            mock.patch.object(FACTORY_CMD_QUICK, "_run_codex_review") as mock_codex,
            mock.patch.object(FACTORY_CMD_QUICK, "_run_gemini_review", return_value=0) as mock_gemini,
        ):
            args = self._make_args(review_lens="quality")
            rc, stdout, _stderr = self._invoke(args)

        self.assertEqual(rc, 0)
        mock_gemini.assert_called_once()
        mock_codex.assert_not_called()
        self.assertIn("gemini.quality-adversarial", stdout)


if __name__ == "__main__":
    unittest.main()
