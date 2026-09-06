#!/usr/bin/env python3
"""command_implement and command_parallel implementations."""
import argparse
import concurrent.futures
import subprocess
import sys
import time
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
    get_changed_files,
    stage_and_commit_if_dirty,
    cherry_pick_commits,
    check_clean_tree,
    prune_orphaned_worktrees,
    PROTECTED_FILES,
    _git_head_sha,
)
from factory_codex_runner import (  # noqa: E402
    RC_HARD_TIMEOUT,
    RC_IDLE_TIMEOUT,
    RC_NOT_FOUND,
    DEFAULT_HARD_TIMEOUT,
    DEFAULT_IDLE_TIMEOUT,
    run_codex,
    run_codex_with_retry,
)

from factory_runlock import acquire_run_lock, release_run_lock, run_lock_path  # noqa: E402
from factory_stages import (  # noqa: E402
    _is_diff_bookkeeping_path,
    checkpoint_progress_state,
    parse_parallel_task_groups,
    unsliced_tasks_error,
)
from factory_parallel import prior_slice_unbuilt, slice_task_declared_files  # noqa: E402

from factory_emit import _emit_next_action  # noqa: E402
from factory_mutating import mutates_state  # noqa: E402


def _implement_lock_path(slug: str) -> Path:
    return run_lock_path(slug, "implement")


def _acquire_implement_lock(slug: str) -> tuple[int, str]:
    """Acquire the per-slug implement run lock (see factory_runlock)."""
    return acquire_run_lock(slug, "implement", "implement")


def _release_implement_lock(fd: int) -> None:
    release_run_lock(fd)


def _codex_specs_dir(slug: str) -> Path:
    # Per the SKILL's Background Dispatch Discipline (Rule 2): keep dispatch
    # specs and transcripts out of /tmp (which is garbage-collected) and inside
    # the run directory instead, for an auditable, GC-safe record per slice.
    return workflow_dir(slug) / "codex-specs"


def _codex_prompt_path(slug: str, i: int) -> Path:
    return _codex_specs_dir(slug) / f"slice-{i}.md"


def _codex_log_path(slug: str, i: int) -> Path:
    return _codex_specs_dir(slug) / f"slice-{i}.codex.log"


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


def _run_codex_command(
    command: list[str], cwd: Path, *, slug: str, index: int
) -> subprocess.CompletedProcess:
    # Delegate to the shared runner so implement and dispatch-codex use the same
    # idle/no-output watchdog instead of a blind 60-minute blocking wall. The
    # liveness status is forwarded to the heartbeat so a stall is visible.
    return run_codex(
        command,
        cwd,
        log_path=_codex_log_path(slug, index),
        label=f"implement[{slug}#{index}]",
        on_status=heartbeat_set_activity,
    )


def _classify_codex_rc(rc: int) -> int:
    """Map a runner stall/not-found sentinel to a printed error + a failure rc."""
    if rc == RC_IDLE_TIMEOUT:
        print(
            f"[error] codex stalled (no output for {int(DEFAULT_IDLE_TIMEOUT)}s); "
            "retried and still stalled — failing the slice",
            file=sys.stderr,
        )
        return 1
    if rc == RC_HARD_TIMEOUT:
        print(
            f"[error] codex exceeded the {int(DEFAULT_HARD_TIMEOUT)}s overall cap",
            file=sys.stderr,
        )
        return 1
    if rc == RC_NOT_FOUND:
        print(
            "[error] codex CLI not found on PATH; install or activate it before implement",
            file=sys.stderr,
        )
        return 1
    return rc


def _record_unsliced_annotation(slug: str) -> None:
    """Record that the operator accepted an unsliced build via --allow-unsliced.

    Mirrors the cap_accepted annotation the checkpoint command writes: an
    append-only entry in state.json's annotations[] so the bypass is auditable
    per run.
    """
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    def mutate(state: dict) -> None:
        annotations = state.setdefault("annotations", [])
        annotations.append(
            {
                "stage": "implement",
                "ts": ts,
                "type": "unsliced_accepted",
                "reason": (
                    "operator passed --allow-unsliced: building without "
                    "[CHECKPOINT] slice boundaries (single-slice feature)"
                ),
                "marker_count": 0,
            }
        )

    update_workflow_state(slug, mutate)


def _git_capture(git_args: list[str]) -> list[str] | None:
    """Run git in REPO_ROOT; return stripped non-empty stdout lines, or None on failure."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *git_args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _porcelain_paths(lines: list[str]) -> list[str]:
    """Repo-relative paths from ``git status --porcelain`` lines (renames → new path)."""
    paths: list[str] = []
    for line in lines:
        # Porcelain v1: "XY path" (or "XY old -> new" for renames).
        path = line[2:].strip() if len(line) > 2 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            paths.append(path)
    return paths


def _real_slice_paths(paths: list[str]) -> list[str]:
    """Drop paths that are never evidence of slice work.

    The factory's own run bookkeeping changes on every dispatch — the prompt
    and transcript under codex-specs/, heartbeat writes to state.json (all
    under docs/workflow/feature-runs/), STATUS.md — and PROTECTED_FILES are
    reverted after every dispatch anyway. Counting either as "the slice was
    built" would let a no-op dispatch slip through the completion gate.
    """
    protected = set(PROTECTED_FILES)
    return [
        path
        for path in paths
        if path not in protected and not _is_diff_bookkeeping_path(path)
    ]


def _short_task(text: str, limit: int = 90) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _slice_completion_error(slug: str, index: int, base_sha: str) -> str | None:
    """Hard slice-completion gate for a serial dispatch that reported success.

    Codex has returned exit 0 while silently skipping the work (user-roles run:
    3 of 5 slices never built, nothing checked). Fail closed when nothing
    verifiable was produced: no new commit AND no working-tree changes, or
    commits whose combined diff against the pre-dispatch HEAD is empty. Run
    bookkeeping and protected files never count as evidence — the dispatch
    prompt/transcript and heartbeat state.json change on every dispatch, so
    without _real_slice_paths this gate could never fire. Returns the error
    message, or None when the dispatch demonstrably changed something. A git
    failure during verification also fails the gate — an unverifiable slice
    must not advance silently.
    """
    log_hint = f"Inspect the codex transcript at {_codex_log_path(slug, index)}."
    commits = _git_capture(["log", "--format=%H", f"{base_sha}..HEAD"])
    if commits is None:
        return (
            f"could not verify slice completion (git log {base_sha[:12]}..HEAD failed) — "
            f"refusing to treat the dispatch as done. {log_hint}"
        )
    # -uall lists untracked files individually; the default collapses them to
    # their deepest untracked directory, which would defeat the bookkeeping
    # prefix filter (e.g. "?? docs/" instead of the codex-specs files).
    status_lines = _git_capture(["status", "--porcelain", "-uall"])
    if status_lines is None:
        return (
            "could not verify slice completion (git status failed) — refusing to "
            f"treat the dispatch as done. {log_hint}"
        )
    dirty = _real_slice_paths(_porcelain_paths(status_lines))
    if not commits and not dirty:
        return (
            "codex exited 0 but produced NO new commit and NO working-tree changes "
            "(beyond run bookkeeping) — the slice was not implemented. This is the "
            f"silent-skip failure mode: do not advance the checkpoint. {log_hint}"
        )
    if commits and not dirty:
        diff_files = _git_capture(["diff", "--name-only", f"{base_sha}..HEAD"])
        if diff_files is None:
            return (
                f"could not verify slice completion (git diff {base_sha[:12]}..HEAD "
                f"failed) — refusing to treat the dispatch as done. {log_hint}"
            )
        if not _real_slice_paths(diff_files):
            return (
                f"codex exited 0 and committed, but the slice diff {base_sha[:12]}..HEAD "
                f"is EMPTY (beyond run bookkeeping) — the commit(s) implemented nothing. "
                f"{log_hint}"
            )
    return None


def _parallel_completion_error(
    commits_by_task: dict[int, list[str]],
    files_by_task: dict[int, set[str]],
    tasks: list[str],
) -> str | None:
    """Per-worker completion gate for a parallel dispatch that reported success.

    Each worker's worktree was auto-committed if dirty before this runs, so a
    worker with no new commits produced nothing at all, and a worker with
    commits but an empty changed-file set (bookkeeping and protected files
    don't count — see _real_slice_paths) produced an empty diff. Either way
    its task was silently skipped — fail closed instead of cherry-picking a
    partial slice.
    """
    for i in range(len(tasks)):
        task = _short_task(tasks[i])
        if not commits_by_task.get(i):
            return (
                f"parallel codex worker {i} exited 0 but produced no commit and no "
                f"working-tree changes — its task was silently skipped: {task}"
            )
        if not _real_slice_paths(sorted(files_by_task.get(i, set()))):
            return (
                f"parallel codex worker {i} committed an EMPTY diff (beyond run "
                f"bookkeeping) — its task was silently skipped: {task}"
            )
    return None


def _changed_paths_since(base_sha: str) -> set[str] | None:
    """Paths changed since base_sha: committed diff plus any working-tree changes.

    -uall so untracked files appear individually (a collapsed "?? dir/" entry
    would never equal a task's declared file path).
    """
    committed = _git_capture(["diff", "--name-only", f"{base_sha}..HEAD"])
    if committed is None:
        return None
    porcelain = _git_capture(["status", "--porcelain", "-uall"])
    if porcelain is None:
        return None
    return set(committed) | set(_porcelain_paths(porcelain))


def _coverage_report(
    task_files: list[tuple[str, list[str]]], changed: set[str]
) -> tuple[list[str], int]:
    """Per-task coverage checklist lines + count of tasks with uncovered files.

    A declared path counts as covered when the diff touched it exactly or
    touched anything under it (directory declarations). Pure so it is unit-
    testable; the caller decides how loudly to print.
    """

    def covered(declared: str) -> bool:
        norm = declared.rstrip("/")
        return any(path == norm or path.startswith(norm + "/") for path in changed)

    lines: list[str] = []
    gaps = 0
    for task, paths in task_files:
        if not paths:
            continue
        missing = [p for p in paths if not covered(p)]
        if missing:
            gaps += 1
            lines.append(f"  ⚠ {_short_task(task)} — missing: {', '.join(missing)}")
        else:
            lines.append(f"  ✓ {_short_task(task)} — {len(paths)} file(s) touched")
    return lines, gaps


def _print_slice_coverage(slug: str, base_sha: str) -> None:
    """Advisory per-task file-coverage report after a successful slice dispatch.

    Cross-checks the file paths each task names against what actually changed.
    Loud warning on gaps but NEVER a hard failure: task prose names files too
    loosely to gate on (renames, paths mentioned as context, helper files).
    """
    index = checkpoint_progress_state(slug)["index"]
    task_files = [
        (task, paths) for task, paths in slice_task_declared_files(slug, index) if paths
    ]
    if not task_files:
        return
    changed = _changed_paths_since(base_sha)
    if changed is None:
        print(
            "[implement] warn: could not diff against the pre-dispatch HEAD — "
            "skipping the per-task coverage report",
            file=sys.stderr,
        )
        return
    lines, gaps = _coverage_report(task_files, changed)
    print(f"[implement] slice {index} per-task file coverage:")
    for line in lines:
        print(line)
    if gaps:
        print(
            f"[implement] WARNING: {gaps} of {len(task_files)} task(s) name files "
            "with no matching change in this slice's diff. Codex may have skipped "
            "them — verify each ⚠ task before checkpointing. (Not fatal: task "
            "prose can name files loosely.)",
            file=sys.stderr,
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
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
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
        "Implement ONLY the tasks listed above for this slice. Do not implement "
        "tasks from other slices and do not work ahead. Commit your changes when done.\n"
        "DO NOT MODIFY: CLAUDE.md, AGENTS.md, MEMORY.md, the docs/ design/architecture docs, "
        "or any file outside this slice's declared scope. The spec/plan above are "
        "context only — they describe the whole feature, not your slice; build just the tasks listed.\n"
    )
    prompt_path.write_text(prompt_text, encoding="utf-8")
    return prompt_text


def _run_serial(slug: str, tasks: list[str]) -> int:
    base_sha = _git_head_sha(REPO_ROOT)
    if not base_sha:
        print(
            "[error] unable to capture the pre-dispatch HEAD — cannot verify slice "
            "completion afterwards, refusing to dispatch",
            file=sys.stderr,
        )
        return 1
    prompt_text = _build_codex_prompt(slug, 0, tasks, [])
    round_number = _implementation_round(slug)
    command = ["codex", "exec", "-m", "gpt-5.4-mini", "-s", "workspace-write", prompt_text]

    def _dispatch() -> subprocess.CompletedProcess:
        heartbeat_set_activity("codex exec running")
        return record_ai_call(
            slug,
            "tasks",
            round_number,
            "implementation",
            "gpt-5.4-mini",
            lambda: _run_codex_command(command, REPO_ROOT, slug=slug, index=0),
            prompt_chars=len(prompt_text),
            prompt_cap=None,
        )

    try:
        result = run_codex_with_retry(_dispatch, label=f"implement[{slug}#0]")
        rc = _classify_codex_rc(result.returncode)
        if rc == 0:
            completion_error = _slice_completion_error(slug, 0, base_sha)
            if completion_error:
                print(f"[error] {completion_error}", file=sys.stderr)
                return 1
        return rc
    finally:
        revert_protected_files()


def _detect_parallel_file_overlap(files_by_task: dict[int, set[str]]) -> str | None:
    """Return a message if two parallel workers changed the same file.

    [P:] annotations are validated for disjoint file scopes at declaration time,
    but a Codex worker can still write outside its declared scope at runtime. Two
    workers touching one file breaks the disjoint-write assumption the parallel
    path relies on, so detect it before cherry-picking and fail loudly instead of
    producing a confusing mid-cherry-pick conflict. Protected files (reverted
    after every worker anyway) are excluded.
    """
    protected = set(PROTECTED_FILES)
    owner: dict[str, int] = {}
    for i in sorted(files_by_task):
        for path in files_by_task[i]:
            if path in protected:
                continue
            if path in owner and owner[path] != i:
                return f"{path} (written by tasks {owner[path]} and {i})"
            owner[path] = i
    return None


def _run_parallel(slug: str, group: dict, max_workers: int = 4) -> int:
    clean, clean_err = check_clean_tree(REPO_ROOT, what="implement")
    if not clean:
        print(f"[error] {clean_err}", file=sys.stderr)
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
                prompt_text = _build_codex_prompt(slug, i, [task], file_scope)
                command = ["codex", "exec", "-m", "gpt-5.4-mini", "-s", "workspace-write", prompt_text]

                def _dispatch(worktree_path=worktree_path, command=command, prompt_text=prompt_text, i=i):
                    def _once():
                        heartbeat_set_activity("codex exec running")
                        return record_ai_call(
                            slug,
                            "tasks",
                            round_number,
                            "implementation",
                            "gpt-5.4-mini",
                            lambda: _run_codex_command(command, worktree_path, slug=slug, index=i),
                            prompt_chars=len(prompt_text),
                            prompt_cap=None,
                        )

                    return run_codex_with_retry(_once, label=f"implement[{slug}#{i}]")

                futures[executor.submit(_dispatch)] = i
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

        files_by_task: dict[int, set[str]] = {}
        try:
            for i in range(len(tasks)):
                worktree_path = worktree_paths[i]
                stage_and_commit_if_dirty(worktree_path, f"task {i}: auto-commit")
                commits_by_task[i] = get_new_commits(worktree_path, base_sha)
                files_by_task[i] = set(get_changed_files(worktree_path, base_sha))
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

        # Slice-completion gate: every worker that reported success must have
        # produced a real commit with a non-empty diff. A silent no-op worker
        # means its task was skipped — fail closed before cherry-picking a
        # partial slice.
        completion_error = _parallel_completion_error(
            commits_by_task, files_by_task, [str(t) for t in tasks]
        )
        if completion_error:
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
            print(f"[error] {completion_error}", file=sys.stderr)
            return 1

        # Runtime [P:] safety: parallel workers must write disjoint file sets.
        # Detect a violation here rather than letting it surface as a confusing
        # cherry-pick conflict (or, worse, silently merge two unrelated edits).
        overlap = _detect_parallel_file_overlap(files_by_task)
        if overlap:
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
            print(
                f"[error] parallel workers wrote overlapping files: {overlap} — the [P:] "
                "file scopes were not disjoint at runtime. Fix the [P:] annotations in "
                "tasks.md or run these tasks serially.",
                file=sys.stderr,
            )
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


@mutates_state("implement")
def command_implement(args: argparse.Namespace) -> int:
    # Reclaim any per-slice worktrees left registered by a prior killed run
    # (their cleanup finally-block never ran). Safe: only prunes this slug's
    # worktrees whose owner pid is dead.
    reclaimed = prune_orphaned_worktrees(args.slug)
    if reclaimed:
        print(f"[implement] pruned {len(reclaimed)} orphaned worktree(s) from a prior run", file=sys.stderr)

    clean, clean_err = check_clean_tree(REPO_ROOT, what="implement")
    if not clean:
        print(f"[error] {clean_err}", file=sys.stderr)
        return 1

    groups = parse_parallel_task_groups(args.slug)
    if not groups:
        print("nothing to implement — all tasks complete or no tasks.md")
        return 0

    # Fail closed on an unsliced tasks.md: zero [CHECKPOINT] markers collapses
    # every slice into one giant dispatch (exp 10, user-roles incidents).
    # --allow-unsliced is the audited escape hatch for a genuinely single-slice feature.
    unsliced = unsliced_tasks_error(args.slug)
    if unsliced:
        if getattr(args, "allow_unsliced", False):
            _record_unsliced_annotation(args.slug)
            print(
                "[implement] --allow-unsliced: building without [CHECKPOINT] slice "
                "boundaries (unsliced_accepted annotation recorded in state.json)",
                file=sys.stderr,
            )
        else:
            print(f"[error] {unsliced}", file=sys.stderr)
            return 1

    # Guard against checkpoint-index drift: never dispatch a slice when the
    # prior slice was never built (e.g. a repair that wrongly advanced the index).
    drift_error = prior_slice_unbuilt(
        args.slug, checkpoint_progress_state(args.slug)["index"]
    )
    if drift_error:
        print(f"[error] {drift_error}", file=sys.stderr)
        return 1

    lock_fd, lock_err = _acquire_implement_lock(args.slug)
    if lock_fd == -1:
        print(lock_err, file=sys.stderr)
        return 1
    try:
        # Captured before any dispatch so the advisory per-task coverage report
        # can compare the slice's declared files against what actually changed.
        coverage_base_sha = _git_head_sha(REPO_ROOT)
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
        if coverage_base_sha:
            _print_slice_coverage(args.slug, coverage_base_sha)
        else:
            print(
                "[implement] warn: could not capture the pre-dispatch HEAD — "
                "skipping the per-task coverage report",
                file=sys.stderr,
            )
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
