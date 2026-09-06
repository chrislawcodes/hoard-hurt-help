#!/usr/bin/env python3
"""Stream a Codex CLI subprocess with an inactivity (idle) watchdog.

Codex `exec` emits a continuous progress stream (file reads, tool calls,
reasoning) on stderr the whole time it works. A fixed wall-clock timeout cannot
tell a slow-but-healthy review from a hung CLI: too short kills good work, too
long burns the whole ceiling on a hang before failing.

This runner streams that output line-by-line and watches for *silence*. While
new output keeps arriving the process runs as long as it needs (up to a
generous absolute backstop ceiling). If the stream goes quiet for longer than
the idle window, the process is treated as hung and killed immediately -- so a
stuck CLI fails in ~one idle window instead of at the backstop. The latest
activity line is echoed periodically as a visible proof-of-life heartbeat.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, TextIO

# Mirrors run_codex_review.py's existing contract: returncode 124 == timed out.
TIMED_OUT_RETURNCODE = 124

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15
DEFAULT_POLL_INTERVAL_SECONDS = 0.5
_TERMINATE_GRACE_SECONDS = 5


def _drain_stream(pipe: TextIO, sink: list[str], mark_activity: Callable[[str], None]) -> None:
    """Read a text pipe line-by-line until EOF, recording each line as activity."""
    try:
        for line in pipe:
            sink.append(line)
            mark_activity(line)
    finally:
        pipe.close()


def _terminate_process_group(proc: subprocess.Popen, *, force: bool) -> None:
    """Signal the child's whole process group (codex spawns sandboxed children)."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return  # already gone -- nothing to kill
    try:
        os.killpg(pgid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        return  # raced with the process exiting on its own


def run_with_idle_watchdog(
    cmd: list[str],
    *,
    hard_ceiling_seconds: int,
    idle_timeout_seconds: int,
    heartbeat_interval_seconds: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    heartbeat_stream: TextIO | None = None,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    label: str = "codex",
) -> subprocess.CompletedProcess:
    """Run ``cmd``, streaming output and killing it if it goes idle or runs too long.

    Returns a CompletedProcess with the full captured stdout/stderr. On an
    idle-kill or ceiling-kill the returncode is ``TIMED_OUT_RETURNCODE`` (124)
    and a ``[watchdog] killed: ...`` marker is appended to stderr so the failure
    report records *why* the run stopped.
    """
    if heartbeat_stream is None:
        heartbeat_stream = sys.stderr

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    state_lock = threading.Lock()
    last_activity = time.monotonic()
    last_line = ""

    def mark_activity(line: str = "") -> None:
        nonlocal last_activity, last_line
        cleaned = line.strip()
        with state_lock:
            last_activity = time.monotonic()
            if cleaned:
                last_line = cleaned

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,  # own process group so we can kill children too
    )
    readers = [
        threading.Thread(target=_drain_stream, args=(proc.stdout, stdout_lines, mark_activity), daemon=True),
        threading.Thread(target=_drain_stream, args=(proc.stderr, stderr_lines, mark_activity), daemon=True),
    ]
    for reader in readers:
        reader.start()

    start = time.monotonic()
    next_heartbeat = start + heartbeat_interval_seconds
    killed_reason: str | None = None

    while proc.poll() is None:
        now = time.monotonic()
        with state_lock:
            idle_for = now - last_activity
            snapshot = last_line
        if idle_for >= idle_timeout_seconds:
            killed_reason = (
                f"no output for {idle_for:.0f}s "
                f"(idle watchdog limit {idle_timeout_seconds}s) -- treating CLI as hung"
            )
            _terminate_process_group(proc, force=False)
            break
        if now - start >= hard_ceiling_seconds:
            killed_reason = f"exceeded hard ceiling {hard_ceiling_seconds}s"
            _terminate_process_group(proc, force=False)
            break
        if now >= next_heartbeat:
            elapsed = int(now - start)
            print(f"[{label} t+{elapsed}s] active: {(snapshot or 'starting')[:160]}", file=heartbeat_stream, flush=True)
            next_heartbeat = now + heartbeat_interval_seconds
        time.sleep(poll_interval_seconds)

    if killed_reason is not None:
        # Signalled SIGTERM above; give it a grace window, then force-kill.
        try:
            proc.wait(timeout=_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _terminate_process_group(proc, force=True)
            proc.wait()
    else:
        proc.wait()

    for reader in readers:
        reader.join(timeout=_TERMINATE_GRACE_SECONDS)

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)
    if killed_reason is not None:
        stderr += f"\n[watchdog] killed: {killed_reason}\n"
        return subprocess.CompletedProcess(cmd, TIMED_OUT_RETURNCODE, stdout=stdout, stderr=stderr)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout=stdout, stderr=stderr)
