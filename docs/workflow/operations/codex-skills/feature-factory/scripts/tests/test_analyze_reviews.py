import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys


SCRIPT_DIR = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


FACTORY_STATE = _load("factory_state")
ANALYZER = _load("factory_cmd_analyze_reviews")


class AnalyzeReviewsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.repo_root = Path(self._tmpdir.name)
        self.runs_root = self.repo_root / "docs" / "workflow" / "feature-runs"
        self.analysis_root = self.repo_root / "docs" / "workflow" / "analysis"
        self.runs_root.mkdir(parents=True, exist_ok=True)

        self._repo_patch = mock.patch.object(FACTORY_STATE, "REPO_ROOT", self.repo_root)
        self._runs_patch = mock.patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", self.runs_root)
        self._analyzer_repo_patch = mock.patch.object(ANALYZER.factory_state, "REPO_ROOT", self.repo_root)
        self._analyzer_runs_patch = mock.patch.object(ANALYZER.factory_state, "FACTORY_RUNS_ROOT", self.runs_root)
        self._repo_patch.start()
        self._runs_patch.start()
        self._analyzer_repo_patch.start()
        self._analyzer_runs_patch.start()
        self.addCleanup(self._repo_patch.stop)
        self.addCleanup(self._runs_patch.stop)
        self.addCleanup(self._analyzer_repo_patch.stop)
        self.addCleanup(self._analyzer_runs_patch.stop)

    def _write_state(
        self,
        slug: str,
        token_usage: object,
        *,
        stages: dict | None = None,
        raw_text: str | None = None,
    ) -> None:
        slug_dir = self.runs_root / slug
        slug_dir.mkdir(parents=True, exist_ok=True)
        path = slug_dir / "state.json"
        if raw_text is not None:
            path.write_text(raw_text, encoding="utf-8")
            return
        payload = {"token_usage": token_usage}
        if stages is not None:
            payload["stages"] = stages
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_review(
        self,
        slug: str,
        name: str,
        *,
        reviewer: str,
        lens: str,
        stage: str,
        status: str,
    ) -> None:
        reviews_dir = self.runs_root / slug / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        (reviews_dir / name).write_text(
            "---\n"
            f'reviewer: "{reviewer}"\n'
            f'lens: "{lens}"\n'
            f'stage: "{stage}"\n'
            f'resolution_status: "{status}"\n'
            "---\n"
            "# Review\n",
            encoding="utf-8",
        )

    def _write_artifact(self, slug: str, relative_path: str, text: str) -> None:
        artifact_path = self.runs_root / slug / relative_path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(text, encoding="utf-8")

    def _record(
        self,
        *,
        stage: str,
        round_number: int,
        activity_type: str,
        model: str,
        duration_seconds: float,
        input_tokens: int = 100,
        output_tokens: int = 10,
        parse_error: str | None = None,
        timestamp: str = "2026-04-25T12:00:00Z",
        cost_usd_estimate: float = 0.5,
        prompt_chars: int | None = None,
        prompt_cap: int | None = None,
    ) -> dict:
        record = {
            "stage": stage,
            "round": round_number,
            "activity_type": activity_type,
            "model": model,
            "duration_seconds": duration_seconds,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "timestamp": timestamp,
            "cost_usd_estimate": cost_usd_estimate,
        }
        if parse_error is not None:
            record["parse_error"] = parse_error
        if prompt_chars is not None:
            record["prompt_chars"] = prompt_chars
        if prompt_cap is not None:
            record["prompt_cap"] = prompt_cap
        return record

    def _run(self, *, out: Path | None = None, top_n: int = 20) -> tuple[int, str, str, str]:
        output = out or (self.analysis_root / "report.md")
        args = argparse.Namespace(out=str(output), top_n=top_n)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = ANALYZER.command_analyze_reviews(args)
        report = output.read_text(encoding="utf-8")
        return rc, stdout.getvalue(), stderr.getvalue(), report

    def test_happy_path_summarizes_model_activity_counts_and_percentiles(self) -> None:
        self._write_state(
            "alpha",
            [
                self._record(stage="spec", round_number=1, activity_type="adversarial_review", model="gpt-5.4-mini", duration_seconds=10.0),
                self._record(stage="spec", round_number=1, activity_type="adversarial_review", model="gpt-5.4-mini", duration_seconds=20.0),
                self._record(stage="plan", round_number=1, activity_type="judge_panel", model="gpt-5.4", duration_seconds=30.0),
                self._record(stage="plan", round_number=1, activity_type="judge_panel", model="gpt-5.4", duration_seconds=40.0),
                self._record(stage="diff", round_number=2, activity_type="adversarial_review", model="gemini-2.5-pro", duration_seconds=50.0),
            ],
            stages={"spec": {"adversarial_rounds": 1, "judge_rounds": 0}, "plan": {"adversarial_rounds": 1, "judge_rounds": 1}},
        )
        self._write_state(
            "beta",
            [
                self._record(stage="spec", round_number=2, activity_type="adversarial_review", model="gpt-5.4-mini", duration_seconds=30.0),
                self._record(stage="tasks", round_number=1, activity_type="judge_panel", model="gpt-5.4", duration_seconds=50.0),
                self._record(stage="tasks", round_number=1, activity_type="judge_panel", model="gpt-5.4", duration_seconds=60.0),
                self._record(stage="diff", round_number=2, activity_type="adversarial_review", model="gemini-2.5-pro", duration_seconds=70.0),
                self._record(stage="diff", round_number=2, activity_type="implementation", model="gpt-5.4-mini", duration_seconds=999.0),
            ],
            stages={"tasks": {"adversarial_rounds": 1, "judge_rounds": 3}},
        )

        rc, _, stderr, report = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(stderr, "")
        self.assertIn("| gpt-5.4-mini | adversarial_review | 3 | 60.0 | 20.0 | 29.0 | 29.8 | 30.0 | 0 | 0.0% |", report)
        self.assertIn("| gpt-5.4 | judge_panel | 4 | 180.0 | 45.0 | 58.5 | 59.7 | 60.0 | 0 | 0.0% |", report)
        self.assertIn("- Total reviewer + judge calls measured: 9", report)
        self.assertIn("| beta | 100.0 | 110.0 | 0.0 | 3 | 1 |", report)

    def test_missing_fields_are_dropped_and_reported(self) -> None:
        self._write_state(
            "gamma",
            [
                self._record(stage="spec", round_number=1, activity_type="adversarial_review", model="gpt-5.4-mini", duration_seconds=10.0),
                {"stage": "spec", "round": 1, "activity_type": "adversarial_review", "duration_seconds": 20.0},
                {"stage": "spec", "round": 1, "activity_type": "adversarial_review", "model": "gpt-5.4-mini"},
            ],
        )

        _, _, _, report = self._run()
        self.assertIn("- Dropped records: 2", report)
        self.assertIn("duration_seconds=1, model=1", report)
        self.assertIn("- Total reviewer + judge calls measured: 1", report)

    def test_empty_state_does_not_crash(self) -> None:
        self._write_state("empty", [])
        rc, _, stderr, report = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(stderr, "")
        self.assertIn("- Total reviewer + judge calls measured: 0", report)

    def test_malformed_state_json_is_skipped_and_warns(self) -> None:
        self._write_state("broken", [], raw_text="{not-json")
        rc, _, stderr, report = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("warning: skipping malformed state.json for broken", stderr)
        self.assertIn("- Slugs with malformed state.json: broken", report)

    def test_parse_error_grouping_truncates_to_shared_pattern(self) -> None:
        shared = "schema violation at path very/long/path/that/keeps/going and going and still has unique suffix "
        self._write_state(
            "delta",
            [
                self._record(
                    stage="spec",
                    round_number=1,
                    activity_type="judge_panel",
                    model="gpt-5.4",
                    duration_seconds=11.0,
                    parse_error=shared + "AAA",
                ),
                self._record(
                    stage="spec",
                    round_number=1,
                    activity_type="judge_panel",
                    model="gpt-5.4",
                    duration_seconds=12.0,
                    parse_error=shared + "BBB",
                ),
                self._record(
                    stage="spec",
                    round_number=1,
                    activity_type="judge_panel",
                    model="gpt-5.4-mini",
                    duration_seconds=13.0,
                    parse_error=shared + "CCC",
                ),
            ],
        )

        _, _, _, report = self._run()
        self.assertIn("| schema violation at path very/long/path/that/keeps/going and going and still... | 3 | gpt-5.4, gpt-5.4-mini | delta |", report)

    def test_artifact_size_section_reports_fixture_distribution(self) -> None:
        self._write_state("alpha", [])
        self._write_state("beta", [])
        self._write_artifact("alpha", "spec.md", "a" * 10)
        self._write_artifact("alpha", "plan.md", "b" * 20)
        self._write_artifact("alpha", "tasks.md", "c" * 30)
        self._write_artifact("alpha", "reviews/implementation.diff.patch", "d" * 40)
        self._write_artifact("beta", "spec.md", "e" * 50)
        self._write_artifact("beta", "plan.md", "f" * 60)
        self._write_artifact("beta", "tasks.md", "g" * 70)
        self._write_artifact("beta", "reviews/implementation.diff.patch", "h" * 80)

        _, _, _, report = self._run()

        self.assertIn("## 8. Artifact Sizes", report)
        self.assertIn("| spec | 2 | 30 | 48 | 50 | 0 | 0 |", report)
        self.assertIn("| plan | 2 | 40 | 58 | 60 | 0 | 0 |", report)
        self.assertIn("| tasks | 2 | 50 | 68 | 70 | 0 | 0 |", report)
        self.assertIn("| diff | 2 | 60 | 78 | 80 | 0 | 0 |", report)
        self.assertIn("| beta | diff | 80 |", report)
        self.assertIn("| beta | tasks | 70 |", report)

    def test_prompt_cap_pressure_section_reports_rollup(self) -> None:
        self._write_state(
            "epsilon",
            [
                self._record(
                    stage="spec",
                    round_number=1,
                    activity_type="adversarial_review",
                    model="gpt-5.4-mini",
                    duration_seconds=10.0,
                    prompt_chars=900,
                    prompt_cap=1000,
                ),
                self._record(
                    stage="plan",
                    round_number=1,
                    activity_type="adversarial_review",
                    model="gpt-5.4-mini",
                    duration_seconds=20.0,
                    prompt_chars=600,
                    prompt_cap=1000,
                ),
                self._record(
                    stage="tasks",
                    round_number=1,
                    activity_type="judge_panel",
                    model="gpt-5.4",
                    duration_seconds=30.0,
                    prompt_chars=1100,
                    prompt_cap=1000,
                ),
                self._record(
                    stage="diff",
                    round_number=1,
                    activity_type="adversarial_review",
                    model="gemini-2.5-pro",
                    duration_seconds=40.0,
                ),
            ],
        )

        _, _, _, report = self._run()

        self.assertIn("## 9. Prompt-Cap Pressure", report)
        self.assertIn(
            "| gpt-5.4-mini | adversarial_review | 2 | 75% | 88% | 1 | 0 |",
            report,
        )
        self.assertIn(
            "| gpt-5.4 | judge_panel | 1 | 110% | 110% | 1 | 1 |",
            report,
        )
        self.assertNotIn("No prompt-size data available yet", report)

    def test_per_feature_metrics_rollup_section(self) -> None:
        """Section 7a renders correctly with two slugs carrying command telemetry."""
        ct_alpha = [
            {"command": "discover", "stage": "spec", "ts": "2026-04-25T10:00:00Z", "wall_seconds": 45.0, "input_bytes_read": 100, "output_bytes_written": 50, "ttl_crossed": False},
            {"command": "checkpoint", "stage": "spec", "ts": "2026-04-25T10:05:00Z", "wall_seconds": 310.0, "input_bytes_read": 200, "output_bytes_written": 80, "ttl_crossed": True},
        ]
        tu_alpha = [
            {"model": "gpt-5.4-mini", "activity_type": "adversarial_review", "input_tokens": 1000, "output_tokens": 200, "duration_seconds": 10.0},
            {"model": "gemini-2.5-pro", "activity_type": "adversarial_review", "input_tokens": 800, "output_tokens": 150, "duration_seconds": 12.0},
        ]
        ct_beta = [
            {"command": "discover", "stage": None, "ts": "2026-04-26T10:00:00Z", "wall_seconds": 20.0, "input_bytes_read": 50, "output_bytes_written": 30, "ttl_crossed": False},
        ]
        tu_beta = [
            {"model": "gpt-5.4", "activity_type": "adversarial_review", "input_tokens": 2000, "output_tokens": 300, "duration_seconds": 15.0},
        ]

        slug_alpha = self.runs_root / "alpha-metrics"
        slug_alpha.mkdir(parents=True, exist_ok=True)
        (slug_alpha / "state.json").write_text(
            json.dumps({"token_usage": tu_alpha, "command_telemetry": ct_alpha}), encoding="utf-8"
        )
        slug_beta = self.runs_root / "beta-metrics"
        slug_beta.mkdir(parents=True, exist_ok=True)
        (slug_beta / "state.json").write_text(
            json.dumps({"token_usage": tu_beta, "command_telemetry": ct_beta}), encoding="utf-8"
        )

        rc, _, stderr, report = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("## 7a. Per-Feature Metrics Rollup", report)
        # alpha has more wall_seconds (355.0) so it should appear before beta (20.0)
        self.assertIn("alpha-metrics", report)
        self.assertIn("beta-metrics", report)
        # alpha: codex_tokens = 1200, gemini_tokens = 950, ttl_crossings = 1, command_count = 2
        self.assertIn("| alpha-metrics | 355.0 | 1200 | 950 | 1 | 2 |", report)
        # beta: codex_tokens = 2300, gemini_tokens = 0, ttl_crossings = 0, command_count = 1
        self.assertIn("| beta-metrics | 20.0 | 2300 | 0 | 0 | 1 |", report)
        # Note paragraph must be present
        self.assertIn("Note on Claude token measurement", report)
        self.assertIn("ttl_crossings", report)
        self.assertIn("/cost", report)


if __name__ == "__main__":
    unittest.main()
