#!/usr/bin/env python3
import argparse
import subprocess
import sys
import time
from pathlib import Path


_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    REPO_ROOT,
    BLOCKED_KEY,
    DISCOVERY_KEY,
    CHECKPOINT_PROGRESS_KEY,
    INIT_HEAD_SHA_KEY,
    checkpoint_manifest_path,
    default_artifact_path,
    default_discovery_state,
    reviews_dir,
    scope_manifest_path,
    workflow_dir,
    save_scope_manifest,
    load_workflow_state,
    save_workflow_state,
    update_workflow_state,
    blocking_unresolved_items,
    discovery_state,
)

from factory_git import (  # noqa: E402
    _git_head_sha,
    _sha_is_valid_ancestor,
    command_path,
    current_branch_name,
    ensure_sync,
    ensure_file,
    run,
    upstream_branch_name,
    warn_if_primary_checkout,
)

from factory_stages import (  # noqa: E402
    CHECKPOINT_STAGES,
    VERIFY_RECONCILIATION,
    _default_checkpoint_progress,
    checkpoint_progress_state,
    diff_review_budget_state,
    later_progress_exists,
    parse_checkpoint_markers,
    parse_parallel_task_groups,
    preferred_diff_base_ref,
    prerequisite_failure,
    reconciliation_state,
    stage_drift_class,
    stage_manifest_state,
    stage_repairable,
    stage_review_inventory,
    stage_status_label,
)

from factory_review import (  # noqa: E402
    UPDATE_REVIEW,
    APPEND_RECONCILIATION,
    DEFAULT_CODEX_MODEL,
    checkpoint_manifest,
    trim_detail,
    _advance_checkpoint_progress,
    record_checkpoint_fallback,
    repair_checkpoint_args,
    required_reviews,
    resolved_review_policy,
    run_checkpoint_fallback,
)

from factory_emit import _emit_next_action  # noqa: E402
from factory_next_action import recommended_next_action  # noqa: E402
from factory_cmd_checkpoint import command_checkpoint  # noqa: E402
from factory_cmd_reconcile import command_reconcile  # noqa: E402
from factory_cmd_discover import command_discover  # noqa: E402
from factory_cmd_advance import command_advance  # noqa: E402
from factory_cmd_dispatch import command_dispatch_codex  # noqa: E402
from factory_cmd_prepare_claude_reviews import command_prepare_claude_reviews  # noqa: E402
from factory_cmd_review_extract import command_review_extract  # noqa: E402
from factory_deliver import (  # noqa: E402
    compose_closeout_text,
    current_pr_payload,
    gather_all_review_paths,
    refresh_delivery_snapshot,
    required_check_summary,
)
from factory_cmd_deliver import command_deliver, command_workflow_closeout  # noqa: E402
from factory_cmd_closeout import command_standalone_closeout  # noqa: E402
# Expose the standalone closeout under the canonical name so it's accessible
# as run_factory.command_closeout in tests and the mutating registry.
command_closeout = command_standalone_closeout
from check_workflow_isolation import command_check_workflow_isolation  # noqa: E402
from factory_cmd_block import command_block  # noqa: E402
from factory_cmd_arch_docs import command_arch_docs  # noqa: E402
from factory_cmd_implement import command_implement, command_parallel, _run_serial, _run_parallel  # noqa: E402
from factory_cmd_status import command_status, command_repair, command_doctor  # noqa: E402
from factory_invariants import run_invariant_checks, set_json_mode  # noqa: E402
from factory_mutating import (  # noqa: E402
    collect_mutating_command_names,
    enumerate_subparser_handlers,
    mutates_state,
)
from factory_cmd_analyze_reviews import command_analyze_reviews  # noqa: E402
from factory_cmd_quick import command_quick  # noqa: E402
from factory_cmd_audit import command_audit  # noqa: E402
from factory_cmd_autopilot import command_autopilot, DEFAULT_MAX_ITERATIONS as _AUTOPILOT_MAX_ITERATIONS  # noqa: E402
from factory_stages import stage_manifest_state  # noqa: E402  (re-imported for invariant check)

_MUTATING_CACHE: frozenset[str] | None = None


def _get_mutating_commands() -> frozenset[str]:
    global _MUTATING_CACHE
    if _MUTATING_CACHE is None:
        _MUTATING_CACHE = collect_mutating_command_names(
            handler for _, handler in enumerate_subparser_handlers(build_parser())
        )
    return _MUTATING_CACHE


def _run_post_invariants(slug: str | None, command_name: str) -> None:
    """Run invariant checks after a state-mutating command.

    Uses the same ``recon_ok`` signal as ``factory_cmd_status`` / the real
    checkpoint decision path, so the contradiction detector evaluates the
    user-visible next-action string — not a hypothetical happy-path one.
    Errors are swallowed — the invariant helper must never abort the caller.
    """
    if not slug:
        return
    try:
        from factory_stages import reconciliation_state  # local import — avoid cycles
        state = load_workflow_state(slug)
        stages = {name: stage_manifest_state(slug, name) for name in CHECKPOINT_STAGES}
        recon_ok, _ = reconciliation_state(slug)
        recommended = recommended_next_action(slug, state, stages, recon_ok)
        appended = run_invariant_checks(state, command_name, recommended)
        if appended:
            save_workflow_state(slug, state)
    except Exception:  # noqa: BLE001 — invariant failure must not abort the caller
        return


@mutates_state("init")
def command_init(args: argparse.Namespace) -> int:
    if not args.path:
        raise SystemExit(
            "init requires at least one --path argument to define the feature scope. "
            "Example: --path app/engine/my_feature"
        )
    warn_if_primary_checkout()
    ensure_sync()
    root = workflow_dir(args.slug)
    reviews = reviews_dir(args.slug)
    reviews.mkdir(parents=True, exist_ok=True)
    ensure_file(root / "spec.md", "Spec")
    ensure_file(root / "plan.md", "Plan")
    ensure_file(root / "tasks.md", "Tasks")
    save_scope_manifest(args.slug, args.path)
    existing_state = load_workflow_state(args.slug)
    existing_state.setdefault(
        "review_policy",
        {
            "sensitive": False,
            "large_structural": False,
            "performance_sensitive": False,
            "extra_gemini_lenses": [],
        },
    )
    existing_state.setdefault(DISCOVERY_KEY, default_discovery_state())
    # For first-time init: require discovery before spec authoring.
    # Re-inits of an in-progress workflow preserve the existing discovery state.
    if not existing_state.get(INIT_HEAD_SHA_KEY):
        discovery = existing_state[DISCOVERY_KEY]
        discovery["required"] = True
        discovery["complete"] = False
    # Always reset checkpoint_progress on init so stale slice state from a
    # previous run does not corrupt the new one.
    existing_state[CHECKPOINT_PROGRESS_KEY] = _default_checkpoint_progress()
    # Record the HEAD SHA at init time so we can verify STATUS.md was updated.
    head_sha = _git_head_sha(REPO_ROOT)
    if head_sha:
        existing_state[INIT_HEAD_SHA_KEY] = head_sha
    save_workflow_state(args.slug, existing_state)
    print(str(root))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--slug", required=True)
    init_parser.add_argument("--path", action="append", default=[])
    init_parser.set_defaults(func=command_init)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.set_defaults(func=command_doctor)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--slug", required=True)
    status_parser.add_argument(
        "--tokens",
        action="store_true",
        help="Print the last 10 command telemetry records; input_bytes_read is a proxy because only FF helper reads are counted.",
    )
    status_parser.set_defaults(func=command_status)

    repair_parser = subparsers.add_parser("repair")
    repair_parser.add_argument("--slug", required=True)
    repair_parser.set_defaults(func=command_repair)

    checkpoint_parser = subparsers.add_parser("checkpoint")
    checkpoint_parser.add_argument("--slug", required=True)
    checkpoint_parser.add_argument("--stage", required=True, choices=CHECKPOINT_STAGES)
    checkpoint_parser.add_argument("--artifact")
    checkpoint_parser.add_argument("--base-ref")
    checkpoint_parser.add_argument("--context", action="append", default=[])
    checkpoint_parser.add_argument("--path", action="append", default=[])
    checkpoint_parser.add_argument("--extra-gemini-lens", action="append", default=[])
    checkpoint_parser.add_argument("--sensitive", action="store_true")
    checkpoint_parser.add_argument("--large-structural", action="store_true")
    checkpoint_parser.add_argument("--performance-sensitive", action="store_true")
    checkpoint_parser.add_argument("--use-existing-artifact", action="store_true")
    checkpoint_parser.add_argument("--auto-context", action="store_true",
                                   help="Enable automatic context file extraction. Defaults on for spec/tasks and off for plan/diff.")
    checkpoint_parser.add_argument("--no-auto-context", action="store_true",
                                   help="Disable automatic context file extraction from the artifact. Defaults off for plan/diff.")
    checkpoint_parser.add_argument("--allow-dirty-path", action="append", default=[])
    checkpoint_parser.add_argument("--max-artifact-chars", type=int, default=50000)
    checkpoint_parser.add_argument("--max-context-chars", type=int, default=60000)
    checkpoint_parser.add_argument("--max-total-chars", type=int, default=250000)
    checkpoint_parser.add_argument("--gemini-timeout-seconds", type=int, default=120)
    checkpoint_parser.add_argument("--gemini-retries", type=int, default=1)
    # Codex backstop ceiling + idle-watchdog window (parity with --gemini-timeout-seconds).
    # The idle watchdog is the real liveness control; the ceiling only catches an
    # "active but never converging" run. See codex_stream_runner.run_with_idle_watchdog.
    checkpoint_parser.add_argument("--codex-timeout-seconds", type=int, default=540)
    checkpoint_parser.add_argument("--codex-idle-timeout-seconds", type=int, default=90)
    # Outer repair wrapper must contain a long Codex review (ceiling 540 + 30 = 570)
    # plus a Gemini pass (120 + 30 = 150); raised 300 -> 780 so the backstop ceiling
    # is the inner watchdog, not this wrapper killing a healthy long plan review.
    checkpoint_parser.add_argument("--repair-timeout-seconds", type=int, default=780)
    checkpoint_parser.add_argument("--allow-large-diff-rerun", action="store_true")
    checkpoint_parser.add_argument(
        "--accept-cap",
        metavar="REASON",
        default=None,
        help=(
            "Proceed past the MAX_ADVERSARIAL_ROUNDS hard cap. "
            "Pass a one-sentence reason; it is recorded in state.json as a cap_accepted annotation."
        ),
    )
    checkpoint_parser.add_argument("--fallback", action="store_true")
    checkpoint_parser.add_argument("--json", action="store_true")
    checkpoint_parser.add_argument(
        "--keep-intermediates",
        action="store_true",
        help="Preserve review intermediate files for debugging instead of deleting them at checkpoint start",
    )
    checkpoint_parser.add_argument("--fast", action="store_true",
        help="Fast path: skip prerequisites, run 1 Gemini + 1 Codex review. Requires --stage diff.")
    checkpoint_parser.add_argument("--diff-review-threshold", type=int, default=None,
        help="Changed-line count at/above which a diff stage gets one Gemini "
             "regression-adversarial review (default 50). Lower to review smaller "
             "slices; raise to review only large ones.")
    checkpoint_parser.set_defaults(func=command_checkpoint)

    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("--slug", required=True)
    reconcile_parser.add_argument("--review", action="append", required=True)
    reconcile_parser.add_argument("--status", required=True)
    reconcile_parser.add_argument("--note", required=True)
    reconcile_parser.set_defaults(func=command_reconcile)

    block_parser = subparsers.add_parser("block")
    block_parser.add_argument("--slug", required=True)
    block_parser.add_argument("--reason")
    block_parser.add_argument("--clear", action="store_true")
    block_parser.set_defaults(func=command_block)

    arch_docs_parser = subparsers.add_parser(
        "arch-docs",
        help="record the architecture-doc decision (ack no change needed, or reset the ack)",
    )
    arch_docs_parser.add_argument("--slug", required=True)
    arch_docs_parser.add_argument("--no-change-needed", action="store_true",
                                  help="ack that this feature needs no design/architecture doc change")
    arch_docs_parser.add_argument("--reason", help="why no doc change is needed (required with --no-change-needed)")
    arch_docs_parser.add_argument("--reset", action="store_true", help="clear a prior no-change ack")
    arch_docs_parser.set_defaults(func=command_arch_docs)

    advance_parser = subparsers.add_parser("advance")
    advance_parser.add_argument("--slug", required=True)
    advance_parser.add_argument(
        "--stage",
        required=True,
        choices=["spec", "plan", "tasks", "diff"],
    )
    advance_parser.add_argument("--reason", required=True)
    advance_parser.set_defaults(func=command_advance)

    dispatch_parser = subparsers.add_parser("dispatch-codex")
    dispatch_parser.add_argument("--slug", required=True)
    dispatch_parser.add_argument("--prompt-path", required=True)
    dispatch_parser.add_argument("--model", default="gpt-5.4-mini")
    dispatch_parser.add_argument("--no-auto-commit", action="store_true")
    dispatch_parser.set_defaults(func=command_dispatch_codex)

    # Claude-only review path (spec 020): stage subagent reviews for a checkpoint.
    prepare_claude_parser = subparsers.add_parser("prepare-claude-reviews")
    prepare_claude_parser.add_argument("--slug", required=True)
    prepare_claude_parser.add_argument("--stage", required=True, choices=CHECKPOINT_STAGES)
    prepare_claude_parser.add_argument("--artifact")
    prepare_claude_parser.add_argument("--base-ref")
    prepare_claude_parser.add_argument("--context", action="append", default=[])
    # Diff-stage scope: the canonical diff is generated here from these paths.
    prepare_claude_parser.add_argument("--path", action="append", default=[])
    prepare_claude_parser.add_argument("--diff-review-threshold", type=int, default=None)
    prepare_claude_parser.add_argument("--max-artifact-chars", type=int, default=50000)
    prepare_claude_parser.add_argument("--max-context-chars", type=int, default=60000)
    prepare_claude_parser.add_argument("--max-total-chars", type=int, default=250000)
    prepare_claude_parser.set_defaults(func=command_prepare_claude_reviews)

    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument("--slug", required=True)
    discover_parser.add_argument("--required", action="store_true")
    discover_parser.add_argument("--count", type=int)
    discover_parser.add_argument("--question")
    discover_parser.add_argument("--recommendation")
    discover_parser.add_argument("--rationale")
    discover_parser.add_argument("--assumption", action="append", default=[])
    discover_parser.add_argument("--summary")
    discover_parser.add_argument("--complete", action="store_true")
    discover_parser.add_argument("--unresolved", type=str,
        help="Add an item to unresolved[]")
    discover_parser.add_argument("--resolve", type=str,
        help="Remove an item from unresolved[] by exact text match")
    discover_parser.add_argument("--defer", type=str,
        help="Mark an unresolved item as deferred by exact text match")
    discover_parser.add_argument("--non-goal", action="append", dest="non_goal", default=None,
        help="Add a string to non_goals[]. Repeat the flag to append multiple values. "
             "If --clear-non-goals is also set, the clear applies BEFORE these appends in the same invocation. "
             "Empty/whitespace-only values are rejected.")
    discover_parser.add_argument("--acceptance-criteria", action="append", dest="acceptance_criteria", default=None,
        help="Add a string to acceptance_criteria[]. Repeat the flag to append multiple values. "
             "If --clear-acceptance-criteria is also set, the clear applies BEFORE these appends in the same invocation. "
             "Empty/whitespace-only values are rejected.")
    discover_parser.add_argument("--goal",
        help="Discovery checklist: the goal — what this feature accomplishes and why")
    discover_parser.add_argument("--audience",
        help="Discovery checklist: who the feature is for (users/audience)")
    discover_parser.add_argument("--constraints",
        help="Discovery checklist: key constraints, dependencies, or risks")
    discover_parser.add_argument("--clear-non-goals", action="store_true",
        help="Empty discovery.non_goals[] BEFORE any --non-goal appends in the same invocation.")
    discover_parser.add_argument("--clear-acceptance-criteria", action="store_true",
        help="Empty discovery.acceptance_criteria[] BEFORE any --acceptance-criteria appends in the same invocation.")
    discover_parser.add_argument("--answer", nargs=2, metavar=("QUESTION", "ANSWER"),
        help="Record answers[QUESTION] = ANSWER")
    discover_parser.add_argument("--force-complete", action="store_true")
    discover_parser.add_argument("--clear", action="store_true")
    discover_parser.add_argument(
        "--force-path",
        dest="force_path",
        choices=["quick", "full", "none", "auto"],
        default="auto",
        help=(
            "Override the size-estimate recommended path printed at discover --complete. "
            "'auto' (default) uses the estimate. 'none' prints the skip-FF message even "
            "if the estimate is not trivial. 'quick'/'full' suppress the skip-FF message "
            "and show the standard recommendation. Does NOT change FF behavior beyond "
            "what is printed — the operator still drives the next step."
        ),
    )
    discover_parser.set_defaults(func=command_discover)

    parallel_parser = subparsers.add_parser(
        "parallel",
        help="Record parallel task analysis result before tasks checkpoint",
    )
    parallel_parser.add_argument("--slug", required=True)
    parallel_parser.add_argument(
        "--note",
        required=True,
        help="Explain what was found or why nothing was safe to parallelise",
    )
    parallel_parser.add_argument(
        "--found",
        action="store_true",
        help="Mark that parallel opportunities were found and annotated with [P:] in tasks.md",
    )
    parallel_parser.set_defaults(func=command_parallel)

    implement_parser = subparsers.add_parser("implement", help="dispatch Codex for next checkpoint slice")
    implement_parser.add_argument("--slug", required=True, help="workflow slug")
    implement_parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        dest="max_workers",
        help="max concurrent Codex workers (default: 4)",
    )
    implement_parser.set_defaults(func=command_implement)

    deliver_parser = subparsers.add_parser("deliver")
    deliver_parser.add_argument("--slug", required=True)
    deliver_parser.add_argument("--create-pr", action="store_true")
    deliver_parser.add_argument("--draft", action="store_true")
    deliver_parser.add_argument("--base")
    deliver_parser.add_argument("--title")
    deliver_parser.add_argument("--reason")
    deliver_parser.add_argument("--refresh", action="store_true")
    deliver_parser.add_argument("--resume-merge-wait", action="store_true")
    deliver_parser.add_argument("--watch-ci", action="store_true")
    deliver_parser.add_argument("--interval", type=int, default=10)
    deliver_parser.add_argument("--merge-when-green", action="store_true")
    deliver_parser.add_argument("--auto-merge", action="store_true")
    deliver_parser.add_argument("--dry-run", action="store_true")
    deliver_parser.add_argument("--override-implementation-rule", action="store_true",
        help="Suppress the implementation-rule WARN if Claude implemented "
             "more than 200 lines without a Codex dispatch. Requires --override-implementation-reason.")
    deliver_parser.add_argument("--override-implementation-reason",
        help="Reason for overriding the implementation-rule WARN. Must be at least 10 characters.")
    deliver_parser.add_argument("--pr-number", dest="pr_number", type=int, default=None,
        help="PR number for the Claude-only web path (no gh CLI). When provided "
             "with --pr-url, deliver records the delivery snapshot from these "
             "values and skips all gh queries.")
    deliver_parser.add_argument("--pr-url", dest="pr_url", default=None,
        help="PR URL for the Claude-only web path (no gh CLI). Pairs with --pr-number.")
    deliver_parser.add_argument("--merge-sha", dest="merge_sha", default=None,
        help="Merge commit SHA for the Claude-only web path (no gh CLI). Optional.")
    deliver_parser.set_defaults(func=command_deliver)

    closeout_parser = subparsers.add_parser(
        "closeout",
        help=(
            "Post-merge closeout: write closeout.md with review coverage, "
            "update state.delivery, tick confirmed slice checkboxes. "
            "Run after `gh pr merge`."
        ),
    )
    closeout_parser.add_argument("--slug", required=True)
    closeout_parser.add_argument("--pr-url", dest="pr_url", default=None,
        help="PR URL (detected via gh CLI if omitted)")
    closeout_parser.add_argument("--pr-number", dest="pr_number", type=int, default=None,
        help="PR number (detected via gh CLI if omitted)")
    closeout_parser.add_argument("--merge-sha", dest="merge_sha", default=None,
        help="Merge commit SHA (detected via gh CLI if omitted)")
    closeout_parser.add_argument("--note", default=None,
        help="Optional 1–2 sentence operator note for the closeout")
    closeout_parser.add_argument("--out", default=None,
        help="Override output path for closeout.md (default: <slug-dir>/closeout.md)")
    closeout_parser.set_defaults(func=command_standalone_closeout)

    review_extract_parser = subparsers.add_parser("review-extract")
    review_extract_parser.add_argument("--slug", required=True)
    review_extract_parser.add_argument("--stage", required=True, choices=["spec", "plan", "tasks", "diff", "closeout"])
    review_extract_parser.add_argument("--format", choices=["jsonl", "json"], default="jsonl")
    review_extract_parser.add_argument("--out")
    review_extract_parser.set_defaults(func=command_review_extract)

    check_isolation_parser = subparsers.add_parser("check-isolation")
    isolation_group = check_isolation_parser.add_mutually_exclusive_group(required=True)
    isolation_group.add_argument("--capture-baseline", type=Path)
    isolation_group.add_argument("--check", action="store_true")
    check_isolation_parser.add_argument("--baseline", type=Path)
    check_isolation_parser.set_defaults(func=command_check_workflow_isolation)

    analyze_reviews_parser = subparsers.add_parser(
        "analyze-reviews",
        help="Aggregate FF review and judge telemetry into a markdown report",
    )
    analyze_reviews_parser.add_argument("--out")
    analyze_reviews_parser.add_argument("--top-n", type=int, default=20)
    analyze_reviews_parser.set_defaults(func=command_analyze_reviews)

    quick_parser = subparsers.add_parser(
        "quick",
        help="Run a single diff review for a small feature (skip full spec/plan/tasks cycle)",
    )
    quick_parser.add_argument("--slug", required=True)
    quick_parser.add_argument("--prompt-path", dest="prompt_path", default=None,
        help="Path to a Codex prompt file. If given, dispatches Codex before the review.")
    quick_parser.add_argument(
        "--review-lens",
        dest="review_lens",
        choices=["correctness", "quality"],
        default="correctness",
        help="correctness = Codex correctness-adversarial (default); quality = Gemini quality-adversarial",
    )
    quick_parser.set_defaults(func=command_quick)

    audit_parser = subparsers.add_parser(
        "audit",
        help="Classify all FF runs as closed/active/abandoned/empty. Read-only reporter.",
    )
    audit_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of markdown.",
    )
    audit_parser.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="Write report to PATH instead of stdout.",
    )
    audit_parser.set_defaults(func=command_audit)

    autopilot_parser = subparsers.add_parser(
        "autopilot",
        help=(
            "Auto-advance through mechanical steps and yield at decision points "
            "(authoring, open findings, failures, delivery).  Resumable."
        ),
    )
    autopilot_parser.add_argument("--slug", required=True, help="workflow slug")
    autopilot_parser.add_argument(
        "--max-iterations",
        type=int,
        default=_AUTOPILOT_MAX_ITERATIONS,
        dest="max_iterations",
        help=f"hard stop after this many loop iterations (default: {_AUTOPILOT_MAX_ITERATIONS})",
    )
    autopilot_parser.add_argument(
        "--allow-deliver",
        action="store_true",
        dest="allow_deliver",
        help="reserved for future use; delivery still stops for approval",
    )
    autopilot_parser.set_defaults(func=command_autopilot)

    return parser


def main() -> int:
    from factory_pollution_check import auto_revert_polluted_state  # noqa: E402
    auto_revert_polluted_state()
    parser = build_parser()
    args = parser.parse_args()
    # FR-009: route invariant-warning output to stderr (always).
    set_json_mode(bool(getattr(args, "json", False)))
    command_name = getattr(args, "_factory_command", None) or _infer_command_name(args)
    # Diff round-1 finding: run invariants in a `finally` so contradictions
    # introduced by a partial-state write before an exception still get
    # caught — the exact class of bug the guardrail exists to detect.
    exit_code = 0
    try:
        exit_code = args.func(args)
        return exit_code
    finally:
        if command_name in _get_mutating_commands():
            _run_post_invariants(getattr(args, "slug", None), command_name)


def _infer_command_name(args: argparse.Namespace) -> str | None:
    """Best-effort extraction of the subcommand name from parsed argparse args."""
    # argparse doesn't expose the selected subcommand name cleanly; inspect the
    # configured func and map it back to a known subcommand.
    func = getattr(args, "func", None)
    if func is None:
        return None
    qualname = getattr(func, "__qualname__", "") or ""
    name = getattr(func, "__name__", "") or ""
    # Common mapping — command_X -> X
    if name.startswith("command_"):
        return name[len("command_"):].replace("_", "-")
    return None


if __name__ == "__main__":
    raise SystemExit(main())
