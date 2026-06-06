"""Tests for factory_cmd_closeout.command_standalone_closeout.

Covers the four required test cases:
  a. All stages fully reviewed → zero skipped lenses in coverage summary.
  b. Diff-stage default reviews removed → diff_lenses_skipped stays empty.
  c. Empty state (no stages, no token_usage, no discovery) → refuse, exit 0, no files.
  d. Re-run is idempotent: generated section of closeout.md is identical on second call.
"""
import argparse
import gc
import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


FACTORY_STATE = _load("factory_state")
FACTORY_CMD_CLOSEOUT = _load("factory_cmd_closeout")

# Shorthand
command_standalone_closeout = FACTORY_CMD_CLOSEOUT.command_standalone_closeout
_GEN_START = FACTORY_CMD_CLOSEOUT._GEN_START
_GEN_END = FACTORY_CMD_CLOSEOUT._GEN_END
_RUN_FACTS_START = FACTORY_CMD_CLOSEOUT._RUN_FACTS_START
_RUN_FACTS_END = FACTORY_CMD_CLOSEOUT._RUN_FACTS_END


def _patch_factory_state_roots(runs_root: Path, repo_root: Path) -> list:
    """Return a list of started patches covering all factory_state instances in memory."""
    patches = []
    for mod in list(gc.get_objects()):
        if not isinstance(mod, types.ModuleType):
            continue
        if getattr(mod, "__name__", "") != "factory_state":
            continue
        if hasattr(mod, "FACTORY_RUNS_ROOT"):
            p = mock.patch.object(mod, "FACTORY_RUNS_ROOT", runs_root)
            p.start()
            patches.append(p)
        if hasattr(mod, "REPO_ROOT"):
            p = mock.patch.object(mod, "REPO_ROOT", repo_root)
            p.start()
            patches.append(p)
    # Also patch the closeout module's imported names
    for attr in ("workflow_dir", "reviews_dir", "load_workflow_state", "save_workflow_state"):
        if hasattr(FACTORY_CMD_CLOSEOUT, attr):
            fn = getattr(FACTORY_CMD_CLOSEOUT, attr)
            # The function refers to FACTORY_RUNS_ROOT inside factory_state —
            # already patched above. No additional patch needed.
    return patches


def _closeout_args(**overrides) -> argparse.Namespace:
    defaults = {
        "slug": "test-slug",
        "pr_url": None,
        "pr_number": None,
        "merge_sha": None,
        "note": None,
        "out": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class CloseoutCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_root = Path(self._tmpdir.name)
        self.runs_root = self.repo_root / "docs" / "workflow" / "feature-runs"
        self.slug = "test-slug"
        self.workflow_dir = self.runs_root / self.slug
        self.workflow_dir.mkdir(parents=True, exist_ok=True)

        self._patches = _patch_factory_state_roots(self.runs_root, self.repo_root)
        self.addCleanup(lambda: [p.stop() for p in self._patches])

        # Suppress gh CLI detection in all tests
        self._gh_patch = mock.patch.object(
            FACTORY_CMD_CLOSEOUT, "_detect_pr_from_gh", return_value={}
        )
        self._gh_patch.start()
        self.addCleanup(self._gh_patch.stop)

    def _write_state(self, state: dict) -> None:
        FACTORY_STATE.save_workflow_state(self.slug, state)

    def _write_review(self, stage: str, reviewer: str, lens: str, content: str = "## Findings\n\nnone\n\n## Residual Risks\n\nnone") -> None:
        rdir = self.workflow_dir / "reviews"
        rdir.mkdir(parents=True, exist_ok=True)
        fname = f"{stage}.{reviewer}.{lens}.review.md"
        (rdir / fname).write_text(content, encoding="utf-8")

    def _make_state_with_stages(self) -> dict:
        state = FACTORY_STATE._default_workflow_state()
        state["schema_version"] = 2
        state["discovery"] = {
            "version": 2,
            "required": True,
            "complete": True,
            "question_count": 0,
            "asked_count": 0,
            "questions": [],
            "assumptions": [],
            "summary": "",
            "updated_at": 0,
            "answers": {},
            "non_goals": [],
            "acceptance_criteria": [],
            "unresolved": [],
        }
        state["stages"] = {
            "spec": {"adversarial_rounds": 3, "annotations": [], "adversarial_sha_history": [], "initial_sha": ""},
            "plan": {"adversarial_rounds": 3, "annotations": [], "adversarial_sha_history": [], "initial_sha": ""},
            "tasks": {"adversarial_rounds": 3, "annotations": [], "adversarial_sha_history": [], "initial_sha": ""},
            "diff": {"adversarial_rounds": 1, "annotations": [], "adversarial_sha_history": [], "initial_sha": ""},
        }
        state["token_usage"] = []
        return state

    def test_all_stages_fully_reviewed_zero_skipped(self) -> None:
        """a. Surviving default lenses present → coverage summary reports zero skipped."""
        state = self._make_state_with_stages()
        self._write_state(state)

        # Write the surviving default lenses for spec and plan only.
        for stage, lenses in [
            ("spec", [("codex", "feasibility-adversarial"), ("gemini", "requirements-adversarial")]),
            ("plan", [("codex", "implementation-adversarial"), ("gemini", "testability-adversarial")]),
        ]:
            for reviewer, lens in lenses:
                self._write_review(stage, reviewer, lens)

        args = _closeout_args(slug=self.slug, pr_url="https://example.com/pr/1", pr_number=1)
        rc = command_standalone_closeout(args)

        self.assertEqual(rc, 0)
        reloaded = FACTORY_STATE.load_workflow_state(self.slug)
        delivery = reloaded.get("delivery", {})
        cov = delivery.get("review_coverage_summary", {})

        # No stage should have skipped lenses.
        for stage in ["spec", "plan", "tasks", "diff", "closeout"]:
            skipped = cov.get(f"{stage}_lenses_skipped", [])
            self.assertEqual(skipped, [], f"Expected no skipped lenses for {stage}, got {skipped}")

    def test_diff_has_no_default_skipped_lenses(self) -> None:
        """b. Diff-stage no longer has default reviews, so skipped stays empty."""
        state = self._make_state_with_stages()
        self._write_state(state)

        # Write the surviving default lenses for spec/plan and one extra diff review.
        for stage, lenses in [
            ("spec", [("codex", "feasibility-adversarial"), ("gemini", "requirements-adversarial")]),
            ("plan", [("codex", "implementation-adversarial"), ("gemini", "testability-adversarial")]),
            ("diff", [("gemini", "quality-adversarial")]),
        ]:
            for reviewer, lens in lenses:
                self._write_review(stage, reviewer, lens)

        args = _closeout_args(slug=self.slug, pr_url="https://example.com/pr/2", pr_number=2)
        rc = command_standalone_closeout(args)

        self.assertEqual(rc, 0)
        reloaded = FACTORY_STATE.load_workflow_state(self.slug)
        delivery = reloaded.get("delivery", {})
        cov = delivery.get("review_coverage_summary", {})

        diff_skipped = cov.get("diff_lenses_skipped", [])
        self.assertEqual(diff_skipped, [])
        # Gemini quality should still be counted as run.
        diff_run = cov.get("diff_lenses_run", [])
        self.assertIn("gemini:quality-adversarial", diff_run)
        self.assertEqual(cov.get("closeout_lenses_skipped", []), [])

    def test_empty_state_refuses_and_exits_zero(self) -> None:
        """c. Empty state → refuses with informative message, exit 0, no files written."""
        # Write a state with none of the meaningful fields populated.
        # "Empty" = discovery never completed, no stages, no token_usage.
        empty_state = FACTORY_STATE._default_workflow_state()
        empty_state["stages"] = {}
        empty_state["token_usage"] = []
        empty_state["discovery"] = {
            "version": 2,
            "required": False,
            "complete": False,  # discovery was never completed
            "question_count": 0,
            "asked_count": 0,
            "questions": [],
            "assumptions": [],
            "summary": "",
            "updated_at": 0,
            "answers": {},
            "non_goals": [],
            "acceptance_criteria": [],
            "unresolved": [],
        }
        self._write_state(empty_state)

        expected_closeout = self.workflow_dir / "closeout.md"
        self.assertFalse(expected_closeout.exists())

        import io as _io
        stderr_buf = _io.StringIO()
        with mock.patch("sys.stderr", stderr_buf):
            rc = command_standalone_closeout(_closeout_args(slug=self.slug))

        self.assertEqual(rc, 0)
        self.assertFalse(expected_closeout.exists(), "closeout.md should not be written for empty state")
        self.assertIn("no runner state", stderr_buf.getvalue())

    def test_rerun_idempotent_generated_block(self) -> None:
        """d. Re-running closeout produces identical generated block."""
        state = self._make_state_with_stages()
        self._write_state(state)

        for stage, lenses in [
            ("spec", [("codex", "feasibility-adversarial"), ("gemini", "requirements-adversarial")]),
            ("plan", [("codex", "implementation-adversarial"), ("gemini", "testability-adversarial")]),
        ]:
            for reviewer, lens in lenses:
                self._write_review(stage, reviewer, lens)

        args = _closeout_args(
            slug=self.slug,
            pr_url="https://example.com/pr/3",
            pr_number=3,
            merge_sha="abc123",
            note="first run",
        )

        # First invocation
        rc1 = command_standalone_closeout(args)
        self.assertEqual(rc1, 0)
        closeout_path = self.workflow_dir / "closeout.md"
        text1 = closeout_path.read_text(encoding="utf-8")

        # Extract generated block from first run
        def _extract_generated(text: str) -> str:
            end_idx = text.find(_GEN_END)
            if end_idx < 0:
                return text
            return text[:end_idx + len(_GEN_END)]

        gen1 = _extract_generated(text1)

        # Add operator notes so we can verify they survive re-run
        with_notes = text1 + "\nHandwritten operator note here."
        closeout_path.write_text(with_notes, encoding="utf-8")

        # Second invocation
        rc2 = command_standalone_closeout(args)
        self.assertEqual(rc2, 0)
        text2 = closeout_path.read_text(encoding="utf-8")
        gen2 = _extract_generated(text2)

        # The generated blocks should be identical (modulo timestamp which is per-run,
        # but we check structural equality — all lines except "Closed at")
        gen1_lines = [l for l in gen1.splitlines() if not l.startswith("- Closed at:")]
        gen2_lines = [l for l in gen2.splitlines() if not l.startswith("- Closed at:")]
        self.assertEqual(gen1_lines, gen2_lines, "Generated block differs between runs")

        # Operator notes should be preserved
        self.assertIn("Handwritten operator note here.", text2)


    # ------------------------------------------------------------------
    # Run Facts tests
    # ------------------------------------------------------------------

    def _make_token_usage_entries(self) -> list[dict]:
        """Return a realistic token_usage list covering spec, plan, tasks, diff stages."""
        base = "2026-04-27T"
        return [
            # spec — two Codex calls + one Gemini call
            {
                "stage": "spec", "round": 1, "activity_type": "adversarial_review",
                "model": "gpt-5.4-mini",
                "started_at": f"{base}10:00:00Z", "ended_at": f"{base}10:01:00Z",
                "duration_seconds": 60.0, "parse_error": None,
            },
            {
                "stage": "spec", "round": 1, "activity_type": "adversarial_review",
                "model": "gpt-5.4-mini",
                "started_at": f"{base}10:01:01Z", "ended_at": f"{base}10:02:00Z",
                "duration_seconds": 59.0, "parse_error": None,
            },
            {
                "stage": "spec", "round": 1, "activity_type": "adversarial_review",
                "model": "gemini-2.5-pro",
                "started_at": f"{base}10:02:01Z", "ended_at": f"{base}10:03:00Z",
                "duration_seconds": 59.0, "parse_error": None,
            },
            # plan — one Codex, one Gemini with parse_error
            {
                "stage": "plan", "round": 1, "activity_type": "adversarial_review",
                "model": "gpt-5.4-mini",
                "started_at": f"{base}10:10:00Z", "ended_at": f"{base}10:11:00Z",
                "duration_seconds": 60.0, "parse_error": None,
            },
            {
                "stage": "plan", "round": 1, "activity_type": "adversarial_review",
                "model": "gemini-2.5-pro",
                "started_at": f"{base}10:11:01Z", "ended_at": f"{base}10:12:00Z",
                "duration_seconds": 59.0, "parse_error": "no Gemini token stats found in stdout",
            },
        ]

    def _make_codex_dispatch_entries(self) -> list[dict]:
        """Two dispatches — one single-try, one retried (same prompt_sha256)."""
        return [
            {
                "head_sha": "aaa", "ts": "20260427T100000_000000Z",
                "prompt_path": "/tmp/slice1.txt", "prompt_sha256": "sha_slice_1",
                "model": "gpt-5.4-mini", "exit_code": 0,
                "stdout_path": "out.txt", "stderr_path": "err.txt",
                "branch_base_sha": None, "lines_added_at_dispatch_time": None,
                "auto_commit": {"skipped": True, "reason": "no codex-introduced changes"},
            },
            # Retry: same prompt_sha256 as next entry
            {
                "head_sha": "bbb", "ts": "20260427T110000_000000Z",
                "prompt_path": "/tmp/slice2.txt", "prompt_sha256": "sha_slice_2",
                "model": "gpt-5.4-mini", "exit_code": 1,
                "stdout_path": "out.txt", "stderr_path": "err.txt",
                "branch_base_sha": None, "lines_added_at_dispatch_time": None,
                "auto_commit": {"skipped": True, "reason": "no codex-introduced changes"},
            },
            {
                "head_sha": "ccc", "ts": "20260427T110500_000000Z",
                "prompt_path": "/tmp/slice2.txt", "prompt_sha256": "sha_slice_2",
                "model": "gpt-5.4-mini", "exit_code": 0,
                "stdout_path": "out.txt", "stderr_path": "err.txt",
                "branch_base_sha": None, "lines_added_at_dispatch_time": None,
                "auto_commit": {"skipped": True, "reason": "no codex-introduced changes"},
            },
        ]

    def _extract_run_facts_section(self, text: str) -> str:
        """Extract the text between begin-run-facts and end-run-facts markers."""
        start = text.find(_RUN_FACTS_START)
        end = text.find(_RUN_FACTS_END)
        if start < 0 or end < 0:
            return ""
        return text[start:end + len(_RUN_FACTS_END)]

    def test_run_facts_full_data(self) -> None:
        """1. State with full token_usage and dispatches → Run Facts shows real numbers."""
        state = self._make_state_with_stages()
        state["token_usage"] = self._make_token_usage_entries()
        state["codex_dispatches"] = self._make_codex_dispatch_entries()
        self._write_state(state)

        args = _closeout_args(slug=self.slug, pr_url="https://example.com/pr/10", pr_number=10)
        rc = command_standalone_closeout(args)
        self.assertEqual(rc, 0)

        text = (self.workflow_dir / "closeout.md").read_text(encoding="utf-8")

        # Run Facts section must be present
        self.assertIn(_RUN_FACTS_START, text)
        self.assertIn(_RUN_FACTS_END, text)
        self.assertIn("## Run Facts", text)

        facts = self._extract_run_facts_section(text)
        # Wall clock: spec starts at 10:00:00, plan ends at 10:12:00 → 72 min total
        self.assertIn("Total:", facts)
        self.assertNotIn("(no data)", facts)

        # Subprocess counts: 3 Codex + 2 Gemini in token_usage
        self.assertIn("Codex: 3 total", facts)
        self.assertIn("Gemini: 2 total", facts)

        # Dispatches: 3 entries → 2 slices (sha_slice_1 + sha_slice_2), 1 retry
        self.assertIn("2 slices", facts)
        self.assertIn("1 retr", facts)

        # Stages exercised
        self.assertIn("spec", facts)
        self.assertIn("plan", facts)

        # Stages skipped (tasks, diff, closeout are absent from token_usage)
        self.assertIn("Stages skipped:", facts)

    def test_run_facts_empty_token_usage(self) -> None:
        """2. State with empty token_usage → Run Facts shows 'no recorded subprocess activity'."""
        state = self._make_state_with_stages()
        state["token_usage"] = []
        state["codex_dispatches"] = []
        self._write_state(state)

        args = _closeout_args(slug=self.slug, pr_url="https://example.com/pr/11", pr_number=11)
        rc = command_standalone_closeout(args)
        self.assertEqual(rc, 0)

        text = (self.workflow_dir / "closeout.md").read_text(encoding="utf-8")
        self.assertIn(_RUN_FACTS_START, text)
        self.assertIn(_RUN_FACTS_END, text)
        # Should indicate no data
        self.assertIn("no recorded subprocess activity", text)

    def test_run_facts_codex_timeout(self) -> None:
        """3. State with Codex timeout in token_usage → timeout count = 1."""
        state = self._make_state_with_stages()
        state["token_usage"] = [
            {
                "stage": "spec", "round": 1, "activity_type": "adversarial_review",
                "model": "gpt-5.4-mini",
                "started_at": "2026-04-27T10:00:00Z", "ended_at": "2026-04-27T10:02:00Z",
                "duration_seconds": 120.0,
                "parse_error": "codex exec timeout after 120s",
            },
            {
                "stage": "spec", "round": 1, "activity_type": "adversarial_review",
                "model": "gpt-5.4-mini",
                "started_at": "2026-04-27T10:02:01Z", "ended_at": "2026-04-27T10:03:00Z",
                "duration_seconds": 59.0,
                "parse_error": None,
            },
        ]
        state["codex_dispatches"] = []
        self._write_state(state)

        args = _closeout_args(slug=self.slug, pr_url="https://example.com/pr/12", pr_number=12)
        rc = command_standalone_closeout(args)
        self.assertEqual(rc, 0)

        text = (self.workflow_dir / "closeout.md").read_text(encoding="utf-8")
        facts = self._extract_run_facts_section(text)

        # 2 Codex calls, 1 timeout, 0 other errors
        self.assertIn("Codex: 2 total", facts)
        self.assertIn("1 timeout", facts)
        self.assertIn("0 other error", facts)

    def test_run_facts_retried_slice(self) -> None:
        """4. State with retried slice → slice retries reported correctly."""
        state = self._make_state_with_stages()
        state["token_usage"] = [
            {
                "stage": "diff", "round": 1, "activity_type": "adversarial_review",
                "model": "gpt-5.4-mini",
                "started_at": "2026-04-27T11:00:00Z", "ended_at": "2026-04-27T11:01:00Z",
                "duration_seconds": 60.0, "parse_error": None,
            },
        ]
        # Three entries with the same prompt_sha256 → 3 attempts for 1 slice
        state["codex_dispatches"] = [
            {
                "head_sha": "a", "ts": "20260427T110000Z", "prompt_path": "/tmp/s.txt",
                "prompt_sha256": "sha_retry_slice",
                "model": "gpt-5.4-mini", "exit_code": 1,
                "stdout_path": "o.txt", "stderr_path": "e.txt",
                "branch_base_sha": None, "lines_added_at_dispatch_time": None,
                "auto_commit": {"skipped": True, "reason": "overlap with operator dirty"},
            },
            {
                "head_sha": "b", "ts": "20260427T110500Z", "prompt_path": "/tmp/s.txt",
                "prompt_sha256": "sha_retry_slice",
                "model": "gpt-5.4-mini", "exit_code": 1,
                "stdout_path": "o.txt", "stderr_path": "e.txt",
                "branch_base_sha": None, "lines_added_at_dispatch_time": None,
                "auto_commit": {"skipped": True, "reason": "overlap with operator dirty"},
            },
            {
                "head_sha": "c", "ts": "20260427T111000Z", "prompt_path": "/tmp/s.txt",
                "prompt_sha256": "sha_retry_slice",
                "model": "gpt-5.4-mini", "exit_code": 0,
                "stdout_path": "o.txt", "stderr_path": "e.txt",
                "branch_base_sha": None, "lines_added_at_dispatch_time": None,
                "auto_commit": {"skipped": True, "reason": "no codex-introduced changes"},
            },
        ]
        self._write_state(state)

        args = _closeout_args(slug=self.slug, pr_url="https://example.com/pr/13", pr_number=13)
        rc = command_standalone_closeout(args)
        self.assertEqual(rc, 0)

        text = (self.workflow_dir / "closeout.md").read_text(encoding="utf-8")
        facts = self._extract_run_facts_section(text)

        # 1 slice (all same sha), 1 retry, 3 attempts
        self.assertIn("1 slices", facts)
        self.assertIn("1 retr", facts)
        self.assertIn("3 attempts", facts)

    def test_run_facts_idempotent(self) -> None:
        """5. Running closeout twice produces identical Run Facts section."""
        state = self._make_state_with_stages()
        state["token_usage"] = self._make_token_usage_entries()
        state["codex_dispatches"] = self._make_codex_dispatch_entries()
        self._write_state(state)

        for stage, lenses in [
            ("spec", [("codex", "feasibility-adversarial"), ("gemini", "requirements-adversarial")]),
            ("plan", [("codex", "implementation-adversarial"), ("gemini", "testability-adversarial")]),
        ]:
            for reviewer, lens in lenses:
                self._write_review(stage, reviewer, lens)

        args = _closeout_args(
            slug=self.slug,
            pr_url="https://example.com/pr/14",
            pr_number=14,
            merge_sha="deadbeef",
        )

        rc1 = command_standalone_closeout(args)
        self.assertEqual(rc1, 0)
        text1 = (self.workflow_dir / "closeout.md").read_text(encoding="utf-8")
        facts1 = self._extract_run_facts_section(text1)

        rc2 = command_standalone_closeout(args)
        self.assertEqual(rc2, 0)
        text2 = (self.workflow_dir / "closeout.md").read_text(encoding="utf-8")
        facts2 = self._extract_run_facts_section(text2)

        self.assertEqual(facts1, facts2, "Run Facts section should be identical on second run")

    def test_run_facts_operator_notes_preserved(self) -> None:
        """6. Regenerating Run Facts leaves Operator Notes intact."""
        state = self._make_state_with_stages()
        state["token_usage"] = self._make_token_usage_entries()
        state["codex_dispatches"] = []
        self._write_state(state)

        args = _closeout_args(
            slug=self.slug,
            pr_url="https://example.com/pr/15",
            pr_number=15,
        )

        # First run to create the file
        rc1 = command_standalone_closeout(args)
        self.assertEqual(rc1, 0)
        closeout_path = self.workflow_dir / "closeout.md"
        text1 = closeout_path.read_text(encoding="utf-8")

        # Simulate operator writing notes after the generated block
        operator_note = "This is a handwritten operator note that must survive regeneration."
        text_with_notes = text1 + "\n" + operator_note
        closeout_path.write_text(text_with_notes, encoding="utf-8")

        # Second run — regenerate
        rc2 = command_standalone_closeout(args)
        self.assertEqual(rc2, 0)
        text2 = closeout_path.read_text(encoding="utf-8")

        # Run Facts should still be there
        self.assertIn(_RUN_FACTS_START, text2)
        self.assertIn(_RUN_FACTS_END, text2)

        # Operator note must be preserved
        self.assertIn(operator_note, text2)

        # Operator note must appear AFTER the generated block
        gen_end_pos = text2.find(_GEN_END)
        note_pos = text2.find(operator_note)
        self.assertGreater(note_pos, gen_end_pos, "Operator note must come after end-generated marker")


if __name__ == "__main__":
    unittest.main()
