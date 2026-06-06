"""Slice 3 — implementation-rule WARN at deliver."""
import io
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


FACTORY_STATE = _load("factory_state")
FACTORY_DELIVER = _load("factory_deliver")


def _mock_run_factory(*, base_succeeds: bool = True, head_sha: str = "abcdef0", added_lines: int = 0):
    """Build a side-effect for subprocess.run that simulates git commands."""
    def side_effect(cmd, *args, **kwargs):
        result = MagicMock()
        result.stdout = ""
        result.stderr = ""
        result.returncode = 0
        if cmd[:2] == ["git", "merge-base"]:
            if base_succeeds:
                result.stdout = "basesha\n"
                result.returncode = 0
            else:
                result.returncode = 1
        elif cmd[:2] == ["git", "rev-parse"] and cmd[2:] == ["HEAD"]:
            result.stdout = head_sha + "\n"
        elif cmd[:2] == ["git", "diff"]:
            # numstat format: <added>\t<deleted>\t<file>
            if added_lines > 0:
                result.stdout = f"{added_lines}\t10\tsome_file.py\n"
            else:
                result.stdout = ""
        return result
    return side_effect


class CheckImplementationRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.tmp_root = Path(self.tmpdir)
        self.slug = "impl-rule-test"
        self._patch = patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", self.tmp_root)
        self._patch.start()
        FACTORY_STATE.workflow_dir(self.slug).mkdir(parents=True, exist_ok=True)
        FACTORY_STATE.atomic_json_write(
            FACTORY_STATE.factory_state_path(self.slug),
            FACTORY_STATE._default_workflow_state(),
        )

    def tearDown(self) -> None:
        self._patch.stop()

    def _set_codex_dispatches(self, dispatches: list[dict]) -> None:
        state = FACTORY_STATE.load_workflow_state(self.slug)
        state["codex_dispatches"] = dispatches
        FACTORY_STATE.atomic_json_write(FACTORY_STATE.factory_state_path(self.slug), state)

    def test_triggered_over_threshold_no_dispatches_no_override(self) -> None:
        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=_mock_run_factory(added_lines=250)):
            status, msg = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")
        self.assertIn("250", msg)
        self.assertIn("Codex dispatch", msg)

    def test_under_threshold_not_triggered(self) -> None:
        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=_mock_run_factory(added_lines=50)):
            status, msg = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "ok")
        self.assertEqual(msg, "")

    def test_threshold_boundary_200_triggers(self) -> None:
        self._set_codex_dispatches([])
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
            with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=200):
                status, msg = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")
        self.assertIn("200", msg)

    def test_threshold_boundary_199_returns_ok(self) -> None:
        self._set_codex_dispatches([])
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
            with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=199):
                status, msg = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "ok")
        self.assertEqual(msg, "")

    def test_added_code_lines_handles_subprocess_exceptions(self) -> None:
        exceptions = [
            subprocess.CalledProcessError(returncode=1, cmd=["git", "diff"]),
            subprocess.TimeoutExpired(cmd=["git", "diff"], timeout=60),
            FileNotFoundError(2, "No such file or directory", "git"),
            OSError(13, "Permission denied"),
        ]
        for exc in exceptions:
            with self.subTest(exception=type(exc).__name__):
                with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=exc):
                    self.assertIsNone(FACTORY_DELIVER._added_code_lines("any_sha"))

    def test_stale_codex_dispatch_does_not_suppress(self) -> None:
        self._set_codex_dispatches([
            {
                "head_sha": "deadbeef-not-an-ancestor",
                "ts": "20260101T000000_000000Z",
                "exit_code": 0,
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": 250,
            }
        ])
        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=_mock_run_factory(added_lines=300)):
            with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=False):
                status, note = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")
        self.assertIn("Codex dispatch", note)

    def test_fresh_dispatch_matching_lines_suppresses(self) -> None:
        self._set_codex_dispatches([
            {
                "exit_code": 0,
                "head_sha": "abc",
                "ts": "20260101T000000_000000Z",
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": 250,
            }
        ])
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
            with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=240):
                with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                    status, note = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "suppressed")
        self.assertIn("covered by codex dispatch 20260101T000000_000000Z", note)
        self.assertIn("(sha abc", note)
        self.assertIn("+250)", note)

    def test_non_ancestor_dispatch_does_not_suppress(self) -> None:
        self._set_codex_dispatches([
            {
                "exit_code": 0,
                "head_sha": "abc",
                "ts": "20260101T000000_000000Z",
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": 240,
            }
        ])
        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=_mock_run_factory(head_sha="abcdef0")):
            with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
                with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=240):
                    with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=False):
                        status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")

    def test_line_drift_above_boundary_does_not_suppress(self) -> None:
        self._set_codex_dispatches([
            {
                "exit_code": 0,
                "head_sha": "abc",
                "ts": "20260101T000000_000000Z",
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": 100,
            }
        ])
        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=_mock_run_factory(head_sha="abcdef0")):
            with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
                with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=200):
                    with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                        status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")

    def test_line_drift_at_boundary_suppresses(self) -> None:
        self._set_codex_dispatches([
            {
                "exit_code": 0,
                "head_sha": "abc",
                "ts": "20260101T000000_000000Z",
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": 200,
            }
        ])
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
            with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=250):
                with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                    status, note = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "suppressed")
        self.assertIn("covered by codex dispatch", note)

    def test_failed_dispatch_is_not_fresh(self) -> None:
        self._set_codex_dispatches([
            {
                "exit_code": 7,
                "head_sha": "abc",
                "ts": "20260101T000000_000000Z",
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": 250,
            }
        ])
        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=_mock_run_factory(head_sha="abcdef0")):
            with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
                with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=240):
                    with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                        status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")

    def test_null_lines_added_recomputes_and_suppresses(self) -> None:
        self._set_codex_dispatches([
            {
                "exit_code": 0,
                "head_sha": "abc",
                "ts": "20260101T000000_000000Z",
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": None,
            }
        ])
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
            with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=240):
                with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                    with patch.object(FACTORY_DELIVER, "_recompute_lines_for_dispatch", return_value=240) as recompute:
                        status, note = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "suppressed")
        self.assertIn("covered by codex dispatch", note)
        recompute.assert_called_once()

    def test_null_lines_added_recompute_failure_does_not_suppress(self) -> None:
        self._set_codex_dispatches([
            {
                "exit_code": 0,
                "head_sha": "abc",
                "ts": "20260101T000000_000000Z",
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": None,
            }
        ])
        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=_mock_run_factory(head_sha="abcdef0")):
            with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
                with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=240):
                    with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                        with patch.object(FACTORY_DELIVER, "_recompute_lines_for_dispatch", return_value=None):
                            status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")

    def test_branch_base_mismatch_recomputes_lines(self) -> None:
        dispatch = {
            "exit_code": 0,
            "head_sha": "abc",
            "ts": "20260101T000000_000000Z",
            "branch_base_sha": "old_base",
            "lines_added_at_dispatch_time": 200,
        }
        self._set_codex_dispatches([dispatch])
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="new_base"):
            with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=240):
                with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                    with patch.object(FACTORY_DELIVER, "_recompute_lines_for_dispatch", return_value=240) as recompute:
                        status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "suppressed")
        recompute.assert_called_once_with(dispatch, "new_base")

    def test_latest_fresh_dispatch_wins(self) -> None:
        self._set_codex_dispatches([
            {
                "exit_code": 0,
                "head_sha": "abc",
                "ts": "20260101T000000_000000Z",
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": 250,
            },
            {
                "exit_code": 0,
                "head_sha": "def",
                "ts": "20260102T000000_000000Z",
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": 250,
            },
        ])
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
            with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=240):
                with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                    status, note = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "suppressed")
        self.assertIn("20260102T000000_000000Z", note)

    def test_legacy_dispatch_missing_keys_does_not_raise(self) -> None:
        self._set_codex_dispatches([
            {"head_sha": "abc"},
        ])
        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=_mock_run_factory(head_sha="abcdef0")):
            with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
                with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=240):
                    status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")

    def test_below_threshold_with_stale_dispatch_returns_ok(self) -> None:
        self._set_codex_dispatches([
            {
                "exit_code": 0,
                "head_sha": "abc",
                "ts": "20260101T000000_000000Z",
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": 250,
            }
        ])
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
            with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=100):
                status, note = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "ok")
        self.assertEqual(note, "")

    def test_override_matching_head_returns_ok(self) -> None:
        state = FACTORY_STATE.load_workflow_state(self.slug)
        state["implementation_rule_override"] = {
            "head_sha": "abcdef0",
            "reason": "explained reason here",
            "at": 12345,
            "operator": "tester",
        }
        FACTORY_STATE.atomic_json_write(FACTORY_STATE.factory_state_path(self.slug), state)
        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=_mock_run_factory(added_lines=300, head_sha="abcdef0")):
            status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "ok")

    def test_override_stale_head_does_not_suppress(self) -> None:
        state = FACTORY_STATE.load_workflow_state(self.slug)
        state["implementation_rule_override"] = {
            "head_sha": "oldsha9",
            "reason": "stale override from earlier HEAD",
            "at": 12345,
            "operator": "tester",
        }
        FACTORY_STATE.atomic_json_write(FACTORY_STATE.factory_state_path(self.slug), state)
        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=_mock_run_factory(added_lines=300, head_sha="newsha1")):
            status, msg = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")
        self.assertIn("300", msg)

    def test_skip_when_branch_base_unresolved(self) -> None:
        stderr = io.StringIO()
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value=None), redirect_stderr(stderr):
            status, msg = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "skipped")
        self.assertTrue(
            msg.startswith(
                "implementation-rule check skipped — could not resolve branch base "
                "(origin/main, fork-point, main"
            )
        )
        self.assertEqual(
            msg,
            "implementation-rule check skipped — could not resolve branch base "
            "(origin/main, fork-point, main all failed)",
        )
        self.assertIn("could not resolve branch base (origin/main, fork-point, main all failed)", stderr.getvalue())

    def test_check_implementation_rule_handles_null_head_sha(self) -> None:
        self._set_codex_dispatches([
            {
                "head_sha": None,
                "exit_code": 0,
                "branch_base_sha": "abc",
                "lines_added_at_dispatch_time": 500,
                "ts": "20260101T000000_000000Z",
            }
        ])
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="abc"):
            with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=500):
                status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")

    def test_check_implementation_rule_handles_recompute_failure(self) -> None:
        self._set_codex_dispatches([
            {
                "head_sha": "abc",
                "exit_code": 0,
                "branch_base_sha": "basesha",
                "lines_added_at_dispatch_time": None,
                "ts": "20260101T000000_000000Z",
            }
        ])
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
            with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=500):
                with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                    with patch.object(FACTORY_DELIVER, "_recompute_lines_for_dispatch", return_value=None) as recompute:
                        status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")
        recompute.assert_called_once()

    def test_check_implementation_rule_handles_null_branch_base_sha(self) -> None:
        self._set_codex_dispatches([
            {
                "head_sha": "abc",
                "exit_code": 0,
                "branch_base_sha": None,
                "lines_added_at_dispatch_time": None,
                "ts": "20260101T000000_000000Z",
            }
        ])
        with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value="basesha"):
            with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=500):
                with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                    with patch.object(FACTORY_DELIVER, "_recompute_lines_for_dispatch", return_value=None) as recompute:
                        status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "triggered")
        recompute.assert_called_once()

    def test_check_implementation_rule_mixed_partial_records(self) -> None:
        valid_head = "abc1234"
        current_base = "basesha"
        with self.subTest("null head sha"):
            self._set_codex_dispatches([
                {
                    "head_sha": None,
                    "exit_code": 0,
                    "branch_base_sha": current_base,
                    "lines_added_at_dispatch_time": 500,
                    "ts": "20260101T000000_000000Z",
                }
            ])
            with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value=current_base):
                with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=500):
                    status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
            self.assertEqual(status, "triggered")

        with self.subTest("null lines recompute suppresses"):
            self._set_codex_dispatches([
                {
                    "head_sha": valid_head,
                    "exit_code": 0,
                    "branch_base_sha": current_base,
                    "lines_added_at_dispatch_time": None,
                    "ts": "20260101T000000_000000Z",
                }
            ])
            with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value=current_base):
                with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=500):
                    with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                        with patch.object(FACTORY_DELIVER, "_recompute_lines_for_dispatch", return_value=480):
                            status, note = FACTORY_DELIVER.check_implementation_rule(self.slug)
            self.assertEqual(status, "suppressed")
            self.assertIn("covered by codex dispatch", note)

        with self.subTest("null branch base recomputes current base"):
            dispatch = {
                "head_sha": valid_head,
                "exit_code": 0,
                "branch_base_sha": None,
                "lines_added_at_dispatch_time": 500,
                "ts": "20260101T000000_000000Z",
            }
            self._set_codex_dispatches([dispatch])
            with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value=current_base):
                with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=500):
                    with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                        with patch.object(FACTORY_DELIVER, "_recompute_lines_for_dispatch", return_value=500) as recompute:
                            status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
            self.assertEqual(status, "suppressed")
            recompute.assert_called_with(dispatch, current_base)

        with self.subTest("missing ts field"):
            self._set_codex_dispatches([
                {
                    "head_sha": valid_head,
                    "exit_code": 0,
                    "branch_base_sha": current_base,
                    "lines_added_at_dispatch_time": 500,
                }
            ])
            with patch.object(FACTORY_DELIVER, "_resolve_branch_base", return_value=current_base):
                with patch.object(FACTORY_DELIVER, "_added_code_lines", return_value=500):
                    with patch.object(FACTORY_DELIVER, "is_ancestor_of_head", return_value=True):
                        status, _ = FACTORY_DELIVER.check_implementation_rule(self.slug)
            self.assertEqual(status, "suppressed")

    def test_skip_when_git_diff_fails(self) -> None:
        def side_effect(cmd, *args, **kwargs):
            result = MagicMock()
            result.stdout = ""
            result.stderr = ""
            result.returncode = 0
            if cmd[:2] == ["git", "merge-base"]:
                result.stdout = "basesha\n"
            elif cmd[:2] == ["git", "diff"]:
                result.returncode = 1
            elif cmd[:2] == ["git", "rev-parse"] and cmd[2:] == ["HEAD"]:
                result.stdout = "abcdef0\n"
            return result

        stderr = io.StringIO()
        with redirect_stderr(stderr), patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=side_effect):
            status, msg = FACTORY_DELIVER.check_implementation_rule(self.slug)
        self.assertEqual(status, "skipped")
        self.assertEqual(msg, "implementation-rule check skipped: git diff failed.")
        self.assertIn("implementation-rule check skipped: git diff failed.", stderr.getvalue())

    def test_resolve_branch_base_returns_origin_main_sha(self) -> None:
        expected = "origin-main-sha"

        def side_effect(cmd, *args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = expected + "\n"
            result.stderr = ""
            return result

        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=side_effect):
            self.assertEqual(FACTORY_DELIVER._resolve_branch_base(), expected)

    def test_resolve_branch_base_falls_through_to_fork_point(self) -> None:
        fork_point_sha = "fork-point-sha"
        results = [
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=0, stdout=fork_point_sha + "\n", stderr=""),
        ]

        def side_effect(cmd, *args, **kwargs):
            return results.pop(0)

        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=side_effect):
            self.assertEqual(FACTORY_DELIVER._resolve_branch_base(), fork_point_sha)

    def test_resolve_branch_base_falls_through_to_local_main(self) -> None:
        local_main_sha = "local-main-sha"
        results = [
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=0, stdout=local_main_sha + "\n", stderr=""),
        ]

        def side_effect(cmd, *args, **kwargs):
            return results.pop(0)

        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=side_effect):
            self.assertEqual(FACTORY_DELIVER._resolve_branch_base(), local_main_sha)

    def test_resolve_branch_base_returns_none_when_all_candidates_fail(self) -> None:
        results = [
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=""),
        ]

        def side_effect(cmd, *args, **kwargs):
            return results.pop(0)

        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=side_effect):
            self.assertIsNone(FACTORY_DELIVER._resolve_branch_base())

    def test_resolve_branch_base_skips_file_not_found_and_keeps_trying(self) -> None:
        local_main_sha = "local-main-sha"
        results = [
            FileNotFoundError("git"),
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=0, stdout=local_main_sha + "\n", stderr=""),
        ]

        def side_effect(cmd, *args, **kwargs):
            item = results.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=side_effect):
            self.assertEqual(FACTORY_DELIVER._resolve_branch_base(), local_main_sha)

    def test_resolve_branch_base_skips_timeout_and_keeps_trying(self) -> None:
        local_main_sha = "local-main-sha"
        results = [
            subprocess.TimeoutExpired(cmd=["git", "merge-base", "origin/main", "HEAD"], timeout=60),
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=0, stdout=local_main_sha + "\n", stderr=""),
        ]

        def side_effect(cmd, *args, **kwargs):
            item = results.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=side_effect):
            self.assertEqual(FACTORY_DELIVER._resolve_branch_base(), local_main_sha)


class RecordOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.tmp_root = Path(self.tmpdir)
        self.slug = "override-test"
        self._patch = patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", self.tmp_root)
        self._patch.start()
        FACTORY_STATE.workflow_dir(self.slug).mkdir(parents=True, exist_ok=True)
        FACTORY_STATE.atomic_json_write(
            FACTORY_STATE.factory_state_path(self.slug),
            FACTORY_STATE._default_workflow_state(),
        )

    def tearDown(self) -> None:
        self._patch.stop()

    def test_record_writes_override_with_head_and_reason(self) -> None:
        with patch.object(FACTORY_DELIVER.subprocess, "run", side_effect=_mock_run_factory(head_sha="ddd")):
            FACTORY_DELIVER.record_implementation_rule_override(
                self.slug, "  Codex quota exhausted, see postmortem  "
            )
        state = FACTORY_STATE.load_workflow_state(self.slug)
        override = state["implementation_rule_override"]
        self.assertEqual(override["head_sha"], "ddd")
        self.assertEqual(override["reason"], "Codex quota exhausted, see postmortem")
        self.assertGreater(override["at"], 0)


if __name__ == "__main__":
    unittest.main()
