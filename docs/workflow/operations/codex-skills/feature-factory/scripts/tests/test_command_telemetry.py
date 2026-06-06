import contextlib
import importlib.util
import io
import json
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys_modules_name = spec.name
    import sys
    sys.modules[sys_modules_name] = mod
    spec.loader.exec_module(mod)
    return mod


FACTORY_STATE = _load("factory_state")
FACTORY_TELEMETRY_COMMANDS = _load("factory_telemetry_commands")


class CommandTelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        import sys
        fake_state = types.ModuleType("factory_state")
        fake_state._cap_command_telemetry = FACTORY_STATE._cap_command_telemetry
        fake_state.update_workflow_state = self._update_workflow_state
        sys.modules["factory_state"] = fake_state
        sys.modules["factory_telemetry_commands"] = FACTORY_TELEMETRY_COMMANDS
        self.state = FACTORY_STATE._default_workflow_state()

    def _update_workflow_state(self, slug: str, mutator):
        mutator(self.state)
        return self.state

    def test_record_command_telemetry_persists_expected_shape(self) -> None:
        slug = "telemetry-shape"

        FACTORY_TELEMETRY_COMMANDS.record_command_telemetry(
            slug=slug,
            command="checkpoint",
            stage="diff",
            wall_seconds=12.345,
            input_bytes_read=11,
            output_bytes_written=22,
            files_read=3,
            files_written=4,
            subprocess_invocations=5,
        )

        record = self.state["command_telemetry"][-1]
        self.assertEqual(
            record,
            {
                "command": "checkpoint",
                "stage": "diff",
                "ts": record["ts"],
                "wall_seconds": 12.345,
                "input_bytes_read": 11,
                "output_bytes_written": 22,
                "files_read": 3,
                "files_written": 4,
                "subprocess_invocations": 5,
                "ttl_crossed": False,
            },
        )

    def test_record_command_telemetry_caps_at_100(self) -> None:
        slug = "telemetry-cap"

        for i in range(105):
            FACTORY_TELEMETRY_COMMANDS.record_command_telemetry(
                slug=slug,
                command="status",
                stage=None,
                wall_seconds=0.1,
                input_bytes_read=i,
                output_bytes_written=i,
                files_read=0,
                files_written=0,
                subprocess_invocations=0,
            )

        self.assertEqual(len(self.state["command_telemetry"]), 100)
        self.assertEqual(self.state["command_telemetry"][0]["input_bytes_read"], 5)
        self.assertEqual(self.state["command_telemetry"][-1]["input_bytes_read"], 104)

    def test_record_command_telemetry_swallows_update_failure(self) -> None:
        slug = "telemetry-failure"

        stderr = io.StringIO()
        fake_state = types.ModuleType("factory_state")
        fake_state._cap_command_telemetry = FACTORY_STATE._cap_command_telemetry

        def _raise(*args, **kwargs):
            raise RuntimeError("boom")

        fake_state.update_workflow_state = _raise
        import sys
        sys.modules["factory_state"] = fake_state
        with contextlib.redirect_stderr(stderr):
            FACTORY_TELEMETRY_COMMANDS.record_command_telemetry(
                slug=slug,
                command="dispatch-codex",
                stage="diff",
                wall_seconds=1.0,
                input_bytes_read=1,
                output_bytes_written=1,
                files_read=1,
                files_written=1,
                subprocess_invocations=1,
            )

        self.assertIn("[telemetry-warning] failed to record command telemetry:", stderr.getvalue())
        self.assertIn("boom", stderr.getvalue())

    def test_ttl_warning_marks_record(self) -> None:
        slug = "telemetry-ttl"

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            FACTORY_TELEMETRY_COMMANDS.record_command_telemetry(
                slug=slug,
                command="checkpoint",
                stage="tasks",
                wall_seconds=271.0,
                input_bytes_read=0,
                output_bytes_written=0,
                files_read=0,
                files_written=0,
                subprocess_invocations=0,
            )

        self.assertIn("[ttl-warning] checkpoint crossed the 5-minute Anthropic prompt-cache TTL", stderr.getvalue())
        self.assertTrue(self.state["command_telemetry"][-1]["ttl_crossed"])


if __name__ == "__main__":
    unittest.main()
