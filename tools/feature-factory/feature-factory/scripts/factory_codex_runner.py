#!/usr/bin/env python3
"""Shared runner for ``codex exec`` subprocesses, with an idle/no-output watchdog.

Both ``implement`` and ``dispatch-codex`` launch ``codex exec``. They used to
each carry their own copy of "start the process, wait, capture output, kill on
timeout". This module is the single shared core so that behaviour cannot drift
between the two call sites again.

The important feature beyond a plain ``subprocess.run`` is the **idle watchdog**:
if Codex produces no output for ``idle_timeout`` seconds (a transient startup
stall: network/model/auth/IPC), the process group is killed and a retryable
result is returned — so a 10-second blip fails fast instead of sitting on a
60-minute wall. A separate ``hard_timeout`` caps total wall-clock time.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Defaults are env-overridable so operators can tune without editing code.
DEFAULT_IDLE_TIMEOUT: float = _env_float("FF_CODEX_IDLE_TIMEOUT", 300.0)
DEFAULT_HARD_TIMEOUT: float = _env_float("FF_CODEX_HARD_TIMEOUT", 3600.0)
DEFAULT_MAX_ATTEMPTS: int = max(1, int(_env_float("FF_CODEX_MAX_ATTEMPTS", 2.0)))
STATUS_INTERVAL_SECONDS: float = 60.0
_KILL_GRACE_SECONDS: float = 2.0
_POLL_SECONDS: float = 1.0

# Return codes the runner uses to signal *why* a run ended, distinct from any
# code Codex itself returns. 124/125/127 follow shell conventions.
RC_HARD_TIMEOUT = 124  # exceeded the overall wall-clock cap
RC_IDLE_TIMEOUT = 125  # no output for idle_timeout seconds (retryable)
RC_NOT_FOUND = 127     # the executable was not found on PATH

# Only an idle stall is worth a free retry — it is the transient-startup case.
# A hard-timeout means Codex genuinely ran long; retrying just doubles the cost.
# A missing CLI will not fix itself on retry.
RETRYABLE_RETURN_CODES = frozenset({RC_IDLE_TIMEOUT})


def kill_process_group(proc: "subprocess.Popen[str]", grace: float = _KILL_GRACE_SECONDS) -> None:
    """SIGTERM the process's whole group, then SIGKILL if it does not exit in ``grace``.

    Codex spawns children; killing only ``proc.pid`` can orphan them. The process
    is started with ``start_new_session=True`` so the whole tree shares one
    process group we can signal at once.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _emit_status(
    label: str,
    elapsed: float,
    idle_for: float,
    log_path: Optional[Path],
    on_status: Optional[Callable[[str], None]],
) -> None:
    elapsed_i = int(elapsed)
    idle_i = int(idle_for)
    line = f"[{label}] running {elapsed_i // 60}m{elapsed_i % 60:02d}s, last output {idle_i}s ago"
    if log_path is not None:
        line += f", log={log_path}"
    print(line, file=sys.stdout, flush=True)
    if on_status is not None:
        on_status(f"codex running, last output {idle_i}s ago")


def run_codex(
    command: list[str],
    cwd: Path,
    *,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    hard_timeout: float = DEFAULT_HARD_TIMEOUT,
    log_path: Optional[Path] = None,
    label: str = "codex",
    on_status: Optional[Callable[[str], None]] = None,
    status_interval: float = STATUS_INTERVAL_SECONDS,
) -> subprocess.CompletedProcess:
    """Run a ``codex exec`` ``command`` with an idle/no-output watchdog.

    Streams stdout+stderr into memory (and to ``log_path`` if given), tracking
    the time of the last byte. If no output arrives for ``idle_timeout`` seconds,
    or the run exceeds ``hard_timeout`` seconds, the process group is killed and
    the returned ``CompletedProcess.returncode`` is :data:`RC_IDLE_TIMEOUT` or
    :data:`RC_HARD_TIMEOUT`. A missing CLI returns :data:`RC_NOT_FOUND` without
    launching anything. Otherwise the real Codex return code is returned. A
    one-line liveness status is printed to stdout every ``status_interval``
    seconds and forwarded to ``on_status`` so a stall is visible, not silent.
    """
    exe = shutil.which(command[0])
    if exe is None:
        message = f"{command[0]} not found on PATH; install or activate it before dispatching"
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(message + "\n", encoding="utf-8")
        print(f"[{label}] {message}", file=sys.stderr, flush=True)
        return subprocess.CompletedProcess(command, RC_NOT_FOUND, stdout="", stderr=message)

    resolved = [exe, *command[1:]]

    log_handle = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("w", encoding="utf-8")

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    state_lock = threading.Lock()
    last_output = time.monotonic()

    def _pump(stream, sink: list[str]) -> None:
        nonlocal last_output
        try:
            for line in iter(stream.readline, ""):
                with state_lock:
                    last_output = time.monotonic()
                    if log_handle is not None:
                        log_handle.write(line)
                        log_handle.flush()
                sink.append(line)
        finally:
            stream.close()

    proc: "subprocess.Popen[str]" = subprocess.Popen(
        resolved,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    readers = [
        threading.Thread(target=_pump, args=(proc.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, stderr_chunks), daemon=True),
    ]
    for thread in readers:
        thread.start()

    start = time.monotonic()
    last_status = start
    stall_rc: Optional[int] = None
    try:
        while True:
            try:
                proc.wait(timeout=_POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            with state_lock:
                idle_for = now - last_output
            if idle_for >= idle_timeout:
                stall_rc = RC_IDLE_TIMEOUT
                break
            if now - start >= hard_timeout:
                stall_rc = RC_HARD_TIMEOUT
                break
            if now - last_status >= status_interval:
                last_status = now
                _emit_status(label, now - start, idle_for, log_path, on_status)
    finally:
        if stall_rc is not None:
            kill_process_group(proc)
        for thread in readers:
            thread.join(timeout=2.0)
        if log_handle is not None:
            log_handle.close()

    rc = stall_rc if stall_rc is not None else proc.returncode
    if stall_rc == RC_IDLE_TIMEOUT:
        print(
            f"[{label}] killed: no output for {int(idle_timeout)}s (idle timeout)",
            file=sys.stderr,
            flush=True,
        )
    elif stall_rc == RC_HARD_TIMEOUT:
        print(
            f"[{label}] killed: exceeded {int(hard_timeout)}s overall (hard timeout)",
            file=sys.stderr,
            flush=True,
        )
    return subprocess.CompletedProcess(command, rc, "".join(stdout_chunks), "".join(stderr_chunks))


def run_codex_with_retry(
    dispatch: Callable[[], subprocess.CompletedProcess],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    label: str = "codex",
) -> subprocess.CompletedProcess:
    """Call ``dispatch`` and retry once on a retryable stall (idle timeout).

    ``dispatch`` runs one full attempt (typically a :func:`run_codex` wrapped in
    telemetry) and returns its ``CompletedProcess``. On an idle-timeout result it
    is retried up to ``max_attempts`` total, since the transient-startup stall the
    watchdog catches usually clears on a fresh launch.
    """
    result = dispatch()
    attempt = 1
    while result.returncode in RETRYABLE_RETURN_CODES and attempt < max_attempts:
        print(
            f"[{label}] attempt {attempt} stalled (idle timeout) — retrying",
            file=sys.stderr,
            flush=True,
        )
        attempt += 1
        result = dispatch()
    return result


__all__ = [
    "DEFAULT_HARD_TIMEOUT",
    "DEFAULT_IDLE_TIMEOUT",
    "DEFAULT_MAX_ATTEMPTS",
    "RC_HARD_TIMEOUT",
    "RC_IDLE_TIMEOUT",
    "RC_NOT_FOUND",
    "RETRYABLE_RETURN_CODES",
    "kill_process_group",
    "run_codex",
    "run_codex_with_retry",
]
