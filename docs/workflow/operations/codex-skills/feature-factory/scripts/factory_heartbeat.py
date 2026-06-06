#!/usr/bin/env python3
"""Thread-based heartbeat sidecar for long-running feature-factory commands."""
from __future__ import annotations

import os
import sys
import threading
import time
import weakref
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from factory_state import with_locked_state

PT_ZONE = ZoneInfo("America/Los_Angeles")

_ACTIVE_EMITTER_LOCK = threading.Lock()
_ACTIVE_EMITTER_REF: weakref.ReferenceType["HeartbeatEmitter"] | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _pt_now() -> datetime:
    return datetime.now(PT_ZONE)


def _monotonic() -> float:
    return time.monotonic()


def _sleep_interruptible(stop_event: threading.Event, seconds: int) -> bool:
    return stop_event.wait(seconds)


def _set_active_emitter(emitter: "HeartbeatEmitter" | None) -> None:
    global _ACTIVE_EMITTER_REF
    with _ACTIVE_EMITTER_LOCK:
        _ACTIVE_EMITTER_REF = weakref.ref(emitter) if emitter is not None else None


def _get_active_emitter() -> "HeartbeatEmitter" | None:
    with _ACTIVE_EMITTER_LOCK:
        if _ACTIVE_EMITTER_REF is None:
            return None
        emitter = _ACTIVE_EMITTER_REF()
        if emitter is None:
            return None
        if emitter._stopped:
            return None
        return emitter


def set_activity(activity: str) -> None:
    """Forward an activity update to the currently running emitter, if any."""
    emitter = _get_active_emitter()
    if emitter is not None:
        emitter.set_activity(activity)


class HeartbeatEmitter:
    """Emit periodic heartbeat lines and persist them to workflow state."""

    def __init__(self, slug: str, stage: str, cadence_seconds: int = 600):
        self.slug = slug
        self.stage = stage
        self.cadence_seconds = int(cadence_seconds)
        self._activity_lock = threading.Lock()
        self._activity = "starting"
        self._start_monotonic = 0.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopped = False

    def __enter__(self) -> "HeartbeatEmitter":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event = threading.Event()
        self._stopped = False
        self._start_monotonic = _monotonic()
        _set_active_emitter(self)
        self._thread = threading.Thread(
            target=self._run,
            name=f"heartbeat-{self.slug}-{self.stage}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        global _ACTIVE_EMITTER_REF
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join()
        self._stopped = True
        with _ACTIVE_EMITTER_LOCK:
            if _ACTIVE_EMITTER_REF is not None and _ACTIVE_EMITTER_REF() is self:
                _ACTIVE_EMITTER_REF = None
        self._thread = None

    def set_activity(self, activity: str) -> None:
        with self._activity_lock:
            cleaned = str(activity).strip()
            self._activity = cleaned or "idle"

    def _current_activity(self) -> str:
        with self._activity_lock:
            return self._activity

    def _emit_state_record(self, activity: str, elapsed_ms: int, timestamp_pt: str, timestamp_utc: str) -> None:
        with with_locked_state(self.slug) as state:
            heartbeats = state.get("heartbeats")
            if not isinstance(heartbeats, list):
                heartbeats = []
            heartbeats = list(heartbeats)
            heartbeats.append(
                {
                    "timestamp_pt": timestamp_pt,
                    "timestamp_utc": timestamp_utc,
                    "stage": self.stage,
                    "activity": activity,
                    "elapsed_ms": elapsed_ms,
                }
            )
            state["heartbeats"] = heartbeats

    def _run(self) -> None:
        last_activity = None
        same_activity_ticks = 0
        warned_activity = None

        while not self._stop_event.is_set():
            if _sleep_interruptible(self._stop_event, self.cadence_seconds):
                break
            if self._stop_event.is_set():
                break

            pt_now = _pt_now()
            utc_now = _utc_now()
            elapsed_ms = max(int((_monotonic() - self._start_monotonic) * 1000), 0)
            activity = self._current_activity()
            minutes, seconds = divmod(elapsed_ms // 1000, 60)
            print(
                f"[heartbeat PT {pt_now.strftime('%H:%M')}] {self.stage}: {activity}, "
                f"elapsed {minutes}m:{seconds:02d}s, pid {os.getpid()} alive",
                file=sys.stdout,
                flush=True,
            )
            self._emit_state_record(activity, elapsed_ms, pt_now.isoformat(), utc_now.isoformat())

            if activity == last_activity:
                same_activity_ticks += 1
            else:
                same_activity_ticks = 1
                warned_activity = None

            if same_activity_ticks >= 3 and warned_activity != activity:
                print(
                    f"[heartbeat PT {pt_now.strftime('%H:%M')}] WARN: stage={self.stage} "
                    f"activity={activity} unchanged for 30+ minutes",
                    file=sys.stdout,
                    flush=True,
                )
                warned_activity = activity

            last_activity = activity


__all__ = ["HeartbeatEmitter", "set_activity"]
