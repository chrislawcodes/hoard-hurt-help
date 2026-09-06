#!/usr/bin/env python3
"""Next-action decision tree for the feature factory workflow."""
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from factory_state import (  # noqa: E402
    BLOCKED_KEY,
    DISCOVERY_KEY,
    DELIVERY_KEY,
    PARALLEL_ANALYSIS_KEY,
    blocking_unresolved_items,
    load_workflow_state,
    workflow_dir,
)

from factory_stages import (  # noqa: E402
    arch_docs_resolved,
    diff_review_budget_state,
    later_progress_exists,
    status_md_changed_since_init,
)


def recommended_next_action(
    slug: str,
    state: dict | None,
    stages: dict[str, dict[str, object]],
    reconciliation_ok: bool,
) -> str:
    if state is None:
        state = load_workflow_state(slug)
    blocked = state.get(BLOCKED_KEY, {})
    if blocked.get("active"):
        return "mark_blocked"
    discovery = state.get(DISCOVERY_KEY, {})
    if blocking_unresolved_items(discovery) or (discovery.get("required") and not discovery.get("complete")):
        return "discover"

    if not stages["spec"]["artifact_exists"] or not stages["spec"]["artifact_meaningful"]:
        if later_progress_exists(stages, "spec")[0]:
            return "mark_blocked"
        return "author_spec"
    if not stages["spec"]["manifest_exists"] or not stages["spec"]["healthy"]:
        return "run_spec_checkpoint"
    if not stages["plan"]["artifact_exists"] or not stages["plan"]["artifact_meaningful"]:
        if later_progress_exists(stages, "plan")[0]:
            return "mark_blocked"
        return "author_plan"
    if not stages["plan"]["manifest_exists"] or not stages["plan"]["healthy"]:
        return "run_plan_checkpoint"
    if not stages["tasks"]["artifact_exists"] or not stages["tasks"]["artifact_meaningful"]:
        if later_progress_exists(stages, "tasks")[0]:
            return "mark_blocked"
        return "author_tasks"
    parallel = state.get(PARALLEL_ANALYSIS_KEY, {})
    if not parallel.get("reviewed"):
        return "record_parallel_analysis"
    if not stages["tasks"]["manifest_exists"] or not stages["tasks"]["healthy"]:
        return "run_tasks_checkpoint"
    if not stages["diff"]["artifact_exists"]:
        return "dispatch_next_slice_to_codex"
    if not stages["diff"]["manifest_exists"] or not stages["diff"]["healthy"]:
        return "run_diff_checkpoint"
    # Re-run the diff only when reviewed-scope code changed since the review — the
    # factory's own post-diff bookkeeping commits must not block reaching `done`.
    if diff_review_budget_state(slug).get("in_scope_change"):
        return "run_diff_checkpoint"
    if not reconciliation_ok:
        return "reconcile_reviews"
    # If any tasks remain unchecked there are more slices to implement before delivering.
    tasks_path = workflow_dir(slug) / "tasks.md"
    if tasks_path.exists() and "- [ ]" in tasks_path.read_text(encoding="utf-8"):
        return "dispatch_next_slice_to_codex"
    # Import here to avoid circular — refresh_delivery_snapshot is in factory_deliver
    from factory_deliver import refresh_delivery_snapshot  # noqa: E402
    delivery = refresh_delivery_snapshot(state.get(DELIVERY_KEY, {}))
    if not delivery.get("pr_url"):
        return "deliver"
    if delivery.get("head_mismatch"):
        return "deliver"
    if delivery.get("checks_summary") in {"pending", "fail", "unknown"}:
        return "deliver"
    if not stages["closeout"]["manifest_exists"]:
        return "closeout"
    if not stages["closeout"]["healthy"]:
        return "run_closeout_checkpoint"
    postmortem_path = workflow_dir(slug) / "postmortem.md"
    if not postmortem_path.exists() or not postmortem_path.read_text(encoding="utf-8").strip():
        return "write_postmortem"
    if not status_md_changed_since_init(slug):
        return "update_status_md"
    if not arch_docs_resolved(slug):
        return "reconcile_arch_docs"
    return "done"
