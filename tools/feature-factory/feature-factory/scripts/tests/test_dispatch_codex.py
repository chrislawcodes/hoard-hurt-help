import argparse
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state  # noqa: E402
import factory_deliver  # noqa: E402
import factory_cmd_dispatch  # noqa: E402
import run_factory  # noqa: E402


SLUG = "dispatch-test"
FIXED_NOW = datetime(2026, 4, 24, 12, 34, 56, 789012)
FIXED_TS = FIXED_NOW.strftime("%Y%m%dT%H%M%S_%fZ")
FIXED_HEAD_SHA = "deadbeefcafebabe"
DEFAULT_BRANCH_BASE = "branch-base-sha"
DEFAULT_LINES = 321
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_PROMPT = "Dispatch Codex on this prompt."


class DispatchCodexTests(unittest.TestCase):
    def _load_modules(self) -> None:
        self.factory_state = factory_state
        self.factory_deliver = factory_deliver
        self.dispatch = factory_cmd_dispatch
        self.run_factory = run_factory

    def setUp(self) -> None:
        self._load_modules()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._repo_root = Path(self._tmpdir.name)
        self._runs_root = self._repo_root / "docs" / "workflow" / "feature-runs"
        self._repo_patch = patch.object(self.factory_state, "REPO_ROOT", self._repo_root)
        self._runs_patch = patch.object(self.factory_state, "FACTORY_RUNS_ROOT", self._runs_root)
        self._repo_patch.start()
        self._runs_patch.start()
        self.addCleanup(self._repo_patch.stop)
        self.addCleanup(self._runs_patch.stop)
        self._write_state()

    def _write_state(self, state: dict | None = None) -> None:
        workflow_dir = self.factory_state.workflow_dir(SLUG)
        workflow_dir.mkdir(parents=True, exist_ok=True)
        payload = self.factory_state._default_workflow_state() if state is None else state
        payload["schema_version"] = 2
        payload.setdefault("codex_dispatches", [])
        self.factory_state.atomic_json_write(self.factory_state.factory_state_path(SLUG), payload)

    def _read_state(self) -> dict:
        return json.loads(self.factory_state.factory_state_path(SLUG).read_text(encoding="utf-8"))

    def _prompt_path(self, text: str, filename: str = "prompt.txt") -> Path:
        path = self.factory_state.workflow_dir(SLUG) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _dispatch_root(self) -> Path:
        return self.factory_state.REPO_ROOT / "docs" / "workflow" / "feature-runs" / SLUG / "codex-dispatches"

    def _update_state_side_effect(self, slug: str, mutator) -> dict:
        path = self.factory_state.factory_state_path(slug)
        state = json.loads(path.read_text(encoding="utf-8"))
        mutator(state)
        self.factory_state.atomic_json_write(path, state)
        return state

    def _parser(self) -> argparse.ArgumentParser:
        return self.run_factory.build_parser()

    def _make_popen(self, *, returncode: int = 0, stdout: str = "codex stdout",
                    stderr: str = "codex stderr") -> Mock:
        # The shared runner streams stdout/stderr line-by-line and polls wait(),
        # so the fake proc exposes real readable streams and an immediate wait().
        proc = Mock()
        proc.pid = 4242
        proc.returncode = returncode
        proc.wait.return_value = None
        proc.stdout = io.StringIO(stdout)
        proc.stderr = io.StringIO(stderr)
        return proc

    def _invoke(
        self,
        *,
        prompt_text: str = DEFAULT_PROMPT,
        prompt_filename: str = "prompt.txt",
        codex_path: str | None = "/usr/bin/codex",
        branch_base: str | None = DEFAULT_BRANCH_BASE,
        lines_added: int | None = DEFAULT_LINES,
        head_sha: str = FIXED_HEAD_SHA,
        now: datetime = FIXED_NOW,
        model: str = DEFAULT_MODEL,
        proc_returncode: int = 0,
        proc_stdout: str = "codex stdout",
        proc_stderr: str = "codex stderr",
        run_codex_result: subprocess.CompletedProcess | None = None,
        precreate_dispatch_ids: list[str] | None = None,
    ) -> tuple[int, str, str, Mock, Mock, Mock, Mock, Mock, Mock, Path]:
        prompt_path = self._prompt_path(prompt_text, filename=prompt_filename)
        if precreate_dispatch_ids:
            dispatch_root = self._dispatch_root()
            for dispatch_id in precreate_dispatch_ids:
                (dispatch_root / dispatch_id).mkdir(parents=True, exist_ok=True)

        run_mock = Mock(
            return_value=subprocess.CompletedProcess(
                args=["git", "rev-parse", "HEAD"],
                returncode=0,
                stdout=f"{head_sha}\n",
                stderr="",
            )
        )
        popen_proc = self._make_popen(
            returncode=proc_returncode,
            stdout=proc_stdout,
            stderr=proc_stderr,
        )
        popen_mock = Mock(return_value=popen_proc)
        update_state_mock = Mock(side_effect=self._update_state_side_effect)
        getpgid_mock = Mock(return_value=popen_proc.pid)
        killpg_mock = Mock()

        # The shared runner is exercised for real (via the mocked Popen above)
        # unless a test injects a finished result to simulate a watchdog kill.
        real_run_codex = self.dispatch.run_codex

        def _run_codex_passthrough(*call_args, **call_kwargs):
            if run_codex_result is not None:
                return run_codex_result
            return real_run_codex(*call_args, **call_kwargs)

        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        with (
            patch.object(self.dispatch.shutil, "which", return_value=codex_path),
            patch.object(self.dispatch.subprocess, "run", run_mock),
            patch.object(self.dispatch.subprocess, "Popen", popen_mock),
            patch.object(self.dispatch, "run_codex", side_effect=_run_codex_passthrough),
            patch.object(self.factory_deliver, "_resolve_branch_base", return_value=branch_base),
            patch.object(self.factory_deliver, "_added_code_lines", return_value=lines_added),
            patch.object(self.factory_state, "update_state", update_state_mock),
            patch.object(self.dispatch.os, "getpgid", getpgid_mock),
            patch.object(self.dispatch.os, "killpg", killpg_mock),
            patch.object(self.dispatch, "datetime") as datetime_mock,
            contextlib.redirect_stdout(stdout_buffer),
            contextlib.redirect_stderr(stderr_buffer),
        ):
            datetime_mock.utcnow.return_value = now
            args = self._parser().parse_args([
                "dispatch-codex",
                "--slug",
                SLUG,
                "--prompt-path",
                str(prompt_path),
                "--model",
                model,
            ])
            try:
                rc = args.func(args)
            except SystemExit as exc:
                rc = exc.code

        return (
            rc,
            stdout_buffer.getvalue(),
            stderr_buffer.getvalue(),
            run_mock,
            popen_mock,
            update_state_mock,
            popen_proc,
            getpgid_mock,
            killpg_mock,
            prompt_path,
        )

    def test_happy_path_records_all_fields_and_returns_zero(self) -> None:
        rc, stdout, stderr, run_mock, popen_mock, update_state_mock, _, _, _, prompt_path = self._invoke()

        self.assertEqual(rc, 0)
        self.assertIn("[workflow] ✓ dispatch-codex", stdout)
        # Auto-commit warnings fire when git is unavailable in the unit-test
        # env; this is documented behavior (FR-007 round-1 Gemini LOW). Allow
        # auto-commit-warning lines but no other stderr noise.
        for line in stderr.splitlines():
            self.assertTrue(
                line.startswith("[auto-commit-warning]"),
                f"unexpected stderr line: {line}",
            )
        run_mock.assert_called_once()
        popen_mock.assert_called_once()
        update_state_mock.assert_called_once()
        state = self._read_state()
        record = state["codex_dispatches"][-1]
        expected_prompt_sha = hashlib.sha256(DEFAULT_PROMPT.encode("utf-8")).hexdigest()
        expected_rel = f"docs/workflow/feature-runs/{SLUG}/codex-dispatches/{FIXED_TS}"
        # auto_commit is ALWAYS present (T08 contract); in unit-test env without
        # git, the auto-commit path skips with a documented reason.
        record_without_auto_commit = {k: v for k, v in record.items() if k != "auto_commit"}
        self.assertEqual(record_without_auto_commit, {
            "head_sha": FIXED_HEAD_SHA,
            "ts": FIXED_TS,
            "prompt_path": str(prompt_path),
            "prompt_sha256": expected_prompt_sha,
            "model": DEFAULT_MODEL,
            "exit_code": 0,
            "stdout_path": f"{expected_rel}/stdout.txt",
            "stderr_path": f"{expected_rel}/stderr.txt",
            "branch_base_sha": DEFAULT_BRANCH_BASE,
            "lines_added_at_dispatch_time": DEFAULT_LINES,
        })
        self.assertIn("auto_commit", record)
        self.assertTrue(record["auto_commit"].get("skipped"))

    def test_non_zero_codex_exit_collapses_to_one(self) -> None:
        rc, stdout, stderr, _, _, update_state_mock, _, _, _, _ = self._invoke(proc_returncode=7)

        self.assertEqual(rc, 1)
        self.assertIn("exit 7", stdout)
        # auto-commit warnings allowed (no git in unit-test env).
        for line in stderr.splitlines():
            self.assertTrue(
                line.startswith("[auto-commit-warning]"),
                f"unexpected stderr line: {line}",
            )
        update_state_mock.assert_called_once()
        self.assertEqual(self._read_state()["codex_dispatches"][-1]["exit_code"], 7)

    def test_quota_exhaustion_writes_artifacts_and_skips_state(self) -> None:
        rc, stdout, stderr, run_mock, popen_mock, update_state_mock, _, _, _, _ = self._invoke(
            proc_stdout="partial stdout",
            proc_stderr="you've hit your usage limit",
        )

        self.assertEqual(rc, 4)
        self.assertEqual(stdout, "")
        self.assertIn("Codex quota exhausted", stderr)
        run_mock.assert_not_called()
        popen_mock.assert_called_once()
        update_state_mock.assert_not_called()
        dispatch_root = self._dispatch_root()
        self.assertTrue(dispatch_root.exists())
        artifact_dirs = list(dispatch_root.iterdir())
        self.assertEqual(len(artifact_dirs), 1)
        self.assertEqual((artifact_dirs[0] / "stdout.txt").read_text(encoding="utf-8"), "partial stdout")
        self.assertIn("usage limit", (artifact_dirs[0] / "stderr.txt").read_text(encoding="utf-8"))

    def test_prompt_over_100000_bytes_rejects_before_codex(self) -> None:
        before = self._read_state()
        rc, _, stderr, run_mock, popen_mock, update_state_mock, _, _, _, _ = self._invoke(
            prompt_text="a" * 100_001,
        )

        self.assertEqual(rc, 2)
        self.assertIn("exceeds 100000-byte hard limit", stderr)
        run_mock.assert_not_called()
        popen_mock.assert_not_called()
        update_state_mock.assert_not_called()
        self.assertEqual(before, self._read_state())
        self.assertFalse(self._dispatch_root().exists())

    def test_prompt_exactly_100000_bytes_runs(self) -> None:
        rc, _, _, _, _, update_state_mock, _, _, _, _ = self._invoke(
            prompt_text="a" * 100_000,
        )

        self.assertEqual(rc, 0)
        update_state_mock.assert_called_once()
        self.assertEqual(self._read_state()["codex_dispatches"][-1]["exit_code"], 0)

    def test_codex_missing_on_path_rejects_before_prompt_dispatch(self) -> None:
        before = self._read_state()
        rc, _, stderr, run_mock, popen_mock, update_state_mock, _, _, _, _ = self._invoke(
            codex_path=None,
        )

        self.assertEqual(rc, 2)
        self.assertIn("codex CLI not found on PATH", stderr)
        run_mock.assert_not_called()
        popen_mock.assert_not_called()
        update_state_mock.assert_not_called()
        self.assertEqual(before, self._read_state())
        self.assertFalse(self._dispatch_root().exists())

    def test_collision_suffixes_are_allocated(self) -> None:
        rc, _, _, _, _, update_state_mock, _, _, _, _ = self._invoke(precreate_dispatch_ids=[FIXED_TS])

        self.assertEqual(rc, 0)
        update_state_mock.assert_called_once()
        state = self._read_state()
        self.assertEqual(state["codex_dispatches"][-1]["ts"], f"{FIXED_TS}_000")
        self.assertTrue((self._dispatch_root() / f"{FIXED_TS}_000").exists())

    def test_second_collision_uses_next_suffix(self) -> None:
        rc, _, _, _, _, update_state_mock, _, _, _, _ = self._invoke(
            precreate_dispatch_ids=[FIXED_TS, f"{FIXED_TS}_000"],
        )

        self.assertEqual(rc, 0)
        update_state_mock.assert_called_once()
        state = self._read_state()
        self.assertEqual(state["codex_dispatches"][-1]["ts"], f"{FIXED_TS}_001")
        self.assertTrue((self._dispatch_root() / f"{FIXED_TS}_001").exists())

    def test_null_branch_base_records_none_fields(self) -> None:
        rc, _, _, _, _, update_state_mock, _, _, _, _ = self._invoke(branch_base=None, lines_added=None)

        self.assertEqual(rc, 0)
        update_state_mock.assert_called_once()
        record = self._read_state()["codex_dispatches"][-1]
        self.assertIsNone(record["branch_base_sha"])
        self.assertIsNone(record["lines_added_at_dispatch_time"])

    def test_timeout_writes_partial_artifacts_and_exits_five(self) -> None:
        # The runner kills the process group itself and returns a timeout
        # sentinel; dispatch writes the partial transcript and exits 5.
        timed_out = subprocess.CompletedProcess(
            args=["codex"],
            returncode=self.dispatch.RC_HARD_TIMEOUT,
            stdout="partial out",
            stderr="partial err",
        )
        rc, _, stderr, _, _, update_state_mock, _, _, _, _ = self._invoke(
            run_codex_result=timed_out,
        )

        self.assertEqual(rc, 5)
        self.assertIn("process group killed", stderr)
        update_state_mock.assert_not_called()
        dispatch_root = self._dispatch_root()
        artifact_dirs = list(dispatch_root.iterdir())
        self.assertEqual(len(artifact_dirs), 1)
        self.assertEqual((artifact_dirs[0] / "stdout.txt").read_text(encoding="utf-8"), "partial out")
        self.assertEqual((artifact_dirs[0] / "stderr.txt").read_text(encoding="utf-8"), "partial err")

    def test_prompt_sha256_is_stable_for_same_content(self) -> None:
        prompt_text = "same content every time"
        rc1, _, _, _, _, _, _, _, _, _ = self._invoke(prompt_text=prompt_text, now=FIXED_NOW)
        rc2, _, _, _, _, _, _, _, _, _ = self._invoke(
            prompt_text=prompt_text,
            now=datetime(2026, 4, 24, 12, 34, 57, 789012),
            prompt_filename="prompt-2.txt",
        )

        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        state = self._read_state()
        self.assertEqual(len(state["codex_dispatches"]), 2)
        expected = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        self.assertEqual(state["codex_dispatches"][0]["prompt_sha256"], expected)
        self.assertEqual(state["codex_dispatches"][1]["prompt_sha256"], expected)


if __name__ == "__main__":
    unittest.main()
