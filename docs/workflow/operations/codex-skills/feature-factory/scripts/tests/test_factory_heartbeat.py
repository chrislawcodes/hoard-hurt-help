import contextlib
import io
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_heartbeat as HEARTBEAT  # noqa: E402


SLUG = "ff-judge-panel"
STAGE = "plan"


class ControlledSleeper:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._allowed = 0

    def allow(self, count: int = 1) -> None:
        with self._cond:
            self._allowed += count
            self._cond.notify_all()

    def __call__(self, stop_event: threading.Event, seconds: int) -> bool:
        del seconds
        with self._cond:
            while self._allowed <= 0 and not stop_event.is_set():
                self._cond.wait(timeout=0.05)
            if stop_event.is_set():
                return True
            self._allowed -= 1
            return False


def _make_clock(times: list[datetime]):
    values = iter(times)

    def _next() -> datetime:
        return next(values)

    return _next


def _make_monotonic(values: list[float]):
    iterator = iter(values)

    def _next() -> float:
        return next(iterator)

    return _next


class FactoryHeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._root_patch = patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(self._tmpdir.name))
        self._root_patch.start()
        self.addCleanup(self._root_patch.stop)
        workflow_root = FACTORY_STATE.workflow_dir(SLUG)
        workflow_root.mkdir(parents=True, exist_ok=True)

    def _patch_clock(self, *, pt_times: list[datetime], utc_times: list[datetime], monotonic_values: list[float], sleeper: ControlledSleeper):
        return contextlib.ExitStack()

    def _start_emitter(
        self,
        *,
        cadence_seconds: int = 1,
        activity: str | None = None,
        pt_times: list[datetime],
        utc_times: list[datetime],
        monotonic_values: list[float],
        sleeper: ControlledSleeper,
    ) -> tuple[HEARTBEAT.HeartbeatEmitter, io.StringIO]:
        pt_clock = _make_clock(pt_times)
        utc_clock = _make_clock(utc_times)
        mono_clock = _make_monotonic(monotonic_values)
        buffer = io.StringIO()
        emitter = HEARTBEAT.HeartbeatEmitter(SLUG, STAGE, cadence_seconds=cadence_seconds)
        patches = [
            patch.object(HEARTBEAT, "_pt_now", side_effect=pt_clock),
            patch.object(HEARTBEAT, "_utc_now", side_effect=utc_clock),
            patch.object(HEARTBEAT, "_monotonic", side_effect=mono_clock),
            patch.object(HEARTBEAT, "_sleep_interruptible", side_effect=sleeper),
        ]
        self._patches = contextlib.ExitStack()
        for p in patches:
            self._patches.enter_context(p)
        self._patches.enter_context(contextlib.redirect_stdout(buffer))
        emitter.start()
        if activity is not None:
            HEARTBEAT.set_activity(activity)
        return emitter, buffer

    def tearDown(self) -> None:
        patch_stack = getattr(self, "_patches", None)
        if patch_stack is not None:
            patch_stack.close()

    def _wait_for_lines(self, buffer: io.StringIO, count: int, timeout: float = 2.0) -> list[str]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
            if len(lines) >= count:
                return lines
            time.sleep(0.01)
        self.fail(f"timed out waiting for {count} heartbeat line(s); got {buffer.getvalue()!r}")

    def test_emitter_writes_line_on_cadence(self) -> None:
        sleeper = ControlledSleeper()
        emitter, buffer = self._start_emitter(
            pt_times=[datetime(2026, 4, 18, 10, 0, tzinfo=HEARTBEAT.PT_ZONE), datetime(2026, 4, 18, 10, 1, tzinfo=HEARTBEAT.PT_ZONE)],
            utc_times=[datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc), datetime(2026, 4, 18, 17, 1, tzinfo=timezone.utc)],
            monotonic_values=[0.0, 1.0],
            sleeper=sleeper,
        )
        HEARTBEAT.set_activity("dispatching judges")
        sleeper.allow()
        lines = self._wait_for_lines(buffer, 1)
        emitter.stop()

        self.assertRegex(lines[0], r"^\[heartbeat PT \d\d:\d\d\] plan: dispatching judges, elapsed 0m:01s, pid \d+ alive$")

    def test_emitter_writes_to_state(self) -> None:
        sleeper = ControlledSleeper()
        emitter, buffer = self._start_emitter(
            pt_times=[datetime(2026, 4, 18, 10, 0, tzinfo=HEARTBEAT.PT_ZONE), datetime(2026, 4, 18, 10, 1, tzinfo=HEARTBEAT.PT_ZONE)],
            utc_times=[datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc), datetime(2026, 4, 18, 17, 1, tzinfo=timezone.utc)],
            monotonic_values=[0.0, 1.0],
            sleeper=sleeper,
        )
        HEARTBEAT.set_activity("awaiting reviewers")
        sleeper.allow()
        self._wait_for_lines(buffer, 1)
        emitter.stop()

        state = json.loads(FACTORY_STATE.factory_state_path(SLUG).read_text(encoding="utf-8"))
        self.assertIn("heartbeats", state)
        self.assertGreaterEqual(len(state["heartbeats"]), 1)
        heartbeat = state["heartbeats"][0]
        self.assertEqual(heartbeat["stage"], STAGE)
        self.assertEqual(heartbeat["activity"], "awaiting reviewers")
        self.assertIn("timestamp_utc", heartbeat)
        self.assertTrue(heartbeat["timestamp_utc"].endswith("Z") or "+" in heartbeat["timestamp_utc"])
        self.assertIn("elapsed_ms", heartbeat)

    def test_emitter_pt_format(self) -> None:
        sleeper = ControlledSleeper()
        emitter, buffer = self._start_emitter(
            pt_times=[datetime(2026, 4, 18, 10, 5, tzinfo=HEARTBEAT.PT_ZONE), datetime(2026, 4, 18, 10, 6, tzinfo=HEARTBEAT.PT_ZONE)],
            utc_times=[datetime(2026, 4, 18, 17, 5, tzinfo=timezone.utc), datetime(2026, 4, 18, 17, 6, tzinfo=timezone.utc)],
            monotonic_values=[0.0, 1.0],
            sleeper=sleeper,
        )
        HEARTBEAT.set_activity("dispatching judges")
        sleeper.allow()
        lines = self._wait_for_lines(buffer, 1)
        emitter.stop()

        self.assertRegex(lines[0], r"^\[heartbeat PT \d\d:\d\d\]")

    def test_emitter_stop_joins_thread(self) -> None:
        sleeper = ControlledSleeper()
        emitter, _ = self._start_emitter(
            pt_times=[datetime(2026, 4, 18, 10, 0, tzinfo=HEARTBEAT.PT_ZONE), datetime(2026, 4, 18, 10, 1, tzinfo=HEARTBEAT.PT_ZONE)],
            utc_times=[datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc), datetime(2026, 4, 18, 17, 1, tzinfo=timezone.utc)],
            monotonic_values=[0.0, 1.0],
            sleeper=sleeper,
        )
        start = time.monotonic()
        emitter.stop()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.0)

    def test_set_activity_reflected_in_next_tick(self) -> None:
        sleeper = ControlledSleeper()
        emitter, buffer = self._start_emitter(
            pt_times=[
                datetime(2026, 4, 18, 10, 0, tzinfo=HEARTBEAT.PT_ZONE),
                datetime(2026, 4, 18, 10, 1, tzinfo=HEARTBEAT.PT_ZONE),
                datetime(2026, 4, 18, 10, 2, tzinfo=HEARTBEAT.PT_ZONE),
            ],
            utc_times=[
                datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 18, 17, 1, tzinfo=timezone.utc),
                datetime(2026, 4, 18, 17, 2, tzinfo=timezone.utc),
            ],
            monotonic_values=[0.0, 1.0, 2.0],
            sleeper=sleeper,
        )
        HEARTBEAT.set_activity("A")
        sleeper.allow()
        lines = self._wait_for_lines(buffer, 1)
        self.assertIn("A", lines[0])

        HEARTBEAT.set_activity("B")
        sleeper.allow()
        lines = self._wait_for_lines(buffer, 2)
        emitter.stop()

        self.assertIn("B", lines[1])

    def test_stale_activity_warning_fires_after_30min(self) -> None:
        sleeper = ControlledSleeper()
        emitter, buffer = self._start_emitter(
            pt_times=[
                datetime(2026, 4, 18, 10, 0, tzinfo=HEARTBEAT.PT_ZONE),
                datetime(2026, 4, 18, 10, 10, tzinfo=HEARTBEAT.PT_ZONE),
                datetime(2026, 4, 18, 10, 20, tzinfo=HEARTBEAT.PT_ZONE),
                datetime(2026, 4, 18, 10, 30, tzinfo=HEARTBEAT.PT_ZONE),
            ],
            utc_times=[
                datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 18, 17, 10, tzinfo=timezone.utc),
                datetime(2026, 4, 18, 17, 20, tzinfo=timezone.utc),
                datetime(2026, 4, 18, 17, 30, tzinfo=timezone.utc),
            ],
            monotonic_values=[0.0, 600.0, 1200.0, 1800.0],
            sleeper=sleeper,
        )
        HEARTBEAT.set_activity("stuck")
        sleeper.allow(3)
        lines = self._wait_for_lines(buffer, 4)
        emitter.stop()

        warn_lines = [line for line in lines if "WARN: stage=plan activity=stuck unchanged for 30+ minutes" in line]
        self.assertEqual(len(warn_lines), 1)

    def test_stale_activity_warning_fires_once(self) -> None:
        sleeper = ControlledSleeper()
        emitter, buffer = self._start_emitter(
            pt_times=[
                datetime(2026, 4, 18, 10, 0, tzinfo=HEARTBEAT.PT_ZONE),
                datetime(2026, 4, 18, 10, 10, tzinfo=HEARTBEAT.PT_ZONE),
                datetime(2026, 4, 18, 10, 20, tzinfo=HEARTBEAT.PT_ZONE),
                datetime(2026, 4, 18, 10, 30, tzinfo=HEARTBEAT.PT_ZONE),
                datetime(2026, 4, 18, 10, 40, tzinfo=HEARTBEAT.PT_ZONE),
                datetime(2026, 4, 18, 10, 50, tzinfo=HEARTBEAT.PT_ZONE),
            ],
            utc_times=[
                datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 18, 17, 10, tzinfo=timezone.utc),
                datetime(2026, 4, 18, 17, 20, tzinfo=timezone.utc),
                datetime(2026, 4, 18, 17, 30, tzinfo=timezone.utc),
                datetime(2026, 4, 18, 17, 40, tzinfo=timezone.utc),
                datetime(2026, 4, 18, 17, 50, tzinfo=timezone.utc),
            ],
            monotonic_values=[0.0, 600.0, 1200.0, 1800.0, 2400.0, 3000.0],
            sleeper=sleeper,
        )
        HEARTBEAT.set_activity("stuck")
        sleeper.allow(5)
        lines = self._wait_for_lines(buffer, 5)
        emitter.stop()

        warn_lines = [line for line in lines if "WARN: stage=plan activity=stuck unchanged for 30+ minutes" in line]
        self.assertEqual(len(warn_lines), 1)

    def test_context_manager_start_stop(self) -> None:
        sleeper = ControlledSleeper()
        pt_times = [
            datetime(2026, 4, 18, 10, 0, tzinfo=HEARTBEAT.PT_ZONE),
            datetime(2026, 4, 18, 10, 1, tzinfo=HEARTBEAT.PT_ZONE),
        ]
        utc_times = [
            datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 18, 17, 1, tzinfo=timezone.utc),
        ]
        monotonic_values = [0.0, 1.0]
        pt_clock = _make_clock(pt_times)
        utc_clock = _make_clock(utc_times)
        mono_clock = _make_monotonic(monotonic_values)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(HEARTBEAT, "_pt_now", side_effect=pt_clock))
            stack.enter_context(patch.object(HEARTBEAT, "_utc_now", side_effect=utc_clock))
            stack.enter_context(patch.object(HEARTBEAT, "_monotonic", side_effect=mono_clock))
            stack.enter_context(patch.object(HEARTBEAT, "_sleep_interruptible", side_effect=sleeper))
            buffer = io.StringIO()
            stack.enter_context(contextlib.redirect_stdout(buffer))
            with HEARTBEAT.HeartbeatEmitter(SLUG, STAGE, cadence_seconds=1) as hb:
                self.assertIsNotNone(hb._thread)
                self.assertTrue(hb._thread.is_alive())
                sleeper.allow()
                self._wait_for_lines(buffer, 1)
            self.assertIsNone(hb._thread)


if __name__ == "__main__":
    unittest.main()
