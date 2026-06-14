import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402

SCRIPT_PATH = SCRIPT_DIR / "factory_state.py"


class FactoryStateTests(unittest.TestCase):
    def test_repo_root_uses_git_toplevel_inside_worktree(self) -> None:
        """A nested worktree should resolve REPO_ROOT from `git rev-parse`."""
        with patch.dict(FACTORY_STATE.os.environ, {"FF_REPO_ROOT": ""}, clear=False), patch.object(
            FACTORY_STATE.subprocess,
            "run",
            return_value=FACTORY_STATE.subprocess.CompletedProcess(
                args=["git", "rev-parse", "--show-toplevel"],
                returncode=0,
                stdout="/tmp/active-worktree\n",
                stderr="",
            ),
        ):
            spec = importlib.util.spec_from_file_location(
                "factory_state_repo_root_test",
                SCRIPT_PATH,
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

        self.assertEqual(module.REPO_ROOT, Path("/tmp/active-worktree").resolve())

    def test_with_locked_state_single_writer_acquires_and_releases(self) -> None:
        """A single writer can enter, mutate, exit, and reopen the same state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(tmpdir)):
                slug = "single-writer"
                state_path = FACTORY_STATE.factory_state_path(slug)
                FACTORY_STATE.atomic_json_write(state_path, FACTORY_STATE._default_workflow_state())

                with FACTORY_STATE.with_locked_state(slug) as state:
                    state["marker"] = "written"

                self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["marker"], "written")

                with FACTORY_STATE.with_locked_state(slug) as state:
                    self.assertEqual(state["marker"], "written")

    def test_with_locked_state_contended_writers_block_and_preserve_data(self) -> None:
        """A second writer waits for the first, then sees the first writer's changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(tmpdir)):
                slug = "contended-writers"
                state_path = FACTORY_STATE.factory_state_path(slug)
                FACTORY_STATE.atomic_json_write(state_path, FACTORY_STATE._default_workflow_state())

                first_entered = threading.Event()
                release_first = threading.Event()
                second_attempted = threading.Event()
                second_acquired = threading.Event()
                seen: dict[str, object] = {}
                errors: list[BaseException] = []

                def first_writer() -> None:
                    try:
                        with FACTORY_STATE.with_locked_state(slug) as state:
                            stage_state = state.setdefault("stages", {}).setdefault(
                                "spec", FACTORY_STATE._default_stage_state()
                            )
                            stage_state["adversarial_rounds"] = 1
                            first_entered.set()
                            if not release_first.wait(5):
                                raise TimeoutError("first writer was never released")
                    except BaseException as exc:  # pragma: no cover - surfaced by assertions below
                        errors.append(exc)

                def second_writer() -> None:
                    try:
                        second_attempted.set()
                        with FACTORY_STATE.with_locked_state(slug) as state:
                            seen["adversarial_rounds"] = state["stages"]["spec"]["adversarial_rounds"]
                            state["stages"]["spec"]["judge_rounds"] = 2
                            second_acquired.set()
                    except BaseException as exc:  # pragma: no cover - surfaced by assertions below
                        errors.append(exc)

                first_thread = threading.Thread(target=first_writer, daemon=True)
                second_thread = threading.Thread(target=second_writer, daemon=True)

                first_thread.start()
                self.assertTrue(first_entered.wait(5), "first writer never acquired the lock")
                second_thread.start()
                self.assertTrue(second_attempted.wait(5), "second writer never attempted the lock")
                self.assertFalse(second_acquired.wait(0.2), "second writer should have been blocked")

                release_first.set()
                first_thread.join(5)
                second_thread.join(5)

                self.assertFalse(first_thread.is_alive(), "first writer deadlocked")
                self.assertFalse(second_thread.is_alive(), "second writer deadlocked")
                self.assertEqual(errors, [])
                self.assertTrue(second_acquired.is_set())
                self.assertEqual(seen["adversarial_rounds"], 1)

                persisted = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["stages"]["spec"]["adversarial_rounds"], 1)
                self.assertEqual(persisted["stages"]["spec"]["judge_rounds"], 2)

    def test_with_locked_state_times_out_after_ten_retries(self) -> None:
        """Lock acquisition fails cleanly after the retry budget is exhausted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(tmpdir)):
                slug = "lock-timeout"
                state_path = FACTORY_STATE.factory_state_path(slug)
                FACTORY_STATE.atomic_json_write(state_path, FACTORY_STATE._default_workflow_state())

                with patch.object(
                    FACTORY_STATE.fcntl,
                    "flock",
                    side_effect=BlockingIOError(11, "Resource temporarily unavailable"),
                ) as mock_flock, patch.object(FACTORY_STATE.time, "sleep", return_value=None) as mock_sleep:
                    with self.assertRaises(TimeoutError) as ctx:
                        with FACTORY_STATE.with_locked_state(slug):
                            pass

                self.assertIn("10 retries", str(ctx.exception))
                self.assertEqual(mock_flock.call_count, 11)
                self.assertEqual(mock_sleep.call_count, 10)

    def test_update_stage_state_writes_nested_and_top_level_keys(self) -> None:
        """Stage writes should land in the new nested shape and mirror adversarial_rounds at the top level."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(tmpdir)):
                slug = "dual-write"
                state_path = FACTORY_STATE.factory_state_path(slug)
                FACTORY_STATE.atomic_json_write(
                    state_path,
                    {
                        "review_policy": {"sensitive": True},
                        "custom_key": "keep",
                        "plan_adversarial_rounds": 7,
                    },
                )

                updated = FACTORY_STATE.update_stage_state(
                    slug,
                    "plan",
                    {
                        "adversarial_rounds": 6,
                        "annotations": [{"stage": "plan", "round": 1, "judge": "codex"}],
                        "adversarial_sha_history": ["sha-1", "sha-2"],
                        "initial_sha": "sha-0",
                    },
                )

                persisted = json.loads(state_path.read_text(encoding="utf-8"))
                plan_state = updated["stages"]["plan"]

                self.assertEqual(updated["schema_version"], 2)
                self.assertEqual(updated["custom_key"], "keep")
                self.assertEqual(plan_state["adversarial_rounds"], 6)
                self.assertEqual(plan_state["annotations"], [{"stage": "plan", "round": 1, "judge": "codex"}])
                self.assertEqual(plan_state["adversarial_sha_history"], ["sha-1", "sha-2"])
                self.assertEqual(plan_state["initial_sha"], "sha-0")
                self.assertEqual(updated["plan_adversarial_rounds"], 6)
                self.assertNotIn("plan_annotations", updated)
                self.assertNotIn("plan_adversarial_sha_history", updated)
                self.assertNotIn("plan_initial_sha", updated)
                self.assertEqual(persisted["plan_adversarial_rounds"], 6)
                self.assertEqual(persisted["stages"]["plan"]["initial_sha"], "sha-0")

    def test_update_stage_state_bumps_schema_version_once(self) -> None:
        """The first stage write upgrades schema_version to 2 and keeps it there."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(tmpdir)):
                slug = "schema-version"
                state_path = FACTORY_STATE.factory_state_path(slug)
                FACTORY_STATE.atomic_json_write(state_path, {"stages": {}, "custom_key": "keep"})

                first = FACTORY_STATE.update_stage_state(slug, "plan", {"adversarial_rounds": 1})
                second = FACTORY_STATE.update_stage_state(slug, "plan", {"judge_rounds": 3})

                self.assertEqual(first["schema_version"], 2)
                self.assertEqual(second["schema_version"], 2)

                persisted = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["schema_version"], 2)
                self.assertEqual(persisted["stages"]["plan"]["adversarial_rounds"], 1)
                self.assertEqual(persisted["stages"]["plan"]["judge_rounds"], 3)

    def test_load_workflow_state_adds_missing_stage_container_without_mutating_disk(self) -> None:
        """Old-format state loads with empty stages and keeps the file unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(tmpdir)):
                slug = "old-format"
                state_path = FACTORY_STATE.factory_state_path(slug)
                original = {
                    "review_policy": {"sensitive": True},
                    "custom": {"x": 1},
                }
                FACTORY_STATE.atomic_json_write(state_path, original)
                original_text = state_path.read_text(encoding="utf-8")

                loaded = FACTORY_STATE.load_workflow_state(slug)

                self.assertEqual(loaded["stages"], {})
                self.assertEqual(loaded["schema_version"], 1)
                self.assertEqual(loaded["custom"], {"x": 1})
                self.assertEqual(loaded["review_policy"], {"sensitive": True})
                self.assertEqual(state_path.read_text(encoding="utf-8"), original_text)


if __name__ == "__main__":
    unittest.main()
