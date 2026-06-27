#!/usr/bin/env python3
"""command_deliver and command_closeout implementations."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    REPO_ROOT,
    DELIVERY_KEY,
    DIRTY_OVERRIDE_KEY,
    CHECKPOINT_FALLBACK_KEY,
    load_workflow_state,
    with_locked_state,
    update_workflow_state,
    checkpoint_manifest_path,
    workflow_dir,
)
from factory_heartbeat import HeartbeatEmitter, set_activity as heartbeat_set_activity  # noqa: E402

from factory_git import (  # noqa: E402
    current_branch_name,
    upstream_branch_name,
    commits_behind_upstream,
    command_path,
    git_output,
    ensure_sync,
)

from factory_stages import (  # noqa: E402
    VERIFY_CHECKPOINT,
    VERIFY_RECONCILIATION,
    REQUIRED_PREDELIVERY_STAGES,
    VERIFY_ON_CLOSEOUT_STAGES,
    verify_checkpoint_manifest,
    reconciliation_state,
    diff_review_budget_state,
)

from factory_review import (  # noqa: E402
    trim_detail,
)

from factory_deliver import (  # noqa: E402
    current_pr_payload,
    required_check_summary,
    build_delivery_record,
    gather_all_review_paths,
    refresh_delivery_snapshot,
    closeout_inventory_text,
    compose_closeout_text,
)

from factory_emit import _emit_next_action  # noqa: E402
from factory_mutating import mutates_state  # noqa: E402


def _base_pr_body(slug: str) -> str:
    return "\n".join(
        [
            "## Workflow",
            f"- slug: `{slug}`",
            f"- spec: `docs/workflow/feature-runs/{slug}/spec.md`",
            f"- plan: `docs/workflow/feature-runs/{slug}/plan.md`",
            f"- tasks: `docs/workflow/feature-runs/{slug}/tasks.md`",
            "",
            "## Verification",
            "- Local workflow checkpoints completed through diff review.",
        ]
    )


def _issue_pr_body(pr: dict | None, slug: str) -> str:
    existing_body = str(pr.get("body", "")) if isinstance(pr, dict) else ""
    if not existing_body:
        return _base_pr_body(slug)
    return existing_body


def _current_pr_body_payload(pr_number: int | None = None) -> dict | None:
    cmd = ["gh", "pr", "view"]
    if pr_number is not None:
        cmd.append(str(pr_number))
    cmd.extend(["--json", "body"])
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        return None
    output = (result.stdout or "").strip() or "{}"
    return json.loads(output)


def _edit_pr_body(body: str) -> None:
    result = subprocess.run(["gh", "pr", "edit", "--body", body], text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(trim_detail(result.stderr or result.stdout or "PR body update failed"))


def _update_pr_body_from_state(slug: str, pr: dict | None) -> None:
    if not pr:
        raise SystemExit("deliver --refresh requires an open PR")
    body = _issue_pr_body(pr, slug)
    _edit_pr_body(body)


def _normalize_pr_merge_fields(pr: dict | None) -> tuple[str, str]:
    if not pr:
        return "", ""
    merge_commit = pr.get("mergeCommit")
    if isinstance(merge_commit, dict):
        merged_sha = str(merge_commit.get("oid", "") or "")
    elif isinstance(merge_commit, str):
        merged_sha = merge_commit
    else:
        merged_sha = ""
    merged_at = str(pr.get("mergedAt", "") or "")
    return merged_sha, merged_at


def _mark_delivery_state(slug: str, updates: dict) -> None:
    update_workflow_state(
        slug,
        lambda state: state.setdefault(DELIVERY_KEY, {}).update(updates),
    )


def _poll_merge_wait(slug: str, pr_number: int, interval_seconds: int = 60) -> dict | None:
    with HeartbeatEmitter(slug, "deliver") as hb:
        hb.set_activity(f"waiting for PR merge (PR #{pr_number})")
        while True:
            hb.set_activity(f"waiting for PR merge (PR #{pr_number})")
            heartbeat_set_activity(f"waiting for PR merge (PR #{pr_number})")
            pr = current_pr_payload(pr_number)
            if not pr:
                raise SystemExit("failed to read PR state while waiting for merge")

            state_value = str(pr.get("state", "") or "").upper()
            merged_sha, merged_at = _normalize_pr_merge_fields(pr)
            if merged_sha or merged_at or state_value == "MERGED":
                _mark_delivery_state(
                    slug,
                    {
                        "merge_wait_state": "merged",
                        "merged_sha": merged_sha,
                        "merged_at_iso8601": merged_at,
                    },
                )
                return pr
            if state_value == "CLOSED":
                _mark_delivery_state(
                    slug,
                    {
                        "merge_wait_state": "failed",
                    },
                )
                return pr
            time.sleep(interval_seconds)


def _resume_merge_wait_if_needed(slug: str, delivery: dict) -> int:
    merge_wait_state = str(delivery.get("merge_wait_state", "none") or "none")
    pr_number = delivery.get("pr_number")
    if merge_wait_state == "waiting" and pr_number:
        _poll_merge_wait(slug, int(pr_number))
        return 0

    if merge_wait_state == "merged":
        merged_sha = str(delivery.get("merged_sha", "") or "")
        merged_at = str(delivery.get("merged_at_iso8601", "") or "")
        if merged_sha and merged_at:
            print("nothing to resume")
            return 0
        if not pr_number:
            print("nothing to resume")
            return 0
        pr = current_pr_payload(int(pr_number))
        if not pr:
            print("nothing to resume")
            return 0
        merged_sha, merged_at = _normalize_pr_merge_fields(pr)
        if not merged_sha and not merged_at:
            print("nothing to resume")
            return 0
        _mark_delivery_state(
            slug,
            {
                "merge_wait_state": "merged",
                "merged_sha": merged_sha,
                "merged_at_iso8601": merged_at,
            },
        )
        print("nothing to resume")
        return 0

    print("nothing to resume")
    return 0


def _deliver_from_explicit_pr(args: argparse.Namespace) -> int:
    """Record the delivery snapshot from operator-provided PR info, no gh.

    This is the Claude-only web path: there is no gh CLI (GitHub is reached
    via MCP), so the operator passes --pr-number / --pr-url (and optionally
    --merge-sha). We skip every gh query and write the delivery state from
    these values, mirroring how the standalone closeout records its snapshot.
    """
    pr_number = getattr(args, "pr_number", None)
    pr_url = str(getattr(args, "pr_url", None) or "")
    merge_sha = str(getattr(args, "merge_sha", None) or "")
    branch = current_branch_name() or ""
    head_sha = git_output("rev-parse", "HEAD") or ""
    delivery_record = {
        "branch": branch,
        "head_sha": head_sha,
        "updated_at": int(time.time()),
        "pr_number": int(pr_number),
        "pr_url": pr_url,
        "merge_sha": merge_sha,
        "merged_sha": merge_sha,
        "upstream": upstream_branch_name() or "",
        "checks_summary": "not-checked",
        "merge_wait_state": "none",
    }
    update_workflow_state(
        args.slug,
        lambda state: state.__setitem__(DELIVERY_KEY, delivery_record),
    )
    print(f"branch: {branch or '(unknown)'}")
    print(f"head: {head_sha}")
    print(f"pr: #{int(pr_number)} — {pr_url}" if pr_url else f"pr: #{int(pr_number)}")
    if merge_sha:
        print(f"merge-sha: {merge_sha}")
    print("checks: not-checked (recorded from explicit PR args; no gh)")
    _emit_next_action(args.slug, "deliver")
    return 0


@mutates_state("deliver")
def command_deliver(args: argparse.Namespace) -> int:
    ensure_sync()
    # Claude-only web path: when the operator supplies explicit PR info, record
    # the delivery snapshot from it and skip all gh-dependent flow. Requires at
    # least --pr-number and --pr-url so the snapshot is identifiable.
    explicit_pr_number = getattr(args, "pr_number", None)
    explicit_pr_url = getattr(args, "pr_url", None)
    if explicit_pr_number is not None or explicit_pr_url:
        if explicit_pr_number is None or not explicit_pr_url:
            raise SystemExit(
                "deliver --pr-number and --pr-url must be provided together "
                "(the Claude-only web path records delivery from both)"
            )
        return _deliver_from_explicit_pr(args)
    if not command_path("gh"):
        raise SystemExit(
            "deliver requires the gh CLI, or explicit PR info for the "
            "Claude-only web path. Either install gh, or pass "
            "--pr-number <n> --pr-url <url> (and optionally --merge-sha <sha>) "
            "to record the delivery snapshot without gh (open/merge the PR via "
            "the GitHub MCP)."
        )
    # PR #751 / FF Housekeeping Slice 3: implementation-rule WARN.
    # Validate the override flag combo eagerly (so a bad invocation fails
    # fast), but defer the state-mutating override write until deliver gates
    # have all passed (per Codex diff-review regression MEDIUM #2 — don't
    # mutate state on an aborted/dry-run deliver).
    impl_rule_override_pending = False
    if getattr(args, "override_implementation_rule", False):
        impl_rule_override_reason = (getattr(args, "override_implementation_reason", None) or "").strip()
        if len(impl_rule_override_reason) < 10:
            print(
                "deliver --override-implementation-rule requires "
                "--override-implementation-reason of at least 10 characters",
                file=sys.stderr,
            )
            raise SystemExit(2)
        impl_rule_override_pending = True
    else:
        from factory_deliver import check_implementation_rule
        status, message = check_implementation_rule(args.slug)
        if status == "triggered":
            if message:
                # Per Codex diff-review regression MEDIUM #3: surface the
                # message so the guardrail doesn't disappear silently.
                print(message, file=sys.stderr)
        elif status == "suppressed":
            if message:
                print(message, file=sys.stderr)
        elif status == "skipped":
            if message:
                print(message, file=sys.stderr)
        elif status == "ok":
            pass
        else:
            raise RuntimeError(f"unexpected check_implementation_rule status: {status!r}")

    auth = subprocess.run(["gh", "auth", "status", "--active"], text=True, capture_output=True)
    if auth.returncode != 0:
        raise SystemExit(trim_detail(auth.stderr or auth.stdout or "GitHub authentication is not ready"))

    state = load_workflow_state(args.slug)

    if args.resume_merge_wait:
        delivery = state.get(DELIVERY_KEY, {})
        return _resume_merge_wait_if_needed(args.slug, delivery)

    if args.refresh:
        pr = _current_pr_body_payload()
        _update_pr_body_from_state(args.slug, pr)
        print(f"branch: {current_branch_name() or '(unknown)'}")
        print(f"head: {git_output('rev-parse', 'HEAD') or ''}")
        if pr and pr.get("url"):
            print(f"pr: {pr.get('url')}")
        _emit_next_action(args.slug, "deliver")
        return 0

    # PR #751 / FF Housekeeping Slice 3 — record the implementation-rule
    # override AFTER the early-exit paths (resume_merge_wait, refresh) have
    # been taken, so we don't mutate state on a no-op deliver.
    if impl_rule_override_pending:
        from factory_deliver import record_implementation_rule_override
        record_implementation_rule_override(args.slug, impl_rule_override_reason)

    for stage in REQUIRED_PREDELIVERY_STAGES:
        manifest_path = checkpoint_manifest_path(args.slug, stage)
        if not manifest_path.exists():
            raise SystemExit(f"deliver requires completed {stage} checkpoint first")
        healthy, detail = verify_checkpoint_manifest(manifest_path)
        if not healthy:
            raise SystemExit(f"deliver requires a healthy {stage} checkpoint first: {trim_detail(detail)}")

    recon_ok, recon_detail = reconciliation_state(args.slug)
    if not recon_ok:
        raise SystemExit(f"deliver requires terminal reconciliation first: {trim_detail(recon_detail)}")

    # Pre-deliver hygiene: a leftover merge-conflict marker is a mechanical
    # failure that should never reach CI. Block before the branch is pushed.
    from factory_deliver import find_conflict_markers
    conflicted = find_conflict_markers()
    if conflicted:
        listed = ", ".join(conflicted[:10]) + (" …" if len(conflicted) > 10 else "")
        raise SystemExit(
            f"deliver blocked: {len(conflicted)} changed file(s) contain unresolved git "
            f"conflict markers: {listed}. Resolve the conflicts and commit before delivering."
        )

    branch = current_branch_name()
    if not branch:
        raise SystemExit("deliver requires a named branch; detached HEAD is not supported")
    upstream = upstream_branch_name()
    head_sha = git_output("rev-parse", "HEAD") or ""
    diff_budget = diff_review_budget_state(args.slug)
    if diff_budget.get("head_mismatch"):
        recorded_head = str(diff_budget.get("recorded_head_sha", ""))[:12]
        current_head = str(diff_budget.get("current_head_sha", ""))[:12]
        raise SystemExit(
            f"deliver requires the current branch to match the reviewed diff HEAD; "
            f"diff artifact HEAD {recorded_head} does not match current HEAD {current_head}. "
            "Rerun reconcile first so the diff manifest can auto-reseal, or run "
            f"checkpoint --slug {args.slug} --stage diff --validation-only before delivering."
        )

    pr = current_pr_payload()
    pr_closed = bool(pr) and pr.get("state") != "OPEN"
    if pr_closed:
        pr = None

    if args.create_pr and not pr:
        if not upstream:
            raise SystemExit("deliver requires a published branch with an upstream before creating a PR")
        behind = commits_behind_upstream()
        if behind is not None and behind > 0:
            print(f"warning: branch is {behind} commit{'s' if behind != 1 else ''} behind upstream — consider rebasing before creating PR")
        title = args.title or f"Workflow: {args.slug}"
        body = _issue_pr_body(None, args.slug)
        cmd = [
            "gh",
            "pr",
            "create",
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ]
        if args.base:
            cmd.extend(["--base", args.base])
        if args.draft:
            cmd.append("--draft")
        if args.dry_run:
            cmd.append("--dry-run")
        result = subprocess.run(cmd, text=True, capture_output=True)
        if result.returncode != 0:
            raise SystemExit(trim_detail(result.stderr or result.stdout or "PR creation failed"))
        if args.dry_run:
            print((result.stdout or "").strip())
        else:
            pr = current_pr_payload()

    if (args.watch_ci or args.merge_when_green or args.auto_merge) and not pr:
        raise SystemExit("deliver requires an open PR before watching checks or merging")

    if pr:
        head_mismatch = bool(pr.get("headRefOid")) and bool(head_sha) and pr.get("headRefOid") != head_sha
        if head_mismatch and (args.merge_when_green or args.auto_merge):
            raise SystemExit(
                f"local HEAD {head_sha[:12]} does not match PR head {str(pr.get('headRefOid'))[:12]}; "
                "push the branch or refresh the PR before merging"
            )
        checks_summary, checks, checks_detail = required_check_summary(
            pr.get("number"),
            watch=args.watch_ci and not args.dry_run,
            interval=args.interval,
        )
        delivery_record = build_delivery_record(pr, checks_summary, checks, branch, head_sha)
        delivery_record["upstream"] = upstream or ""
        delivery_record["checks_detail"] = checks_detail
        delivery_record["head_mismatch"] = head_mismatch
        _update_pr_body_from_state(args.slug, pr)

        if args.dry_run:
            print(f"branch: {branch}")
            print(f"head: {head_sha}")
            print(f"pr: {delivery_record.get('pr_url', '') or 'not created'}")
            print(f"checks: {checks_summary}")
            if delivery_record.get("merge_state_status"):
                print(f"merge-state: {delivery_record.get('merge_state_status')}")
            if head_mismatch:
                print("head-mismatch: yes")
            return 0

        if args.auto_merge and not args.dry_run:
            merge_cmd = [
                "gh",
                "pr",
                "merge",
                str(pr["number"]),
                "--auto",
                "--squash",
                "--match-head-commit",
                head_sha,
            ]
            result = subprocess.run(merge_cmd, text=True, capture_output=True)
            if result.returncode != 0:
                raise SystemExit(trim_detail(result.stderr or result.stdout or "Auto-merge enable failed"))
            refreshed_delivery = refresh_delivery_snapshot(delivery_record)
            update_workflow_state(
                args.slug,
                lambda state: state.__setitem__(DELIVERY_KEY, refreshed_delivery),
            )
        elif args.merge_when_green and not args.dry_run:
            if checks_summary != "pass":
                raise SystemExit(f"cannot merge while required checks are {checks_summary}")
            merged_pr_number = pr["number"]
            merge_cmd = [
                "gh",
                "pr",
                "merge",
                str(merged_pr_number),
                "--squash",
                "--match-head-commit",
                head_sha,
            ]
            result = subprocess.run(merge_cmd, text=True, capture_output=True)
            if result.returncode != 0:
                raise SystemExit(trim_detail(result.stderr or result.stdout or "Merge failed"))
            pr = current_pr_payload()
            checks_summary, checks, checks_detail = required_check_summary(merged_pr_number, watch=False)
            update_workflow_state(
                args.slug,
                lambda state: state.__setitem__(
                    DELIVERY_KEY,
                    {
                        **build_delivery_record(pr, checks_summary, checks, branch, head_sha),
                        "upstream": upstream or "",
                        "checks_detail": checks_detail,
                        "head_mismatch": False,
                    },
                ),
            )
        else:
            update_workflow_state(
                args.slug,
                lambda state: state.__setitem__(DELIVERY_KEY, delivery_record),
            )

        if pr.get("number") and not args.dry_run:
            update_workflow_state(
                args.slug,
                lambda state: state.setdefault(DELIVERY_KEY, {}).update({"merge_wait_state": "waiting"}),
            )
            _poll_merge_wait(args.slug, int(pr["number"]))

    else:
        if args.dry_run:
            print(f"branch: {branch}")
            print(f"head: {head_sha}")
            print("pr: not created (dry-run)")
            return 0
        if pr_closed:
            # PR is already merged or closed — preserve the recorded delivery state
            # rather than overwriting pr_url with "" and resetting the workflow.
            state_snapshot = load_workflow_state(args.slug).get(DELIVERY_KEY, {})
            print(f"branch: {branch}")
            print(f"head: {head_sha}")
            print(f"pr: {state_snapshot.get('pr_url', '')} (already closed/merged, state preserved)")
            return 0
        update_workflow_state(
            args.slug,
            lambda state: state.__setitem__(
                DELIVERY_KEY,
                {
                    "branch": branch,
                    "head_sha": head_sha,
                    "updated_at": int(time.time()),
                    "upstream": upstream or "",
                    "pr_url": "",
                    "checks_summary": "not-started",
                },
            ),
        )

    delivery = load_workflow_state(args.slug).get(DELIVERY_KEY, {})
    print(f"branch: {branch}")
    print(f"head: {head_sha}")
    if delivery.get("pr_url"):
        print(f"pr: {delivery.get('pr_url')}")
        print(f"checks: {delivery.get('checks_summary', 'unknown')}")
        if delivery.get("merge_state_status"):
            print(f"merge-state: {delivery.get('merge_state_status')}")
        if delivery.get("head_mismatch"):
            print("head-mismatch: yes")
        if delivery.get("merge_wait_state") and delivery.get("merge_wait_state") != "none":
            print(f"merge-wait: {delivery.get('merge_wait_state')}")
    else:
        print("pr: not created")
    _emit_next_action(args.slug, "deliver")
    return 0


@mutates_state("workflow-closeout")
def command_workflow_closeout(args: argparse.Namespace) -> int:
    from factory_cmd_checkpoint import command_checkpoint  # noqa: E402
    ensure_sync()
    root = workflow_dir(args.slug)
    reviews = gather_all_review_paths(args.slug, include_closeout=False)
    plan_path = root / "plan.md"
    state = load_workflow_state(args.slug)
    delivery = refresh_delivery_snapshot(state.get(DELIVERY_KEY, {}))
    if not delivery.get("pr_url"):
        raise SystemExit("closeout requires a delivered PR first")
    if delivery.get("checks_summary") != "pass":
        raise SystemExit(
            f"closeout requires passing checks first; "
            f"current checks are {delivery.get('checks_summary', 'unknown')}"
        )

    missing_stages = []
    for stage in VERIFY_ON_CLOSEOUT_STAGES:
        manifest_path = checkpoint_manifest_path(args.slug, stage)
        if not manifest_path.exists():
            missing_stages.append(stage)
    if missing_stages:
        raise SystemExit(
            "Closeout requires completed checkpoint manifests for: "
            + ", ".join(missing_stages)
        )

    from factory_git import run  # noqa: E402
    for stage in VERIFY_ON_CLOSEOUT_STAGES:
        manifest_path = checkpoint_manifest_path(args.slug, stage)
        run([sys.executable, str(VERIFY_CHECKPOINT), "--checkpoint-manifest", str(manifest_path)])

    if reviews:
        cmd = [
            sys.executable,
            str(VERIFY_RECONCILIATION),
            "--plan",
            str(plan_path),
            "--require-terminal",
        ]
        for review in reviews:
            cmd.extend(["--review", str(review)])
        run(cmd)

    summary_path = root / "closeout.md"
    closeout_manifest_path = checkpoint_manifest_path(args.slug, "closeout")
    dirty_override = state.get(DIRTY_OVERRIDE_KEY, {})
    checkpoint_fallback = state.get(CHECKPOINT_FALLBACK_KEY, {})
    backup_text = summary_path.read_text(encoding="utf-8") if summary_path.exists() else None
    backup_manifest = closeout_manifest_path.read_text(encoding="utf-8") if closeout_manifest_path.exists() else None
    inventory_text = closeout_inventory_text(args.slug, root, plan_path, reviews, delivery, dirty_override, checkpoint_fallback)
    summary_text = compose_closeout_text(backup_text or "", inventory_text)
    summary_path.write_text(summary_text, encoding="utf-8")

    closeout_args = argparse.Namespace(
        slug=args.slug,
        stage="closeout",
        artifact=str(summary_path),
        base_ref=None,
        context=[],
        path=[],
        extra_gemini_lens=[],
        sensitive=False,
        large_structural=False,
        performance_sensitive=False,
        use_existing_artifact=True,
        allow_dirty_path=[],
        max_artifact_chars=None,
        max_context_chars=None,
        max_total_chars=None,
        gemini_timeout_seconds=120,
        gemini_retries=1,
        repair_timeout_seconds=300,
        fallback=False,
        allow_large_diff_rerun=False,
    )
    checkpoint_result = command_checkpoint(closeout_args)
    if checkpoint_result != 0:
        if backup_text is None:
            summary_path.unlink(missing_ok=True)
        else:
            summary_path.write_text(backup_text, encoding="utf-8")
        if backup_manifest is None:
            closeout_manifest_path.unlink(missing_ok=True)
        else:
            closeout_manifest_path.write_text(backup_manifest, encoding="utf-8")
        return checkpoint_result

    refreshed_state = load_workflow_state(args.slug)
    refreshed_delivery = refresh_delivery_snapshot(refreshed_state.get(DELIVERY_KEY, {}))
    refreshed_inventory = closeout_inventory_text(
        args.slug,
        root,
        plan_path,
        reviews,
        refreshed_delivery,
        refreshed_state.get(DIRTY_OVERRIDE_KEY, {}),
        refreshed_state.get(CHECKPOINT_FALLBACK_KEY, {}),
    )
    refreshed_summary = compose_closeout_text(backup_text or "", refreshed_inventory)
    if refreshed_summary != summary_text:
        summary_path.write_text(refreshed_summary, encoding="utf-8")
        checkpoint_result = command_checkpoint(closeout_args)
        if checkpoint_result != 0:
            if backup_text is None:
                summary_path.unlink(missing_ok=True)
            else:
                summary_path.write_text(backup_text, encoding="utf-8")
            if backup_manifest is None:
                closeout_manifest_path.unlink(missing_ok=True)
            else:
                closeout_manifest_path.write_text(backup_manifest, encoding="utf-8")
            return checkpoint_result

    print(str(summary_path))
    _emit_next_action(args.slug, "closeout")
    return 0
