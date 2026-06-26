#!/usr/bin/env python3
"""command_checkpoint implementation."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    REPO_ROOT,
    CHECKPOINT_PROGRESS_KEY,
    DIRTY_OVERRIDE_KEY,
    PARALLEL_ANALYSIS_KEY,
    atomic_json_write,
    normalized_repo_path,
    update_stage_state,
    with_locked_state,
    workflow_dir,
    load_scope_manifest,
    reviews_dir,
    scope_manifest_path,
    checkpoint_manifest_path,
    default_artifact_path,
    load_workflow_state,
    update_workflow_state,
    discovery_state,
    blocking_unresolved_items,
    discovery_blockers_are_malformed,
    save_scope_manifest,
)

from factory_git import (  # noqa: E402
    _sha_is_valid_ancestor,
    _git_head_sha,
    ensure_sync,
    revert_protected_files,
)

from factory_stages import (  # noqa: E402
    HARD_DIFF_ARTIFACT_MAX_CHARS,
    LARGE_DIFF_RERUN_WARN_CHARS,
    CHECKPOINT_STAGES,
    STAGE_ARTIFACT_HEADINGS,
    _CHECKPOINT_MARKER_RE,
    parse_checkpoint_markers,
    checkpoint_progress_state,
    _default_checkpoint_progress,
    artifact_has_meaningful_content,
    diff_review_budget_state,
    preferred_diff_base_ref,
    prerequisite_failure,
    reconciliation_state,
    stage_manifest_state,
)

from factory_review import (  # noqa: E402
    WRITE_DIFF,
    REPAIR,
    SMALL_TASK_SET_THRESHOLD,
    trim_detail,
    required_reviews,
    resolved_review_policy,
    checkpoint_manifest,
    run_checkpoint_fallback,
    record_checkpoint_fallback,
    _advance_checkpoint_progress,
    _AUTO_CONTEXT_MAX_FILES,
    _AUTO_CONTEXT_EXTENSIONS,
    _AUTO_CONTEXT_PATH_RE,
    _extract_file_paths_from_artifact,
)

from factory_review_specs import (  # noqa: E402
    _count_findings_by_severity,
    _strip_non_finding_markdown,
    _findings_scan_text,
    _SEVERITY_ORDER,
    DIFF_REVIEW_DEFAULT_MIN_CHANGED_LINES,
    count_changed_diff_lines,
    resolve_reviewer_override,
)

from factory_emit import _emit_next_action  # noqa: E402
from factory_heartbeat import HeartbeatEmitter, set_activity as heartbeat_set_activity  # noqa: E402
from factory_next_action import recommended_next_action  # noqa: E402
from workflow_utils import normalized_artifact_hash  # noqa: E402
from factory_mutating import mutates_state  # noqa: E402

MAX_ADVERSARIAL_ROUNDS = 3


def _ensure_stage_state_blob(state: dict, stage: str) -> dict:
    stages = state.setdefault("stages", {})
    stage_state = stages.get(stage)
    if not isinstance(stage_state, dict):
        stage_state = {}
    stage_state.setdefault("adversarial_rounds", 0)
    stage_state.setdefault("annotations", [])
    stage_state.setdefault("adversarial_sha_history", [])
    stage_state.setdefault("initial_sha", "")
    stages[stage] = stage_state
    try:
        schema_version = int(state.get("schema_version", 1))
    except (TypeError, ValueError):
        schema_version = 1
    if schema_version < 2:
        state["schema_version"] = 2
    return stage_state


def _stage_int(stage_state: dict, key: str) -> int:
    try:
        return int(stage_state.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _checkpoint_next_action_payload(slug: str, state: dict, completed_stage: str) -> dict[str, object]:
    stages = {stage: stage_manifest_state(slug, stage) for stage in CHECKPOINT_STAGES}
    recon_ok, _ = reconciliation_state(slug)
    next_action = recommended_next_action(slug, state, stages, recon_ok)
    return {
        "next": next_action,
        "reason": f"{completed_stage} checkpoint completed",
        "blockers": [],
    }


def _persist_last_action_result(slug: str, payload: dict[str, object]) -> None:
    update_workflow_state(
        slug,
        lambda state: state.__setitem__("last_action_result", payload),
    )


def _rollback_adversarial_round(slug: str, stage: str) -> None:
    state = load_workflow_state(slug)
    stage_state = state.get("stages", {}).get(stage, {})
    if not isinstance(stage_state, dict):
        stage_state = {}
    current_rounds = _stage_int(stage_state, "adversarial_rounds")
    update_stage_state(
        slug,
        stage,
        {"adversarial_rounds": max(current_rounds - 1, 0)},
    )


def _print_findings_summary(slug: str, stage: str, reviews_ran: list[dict] | None) -> None:
    """Print a non-blocking findings summary to stderr after a successful checkpoint.

    When no reviews were configured for the stage, prints a single informational
    line. When reviews ran, scans each review file for actionable findings and
    prints per-file severity counts. Files with zero findings across all
    severities are omitted. The inspect section lists full repo-relative paths
    so the operator can copy-paste directly into cat or an editor.
    """
    print(f"[ff] checkpoint completed for stage={stage}", file=sys.stderr)

    if reviews_ran is None or len(reviews_ran) == 0:
        print(f"[ff] no default reviews configured for this stage", file=sys.stderr)
        return

    rev_dir = reviews_dir(slug)
    # Glob review files for this stage (not .raw.txt / .stderr.txt etc.)
    review_files = sorted(rev_dir.glob(f"{stage}.*.review.md"))
    if not review_files:
        print("[ff] reviews ran, no actionable findings raised", file=sys.stderr)
        return

    files_with_findings: list[tuple[Path, dict[str, int]]] = []
    for review_path in review_files:
        try:
            raw = _strip_non_finding_markdown(_findings_scan_text(review_path.read_text(encoding="utf-8"))).lower()
        except OSError:
            continue
        counts = _count_findings_by_severity(raw)
        if any(v > 0 for v in counts.values()):
            files_with_findings.append((review_path, counts))

    if not files_with_findings:
        print("[ff] reviews ran, no actionable findings raised", file=sys.stderr)
        return

    print("[ff] findings raised:", file=sys.stderr)
    for review_path, counts in files_with_findings:
        parts = [f"{counts[sev]} {sev}" for sev in _SEVERITY_ORDER if counts.get(sev, 0) > 0]
        print(f"[ff]   {review_path.name}: {' · '.join(parts)}", file=sys.stderr)

    print("[ff] inspect:", file=sys.stderr)
    for review_path, _ in files_with_findings:
        repo_rel = f"docs/workflow/feature-runs/{slug}/reviews/{review_path.name}"
        print(f"[ff]   {repo_rel}", file=sys.stderr)


def _prior_stage_for_checkpoint(stage: str) -> str | None:
    prior_stage_by_stage = {
        "plan": "spec",
        "tasks": "plan",
        "diff": "tasks",
    }
    return prior_stage_by_stage.get(stage)


def _gc_review_intermediates(slug: str, stage: str, keep: bool) -> list[Path]:
    if keep:
        return []
    reviews = reviews_dir(slug)
    deleted: list[Path] = []
    for suffix in (
        ".narrowed.txt",
        ".narrowed.json",
        ".raw.txt",
        ".stdout.txt",
        ".stderr.txt",
    ):
        for path in sorted(reviews.glob(f"{stage}.*{suffix}")):
            path.unlink(missing_ok=True)
            deleted.append(path)
    return deleted


def _effective_auto_context(args: argparse.Namespace) -> bool:
    if getattr(args, "auto_context", False) and getattr(args, "no_auto_context", False):
        raise SystemExit("--auto-context cannot be combined with --no-auto-context")
    if getattr(args, "auto_context", False):
        return True
    if getattr(args, "no_auto_context", False):
        return False
    return args.stage in {"spec", "tasks"}


@mutates_state("checkpoint")
def command_checkpoint(args: argparse.Namespace) -> int:
    fast = getattr(args, "fast", False)
    if fast and args.stage != "diff":
        raise SystemExit("--fast requires --stage diff")
    auto_context_enabled = _effective_auto_context(args)

    ensure_sync()
    root = workflow_dir(args.slug)
    reviews = reviews_dir(args.slug)
    root.mkdir(parents=True, exist_ok=True)
    reviews.mkdir(parents=True, exist_ok=True)
    keep_intermediates = getattr(args, "keep_intermediates", False)
    with with_locked_state(args.slug):
        _gc_review_intermediates(args.slug, args.stage, keep_intermediates)

    if not fast:
        prereq_error = prerequisite_failure(args.slug, args.stage)
        if prereq_error:
            raise SystemExit(prereq_error)
    if not fast and args.stage == "tasks":
        parallel = load_workflow_state(args.slug).get(PARALLEL_ANALYSIS_KEY, {})
        if not parallel.get("reviewed"):
            raise SystemExit(
                "tasks checkpoint requires parallel analysis first; "
                "run 'parallel --slug <slug> --note \"...\"' to record whether "
                "parallel implementation opportunities exist in tasks.md"
            )
    if not fast and args.stage == "spec":
        discovery = discovery_state(args.slug)
        blocking = blocking_unresolved_items(discovery)
        if blocking:
            if discovery_blockers_are_malformed(discovery):
                raise SystemExit(
                    "spec checkpoint requires discovery state to be repaired first; "
                    "use discover --clear to reset malformed discovery state"
                )
            raise SystemExit(
                "spec checkpoint requires discovery unresolved items to be resolved or deferred first; "
                "use discover --resolve or discover --defer before checkpointing spec"
            )
        if discovery.get("required") and not discovery.get("complete"):
            raise SystemExit(
                "spec checkpoint requires discovery to be complete first; "
                "record the remaining questions and assumptions with the discover command"
            )
    policy = resolved_review_policy(args.slug, args)
    context_paths = [normalized_repo_path(path, "context path") for path in args.context]
    allow_dirty_paths = [normalized_repo_path(path, "allow-dirty path") for path in args.allow_dirty_path]
    prior_diff_budget = diff_review_budget_state(args.slug) if args.stage == "diff" else None

    # Capture HEAD SHA now — before any reviews launch — so it reflects the
    # state of the repo at diff generation time, not after verify completes.
    pending_head_sha: str = _git_head_sha(REPO_ROOT) or "" if args.stage == "diff" else ""

    if args.stage == "diff":
        # Determine whether a checkpoint-scoped base_ref should override the default.
        marker_count, current_markers_sha = parse_checkpoint_markers(args.slug)
        if marker_count == 0:
            print(
                "warning: tasks.md has no [CHECKPOINT] markers — diff review will cover "
                "the full branch. Add [CHECKPOINT] markers to tasks.md to scope each "
                "diff review to a single implementation slice.",
                file=sys.stderr,
            )
        progress = checkpoint_progress_state(args.slug)
        use_checkpoint_base = False
        if marker_count > 0 and not args.base_ref:
            idx = progress["index"]
            stored_sha = progress["markers_sha"]
            last_head = progress["last_diff_head_sha"]
            if idx == 0:
                pass  # first slice — use branch base as normal
            elif idx >= marker_count:
                print(
                    f"warning: checkpoint_progress.index ({idx}) >= marker count ({marker_count}); "
                    "tasks.md may have had markers removed — resetting checkpoint progress and using branch base.",
                    file=sys.stderr,
                )
                update_workflow_state(
                    args.slug,
                    lambda s: s.__setitem__(CHECKPOINT_PROGRESS_KEY, _default_checkpoint_progress()),
                )
                args.base_ref = None
            elif stored_sha != current_markers_sha:
                print(
                    "warning: [CHECKPOINT] marker lines changed since last checkpoint — "
                    "resetting checkpoint progress and using branch base.",
                    file=sys.stderr,
                )
                update_workflow_state(
                    args.slug,
                    lambda s: s.__setitem__(CHECKPOINT_PROGRESS_KEY, _default_checkpoint_progress()),
                )
                args.base_ref = None
            elif _sha_is_valid_ancestor(last_head):
                use_checkpoint_base = True
                args.base_ref = last_head
            else:
                print(
                    f"warning: last_diff_head_sha {last_head[:12] if last_head else '(empty)'} is not a "
                    "valid ancestor of HEAD (possible rebase/amend) — "
                    "resetting checkpoint progress and using branch base.",
                    file=sys.stderr,
                )
                update_workflow_state(
                    args.slug,
                    lambda s: s.__setitem__(CHECKPOINT_PROGRESS_KEY, _default_checkpoint_progress()),
                )
                args.base_ref = None
        _ = use_checkpoint_base  # consumed above via args.base_ref
        args.base_ref = preferred_diff_base_ref(args.slug, args.base_ref)

    scope_manifest = scope_manifest_path(args.slug)
    if args.stage == "diff" and args.path:
        scope_manifest = save_scope_manifest(args.slug, args.path)

    if args.stage == "diff":
        # scope.json records the scoped paths plus the feature-run dir as
        # expected-dirty (see save_scope_manifest). Flow those into the diff's
        # dirty-tolerance and the recorded manifest. Without this they were
        # dropped: every diff checkpoint demanded a manual --allow-dirty-path for
        # paths the scope already declared, and the manifest persisted
        # allowed_dirty_paths: []. CLI --allow-dirty-path values still merge in.
        scope_dirty = load_scope_manifest(args.slug).get("allowed_dirty_paths", [])
        allow_dirty_paths = list(dict.fromkeys([*allow_dirty_paths, *scope_dirty]))

    artifact_path = Path(args.artifact).resolve() if args.artifact else default_artifact_path(args.slug, args.stage)

    # Auto-add files mentioned in the spec/artifact as context so reviewers
    # can verify assumptions against real code rather than generating unfounded findings.
    if auto_context_enabled:
        auto_context = _extract_file_paths_from_artifact(artifact_path, REPO_ROOT)
        for p in auto_context:
            if len(context_paths) >= _AUTO_CONTEXT_MAX_FILES:
                break
            if p not in context_paths:
                context_paths.append(p)

    if args.stage in STAGE_ARTIFACT_HEADINGS and not artifact_has_meaningful_content(args.stage, artifact_path):
        raise SystemExit(
            f"{args.stage} checkpoint requires a non-stub artifact first: {artifact_path}"
        )
    if args.stage == "diff":
        diff_budget = prior_diff_budget or diff_review_budget_state(args.slug)
        if (
            diff_budget
            and diff_budget.get("codex_review_present")
            and diff_budget.get("large_artifact")
            and diff_budget.get("artifact_exists")
            and diff_budget.get("artifact_changed_since_codex")
            and not args.allow_large_diff_rerun
        ):
            raise SystemExit(
                "Large diff rerun would regenerate the Codex review for a "
                f"{len(artifact_path.read_text(encoding='utf-8')) if artifact_path.exists() else 0}-character artifact. "
                "Batch more implementation fixes before rerunning the diff checkpoint with "
                f"checkpoint --slug {args.slug} --stage diff --max-artifact-chars <N> --allow-large-diff-rerun, "
                "or pass --allow-large-diff-rerun if this spend is intentional."
            )
    if args.stage == "diff" and not args.use_existing_artifact:
        if not scope_manifest.exists():
            raise SystemExit("Diff checkpoint requires a saved scope manifest or explicit --path values")
        write_diff_result = subprocess.run(
            [
                sys.executable,
                str(WRITE_DIFF),
                "--repo",
                str(REPO_ROOT),
                "--output",
                str(artifact_path),
                "--path-manifest",
                str(scope_manifest),
                *sum((["--allow-dirty-path", path] for path in allow_dirty_paths), []),
                *([] if not args.base_ref else ["--base-ref", args.base_ref]),
            ],
            text=True,
            capture_output=True,
        )
        if write_diff_result.returncode != 0:
            # Surface the FULL underlying error (e.g. "Canonical diff is empty for
            # the requested scope", or a real traceback) instead of trimming it to
            # a generic dirty-path hint — the true cause is often not a dirty path.
            detail = (write_diff_result.stderr or write_diff_result.stdout or "diff generation failed").strip()
            raise SystemExit(
                "Diff generation failed:\n"
                f"{detail}\n\n"
                "If a path outside the diff scope is intentionally dirty, re-run with "
                f"checkpoint --slug {args.slug} --stage diff --allow-dirty-path <path> "
                "for each such path."
            )
        diff_meta_path = artifact_path.with_suffix(artifact_path.suffix + ".json")
        if diff_meta_path.exists():
            diff_meta = json.loads(diff_meta_path.read_text(encoding="utf-8"))
            args.base_ref = diff_meta.get("git_base_ref") or args.base_ref
    elif not artifact_path.exists():
        raise SystemExit(f"Artifact does not exist: {artifact_path}")

    if args.stage == "diff":
        diff_meta_path = artifact_path.with_suffix(artifact_path.suffix + ".json")
        if diff_meta_path.exists():
            diff_meta = json.loads(diff_meta_path.read_text(encoding="utf-8"))
            args.base_ref = diff_meta.get("git_base_ref") or args.base_ref
            if args.use_existing_artifact:
                recorded_head = diff_meta.get("git_head_sha", "")
                if recorded_head:
                    current_head = _git_head_sha(REPO_ROOT)
                    if current_head and current_head != recorded_head:
                        raise SystemExit(
                            f"Existing diff artifact is stale: recorded HEAD {recorded_head[:12]} "
                            f"does not match current HEAD {current_head[:12]}. "
                            "Drop --use-existing-artifact to regenerate the diff, "
                            "or pass --base-ref if you intentionally want to review an older slice."
                        )

    if args.stage == "diff" and artifact_path.exists():
        diff_text = artifact_path.read_text(encoding="utf-8")
        if len(diff_text) > HARD_DIFF_ARTIFACT_MAX_CHARS:
            raise SystemExit(
                f"Diff artifact exceeds hard cap ({len(diff_text)} > {HARD_DIFF_ARTIFACT_MAX_CHARS}). "
                "Split the review scope into smaller workflow paths or use a smaller diff artifact. "
                "To intentionally override, pass --max-artifact-chars <N> --allow-large-diff-rerun."
            )
        if len(diff_text) >= LARGE_DIFF_RERUN_WARN_CHARS:
            print(
                "warning: large diff artifact detected; future diff reruns will regenerate "
                "the Codex review unless the artifact stays unchanged. Batch follow-up fixes when possible.",
                file=sys.stderr,
            )
        # Partial-review guard: the reviewer truncates the artifact to
        # --max-artifact-chars. If the diff is bigger, the adversarial review only
        # sees the first N chars — a partial review that otherwise passes silently
        # (this is what let an oversized extraction slice slip through). Warn loudly.
        review_budget = args.max_artifact_chars
        if review_budget and len(diff_text) > review_budget:
            print(
                f"warning: diff artifact is {len(diff_text)} chars but the review budget "
                f"(--max-artifact-chars) is {review_budget}; the adversarial review will only "
                f"see the first {review_budget} chars — a PARTIAL review. Split the scope into "
                f"smaller slices, or pass --max-artifact-chars {len(diff_text)} to review the whole diff.",
                file=sys.stderr,
            )

    artifact_sha = normalized_artifact_hash(args.stage, artifact_path)
    with with_locked_state(args.slug) as state:
        stage_state = _ensure_stage_state_blob(state, args.stage)
        current_rounds = _stage_int(stage_state, "adversarial_rounds")
        if current_rounds >= MAX_ADVERSARIAL_ROUNDS:
            accept_cap: str = getattr(args, "accept_cap", None) or ""
            if not accept_cap.strip():
                print(
                    f"checkpoint blocked: {args.stage} has already completed {current_rounds} adversarial "
                    f"review round(s) — hard cap is {MAX_ADVERSARIAL_ROUNDS}.\n"
                    f"If remaining findings are acceptable risk, rerun with:\n"
                    f"  --accept-cap \"<one sentence explaining why you're proceeding>\"",
                    file=sys.stderr,
                )
                return 1
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            annotations = state.setdefault("annotations", [])
            annotations.append({
                "stage": args.stage,
                "ts": ts,
                "type": "cap_accepted",
                "reason": accept_cap.strip(),
                "rounds_at_cap": current_rounds,
            })
        next_rounds = current_rounds + 1
        stage_state["adversarial_rounds"] = next_rounds
        stage_state["adversarial_sha_history"] = list(stage_state.get("adversarial_sha_history", []))
        stage_state["adversarial_sha_history"].append(artifact_sha)
        if not str(stage_state.get("initial_sha", "")).strip():
            stage_state["initial_sha"] = artifact_sha
        state[f"{args.stage}_adversarial_rounds"] = next_rounds

    reviews_arg = getattr(args, "required_reviews", None)
    if reviews_arg is None:
        small_task_set = False
        if args.stage in ("tasks", "closeout"):
            # For "tasks": count from the artifact (tasks.md is the artifact).
            # For "closeout": count from tasks.md in the workflow dir (artifact is closeout.md).
            tasks_file = artifact_path if args.stage == "tasks" else workflow_dir(args.slug) / "tasks.md"
            if tasks_file.exists():
                try:
                    task_text = tasks_file.read_text(encoding="utf-8")
                    task_count = sum(
                        1 for line in task_text.splitlines()
                        if line.strip().startswith("- [ ]") or line.strip().startswith("- [x]") or line.strip().startswith("- [X]")
                    )
                    if task_count < SMALL_TASK_SET_THRESHOLD:
                        small_task_set = True
                        print(
                            f"info: small task set ({task_count} tasks < {SMALL_TASK_SET_THRESHOLD} threshold) — "
                            f"{args.stage} stage has no default reviews; only explicitly requested lenses will run.",
                            file=sys.stderr,
                        )
                except Exception:
                    pass  # count failed — fall back to full reviews
        # Size the diff so substantial slices get one independent Gemini review.
        # The artifact has already been written (or validated to exist) above.
        diff_changed_lines: int | None = None
        diff_review_threshold = (
            getattr(args, "diff_review_threshold", None) or DIFF_REVIEW_DEFAULT_MIN_CHANGED_LINES
        )
        if args.stage == "diff" and artifact_path.exists():
            diff_changed_lines = count_changed_diff_lines(
                artifact_path.read_text(encoding="utf-8")
            )
        reviews_arg = required_reviews(
            args.stage,
            policy["sensitive"],
            policy["large_structural"],
            policy["performance_sensitive"],
            policy["extra_gemini_lenses"],
            fast=fast,
            small_task_set=small_task_set,
            diff_changed_lines=diff_changed_lines,
            diff_review_threshold=diff_review_threshold,
            reviewer_override=resolve_reviewer_override({"review_policy": policy}),
        )

    manifest = checkpoint_manifest(
        args.slug,
        args.stage,
        artifact_path,
        args.base_ref,
        context_paths,
        reviews_arg,
        args.max_artifact_chars,
        args.max_context_chars,
        args.max_total_chars,
        allow_dirty_paths,
    )
    manifest_path = checkpoint_manifest_path(args.slug, args.stage)
    atomic_json_write(manifest_path, manifest)
    if args.stage == "diff":
        if allow_dirty_paths:
            update_workflow_state(
                args.slug,
                lambda state: state.__setitem__(
                    DIRTY_OVERRIDE_KEY,
                    {
                        "allowed_dirty_paths": allow_dirty_paths,
                        "used": True,
                        "updated_at": int(time.time()),
                    },
                ),
            )
        else:
            # No --allow-dirty-path on this run — clear any stale override from a prior run.
            update_workflow_state(
                args.slug,
                lambda state: state.pop(DIRTY_OVERRIDE_KEY, None),
            )

    with HeartbeatEmitter(args.slug, args.stage):
        heartbeat_set_activity("awaiting reviewers")
        for review_spec in reviews_arg or []:
            reviewer = str(review_spec.get("reviewer", "")).strip()
            lens = str(review_spec.get("lens", "")).strip()
            if reviewer and lens:
                heartbeat_set_activity(f"dispatching {reviewer}.{lens}")
                heartbeat_set_activity(f"reviewer {reviewer}.{lens} running")
        cmd = [
            sys.executable,
            str(REPAIR),
            "--checkpoint-manifest",
            str(manifest_path),
            "--workspace-dir",
            str(REPO_ROOT),
            "--gemini-timeout-seconds",
            str(args.gemini_timeout_seconds),
            "--gemini-retries",
            str(args.gemini_retries),
            # getattr keeps older callers / test namespaces that predate the
            # codex knobs working; run_factory's parser supplies real defaults.
            "--codex-timeout-seconds",
            str(getattr(args, "codex_timeout_seconds", 540)),
            "--codex-idle-timeout-seconds",
            str(getattr(args, "codex_idle_timeout_seconds", 90)),
        ]
        try:
            result = subprocess.run(
                cmd,
                text=True,
                timeout=args.repair_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            if not args.fallback:
                _rollback_adversarial_round(args.slug, args.stage)
                print(
                    f"checkpoint blocked: repair exceeded {args.repair_timeout_seconds}s for "
                    f"{args.stage} on {args.slug}",
                    file=sys.stderr,
                )
                return 1
            fallback_reason = f"repair exceeded {args.repair_timeout_seconds}s"
        except Exception:
            _rollback_adversarial_round(args.slug, args.stage)
            raise
        else:
            heartbeat_set_activity("aggregating results")
            if result.returncode == 0:
                reverted = revert_protected_files()
                if reverted:
                    print(f"reverted protected files: {', '.join(reverted)}", file=sys.stderr)
                _advance_checkpoint_progress(
                    args.slug, args.stage, pending_head_sha,
                    repoint_only=getattr(args, "repair", False),
                )
                post_state = load_workflow_state(args.slug)
                payload = _checkpoint_next_action_payload(args.slug, post_state, args.stage)
                _persist_last_action_result(args.slug, payload)
                heartbeat_set_activity("all reviews complete")
                _print_findings_summary(args.slug, args.stage, reviews_arg)
                if args.json:
                    print(json.dumps(payload))
                else:
                    _emit_next_action(args.slug, f"{args.stage} checkpoint")
                return 0
            if not args.fallback:
                _rollback_adversarial_round(args.slug, args.stage)
                return result.returncode
            fallback_reason = f"repair exited {result.returncode}"

        heartbeat_set_activity("reviewer gemini.requirements running")
        fallback_ok, fallback_detail = run_checkpoint_fallback(
            manifest_path,
            REPO_ROOT,
            args.gemini_timeout_seconds,
            args.gemini_retries,
        )
        if not fallback_ok:
            _rollback_adversarial_round(args.slug, args.stage)
            print(f"checkpoint blocked: fallback review path failed for {args.stage} on {args.slug}: {trim_detail(fallback_detail)}", file=sys.stderr)
            return 1
        reverted = revert_protected_files()
        if reverted:
            print(f"reverted protected files: {', '.join(reverted)}", file=sys.stderr)
        record_checkpoint_fallback(args.slug, args.stage, fallback_reason)
        _advance_checkpoint_progress(
            args.slug, args.stage, pending_head_sha,
            repoint_only=getattr(args, "repair", False),
        )
        heartbeat_set_activity("aggregating results")
        print(f"warning: fallback checkpoint path used for {args.stage} on {args.slug}: {fallback_reason}", file=sys.stderr)
        post_state = load_workflow_state(args.slug)
        payload = _checkpoint_next_action_payload(args.slug, post_state, args.stage)
        _persist_last_action_result(args.slug, payload)
        heartbeat_set_activity("all reviews complete")
        _print_findings_summary(args.slug, args.stage, reviews_arg)
        if args.json:
            print(json.dumps(payload))
        else:
            _emit_next_action(args.slug, f"{args.stage} checkpoint")
        return 0
