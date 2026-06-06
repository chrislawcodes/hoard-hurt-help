#!/usr/bin/env python3
"""Per-slug exclusive run locks for mutating Feature Factory commands.

A small shared helper so long-running commands (implement, autopilot, …) can
guard a slug against a second concurrent run of the same kind. flock-based, so
the OS releases the lock automatically if the holder process crashes — no stale
locks to clean up. The caller MUST keep the returned fd open for the whole run
and release it in a finally block.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


def run_lock_path(slug: str, name: str) -> Path:
    # Resolve workflow_dir at call time (not import time) so the test harness,
    # which reloads factory_state per-test with a fresh runs-root, is honored.
    from factory_state import workflow_dir
    return workflow_dir(slug) / f".{name}.lock"


def acquire_run_lock(slug: str, name: str, what: str | None = None) -> tuple[int, str]:
    """Open and exclusively flock the per-slug ``<name>`` lockfile.

    Returns ``(fd, "")`` on success. The caller MUST hold ``fd`` open for the
    entire run and call :func:`release_run_lock` in a finally block — keeping the
    fd open lets the OS auto-release the lock if the process crashes.

    Returns ``(-1, error_message)`` when the lock is already held by another
    invocation (EAGAIN / EACCES). The caller should print the message to stderr
    and return non-zero without doing the work.
    """
    label = what or name
    lock_path = run_lock_path(slug, name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                raw = os.read(fd, 4096).decode("utf-8", errors="replace")
                holder: dict = json.loads(raw) if raw.strip() else {}
            except Exception:
                holder = {}
            os.close(fd)
            pid = holder.get("pid", "unknown")
            started = holder.get("started_at", "unknown")
            return -1, (
                f"[error] {label} already running for slug {slug!r} "
                f"(pid {pid}, started {started}). "
                "Wait for it to finish, or kill it and retry."
            )
        os.close(fd)
        raise
    payload = json.dumps({
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "slug": slug,
    }).encode("utf-8")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload)
    return fd, ""


def release_run_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
