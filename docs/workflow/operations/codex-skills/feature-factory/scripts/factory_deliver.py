#!/usr/bin/env python3
"""GitHub CLI interaction, delivery records, and closeout text generation."""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Literal

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    REPO_ROOT,
    load_checkpoint_manifest,
    load_workflow_state,
    is_ancestor_of_head,
    update_workflow_state,
)

REVIEW_SCRIPTS = REPO_ROOT / "docs" / "workflow" / "operations" / "codex-skills" / "review-lens" / "scripts"
if str(REVIEW_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(REVIEW_SCRIPTS))

from workflow_utils import resolve_stored_path  # noqa: E402

from factory_git import current_branch_name, command_path  # noqa: E402
from factory_stages import VERIFY_ON_CLOSEOUT_STAGES  # noqa: E402


# ---------------------------------------------------------------------------
# Implementation rule check (PR #751 / FF Housekeeping Slice 3)
# ---------------------------------------------------------------------------

_IMPLEMENTATION_RULE_THRESHOLD = 200
_IMPLEMENTATION_RULE_CODE_GLOBS = ("*.py", "*.ts", "*.tsx", "*.js", "*.jsx")
_IMPLEMENTATION_RULE_TEST_EXCLUDES = (
    ":!**/test_*",
    ":!**/*_test.py",
    ":!**/tests/**",
)


def _resolve_branch_base() -> str | None:
    """Find a sane branch base.

    Tries in order:
      1. git merge-base origin/main HEAD
      2. git merge-base --fork-point origin/main HEAD
         (correct branch-point even on long-lived branches > 50 commits)
      3. git merge-base main HEAD
         (last resort; local main may have diverged from origin/main)
      4. None — caller skips the implementation-rule check entirely
         (per spec FR-021: honest skip beats silent under-report)
    """
    candidates: list[list[str]] = [
        ["git", "merge-base", "origin/main", "HEAD"],
        ["git", "merge-base", "--fork-point", "origin/main", "HEAD"],
        ["git", "merge-base", "main", "HEAD"],
    ]
    for cmd in candidates:
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError, OSError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


# ---------------------------------------------------------------------------
# Pre-deliver hygiene: unresolved git conflict markers
# ---------------------------------------------------------------------------

# The empty-tree SHA — used as a fallback base when no branch base can be
# resolved (detached HEAD, no origin), so the whole tree is treated as changed
# and still gets scanned rather than silently skipping the gate.
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# A genuine conflict always carries the angle-bracket markers. We do NOT match a
# bare ``=======`` separator on its own: it collides with reStructuredText and
# Markdown section underlines, and the angle-bracket markers are sufficient.
_CONFLICT_MARKER_RE = re.compile(r"^(?:<<<<<<< |>>>>>>> |\|\|\|\|\|\|\| )", re.MULTILINE)


def find_conflict_markers() -> list[str]:
    """Return repo-relative paths of branch-changed files with git conflict markers.

    Scans only files changed since the merge-base with the default branch, so
    unrelated pre-existing content elsewhere is never flagged. Binary or
    unreadable files are skipped. Never raises — a scan failure returns ``[]``
    rather than blocking a legitimate delivery.
    """
    base = _resolve_branch_base() or _EMPTY_TREE_SHA
    cmd = [
        "git", "-C", str(REPO_ROOT), "diff",
        "--name-only", "--diff-filter=ACMR", base, "HEAD",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []
    if result.returncode != 0:
        return []
    flagged: list[str] = []
    for rel in result.stdout.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        target = REPO_ROOT / rel
        if not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable — not a text conflict
        if _CONFLICT_MARKER_RE.search(text):
            flagged.append(rel)
    return sorted(flagged)


def _added_code_lines(branch_base: str) -> int | None:
    """Count added lines in code files (not tests, not docs) since branch_base."""
    cmd = [
        "git",
        "diff",
        "--numstat",
        f"{branch_base}..HEAD",
        "--",
        *_IMPLEMENTATION_RULE_CODE_GLOBS,
        *_IMPLEMENTATION_RULE_TEST_EXCLUDES,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    total_added = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        added_str = parts[0]
        if added_str == "-":
            continue
        try:
            total_added += int(added_str)
        except ValueError:
            continue
    return total_added


def _recompute_lines_for_dispatch(
    entry: dict, current_branch_base: str | None
) -> int | None:
    """Recompute lines_added for a dispatch entry against the current branch base.

    Used when entry.branch_base_sha differs from current_branch_base, OR when
    entry.lines_added_at_dispatch_time is None (recovery from a transient base
    failure at dispatch time).

    Returns None on any subprocess exception (malformed SHA, missing object,
    git not on PATH, timeout) — never raises.
    """
    head_sha = entry.get("head_sha")
    if not head_sha or not isinstance(head_sha, str) or not current_branch_base:
        return None
    cmd = [
        "git", "diff", "--numstat",
        f"{current_branch_base}..{head_sha}",
        "--",
        *_IMPLEMENTATION_RULE_CODE_GLOBS,
        *_IMPLEMENTATION_RULE_TEST_EXCLUDES,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    total_added = 0
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        added_str = parts[0]
        if added_str == "-":
            continue
        try:
            total_added += int(added_str)
        except ValueError:
            continue
    return total_added


def check_implementation_rule(slug: str) -> tuple[Literal["triggered", "suppressed", "skipped", "ok"], str]:
    """Return (status, message). See spec FR-012-016."""
    branch_base = _resolve_branch_base()
    if branch_base is None:
        message = (
            "implementation-rule check skipped — could not resolve branch base "
            "(origin/main, fork-point, main all failed)"
        )
        print(message, file=sys.stderr)
        return ("skipped", message)
    added = _added_code_lines(branch_base)
    if added is None:
        message = "implementation-rule check skipped: git diff failed."
        print(message, file=sys.stderr)
        return ("skipped", message)
    if added < _IMPLEMENTATION_RULE_THRESHOLD:
        return ("ok", "")

    state = load_workflow_state(slug)
    dispatches = state.get("codex_dispatches") or []
    fresh_entries: list[tuple[dict, int]] = []
    for entry in dispatches:
        if not isinstance(entry, dict):
            continue
        if entry.get("exit_code") != 0:
            continue
        if not is_ancestor_of_head(entry.get("head_sha")):
            continue
        entry_base = entry.get("branch_base_sha")
        entry_lines = entry.get("lines_added_at_dispatch_time")
        if entry_base == branch_base and isinstance(entry_lines, int):
            snapshot = entry_lines
        else:
            snapshot = _recompute_lines_for_dispatch(entry, branch_base)
        if snapshot is None:
            continue
        if abs(added - snapshot) > 50:
            continue
        fresh_entries.append((entry, snapshot))

    if fresh_entries:
        best_entry, best_snapshot = max(
            fresh_entries,
            key=lambda pair: pair[0].get("ts") or "",
        )
        head_short = (best_entry.get("head_sha") or "")[:7]
        note = (
            f"implementation-rule check skipped — covered by codex dispatch "
            f"{best_entry.get('ts')} (sha {head_short}, +{best_snapshot})"
        )
        return ("suppressed", note)

    head_result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    )
    head_sha = head_result.stdout.strip() if head_result.returncode == 0 else ""
    override = state.get("implementation_rule_override") or {}
    if isinstance(override, dict) and override.get("head_sha") == head_sha and head_sha:
        return ("ok", "")

    msg = (
        f"⚠ implementation-rule: {added} non-test code lines added with no recorded "
        f"Codex dispatch. The postmortem must explain why Claude implemented this PR. "
        f"Suppress with --override-implementation-rule "
        f"--override-implementation-reason \"<text>\" (>= 10 chars)."
    )
    return ("triggered", msg)


def record_implementation_rule_override(slug: str, reason: str) -> None:
    head_result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    )
    head_sha = head_result.stdout.strip() if head_result.returncode == 0 else ""
    operator = os.environ.get("USER") or "unknown"
    override = {
        "at": int(time.time()),
        "reason": reason.strip(),
        "operator": operator,
        "head_sha": head_sha,
    }

    def _mutate(state: dict) -> None:
        state["implementation_rule_override"] = override

    update_workflow_state(slug, _mutate)


# ---------------------------------------------------------------------------
# GitHub CLI helpers
# ---------------------------------------------------------------------------


def gh_json(args: list[str]) -> dict | list:
    result = subprocess.run(["gh", *args], text=True, capture_output=True)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "gh command failed").strip()
        raise SystemExit(message)
    output = result.stdout.strip() or "{}"
    return json.loads(output)


def current_pr_payload(pr_number: int | None = None) -> dict | None:
    cmd = [
        "gh",
        "pr",
        "view",
    ]
    if pr_number is not None:
        cmd.append(str(pr_number))
    cmd.extend(
        [
            "--json",
            "number,url,state,isDraft,headRefName,headRefOid,baseRefName,mergeable,mergeStateStatus,statusCheckRollup,body,mergeCommit,mergedAt",
        ]
    )
    result = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def _normalize_iso8601_utc(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    candidate = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return value.strip()
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def required_check_summary(pr_number: int | None = None, watch: bool = False, interval: int = 10) -> tuple[str, list[dict], str]:
    cmd = ["gh", "pr", "checks"]
    if pr_number is not None:
        cmd.append(str(pr_number))
    cmd.extend(["--required", "--json", "name,state,bucket,link,workflow"])
    if watch:
        cmd.extend(["--watch", "--interval", str(interval)])
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode not in {0, 8}:
        message = (result.stderr or result.stdout or "gh pr checks failed").strip()
        raise SystemExit(message)
    stdout = result.stdout.strip() or "[]"
    checks = json.loads(stdout)
    buckets = {item.get("bucket", "") for item in checks}
    if not checks:
        summary = "unknown"
    elif "fail" in buckets or "cancel" in buckets:
        summary = "fail"
    elif "pending" in buckets:
        summary = "pending"
    elif buckets <= {"pass", "skipping"}:
        summary = "pass"
    else:
        summary = "unknown"
    detail = (result.stderr or "").strip()
    return summary, checks, detail


def build_delivery_record(pr: dict | None, checks_summary: str, checks: list[dict], branch: str, head_sha: str) -> dict:
    merge_commit = ""
    if pr:
        raw_merge_commit = pr.get("mergeCommit")
        if isinstance(raw_merge_commit, dict):
            merge_commit = str(raw_merge_commit.get("oid", "") or "")
        elif isinstance(raw_merge_commit, str):
            merge_commit = raw_merge_commit
    return {
        "branch": branch,
        "head_sha": head_sha,
        "updated_at": int(time.time()),
        "pr_number": pr.get("number") if pr else None,
        "pr_url": pr.get("url") if pr else "",
        "pr_state": pr.get("state") if pr else "",
        "pr_is_draft": bool(pr.get("isDraft")) if pr else False,
        "pr_head_branch": pr.get("headRefName") if pr else "",
        "pr_head_sha": pr.get("headRefOid") if pr else "",
        "pr_base_branch": pr.get("baseRefName") if pr else "",
        "mergeable": pr.get("mergeable") if pr else "",
        "merge_state_status": pr.get("mergeStateStatus") if pr else "",
        "checks_summary": checks_summary,
        "required_checks": checks,
        "merge_wait_state": "none",
        "merged_sha": merge_commit,
        "merged_at_iso8601": _normalize_iso8601_utc(pr.get("mergedAt") if pr else ""),
    }


def gather_all_review_paths(slug: str, include_closeout: bool = True) -> list[Path]:
    stages = list(VERIFY_ON_CLOSEOUT_STAGES)
    if include_closeout:
        stages.append("closeout")
    paths: list[Path] = []
    for stage in stages:
        manifest = load_checkpoint_manifest(slug, stage) or {}
        for review in manifest.get("required_reviews", []):
            paths.append(resolve_stored_path(review["path"], REPO_ROOT))
    return paths


def refresh_delivery_snapshot(delivery: dict) -> dict:
    pr_number = delivery.get("pr_number")
    if not pr_number or not command_path("gh"):
        return delivery
    try:
        pr = current_pr_payload(int(pr_number))
    except Exception:
        return delivery
    if not pr:
        return delivery

    branch = str(delivery.get("branch", "") or pr.get("headRefName", ""))
    head_sha = str(delivery.get("head_sha", "") or pr.get("headRefOid", "") or "")
    try:
        checks_summary, checks, checks_detail = required_check_summary(int(pr_number), watch=False)
    except SystemExit:
        return delivery

    refreshed = build_delivery_record(pr, checks_summary, checks, branch, head_sha)
    refreshed["upstream"] = delivery.get("upstream", "")
    refreshed["checks_detail"] = checks_detail
    refreshed["head_mismatch"] = bool(pr.get("headRefOid")) and bool(head_sha) and pr.get("headRefOid") != head_sha
    previous_wait_state = str(delivery.get("merge_wait_state", "none") or "none")
    if str(pr.get("state", "")).upper() == "OPEN" and previous_wait_state == "waiting":
        refreshed["merge_wait_state"] = previous_wait_state
        refreshed["merged_sha"] = delivery.get("merged_sha", "")
        refreshed["merged_at_iso8601"] = delivery.get("merged_at_iso8601", "")
    return refreshed


# ---------------------------------------------------------------------------
# Closeout text generation
# ---------------------------------------------------------------------------


def closeout_inventory_text(
    slug: str,
    root: Path,
    plan_path: Path,
    reviews: list[Path],
    delivery: dict,
    dirty_override: dict,
    fallback: dict,
) -> str:
    lines = [
        f"# Closeout: {slug}",
        "",
        "## Outputs",
        f"- spec: `{root / 'spec.md'}`",
        f"- plan: `{plan_path}`",
        f"- tasks: `{root / 'tasks.md'}`",
        "",
        "## Reviews",
    ]
    for review in reviews:
        lines.append(f"- `{review}`")
    lines.extend(
        [
            "",
            "## Delivery",
            f"- branch: `{delivery.get('branch', current_branch_name() or '')}`",
            f"- pr: `{delivery.get('pr_url', '') or 'not-created'}`",
            f"- checks: `{delivery.get('checks_summary', 'unknown')}`",
            f"- merge-state: `{delivery.get('merge_state_status', '') or 'unknown'}`",
        ]
    )
    if dirty_override.get("used"):
        lines.append("- dirty-override: used")
        for path in dirty_override.get("allowed_dirty_paths", []):
            lines.append(f"- allow-dirty-path: `{path}`")
    if fallback.get("used"):
        lines.extend(
            [
                "",
                "## Fallback",
                f"- stage: `{fallback.get('stage', '')}`",
                f"- reason: `{fallback.get('reason', '')}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Next Action",
            "- Share the closeout summary with the user and call out any deferred or open risks.",
        ]
    )
    return "\n".join(lines) + "\n"


def compose_closeout_text(existing_text: str, inventory_text: str) -> str:
    if not existing_text.strip():
        return inventory_text
    if "## Workflow Inventory" in existing_text:
        before, _ = existing_text.split("## Workflow Inventory", 1)
        return before.rstrip() + "\n\n" + inventory_text
    return existing_text.rstrip() + "\n\n" + inventory_text
