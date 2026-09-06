"""Fail-closed slicing gates: 0-marker hard fail + slice-completion verification.

Three real incidents motivate these gates:
- exp 10 (liars-dice): a marker-format mismatch meant 0 [CHECKPOINT] markers
  were detected; the runner only WARNED and built the whole feature as one
  giant slice, whose diff was too big to review (a circular test passed CI).
- user-roles: markers on bold "**Verify:**" lines → 0 detected → all 5 slices
  collapsed into one Codex dispatch.
- user-roles: Codex reported success but silently skipped 3 of 5 slices;
  nothing verified completion.

Covers:
- factory_stages.unsliced_tasks_error (the gate check + legacy fail-open)
- command_implement wiring (blocked / --allow-unsliced annotation / legacy)
- factory_cmd_implement._slice_completion_error (real throwaway git repo)
- factory_cmd_implement._parallel_completion_error (pure)
- factory_cmd_implement._coverage_report (pure)
- factory_parallel.slice_task_declared_files + fence-consistent slicing
- run_factory --allow-unsliced flag parsing

The scripts dir is put on sys.path by the package conftest.py.
"""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_stages as FS  # noqa: E402
import factory_parallel as FP  # noqa: E402
import factory_cmd_implement as IMPL  # noqa: E402
import run_factory as RUN_FACTORY  # noqa: E402

SLUG = "slicing-gate-test"

_UNSLICED_TASKS_MD = "# Tasks\n\n- [ ] T1 build everything\n- [ ] T2 test everything\n"
_SLICED_TASKS_MD = (
    "# Tasks\n\n"
    "- [ ] T1 build the model\n"
    "- end of slice 0 [CHECKPOINT]\n"
    "- [ ] T2 build the routes\n"
    "- end of slice 1 [CHECKPOINT]\n"
)


class _RunsRootCase(unittest.TestCase):
    """Base: explicit FACTORY_RUNS_ROOT isolation + state helpers."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._runs_root = Path(self._tmpdir.name) / "docs" / "workflow" / "feature-runs"
        runs_patch = patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", self._runs_root)
        runs_patch.start()
        self.addCleanup(runs_patch.stop)

    def _write_state(self, init_sha: str) -> None:
        wdir = FACTORY_STATE.workflow_dir(SLUG)
        wdir.mkdir(parents=True, exist_ok=True)
        state = FACTORY_STATE._default_workflow_state()
        state["schema_version"] = 2
        state[FACTORY_STATE.INIT_HEAD_SHA_KEY] = init_sha
        FACTORY_STATE.atomic_json_write(FACTORY_STATE.factory_state_path(SLUG), state)

    def _write_tasks(self, text: str) -> Path:
        wdir = FACTORY_STATE.workflow_dir(SLUG)
        wdir.mkdir(parents=True, exist_ok=True)
        tasks_path = wdir / "tasks.md"
        tasks_path.write_text(text, encoding="utf-8")
        return tasks_path

    def _read_state(self) -> dict:
        return json.loads(
            FACTORY_STATE.factory_state_path(SLUG).read_text(encoding="utf-8")
        )


class UnslicedTasksErrorTests(_RunsRootCase):
    """The gate check itself: when it fires and when it fails open."""

    def test_fires_on_meaningful_tasks_with_zero_markers(self) -> None:
        self._write_state(init_sha="abc123")
        tasks_path = self._write_tasks(_UNSLICED_TASKS_MD)
        msg = FS.unsliced_tasks_error(SLUG)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("zero [CHECKPOINT] markers", msg)
        self.assertIn(str(tasks_path), msg)
        self.assertIn("--allow-unsliced", msg)

    def test_error_lists_every_accepted_marker_form_with_example(self) -> None:
        self._write_state(init_sha="abc123")
        self._write_tasks(_UNSLICED_TASKS_MD)
        msg = FS.unsliced_tasks_error(SLUG)
        assert msg is not None
        for name, example in FS.CHECKPOINT_MARKER_EXAMPLES:
            self.assertIn(name, msg)
            self.assertIn(example, msg)

    def test_fails_open_for_legacy_run_without_init_sha(self) -> None:
        self._write_state(init_sha="")
        self._write_tasks(_UNSLICED_TASKS_MD)
        self.assertIsNone(FS.unsliced_tasks_error(SLUG))

    def test_silent_when_markers_present(self) -> None:
        self._write_state(init_sha="abc123")
        self._write_tasks(_SLICED_TASKS_MD)
        self.assertIsNone(FS.unsliced_tasks_error(SLUG))

    def test_silent_when_tasks_md_missing_or_stub(self) -> None:
        self._write_state(init_sha="abc123")
        self.assertIsNone(FS.unsliced_tasks_error(SLUG))  # missing
        self._write_tasks("# Tasks\n")
        self.assertIsNone(FS.unsliced_tasks_error(SLUG))  # heading-only stub

    def test_marker_only_inside_code_fence_still_fires(self) -> None:
        # A quoted example inside a fence must not satisfy the gate.
        self._write_state(init_sha="abc123")
        self._write_tasks(
            "# Tasks\n\n- [ ] T1 build\n```\n- fake boundary [CHECKPOINT]\n```\n"
        )
        msg = FS.unsliced_tasks_error(SLUG)
        self.assertIsNotNone(msg)

    def test_user_roles_bold_verify_markers_satisfy_the_gate(self) -> None:
        # Regression: the exact form that produced the incident now counts as
        # a marker, so the gate stays silent.
        self._write_state(init_sha="abc123")
        self._write_tasks(
            "# Tasks\n\n- [ ] T1 build roles\n**Verify:** roles enforced [CHECKPOINT]\n"
        )
        self.assertIsNone(FS.unsliced_tasks_error(SLUG))


def _noop_heartbeat() -> MagicMock:
    hb = MagicMock()
    hb.return_value.__enter__ = MagicMock(return_value=None)
    hb.return_value.__exit__ = MagicMock(return_value=False)
    return hb


class CommandImplementUnslicedGateTests(_RunsRootCase):
    """command_implement wiring: hard fail, --allow-unsliced, legacy fail-open."""

    def _invoke(self, argv: list[str]) -> tuple[int, str, str, MagicMock]:
        parser = RUN_FACTORY.build_parser()
        args = parser.parse_args(argv)
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with (
            patch.object(IMPL, "check_clean_tree", return_value=(True, "")),
            patch.object(IMPL, "prune_orphaned_worktrees", return_value=[]),
            patch.object(IMPL, "_run_serial", return_value=0) as mock_serial,
            patch.object(IMPL, "_git_head_sha", return_value="base-sha"),
            patch.object(IMPL, "_print_slice_coverage"),
            patch.object(IMPL, "HeartbeatEmitter", _noop_heartbeat()),
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            rc = args.func(args)
        return rc, stdout_buf.getvalue(), stderr_buf.getvalue(), mock_serial

    def test_zero_markers_hard_fails_before_any_dispatch(self) -> None:
        self._write_state(init_sha="abc123")
        self._write_tasks(_UNSLICED_TASKS_MD)
        rc, _out, err, mock_serial = self._invoke(["implement", "--slug", SLUG])
        self.assertEqual(rc, 1)
        self.assertIn("zero [CHECKPOINT] markers", err)
        self.assertIn("### [CHECKPOINT] Slice 1", err)
        mock_serial.assert_not_called()

    def test_zero_markers_writes_no_annotation_when_blocked(self) -> None:
        self._write_state(init_sha="abc123")
        self._write_tasks(_UNSLICED_TASKS_MD)
        self._invoke(["implement", "--slug", SLUG])
        self.assertEqual(self._read_state().get("annotations", []), [])

    def test_allow_unsliced_dispatches_and_records_annotation(self) -> None:
        self._write_state(init_sha="abc123")
        self._write_tasks(_UNSLICED_TASKS_MD)
        rc, _out, err, mock_serial = self._invoke(
            ["implement", "--slug", SLUG, "--allow-unsliced"]
        )
        self.assertEqual(rc, 0, f"stderr={err!r}")
        mock_serial.assert_called_once()
        annotations = self._read_state().get("annotations", [])
        self.assertEqual(len(annotations), 1)
        self.assertEqual(annotations[0]["type"], "unsliced_accepted")
        self.assertEqual(annotations[0]["stage"], "implement")
        self.assertTrue(annotations[0]["reason"])

    def test_legacy_run_without_init_sha_is_not_gated(self) -> None:
        self._write_state(init_sha="")
        self._write_tasks(_UNSLICED_TASKS_MD)
        rc, _out, err, mock_serial = self._invoke(["implement", "--slug", SLUG])
        self.assertEqual(rc, 0, f"stderr={err!r}")
        mock_serial.assert_called_once()
        self.assertEqual(self._read_state().get("annotations", []), [])

    def test_sliced_tasks_pass_the_gate(self) -> None:
        self._write_state(init_sha="abc123")
        self._write_tasks(_SLICED_TASKS_MD)
        rc, _out, err, mock_serial = self._invoke(["implement", "--slug", SLUG])
        self.assertEqual(rc, 0, f"stderr={err!r}")
        mock_serial.assert_called_once()


class AllowUnslicedFlagParsingTests(unittest.TestCase):
    def test_default_is_false_and_flag_parses(self) -> None:
        parser = RUN_FACTORY.build_parser()
        args = parser.parse_args(["implement", "--slug", "s"])
        self.assertFalse(args.allow_unsliced)
        args = parser.parse_args(["implement", "--slug", "s", "--allow-unsliced"])
        self.assertTrue(args.allow_unsliced)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


class SliceCompletionErrorTests(_RunsRootCase):
    """The serial-dispatch completion gate against a real throwaway git repo."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = Path(self._tmpdir.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "test@test.invalid")
        _git(self.repo, "config", "user.name", "test")
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "seed")
        self.base_sha = _git(self.repo, "rev-parse", "HEAD")
        repo_patch = patch.object(IMPL, "REPO_ROOT", self.repo)
        repo_patch.start()
        self.addCleanup(repo_patch.stop)

    def test_no_commit_and_clean_tree_fails(self) -> None:
        msg = IMPL._slice_completion_error(SLUG, 0, self.base_sha)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("NO new commit", msg)
        self.assertIn("slice-0.codex.log", msg)

    def test_uncommitted_changes_pass(self) -> None:
        (self.repo / "new_file.py").write_text("x = 1\n", encoding="utf-8")
        self.assertIsNone(IMPL._slice_completion_error(SLUG, 0, self.base_sha))

    def test_real_commit_passes(self) -> None:
        (self.repo / "feature.py").write_text("y = 2\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "feature")
        self.assertIsNone(IMPL._slice_completion_error(SLUG, 0, self.base_sha))

    def test_empty_commit_fails(self) -> None:
        _git(self.repo, "commit", "-q", "--allow-empty", "-m", "empty")
        msg = IMPL._slice_completion_error(SLUG, 0, self.base_sha)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("EMPTY", msg)

    def test_unverifiable_git_state_fails_closed(self) -> None:
        msg = IMPL._slice_completion_error(SLUG, 0, "0" * 40)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("could not verify", msg)

    def test_run_bookkeeping_dirt_is_not_completion_evidence(self) -> None:
        # Every dispatch leaves the prompt/transcript under feature-runs/ and a
        # heartbeat-touched state.json — without filtering, the "no changes"
        # branch could never fire in production.
        bookkeeping = self.repo / "docs/workflow/feature-runs" / SLUG / "codex-specs"
        bookkeeping.mkdir(parents=True)
        (bookkeeping / "slice-0.md").write_text("prompt\n", encoding="utf-8")
        (bookkeeping / "slice-0.codex.log").write_text("log\n", encoding="utf-8")
        msg = IMPL._slice_completion_error(SLUG, 0, self.base_sha)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("NO new commit", msg)

    def test_protected_file_dirt_is_not_completion_evidence(self) -> None:
        # Protected files are reverted after every dispatch — an edit there is
        # not slice work.
        (self.repo / IMPL.PROTECTED_FILES[0]).write_text("drive-by\n", encoding="utf-8")
        msg = IMPL._slice_completion_error(SLUG, 0, self.base_sha)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("NO new commit", msg)

    def test_bookkeeping_only_commit_is_an_empty_slice_diff(self) -> None:
        bookkeeping = self.repo / "docs/workflow/feature-runs" / SLUG
        bookkeeping.mkdir(parents=True)
        (bookkeeping / "state.json").write_text("{}\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "bookkeeping only")
        msg = IMPL._slice_completion_error(SLUG, 0, self.base_sha)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("EMPTY", msg)


class ParallelCompletionErrorTests(unittest.TestCase):
    """The per-worker completion gate for parallel dispatches (pure)."""

    def test_all_workers_with_commits_and_files_pass(self) -> None:
        self.assertIsNone(
            IMPL._parallel_completion_error(
                {0: ["sha0"], 1: ["sha1"]},
                {0: {"app/a.py"}, 1: {"app/b.py"}},
                ["task a", "task b"],
            )
        )

    def test_worker_with_no_commit_fails_and_names_the_task(self) -> None:
        msg = IMPL._parallel_completion_error(
            {0: ["sha0"], 1: []},
            {0: {"app/a.py"}, 1: set()},
            ["task a", "- [ ] T2 build app/b.py"],
        )
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("worker 1", msg)
        self.assertIn("T2 build app/b.py", msg)
        self.assertIn("silently skipped", msg)

    def test_worker_with_empty_diff_fails(self) -> None:
        msg = IMPL._parallel_completion_error(
            {0: ["sha0"]}, {0: set()}, ["task a"]
        )
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("EMPTY diff", msg)

    def test_worker_touching_only_bookkeeping_or_protected_fails(self) -> None:
        msg = IMPL._parallel_completion_error(
            {0: ["sha0"]},
            {0: {IMPL.PROTECTED_FILES[0], "docs/workflow/feature-runs/x/state.json"}},
            ["task a"],
        )
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("EMPTY diff", msg)


class CoverageReportTests(unittest.TestCase):
    """Per-task coverage checklist (pure): warn on gaps, never gate."""

    def test_touched_files_are_checked_off(self) -> None:
        lines, gaps = IMPL._coverage_report(
            [("- [ ] T1 edit app/web/routes.py", ["app/web/routes.py"])],
            {"app/web/routes.py"},
        )
        self.assertEqual(gaps, 0)
        self.assertEqual(len(lines), 1)
        self.assertIn("✓", lines[0])

    def test_directory_declaration_covers_children(self) -> None:
        lines, gaps = IMPL._coverage_report(
            [("- [ ] T1 rework app/web", ["app/web"])],
            {"app/web/routes.py"},
        )
        self.assertEqual(gaps, 0)

    def test_untouched_file_is_a_gap_naming_the_path(self) -> None:
        lines, gaps = IMPL._coverage_report(
            [
                ("- [ ] T1 edit app/a.py", ["app/a.py"]),
                ("- [ ] T2 edit app/b.py", ["app/b.py"]),
            ],
            {"app/a.py"},
        )
        self.assertEqual(gaps, 1)
        self.assertTrue(any("⚠" in line and "app/b.py" in line for line in lines))

    def test_tasks_without_paths_are_skipped(self) -> None:
        lines, gaps = IMPL._coverage_report([("- [ ] T1 think hard", [])], set())
        self.assertEqual((lines, gaps), ([], 0))


_PER_TASK_TASKS_MD = """# Tasks

- [ ] T1 implement app/games/foo/engine.py [P: app/games/foo/engine.py]
- [ ] T2 add tests/test_engine.py
- end of slice 0 [CHECKPOINT]
- [x] T3 wire app/web/routes.py
- end of slice 1 [CHECKPOINT]
"""

_FENCED_TASKS_MD = """# Tasks

- [ ] T1 real task app/a.py
```markdown
- [ ] fake task inside fence app/fake.py
- fake boundary [CHECKPOINT]
```
- [ ] T2 also real app/b.py
- end of slice 0 [CHECKPOINT]
- [ ] T3 slice one task app/c.py
"""


class SliceTaskDeclaredFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.run_dir = self.root / "run"
        self.repo = self.root / "repo"
        self.run_dir.mkdir()
        self.repo.mkdir()

    def _patches(self) -> tuple:
        return (
            patch.object(FP._stages, "workflow_dir", return_value=self.run_dir),
            patch.object(FP._stages, "REPO_ROOT", self.repo),
        )

    def test_per_task_paths_for_each_slice(self) -> None:
        (self.run_dir / "tasks.md").write_text(_PER_TASK_TASKS_MD, encoding="utf-8")
        p1, p2 = self._patches()
        with p1, p2:
            slice0 = FP.slice_task_declared_files("slug", 0)
            slice1 = FP.slice_task_declared_files("slug", 1)
        self.assertEqual(len(slice0), 2)
        self.assertIn("T1", slice0[0][0])
        self.assertEqual(slice0[0][1], ["app/games/foo/engine.py"])
        self.assertEqual(slice0[1][1], ["tests/test_engine.py"])
        # Checked-off tasks are still read back (completed slices).
        self.assertEqual(slice1, [("- [x] T3 wire app/web/routes.py", ["app/web/routes.py"])])

    def test_aggregate_slice_declared_files_unchanged(self) -> None:
        (self.run_dir / "tasks.md").write_text(_PER_TASK_TASKS_MD, encoding="utf-8")
        p1, p2 = self._patches()
        with p1, p2:
            self.assertEqual(
                FP.slice_declared_files("slug", 0),
                ["app/games/foo/engine.py", "tests/test_engine.py"],
            )

    def test_fenced_markers_and_tasks_are_ignored_consistently(self) -> None:
        # The fenced fake marker must not shift slice boundaries and the fenced
        # fake task must not be collected — slicing stays consistent with
        # parse_checkpoint_markers (which counts 1 marker here).
        (self.run_dir / "tasks.md").write_text(_FENCED_TASKS_MD, encoding="utf-8")
        p1, p2 = self._patches()
        with p1, p2:
            slice0 = FP.slice_task_declared_files("slug", 0)
            slice1 = FP.slice_task_declared_files("slug", 1)
        self.assertEqual([paths for _t, paths in slice0], [["app/a.py"], ["app/b.py"]])
        self.assertEqual([paths for _t, paths in slice1], [["app/c.py"]])


class ParseParallelTaskGroupsFenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = Path(self._tmp.name) / "run"
        self.run_dir.mkdir()
        (self.run_dir / "tasks.md").write_text(_FENCED_TASKS_MD, encoding="utf-8")

    def _groups(self, index: int) -> list[dict]:
        with (
            patch.object(FP._stages, "workflow_dir", return_value=self.run_dir),
            patch.object(
                FP._stages,
                "checkpoint_progress_state",
                return_value={"index": index, "markers_sha": "", "last_diff_head_sha": ""},
            ),
        ):
            return FP.parse_parallel_task_groups("slug")

    def test_slice_zero_excludes_fenced_task_and_stops_at_real_marker(self) -> None:
        groups = self._groups(0)
        self.assertEqual(len(groups), 1)
        tasks = groups[0]["tasks"]
        self.assertEqual(len(tasks), 2)
        self.assertIn("T1 real task", tasks[0])
        self.assertIn("T2 also real", tasks[1])

    def test_slice_one_starts_after_the_real_marker_only(self) -> None:
        groups = self._groups(1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["tasks"]), 1)
        self.assertIn("T3 slice one task", groups[0]["tasks"][0])


if __name__ == "__main__":
    unittest.main()
