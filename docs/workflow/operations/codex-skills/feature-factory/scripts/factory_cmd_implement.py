#!/usr/bin/env python3
"""command_implement and command_parallel implementations."""
import argparse
import concurrent.futures
import errno
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    REPO_ROOT,
    PARALLEL_ANALYSIS_KEY,
    load_workflow_state,
    workflow_dir,
    update_workflow_state,
)
from factory_heartbeat import HeartbeatEmitter, set_activity as heartbeat_set_activity  # noqa: E402
from factory_telemetry import record_ai_call  # noqa: E402

from factory_git import (  # noqa: E402
    ensure_sync,
    revert_protected_files,
    create_worktree,
    remove_all_worktrees,
    get_new_commits,
    stage_and_commit_if_dirty,
    cherry_pick_commits,
)

from factory_runlock import acquire_run_lock, release_run_lock, run_lock_path  # noqa: E402
from factory_stages import parse_parallel_task_groups  # noqa: E402

from factory_emit import _emit_next_action  # noqa: E402
from factory_mutating import mutates_state  # noqa: E402


def _implement_lock_path(slug: str) -> Path:
    return run_lock_path(slug, "implement")


def _acquire_implement_lock(slug: str) -> tuple[int, str]:
    """Acquire the per-slug implement run lock (see factory_runlock)."""
    return acquire_run_lock(slug, "implement", "implement")


def _release_implement_lock(fd: int) -> None:
    release_run_lock(fd)


def _codex_prompt_path(slug: str, i: int) -> Path:
    safe_slug = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in slug)
    return Path(f"/tmp/codex-impl-{safe_slug}-{os.getpid()}-{i}.txt")


def _implementation_round(slug: str) -> int:
    state = load_workflow_state(slug)
    stages = state.get("stages", {})
    if not isinstance(stages, dict):
        return 0
    stage_state = stages.get("tasks", {})
    if not isinstance(stage_state, dict):
        return 0
    try:
        return int(stage_state.get("adversarial_rounds", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _run_codex_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            timeout=3600,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
        )


def _build_codex_prompt(slug: str, i: int, tasks: list[str], file_scope: list[str]) -> str:
    root = workflow_dir(slug)
    # Prefer compact summaries — they contain everything Codex needs for implementation
    # without the full narrative. Fall back to full files when summaries don't exist yet.
    spec_path = root / "spec-acceptance.md" if (root / "spec-acceptance.md").exists() else root / "spec.md"
    plan_path = root / "plan-summary.md" if (root / "plan-summary.md").exists() else root / "plan.md"

    spec_content = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
    plan_content = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""

    prompt_path = _codex_prompt_path(slug, i)
    prompt_text = (
        "# Implementation Task\n\n"
        "## Context\n"
        f"{spec_content}\n\n"
        "## Plan\n"
        f"{plan_content}\n\n"
        "## Tasks to implement (your scope)\n"
        f"{chr(10).join(map(str, tasks))}\n\n"
        "## File scope\n"
        f"{chr(10).join(map(str, file_scope)) if file_scope else '(no specific scope — implement all tasks)'}\n\n"
        "Implement the tasks above. Commit your changes when done.\n"
        "DO NOT MODIFY: CLAUDE.md, AGENTS.md, MEMORY.md, the docs/ design/architecture docs, or any file not in your file scope.\n"
    )
    prompt_path.write_text(prompt_text, encoding="utf-8")
    return prompt_text


def _run_serial(slug: str, tasks: list[str]) -> int:
    prompt_path = _codex_prompt_path(slug, 0)
    prompt_text = _build_codex_prompt(slug, 0, tasks, [])
    round_number = _implementation_round(slug)
    rc = 1
    try:
        heartbeat_set_activity("codex exec running")
        result = record_ai_call(
            slug,
            "tasks",
            round_number,
            "implementation",
            "gpt-5.4-mini",
            lambda: _run_codex_command(
                ["codex", "exec", "-m", "gpt-5.4-mini", "-s", "workspace-write", prompt_text],
                REPO_ROOT,
            ),
            prompt_chars=len(prompt_text),
            prompt_cap=None,
        )
        rc = result.returncode
        if rc == 124:
            print("[error] codex execution timed out after 3600 seconds", file=sys.stderr)
            rc = 1
    except subprocess.TimeoutExpired:
        print("[error] codex execution timed out after 3600 seconds", file=sys.stderr)
        rc = 1
    finally:
        revert_protected_files()
        prompt_path.unlink(missing_ok=True)
    return rc


def _run_parallel(slug: str, group: dict, max_workers: int = 4) -> int:
    status = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        print(
            f"[error] unable to check working tree status: {status.stderr.strip() or status.stdout.strip() or 'git status failed'}",
            file=sys.stderr,
        )
        return 1
    if status.stdout.strip():
        print("[error] working tree must be clean before implement", file=sys.stderr)
        return 1

    head_result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if head_result.returncode != 0:
        print(
            f"[error] unable to capture base commit: {head_result.stderr.strip() or head_result.stdout.strip() or 'git rev-parse failed'}",
            file=sys.stderr,
        )
        return 1
    base_sha = head_result.stdout.strip()
    round_number = _implementation_round(slug)

    worktree_paths: list[Path] = []
    prompt_paths: list[Path] = []
    failure_message = ""
    failure = False
    commits_by_task: dict[int, list[str]] = {}
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures: dict[concurrent.futures.Future, int] = {}
        tasks = list(group.get("tasks") or [])
        file_scope = list(group.get("files") or [])
        for i, task in enumerate(tasks):
            try:
                worktree_path = create_worktree(slug, i)
                worktree_paths.append(worktree_path)
                prompt_path = _codex_prompt_path(slug, i)
                prompt_paths.append(prompt_path)
                prompt_text = _build_codex_prompt(slug, i, [task], file_scope)
                def _call(worktree_path=worktree_path, prompt_text=prompt_text):
                    heartbeat_set_activity("codex exec running")
                    return _run_codex_command(
                        ["codex", "exec", "-m", "gpt-5.4-mini", "-s", "workspace-write", prompt_text],
                        worktree_path,
                    )
                futures[
                    executor.submit(
                        lambda _call=_call, prompt_text=prompt_text: record_ai_call(
                            slug,
                            "tasks",
                            round_number,
                            "implementation",
                            "gpt-5.4-mini",
                            _call,
                            prompt_chars=len(prompt_text),
                            prompt_cap=None,
                        ),
                    )
                ] = i
            except Exception as exc:
                failure = True
                if not failure_message:
                    failure_message = f"[error] failed to prepare codex worker {i}: {exc}"
                break

        try:
            for future in concurrent.futures.as_completed(futures):
                i = futures[future]
                try:
                    result = future.result()
                    if result.returncode != 0 and not failure:
                        failure = True
                        failure_message = f"[error] codex worker {i} failed with return code {result.returncode}"
                except Exception as exc:
                    if not failure:
                        failure = True
                        failure_message = f"[error] codex worker {i} failed: {exc}"
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        if failure:
            reset_result = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "reset", "--hard", base_sha],
                capture_output=True,
                text=True,
            )
            if reset_result.returncode != 0:
                print(
                    f"[warn] failed to reset repository to {base_sha[:12]}: {reset_result.stderr.strip() or reset_result.stdout.strip() or 'git reset failed'}",
                    file=sys.stderr,
                )
            print(failure_message, file=sys.stderr)
            return 1

        try:
            for i in range(len(tasks)):
                worktree_path = worktree_paths[i]
                stage_and_commit_if_dirty(worktree_path, f"task {i}: auto-commit")
                commits_by_task[i] = get_new_commits(worktree_path, base_sha)
        except Exception as exc:
            reset_result = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "reset", "--hard", base_sha],
                capture_output=True,
                text=True,
            )
            if reset_result.returncode != 0:
                print(
                    f"[warn] failed to reset repository to {base_sha[:12]}: {reset_result.stderr.strip() or reset_result.stdout.strip() or 'git reset failed'}",
                    file=sys.stderr,
                )
            print(f"[error] failed to collect commits from worker worktrees: {exc}", file=sys.stderr)
            return 1

        all_commits = [c for i in sorted(commits_by_task) for c in commits_by_task[i]]
        cherry_pick_ok, cherry_pick_detail = cherry_pick_commits(all_commits)
        if not cherry_pick_ok:
            reset_result = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "reset", "--hard", base_sha],
                capture_output=True,
                text=True,
            )
            if reset_result.returncode != 0:
                print(
                    f"[warn] failed to reset repository to {base_sha[:12]}: {reset_result.stderr.strip() or reset_result.stdout.strip() or 'git reset failed'}",
                    file=sys.stderr,
                )
            print(f"[error] cherry-pick conflict: {cherry_pick_detail}", file=sys.stderr)
            return 1

        revert_protected_files()
        return 0
    finally:
        remove_all_worktrees(worktree_paths)
        for prompt_path in prompt_paths:
            prompt_path.unlink(missing_ok=True)


@mutates_state("implement")
def command_implement(args: argparse.Namespace) -> int:
    status = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        print(
            f"[error] unable to check working tree status: {status.stderr.strip() or status.stdout.strip() or 'git status failed'}",
            file=sys.stderr,
        )
        return 1
    if status.stdout.strip():
        print("[error] working tree must be clean before implement", file=sys.stderr)
        return 1

    groups = parse_parallel_task_groups(args.slug)
    if not groups:
        print("nothing to implement — all tasks complete or no tasks.md")
        return 0

    lock_fd, lock_err = _acquire_implement_lock(args.slug)
    if lock_fd == -1:
        print(lock_err, file=sys.stderr)
        return 1
    try:
        with HeartbeatEmitter(args.slug, "implement"):
            for group in groups:
                heartbeat_set_activity("codex exec running")
                if not group["parallel"]:
                    if group.get("overlap_warning"):
                        print(f"[warn] {group['overlap_warning']} — running serially", file=sys.stderr)
                    rc = _run_serial(args.slug, group["tasks"])
                else:
                    print(f"[implement] dispatching {len(group['tasks'])} parallel Codex workers...")
                    rc = _run_parallel(args.slug, group, max_workers=args.max_workers)
                if rc != 0:
                    return rc
        return 0
    finally:
        _release_implement_lock(lock_fd)


@mutates_state("parallel")
def command_parallel(args: argparse.Namespace) -> int:
    """Record whether the agent looked for parallel implementation opportunities.

    Enforces that the agent explicitly considered parallelisation before the
    tasks checkpoint. If --found is passed, validates that [P: file...] annotations
    exist in tasks.md and that no two annotated tasks share a file (which would
    cause a conflict at implement time).
    """
    ensure_sync()
    note = (args.note or "").strip()
    if not note:
        raise SystemExit(
            "parallel requires --note explaining what was found or why nothing "
            "was safe to parallelise (e.g. 'all tasks share the schema migration')"
        )
    tasks_path = workflow_dir(args.slug) / "tasks.md"
    if not tasks_path.exists():
        raise SystemExit("parallel requires tasks.md to exist — write tasks first")

    if args.found:
        groups = parse_parallel_task_groups(args.slug)
        parallel_groups = [g for g in groups if g["parallel"]]
        if not parallel_groups:
            raise SystemExit(
                "parallel --found requires [P: file1, file2] annotations on tasks in "
                "tasks.md — no valid parallel task groups detected. Add annotations or "
                "omit --found if no safe parallelism exists."
            )
        for group in groups:
            if group.get("overlap_warning"):
                raise SystemExit(
                    f"parallel --found blocked: {group['overlap_warning']} — "
                    "parallel tasks must not share files. Fix the [P:] annotations "
                    "before recording parallel opportunities."
                )
        print(f"[parallel] {len(parallel_groups)} parallel group(s) validated, no file conflicts")

    def mutate(state: dict) -> None:
        state[PARALLEL_ANALYSIS_KEY] = {
            "reviewed": True,
            "found": bool(args.found),
            "note": note,
            "updated_at": int(time.time()),
        }

    update_workflow_state(args.slug, mutate)
    result = "opportunities found and validated" if args.found else "no safe opportunities found"
    print(f"[parallel] analysis recorded: {result}")
    print(f"[parallel] note: {note}")
    _emit_next_action(args.slug, "parallel analysis")
    return 0
