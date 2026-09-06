#!/usr/bin/env python3
"""Judge-panel PR body rendering and sentinel replacement helpers."""
from __future__ import annotations

from datetime import datetime, timezone
import textwrap
from typing import Any

SENTINEL_BEGIN = "<!-- ff-judge-panel:begin -->"
SENTINEL_END = "<!-- ff-judge-panel:end -->"


def now_iso8601_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _coerce_mapping(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _coerce_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _excerpt(text: object, limit: int = 120) -> str:
    if not isinstance(text, str):
        return ""
    normalized = " ".join(text.split())
    return textwrap.shorten(normalized, width=limit, placeholder="...")


def _findings_pushed_aside(resolved_concerns: list[dict]) -> list[dict]:
    """Return just the deferred + dismissed concerns, in stage order.

    These are the findings the operator explicitly chose NOT to fix — either
    pushed to follow-up (`deferred`) or judged invalid (`dismissed`). They
    render in a dedicated PR-body section with plain-language explanations so
    a human reviewer can see the compromises before merging.
    """
    stage_order = {"spec": 0, "plan": 1, "tasks": 2, "diff": 3, "closeout": 4}
    pushed: list[dict] = []
    for concern in resolved_concerns:
        if concern.get("deferred_reason") or concern.get("dismissed_reason"):
            pushed.append(concern)
    pushed.sort(key=lambda c: (stage_order.get(str(c.get("stage", "")), 99), str(c.get("judge", ""))))
    return pushed


def _deferred_reviews(slug: str | None) -> list[dict]:
    """P3-7 (adversarial-review finding): surface deferred/failed reviews.

    A review can be `resolution_status: deferred` when the reviewer subprocess
    failed (timeout, abort, fallback). Those deferrals never make it into
    state.stages[X].unresolved_concerns — they live only in the review file
    frontmatter. Without including them in the summary, a human reviewer
    reading the PR body would see "no concerns pushed aside" even when an
    entire review path was skipped.
    """
    if not slug:
        return []
    try:
        from pathlib import Path as _Path
        import re as _re
        repo_root = _Path(__file__).resolve().parents[5]
        reviews_dir = repo_root / "docs" / "workflow" / "feature-runs" / slug / "reviews"
        if not reviews_dir.exists():
            return []
    except Exception:
        return []

    deferred: list[dict] = []
    frontmatter_re = _re.compile(r"^---\n(.*?)\n---", _re.DOTALL)
    for review_path in sorted(reviews_dir.glob("*.review.md")):
        try:
            content = review_path.read_text(encoding="utf-8")
        except OSError:
            continue
        match = frontmatter_re.match(content)
        if not match:
            continue
        frontmatter = match.group(1)
        status_match = _re.search(r'resolution_status:\s*"([^"]+)"', frontmatter)
        if not status_match or status_match.group(1) != "deferred":
            continue
        note_match = _re.search(r'resolution_note:\s*"([^"]*)"', frontmatter)
        reviewer_match = _re.search(r'reviewer:\s*"([^"]+)"', frontmatter)
        lens_match = _re.search(r'lens:\s*"([^"]+)"', frontmatter)
        stage_match = _re.search(r'stage:\s*"([^"]+)"', frontmatter)
        deferred.append(
            {
                "path": str(review_path.relative_to(repo_root)),
                "stage": stage_match.group(1) if stage_match else "?",
                "reviewer": reviewer_match.group(1) if reviewer_match else "?",
                "lens": lens_match.group(1) if lens_match else "?",
                "note": note_match.group(1) if note_match else "",
            }
        )
    return deferred


def render_findings_pushed_aside_block(
    resolved_concerns: list[dict], slug: str | None = None
) -> list[str]:
    """Render the plain-language 'Findings Pushed Aside' PR-body section.

    Produces the same human-readable summary for every feature the Factory
    ships. Reviewers reading a PR should never have to dig through state.json
    or review files to see which adversarial findings got waved off.

    ``slug`` is optional for back-compat — when provided, the section also
    surfaces review-level deferrals (runner failures, fallbacks) per P3-7.
    """
    pushed = _findings_pushed_aside(resolved_concerns)
    deferred_reviews = _deferred_reviews(slug) if slug else []
    if not pushed and not deferred_reviews:
        return []

    lines: list[str] = [
        "## Findings Pushed Aside",
        "",
        "These are concerns that reviewers or judges flagged during the Feature "
        "Factory review cycle that the feature author explicitly chose **not to fix** "
        "in this PR. Each has a reason. Read them before merging.",
        "",
    ]
    for concern in pushed:
        stage = str(concern.get("stage", "") or "?")
        judge = str(concern.get("judge", "") or "?")
        concern_id = str(concern.get("id", "") or "")
        round_raised = concern.get("round_raised", concern.get("round", "?"))
        reasoning = str(concern.get("reasoning", "") or "").strip()
        summary = _excerpt(reasoning, limit=160) or "(no reasoning captured)"
        if concern.get("dismissed_reason"):
            action = "dismissed"
            reason = str(concern.get("dismissed_reason", "") or "").strip()
            action_label = "Why it was dismissed (reviewer was wrong)"
        else:
            action = "deferred"
            reason = str(concern.get("deferred_reason", "") or "").strip()
            action_label = "Why it was deferred (fix is follow-up work)"

        id_label = f" `{concern_id}`" if concern_id else ""
        lines.extend(
            [
                f"### {stage} stage — {judge} judge{id_label} ({action})",
                f"- **What was flagged:** {summary}",
                f"- **{action_label}:** {reason or '(no reason provided — operator should add one)'}",
                f"- **Round raised:** {round_raised}",
                "",
            ]
        )
    if deferred_reviews:
        lines.extend(
            [
                "### Skipped reviews (runner / tool failures)",
                "",
                "These are adversarial review passes that never produced usable findings — the "
                "reviewer subprocess timed out, aborted, or otherwise failed. A pushed-aside "
                "review is a gap in review coverage for this feature that a human merger should "
                "weigh explicitly.",
                "",
            ]
        )
        for review in deferred_reviews:
            note = review.get("note", "") or "(no resolution note)"
            lines.append(
                f"- **{review['stage']} / {review['reviewer']} / {review['lens']}** "
                f"([`{review['path']}`]({review['path']})): {note}"
            )
        lines.append("")
    return lines


def _concern_is_resolved(concern: dict) -> bool:
    """FR-005a — a concern is resolved once addressed, deferred, or dismissed.

    Resolution requires the STATE-BEARING field to be set (``addressed_at``,
    ``deferred_reason``, or ``dismissed_reason``). ``addressed_by`` alone is
    evidence, not resolution — a concern with only evidence would still
    block checkpoint per FR-004, so it must render as open in the PR body.

    Missing fields are treated as None (unresolved). Older concerns written
    before FR-003 simply never populate these fields and keep rendering as
    open, which matches prior behavior.
    """
    return any(
        concern.get(field) is not None
        for field in ("addressed_at", "deferred_reason", "dismissed_reason")
    )


def collect_judge_panel_entries(
    state: dict,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    """Return (open_concerns, annotations, override, resolved_concerns).

    Open concerns render in the ``## ⚠ Unresolved Judge Concerns`` block.
    Resolved concerns render in a separate ``## Resolved Concerns`` block so
    the audit trail is preserved without confusing the operator about open
    items (FR-005a).
    """
    concerns: list[dict[str, Any]] = []
    resolved_concerns: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []
    stages = state.get("stages", {})
    if isinstance(stages, dict):
        for stage_name, stage_state in stages.items():
            stage_blob = _coerce_mapping(stage_state)
            if not stage_blob:
                continue
            for concern in _coerce_list(stage_blob.get("unresolved_concerns")):
                concern_blob = _coerce_mapping(concern)
                if not concern_blob:
                    continue
                merged = dict(concern_blob)
                merged.setdefault("stage", stage_name)
                if _concern_is_resolved(merged):
                    resolved_concerns.append(merged)
                else:
                    concerns.append(merged)
            for annotation in _coerce_list(stage_blob.get("annotations")):
                annotation_blob = _coerce_mapping(annotation)
                if not annotation_blob:
                    continue
                merged = dict(annotation_blob)
                merged.setdefault("stage", stage_name)
                annotations.append(merged)

    override = _coerce_mapping(state.get("override"))
    return concerns, annotations, override, resolved_concerns


def render_judge_panel_block(state: dict, slug: str | None = None) -> str:
    concerns, annotations, override, resolved_concerns = collect_judge_panel_entries(state)
    if not concerns and not annotations and not override and not resolved_concerns:
        return ""

    lines: list[str] = [SENTINEL_BEGIN]
    # Diff round-1 LOW finding: skip the unresolved heading when every concern
    # is resolved — otherwise operators see an empty section and mis-read it.
    if concerns:
        lines.append("## ⚠ Unresolved Judge Concerns")

    for concern in concerns:
        stage = str(concern.get("stage", "") or "")
        judge = str(concern.get("judge", "") or "")
        confidence = concern.get("confidence", "")
        reasoning = str(concern.get("reasoning", "") or "").strip()
        round_raised = concern.get("round_raised", concern.get("round", ""))
        concern_id = str(concern.get("id", "") or "")
        also_raised = concern.get("also_raised_in_round", concern.get("persisted_across_rounds", []))
        if not isinstance(also_raised, list):
            also_raised = [also_raised] if also_raised not in ("", None) else []
        heading_parts = [part for part in (stage, judge) if part]
        heading = " / ".join(heading_parts) if heading_parts else "Concern"
        concern_lines = [f"### {heading}"]
        if concern_id:
            # FR-005a — print id in the open block so operators can use it
            # with `checkpoint --address/--defer/--dismiss <id>` without
            # having to read state.json to recover it.
            concern_lines.append(f"- id: `{concern_id}`")
        concern_lines.extend(
            [
                f"- stage: `{stage}`",
                f"- judge: `{judge}`",
                f"- confidence: `{confidence}`",
                f"- reasoning: {reasoning or '(none)'}",
                f"- also_raised_in_round: `{', '.join(str(item) for item in also_raised) if also_raised else ''}`",
                f"- round_raised: `{round_raised}`",
                "",
            ]
        )
        lines.extend(concern_lines)

    # Plain-language summary of deferred/dismissed findings — rendered BEFORE
    # the full resolved-concerns dump so a human reviewer sees it first.
    lines.extend(render_findings_pushed_aside_block(resolved_concerns, slug=slug))

    if resolved_concerns:
        lines.append("## Resolved Concerns")
        for concern in resolved_concerns:
            stage = str(concern.get("stage", "") or "")
            judge = str(concern.get("judge", "") or "")
            concern_id = str(concern.get("id", "") or "")
            if concern.get("addressed_at") is not None:
                # addressed_at is the state-bearing field; addressed_by is evidence.
                # Keep the two aligned with _concern_is_resolved so the PR body
                # and the checkpoint gate (FR-004) never disagree.
                status = "addressed"
                detail = str(concern.get("addressed_by", "") or "").strip() or "(no evidence)"
            elif concern.get("deferred_reason"):
                status = "deferred"
                detail = str(concern.get("deferred_reason", "") or "").strip()
            elif concern.get("dismissed_reason"):
                status = "dismissed"
                detail = str(concern.get("dismissed_reason", "") or "").strip()
            else:
                status = "resolved"
                detail = ""
            id_label = f"`{concern_id}`" if concern_id else ""
            lines.append(
                f"- {stage} / {judge} {id_label} — **{status}**: {detail}".rstrip()
            )
        lines.append("")

    if annotations:
        lines.append("## Annotations")
        for annotation in annotations:
            stage = str(annotation.get("stage", "") or "")
            judge = str(annotation.get("judge", "") or "")
            confidence = annotation.get("confidence", "")
            round_value = annotation.get("round", "")
            excerpt = _excerpt(annotation.get("reasoning", ""))
            lines.append(
                f"- {stage} / {judge} (round {round_value}, confidence {confidence}): {excerpt or '(no reasoning excerpt)'}"
            )
        lines.append("")

    if override:
        lines.extend(
            [
                "## Override",
                "### ⚠ Shipped over judge objection",
                f"- reason: {str(override.get('reason', '') or '').strip()}",
                f"- operator_id: `{str(override.get('operator_id', '') or '').strip()}`",
                f"- timestamp: `{str(override.get('timestamp_iso8601_utc', '') or '').strip()}`",
                "- affected concerns:",
            ]
        )
        affected = _coerce_list(override.get("affected_concerns"))
        if affected:
            for concern in affected:
                concern_blob = _coerce_mapping(concern)
                if not concern_blob:
                    continue
                stage = str(concern_blob.get("stage", "") or "")
                judge = str(concern_blob.get("judge", "") or "")
                reasoning = str(concern_blob.get("reasoning", "") or "").strip()
                round_raised = concern_blob.get("round_raised", concern_blob.get("round", ""))
                lines.append(
                    f"  - {stage} / {judge} (round {round_raised}): {reasoning or '(none)'}"
                )
        else:
            lines.append("  - (none)")

    lines.append(SENTINEL_END)
    return "\n".join(lines) + "\n"


def upsert_judge_panel_block(existing_body: str, rendered_block: str) -> tuple[str, bool]:
    if not rendered_block:
        begin = existing_body.find(SENTINEL_BEGIN)
        end = existing_body.find(SENTINEL_END, begin + len(SENTINEL_BEGIN)) if begin != -1 else -1
        if begin == -1 or end == -1 or end < begin:
            return existing_body, False
        return existing_body[:begin] + existing_body[end + len(SENTINEL_END):], False

    begin = existing_body.find(SENTINEL_BEGIN)
    end = existing_body.find(SENTINEL_END, begin + len(SENTINEL_BEGIN)) if begin != -1 else -1
    if begin != -1 and end != -1 and end > begin:
        return existing_body[:begin] + rendered_block + existing_body[end + len(SENTINEL_END):], False

    prefix = f"{rendered_block}\n\n"
    if not existing_body:
        return rendered_block, True
    return prefix + existing_body, True
