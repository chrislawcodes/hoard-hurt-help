#!/usr/bin/env python3
"""Per-command telemetry for the feature-factory orchestrator."""
from __future__ import annotations

import contextlib
import dataclasses
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, UTC


_TTL_SECONDS = 270.0
_telemetry_ctx = threading.local()


@dataclasses.dataclass
class TelemetryCounters:
    input_bytes_read: int = 0
    output_bytes_written: int = 0
    files_read: int = 0
    files_written: int = 0
    subprocess_invocations: int = 0


def _stack() -> list[TelemetryCounters]:
    stack = getattr(_telemetry_ctx, "stack", None)
    if stack is None:
        stack = []
        _telemetry_ctx.stack = stack
    return stack


def _saved_subprocess_run():
    return getattr(_telemetry_ctx, "saved_subprocess_run", subprocess.run)


def _saved_subprocess_popen():
    return getattr(_telemetry_ctx, "saved_subprocess_popen", subprocess.Popen)


def current_ctx() -> TelemetryCounters | None:
    stack = getattr(_telemetry_ctx, "stack", None)
    if not stack:
        return None
    return stack[-1]


def _patch_subprocess() -> None:
    def _run(*args, **kwargs):
        ctx = current_ctx()
        if ctx is not None:
            ctx.subprocess_invocations += 1
        return _saved_subprocess_run()(*args, **kwargs)

    def _popen(*args, **kwargs):
        ctx = current_ctx()
        if ctx is not None:
            ctx.subprocess_invocations += 1
        return _saved_subprocess_popen()(*args, **kwargs)

    subprocess.run = _run  # type: ignore[assignment]
    subprocess.Popen = _popen  # type: ignore[assignment]


def _restore_subprocess() -> None:
    subprocess.run = _saved_subprocess_run()  # type: ignore[assignment]
    subprocess.Popen = _saved_subprocess_popen()  # type: ignore[assignment]


@contextmanager
def command_telemetry_scope(slug: str | None, command: str, stage: str | None):
    counters = TelemetryCounters()
    stack = _stack()
    depth = getattr(_telemetry_ctx, "depth", 0)
    stack.append(counters)
    if depth == 0:
        _telemetry_ctx.saved_subprocess_run = subprocess.run
        _telemetry_ctx.saved_subprocess_popen = subprocess.Popen
        _patch_subprocess()
    _telemetry_ctx.depth = depth + 1
    start = time.perf_counter()
    try:
        yield counters
    finally:
        wall_seconds = time.perf_counter() - start
        stack.pop()
        _telemetry_ctx.depth = max(depth, 0)
        if depth == 0:
            _restore_subprocess()
            if hasattr(_telemetry_ctx, "saved_subprocess_run"):
                delattr(_telemetry_ctx, "saved_subprocess_run")
            if hasattr(_telemetry_ctx, "saved_subprocess_popen"):
                delattr(_telemetry_ctx, "saved_subprocess_popen")
            if hasattr(_telemetry_ctx, "depth"):
                delattr(_telemetry_ctx, "depth")
        if slug:
            try:
                record_command_telemetry(
                    slug=slug,
                    command=command,
                    stage=stage,
                    wall_seconds=wall_seconds,
                    input_bytes_read=counters.input_bytes_read,
                    output_bytes_written=counters.output_bytes_written,
                    files_read=counters.files_read,
                    files_written=counters.files_written,
                    subprocess_invocations=counters.subprocess_invocations,
                )
            except Exception as exc:  # noqa: BLE001 - telemetry must never abort the command
                print(f"[telemetry-warning] failed to record command telemetry: {exc}", file=sys.stderr)


def record_command_telemetry(
    slug: str,
    command: str,
    stage: str | None,
    wall_seconds: float,
    input_bytes_read: int,
    output_bytes_written: int,
    files_read: int,
    files_written: int,
    subprocess_invocations: int,
) -> None:
    """Best-effort append of a telemetry record."""
    ttl_crossed = wall_seconds > _TTL_SECONDS
    record = {
        "command": command,
        "stage": stage,
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "wall_seconds": round(wall_seconds, 3),
        "input_bytes_read": int(input_bytes_read),
        "output_bytes_written": int(output_bytes_written),
        "files_read": int(files_read),
        "files_written": int(files_written),
        "subprocess_invocations": int(subprocess_invocations),
        "ttl_crossed": ttl_crossed,
    }

    from factory_state import _cap_command_telemetry, update_workflow_state  # noqa: E402

    def mutate(state: dict) -> None:
        records = state.get("command_telemetry")
        if not isinstance(records, list):
            records = []
            state["command_telemetry"] = records
        records.append(record)
        _cap_command_telemetry(state)

    try:
        update_workflow_state(slug, mutate)
    except Exception as exc:  # noqa: BLE001 - best-effort only
        print(f"[telemetry-warning] failed to record command telemetry: {exc}", file=sys.stderr)
    if ttl_crossed:
        print(
            f"[ttl-warning] {command} crossed the 5-minute Anthropic prompt-cache TTL "
            f"(wall={wall_seconds:.1f}s); subsequent orchestrator reads will be uncached. "
            "Consider batching follow-up commands.",
            file=sys.stderr,
        )
