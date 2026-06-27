#!/usr/bin/env python3
"""command_status, command_repair, and command_doctor implementations."""
import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    REPO_ROOT,
    BLOCKED_KEY,
    DELIVERY_KEY,
    DIRTY_OVERRIDE_KEY,
    CHECKPOINT_FALLBACK_KEY,
    blocking_unresolved_items,
    discovery_blockers_are_malformed,
    load_workflow_state,
    discovery_state,
)

from factory_git import (  # noqa: E402
    SYNC_SCRIPT,
    current_branch_name,
    upstream_branch_name,
    commits_behind_upstream,
    command_path,
    ensure_sync,
)

from factory_stages import (  # noqa: E402
    VERIFY_CHECKPOINT,
    VERIFY_RECONCILIATION,
    LARGE_DIFF_RERUN_WARN_CHARS,
    CHECKPOINT_STAGES,
    stage_manifest_state,
    stage_review_inventory,
    stage_drift_class,
    stage_repairable,
    stage_status_label,
    later_progress_exists,
    reconciliation_state,
    diff_review_budget_state,
    checkpoint_progress_state,
)

from factory_review import (  # noqa: E402
    WRITE_DIFF,
    REPAIR,
    trim_detail,
    repair_checkpoint_args,
)

from factory_next_action import recommended_next_action  # noqa: E402

from factory_deliver import refresh_delivery_snapshot  # noqa: E402
from factory_mutating import mutates_state, readonly_command  # noqa: E402
from factory_size_estimate import estimate_size  # noqa: E402


def _print_command_telemetry(state: dict) -> None:
    records = state.get("command_telemetry", [])
    if not isinstance(records, list):
        records = []
    recent = records[-10:]
    print("")
    print("command-telemetry:")
    if not recent:
        print("- (no records)")
        return
    header = (
        f"{'command':<16} {'stage':<10} {'ts':<20} {'wall_seconds':<12} "
        f"{'input_bytes_read':<17} {'output_bytes_written':<20} {'ttl_crossed':<11}"
    )
    print(header)
    for record in recent:
        print(
            f"{str(record.get('command', '')):<16} "
            f"{str(record.get('stage', '')):<10} "
            f"{str(record.get('ts', '')):<20} "
            f"{str(record.get('wall_seconds', '')):<12} "
            f"{str(record.get('input_bytes_read', '')):<17} "
            f"{str(record.get('output_bytes_written', '')):<20} "
            f"{str(record.get('ttl_crossed', '')):<11}"
        )


@readonly_command("status")
def command_status(args: argparse.Namespace) -> int:
    ensure_sync()
    state = load_workflow_state(args.slug)
    stages = {stage: stage_manifest_state(args.slug, stage) for stage in CHECKPOINT_STAGES}
    recon_ok, recon_detail = reconciliation_state(args.slug)
    next_action = recommended_next_action(args.slug, state, stages, recon_ok)
    last_action_result = state.get("last_action_result", None)
    branch = current_branch_name() or "(detached HEAD)"
    upstream = upstream_branch_name() or "(none)"
    blocked = state.get(BLOCKED_KEY, {})
    delivery = refresh_delivery_snapshot(state.get(DELIVERY_KEY, {}))
    discovery = discovery_state(args.slug)
    dirty_override = state.get(DIRTY_OVERRIDE_KEY, {})
    checkpoint_fallback = state.get(CHECKPOINT_FALLBACK_KEY, {})
    diff_budget = diff_review_budget_state(args.slug)

    behind = commits_behind_upstream()

    if next_action == "judge_panel":
        print("⚠ next: judge_panel")
    print(f"workflow: {args.slug}")
    print(f"branch: {branch}")
    print(f"upstream: {upstream}")
    if behind is not None and behind > 0:
        print(f"warning: branch is {behind} commit{'s' if behind != 1 else ''} behind upstream — rebase before creating PR")
    if isinstance(last_action_result, dict) and last_action_result.get("next"):
        reason = str(last_action_result.get("reason", "")).strip()
        next_label = str(last_action_result.get("next", "")).strip()
        if reason:
            print(f"Last recommended action: {next_label} — {reason}")
        else:
            print(f"Last recommended action: {next_label}")
        blockers = last_action_result.get("blockers", [])
        if isinstance(blockers, list) and blockers:
            print(f"Last action blockers: {', '.join(str(item) for item in blockers)}")
    print("")
    print("stages:")
    for stage in CHECKPOINT_STAGES:
        stage_state = stages[stage]
        status = stage_status_label(args.slug, stage, stage_state)
        detail = trim_detail(str(stage_state.get("detail", "")))
        _, orphaned = stage_review_inventory(args.slug, stage)
        if orphaned:
            orphaned_note = f"orphaned historical reviews: {len(orphaned)}"
            detail = f"{detail}; {orphaned_note}" if detail else orphaned_note
        print(f"- {stage}: {status}" + (f" ({detail})" if detail else ""))

    print("")
    print("reconciliation:")
    if recon_ok:
        print("- terminal")
    else:
        print(f"- blocked ({trim_detail(recon_detail)})")

    print("")
    print("blocked-state:")
    if blocked.get("active"):
        print(f"- active: {blocked.get('reason', '')}")
    else:
        print("- inactive")

    blocking = blocking_unresolved_items(discovery)
    unresolved = discovery.get("unresolved", [])
    if discovery.get("required") or not discovery.get("complete") or discovery.get("asked_count") or discovery.get("assumptions") or discovery.get("summary") or blocking or (isinstance(unresolved, list) and unresolved):
        print("")
        print("discovery:")
        print(f"- required: {'yes' if discovery.get('required') else 'no'}")
        print(f"- complete: {'yes' if discovery.get('complete') else 'no'}")
        print(f"- question-count: {discovery.get('question_count', 0)}")
        print(f"- asked-count: {discovery.get('asked_count', 0)}")
        remaining = max(int(discovery.get("question_count", 0)) - int(discovery.get("asked_count", 0)), 0)
        print(f"- remaining: {remaining}")
        if discovery.get("assumptions"):
            print(f"- assumptions: {len(discovery.get('assumptions', []))}")
        if discovery.get("summary"):
            print(f"- summary: {trim_detail(str(discovery.get('summary', '')))}")
        if isinstance(unresolved, list) and unresolved:
            print(f"- unresolved-open: {len(blocking)}")
            print(f"- unresolved-deferred: {len(unresolved) - len(blocking)}")
            if blocking:
                if discovery_blockers_are_malformed(discovery):
                    print("- action: use discover --clear to repair malformed discovery state")
                else:
                    print("- action: resolve or defer unresolved items before spec")
        elif blocking:
            print(f"- unresolved-open: {len(blocking)}")
            if discovery_blockers_are_malformed(discovery):
                print("- action: use discover --clear to repair malformed discovery state")
            else:
                print("- action: resolve or defer unresolved items before spec")

    cp = checkpoint_progress_state(args.slug)
    if cp["index"] > 0:
        print("")
        print("checkpoint-progress:")
        print(f"- index: {cp['index']}")
        last = cp["last_diff_head_sha"]
        print(f"- last-diff-head: {last[:12] if last else '(none)'}")

    print("")
    print("delivery:")
    if delivery.get("pr_url"):
        line = f"- pr: {delivery.get('pr_url')} [{delivery.get('pr_state', '')}]"
        if delivery.get("pr_is_draft"):
            line += " draft"
        print(line)
        print(f"- checks: {delivery.get('checks_summary', 'unknown')}")
        if delivery.get("merge_state_status"):
            print(f"- merge-state: {delivery.get('merge_state_status')}")
        if delivery.get("head_mismatch"):
            print("- head-mismatch: local HEAD differs from PR head")
            print("- action: refresh the PR head before delivering")
    else:
        print("- not-started")

    if dirty_override.get("used"):
        print("")
        print("dirty-override:")
        print("- used: yes")
        for path in dirty_override.get("allowed_dirty_paths", []):
            print(f"- allow-dirty-path: {path}")

    if checkpoint_fallback.get("used"):
        print("")
        print("checkpoint-fallback:")
        print(f"- stage: {checkpoint_fallback.get('stage', '')}")
        print(f"- reason: {checkpoint_fallback.get('reason', '')}")

    if diff_budget.get("artifact_exists"):
        print("")
        print("diff-review-budget:")
        print(f"- artifact-bytes: {diff_budget.get('artifact_bytes', 0)}")
        if diff_budget.get("recorded_base_ref"):
            base_ref = str(diff_budget.get("recorded_base_ref", ""))
            base_sha = str(diff_budget.get("recorded_base_sha", ""))[:12]
            print(f"- recorded-base: {base_ref} [{base_sha}]")
        if diff_budget.get("large_artifact"):
            print(f"- large-artifact: yes (>= {LARGE_DIFF_RERUN_WARN_CHARS} bytes)")
        else:
            print("- large-artifact: no")
        if diff_budget.get("in_scope_change"):
            recorded_head = str(diff_budget.get("recorded_head_sha", ""))[:12]
            current_head = str(diff_budget.get("current_head_sha", ""))[:12]
            print(f"- rerun-likely: yes (in-scope code changed; diff artifact HEAD {recorded_head} != current HEAD {current_head})")
            print(
                f"- scope-basis: last-reviewed-head [{recorded_head}]"
            )
        elif diff_budget.get("artifact_changed_since_codex"):
            print("- rerun-likely: yes (artifact changed since the last Codex review)")
        elif diff_budget.get("suggested_base_ref"):
            suggested = str(diff_budget.get("suggested_base_ref", ""))[:12]
            print(f"- scope-basis: {diff_budget.get('scope_basis', 'branch-merge-base')} [{suggested}]")
        else:
            print("- rerun-likely: no")
        if diff_budget.get("large_artifact") and (
            diff_budget.get("head_mismatch") or diff_budget.get("artifact_changed_since_codex")
        ):
            print("- advice: batch more implementation fixes before rerunning the diff checkpoint")

    invariant_warnings = state.get("invariant_warnings") or []
    if invariant_warnings:
        print("")
        print("invariant-warnings:")
        total = len(invariant_warnings)
        print(f"- total: {total}")
        for entry in invariant_warnings[-5:]:
            at = entry.get("at", 0)
            command = entry.get("command", "")
            stage = entry.get("stage", "")
            detail = trim_detail(str(entry.get("detail", "")))
            print(f"- [{at}] {command} stage={stage}: {detail}")

    try:
        est = estimate_size(args.slug)
        print("")
        print(f"size-estimate: {est['size']} ({est['reasoning']})")
    except Exception:
        pass  # size estimate is advisory; never fail status

    print("")
    print(f"next-action: {next_action}")
    if getattr(args, "tokens", False):
        _print_command_telemetry(state)
    return 0


@mutates_state("repair")
def command_repair(args: argparse.Namespace) -> int:
    from factory_cmd_checkpoint import command_checkpoint  # noqa: E402
    ensure_sync()
    state = load_workflow_state(args.slug)
    stages = {stage: stage_manifest_state(args.slug, stage) for stage in CHECKPOINT_STAGES}
    repaired: list[str] = []
    notes: list[str] = []
    blocked_reason = ""

    print(f"workflow: {args.slug}")
    print("repair:")

    for stage in ["spec", "plan", "tasks", "diff"]:
        stage_state = stages[stage]
        drift = stage_drift_class(stage, stage_state)
        _, orphaned = stage_review_inventory(args.slug, stage)
        if stage_repairable(args.slug, stage, stage_state):
            print(f"- {stage}: repairing {drift}")
            result = command_checkpoint(repair_checkpoint_args(args.slug, stage, stage_state))
            if result != 0:
                blocked_reason = f"{stage} repair failed"
                break
            refreshed = stage_manifest_state(args.slug, stage)
            stages[stage] = refreshed
            if not refreshed["healthy"]:
                blocked_reason = f"{stage} remains unhealthy: {trim_detail(str(refreshed.get('detail', '')))}"
                break
            repaired.append(stage)
            if orphaned:
                notes.append(f"{stage}: preserved {len(orphaned)} orphaned historical review file(s)")
            continue

        print(f"- {stage}: {stage_status_label(args.slug, stage, stage_state)}")

        if drift in {"missing-artifact", "stub-artifact"} and later_progress_exists(stages, stage)[0]:
            blocked_reason = f"{stage} has {drift.replace('-', ' ')} while later workflow material exists"
            break
        if drift == "unhealthy-manifest" and not stage_repairable(args.slug, stage, stage_state):
            blocked_reason = f"{stage} checkpoint is not safely repairable: {trim_detail(str(stage_state.get('detail', '')))}"
            break

    if not blocked_reason:
        # Repair closeout if manifest is stale (only when it exists and is unhealthy)
        closeout_state = stages["closeout"]
        closeout_drift = stage_drift_class("closeout", closeout_state)
        if closeout_drift == "unhealthy-manifest" and stage_repairable(args.slug, "closeout", closeout_state):
            print("- closeout: repairing unhealthy-manifest")
            result = command_checkpoint(repair_checkpoint_args(args.slug, "closeout", closeout_state))
            if result != 0:
                blocked_reason = "closeout repair failed"
            else:
                refreshed = stage_manifest_state(args.slug, "closeout")
                stages["closeout"] = refreshed
                if not refreshed["healthy"]:
                    blocked_reason = f"closeout remains unhealthy: {trim_detail(str(refreshed.get('detail', '')))}"
                else:
                    repaired.append("closeout")
        elif closeout_drift == "unhealthy-manifest":
            # unhealthy-manifest but not repairable — block so repair doesn't silently succeed
            blocked_reason = "closeout is unhealthy but not repairable"
        elif closeout_drift not in {"not-checkpointed", "missing-artifact", "stub-artifact"}:
            print(f"- closeout: {stage_status_label(args.slug, 'closeout', closeout_state)}")

    if blocked_reason:
        print(f"- blocked: {blocked_reason}")
        for note in notes:
            print(f"- note: {note}")
        return 1

    recon_ok, recon_detail = reconciliation_state(args.slug)
    next_action = recommended_next_action(args.slug, state, stages, recon_ok)
    print("")
    if repaired:
        print(f"- repaired: {', '.join(repaired)}")
    else:
        print("- repaired: none")
    if notes:
        for note in notes:
            print(f"- note: {note}")
    if not recon_ok:
        print(f"- follow-up: reconciliation still blocked ({trim_detail(recon_detail)})")
    print(f"next-action: {next_action}")
    return 0


@readonly_command("doctor")
def command_doctor(args: argparse.Namespace) -> int:
    from factory_git import git_output  # noqa: E402
    checks: list[tuple[str, str, str]] = []

    def add(name: str, level: str, detail: str) -> None:
        checks.append((name, level, detail))

    repo_root = git_output("rev-parse", "--show-toplevel")
    add("repo-root", "ok" if repo_root == str(REPO_ROOT) else "fail", repo_root or "not inside a git repo")
    add(
        "python",
        "ok" if sys.version_info >= (3, 10) else "fail",
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    )

    for label, path in {
        "sync-script": SYNC_SCRIPT,
        "feature-runner": Path(__file__).resolve(),
        "write-diff": WRITE_DIFF,
        "repair": REPAIR,
        "verify-checkpoint": VERIFY_CHECKPOINT,
        "verify-reconciliation": VERIFY_RECONCILIATION,
    }.items():
        add(label, "ok" if path.exists() else "fail", str(path))

    for tool_name in ["git", "codex", "gemini", "gh"]:
        found = command_path(tool_name)
        level = "ok" if found else ("warn" if tool_name == "gh" else "fail")
        add(f"tool:{tool_name}", level, found or "not installed")

    sync_check = subprocess.run([sys.executable, str(SYNC_SCRIPT), "--check"], text=True, capture_output=True)
    add("skill-sync", "ok" if sync_check.returncode == 0 else "warn", "in sync" if sync_check.returncode == 0 else "needs sync")

    branch = current_branch_name()
    add("git-branch", "ok" if branch else "warn", branch or "detached HEAD")
    upstream = upstream_branch_name()
    add("git-upstream", "ok" if upstream else "warn", upstream or "no upstream configured")

    if command_path("gh"):
        auth = subprocess.run(["gh", "auth", "status", "--active"], text=True, capture_output=True)
        auth_detail = (auth.stderr or auth.stdout or "").strip().splitlines()
        add("gh-auth", "ok" if auth.returncode == 0 else "warn", auth_detail[0] if auth_detail else "unknown")

    failed = False
    for name, level, detail in checks:
        print(f"[{level.upper()}] {name}: {detail}")
        if level == "fail":
            failed = True
    return 1 if failed else 0
