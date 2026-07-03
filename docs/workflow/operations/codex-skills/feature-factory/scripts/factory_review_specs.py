#!/usr/bin/env python3
"""Review spec definitions, finding detection wrappers, and lens selection.

Pure helpers with no workflow-state side effects. Extracted from
factory_review.py to keep each module under the 400-line source limit.

Finding detection itself lives in review-lens/scripts/review_findings.py — the
single source of truth for the structured findings JSON contract, the legacy
prose-shape regex, and the fail-closed classification. This module re-exports
those names (several callers and tests import them from here) and adds the
file-level wrappers the engine uses.
"""
import os
import sys
from pathlib import Path

from factory_io import read_text

# The findings contract is shared with the review-lens scripts (verify/repair/
# runners import it as a sibling), so it lives there; resolve it relative to
# this file so the engine stays portable across repos.
_REVIEW_LENS_SCRIPTS = Path(__file__).resolve().parents[2] / "review-lens" / "scripts"
if str(_REVIEW_LENS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_REVIEW_LENS_SCRIPTS))

from review_findings import (  # noqa: E402,F401  (re-exported for engine callers + tests)
    ACTIONABLE_FINDING_SHAPES,
    FINDINGS_SOURCE_JSON,
    FINDINGS_SOURCE_LEGACY,
    FINDINGS_UNPARSEABLE,
    NONTRIVIAL_BODY_MIN_CHARS,
    ReviewFindingsClassification,
    _ACTIONABLE_FINDING_RE,
    _SEVERITY_ORDER,
    _count_findings_by_severity,
    _findings_scan_text,
    _strip_non_finding_markdown,
    classify_review_text,
    parse_findings_json,
    unparseable_classification,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Routine reviews use Flash-Lite: Pro-derived reasoning at ~1/8th the cost,
# fast, and proven adequate on spec/plan/tasks artifacts. Sensitive checkpoints
# (the --sensitive flag) escalate to Pro for the deepest reasoning.
# Both IDs verified callable via the gemini CLI on 2026-06-06; note Pro requires
# the "-preview" suffix (bare "gemini-3.1-pro" returns ModelNotFoundError).
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
SENSITIVE_GEMINI_MODEL = "gemini-3.1-pro-preview"
DEFAULT_CODEX_MODEL = "gpt-5.4-mini"

# Claude-only review path (spec 020): when the reviewer override is "claude",
# every lens this module routes is staffed by a Claude subagent instead of the
# Gemini/Codex CLIs, so the factory runs on the subscription with no external
# CLI binaries. The lens routing per stage is unchanged — only who reviews. The
# model string is metadata + the pricing/telemetry key; it must start with
# "claude-" so factory_telemetry routes it to the Claude parser/pricing.
DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"
CLAUDE_REVIEWER = "claude"


def resolve_reviewer_override(state: dict | None = None) -> str | None:
    """Resolve the active reviewer override, or None for the default Gemini/Codex mix.

    Order: the ``FF_REVIEWER`` environment variable wins (ad-hoc per-run control),
    then ``review_policy.reviewer`` persisted in workflow state (set by
    prepare-claude-reviews). Returns the lowercased override string or None.
    """
    env_value = (os.environ.get("FF_REVIEWER") or "").strip().lower()
    if env_value:
        return env_value
    if isinstance(state, dict):
        policy = state.get("review_policy")
        if isinstance(policy, dict):
            persisted = policy.get("reviewer")
            if isinstance(persisted, str) and persisted.strip():
                return persisted.strip().lower()
    return None

SMALL_TASK_SET_THRESHOLD = 15

# Default diff-review gate: a slice that changes at least this many lines gets
# one independent Gemini regression-adversarial review. Codex is always the
# implementer of the diff, so the diff reviewer is Gemini to keep the lens
# independent of the author. Smaller diffs rely on preflight + CI; this exists
# because CI here is only ruff/mypy/pytest and cannot catch logic the tests do
# not exercise — the substantial slices are where such bugs hide. Operators
# override per-call with `checkpoint --stage diff --diff-review-threshold N`.
DIFF_REVIEW_DEFAULT_MIN_CHANGED_LINES = 50

# Auto-accept is only legitimate when the review PROVED itself clean: either
# the affirmative structured clean bill ({"reviewed": true, "findings": []}),
# or a legacy-format file with zero regex-detected findings and a trivial
# body. An unparseable review must never carry this note — it fails closed.
_AUTO_ACCEPT_NOTE = (
    "Clean review confirmed (affirmative empty findings JSON, or trivial legacy "
    "review with zero detected findings) — auto-accepted"
)


# ---------------------------------------------------------------------------
# Review helpers
# ---------------------------------------------------------------------------


def classify_review_findings(review_path: Path) -> ReviewFindingsClassification:
    """Classify a review file's findings (fail closed on unreadable files).

    See review_findings.classify_review_text for the precedence rules. A file
    that cannot be read is UNPARSEABLE — an unreadable review must never count
    as a clean one.
    """
    try:
        text = read_text(review_path)
    except OSError as exc:
        return unparseable_classification(f"review file could not be read: {exc}")
    return classify_review_text(text)


def detect_actionable_findings(review_path: Path) -> bool:
    """Return True if the review contains findings — or cannot be proven clean.

    A valid structured JSON block is the source of truth; legacy prose reviews
    fall back to the shape regex. UNPARSEABLE reviews (malformed JSON, or a
    non-trivial body with nothing recognizable) return True: fail closed, so
    the caller treats the review as needing attention instead of auto-accepting.
    """
    classification = classify_review_findings(review_path)
    if classification.is_unparseable:
        return True
    return classification.has_findings


def trim_detail(text: str, limit: int = 240) -> str:
    stripped = " ".join(text.split())
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 3] + "..."


def count_changed_diff_lines(diff_text: str) -> int:
    """Return the number of added/removed content lines in a unified diff.

    Counts lines that start with a single ``+`` or ``-`` (added or removed
    content) and skips the ``+++``/``---`` file headers. Hunk headers (``@@``)
    and unchanged context lines do not count. Empty input returns 0. This is the
    measure the diff-review gate uses to decide whether a slice is substantial
    enough to warrant an independent review.
    """
    changed = 0
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") or line.startswith("-"):
            changed += 1
    return changed


def pick_secondary_lens(primary: str, default: str, candidates: list[str]) -> str:
    ordered = [*candidates, default]
    seen: set[str] = set()
    for lens in ordered:
        if not lens or lens in seen:
            continue
        seen.add(lens)
        if lens != primary:
            return lens
    return default if default != primary else f"{primary}-secondary"


def _apply_reviewer_override(
    reviews: list[dict[str, str]], reviewer_override: str | None
) -> list[dict[str, str]]:
    """Re-staff every routed lens with the override reviewer, keeping the lenses.

    Only "claude" is supported today (spec 020). The per-stage lens selection above
    is the source of truth for *what* gets reviewed; this changes *who* reviews it.
    Unknown overrides are ignored so a typo can't silently drop reviews.
    """
    if reviewer_override != CLAUDE_REVIEWER:
        return reviews
    return [
        {**review, "reviewer": CLAUDE_REVIEWER, "model": DEFAULT_CLAUDE_MODEL}
        for review in reviews
    ]


def required_reviews(
    stage: str,
    sensitive: bool,
    large_structural: bool,
    performance_sensitive: bool,
    extra_gemini: list[str],
    fast: bool = False,
    small_task_set: bool = False,
    diff_changed_lines: int | None = None,
    diff_review_threshold: int = DIFF_REVIEW_DEFAULT_MIN_CHANGED_LINES,
    reviewer_override: str | None = None,
) -> list[dict[str, str]]:
    if fast:
        return _apply_reviewer_override(
            [
                {"reviewer": "codex", "lens": "correctness-adversarial", "model": DEFAULT_CODEX_MODEL},
                {"reviewer": "gemini", "lens": "regression-adversarial", "model": DEFAULT_GEMINI_MODEL},
            ],
            reviewer_override,
        )

    # Bundle 2: tasks and closeout stages have no default adversarial reviews
    # (tasks is a mechanical translation of plan, caught by failed
    # implementation; closeout is documentation). The diff stage gets a single
    # independent Gemini review, but only for substantial slices — see the gate
    # below. Spec and plan reviews carry the design-stage leverage. Operators
    # can still pass --extra-gemini-lens to add reviews to any stage.
    gemini_lens = ""
    codex_primary = ""
    codex_secondary = ""

    if stage == "spec":
        gemini_lens = "requirements-adversarial"
        codex_primary = "feasibility-adversarial"
        codex_secondary = ""
    elif stage == "plan":
        gemini_lens = "testability-adversarial"
        codex_primary = "implementation-adversarial"
        codex_secondary = ""
    elif stage == "diff":
        # Size-gated default: a slice changing >= threshold lines gets one
        # Gemini regression-adversarial review. Gemini (not Codex) because Codex
        # authored the diff, so a Codex lens here would be self-review. When the
        # diff size is unknown (diff_changed_lines is None), keep the historical
        # no-default-review behavior so callers that don't size the diff are
        # unaffected.
        if diff_changed_lines is not None and diff_changed_lines >= diff_review_threshold:
            gemini_lens = "regression-adversarial"
    elif stage in {"tasks", "closeout"}:
        pass
    else:
        raise ValueError(f"Unsupported stage: {stage}")

    if small_task_set and stage in ("tasks", "closeout") and not extra_gemini:
        return []  # no lenses to re-staff

    # Sensitive checkpoints escalate the Gemini reviewer to Pro for deeper
    # reasoning; routine checkpoints use the cheaper Flash-Lite default.
    gemini_model = SENSITIVE_GEMINI_MODEL if sensitive else DEFAULT_GEMINI_MODEL

    reviews: list[dict[str, str]] = []
    for reviewer, lens, model in (
        ("codex", codex_primary, DEFAULT_CODEX_MODEL),
        ("codex", codex_secondary, DEFAULT_CODEX_MODEL),
        ("gemini", gemini_lens, gemini_model),
    ):
        if not lens:
            continue
        reviews.append({
            "reviewer": reviewer,
            "lens": lens,
            "model": model,
        })
    seen_gemini_lenses = {review["lens"] for review in reviews if review["reviewer"] == "gemini"}
    for lens in extra_gemini:
        candidate = lens.strip()
        if not candidate or candidate in seen_gemini_lenses:
            continue
        reviews.append({
            "reviewer": "gemini",
            "lens": candidate,
            "model": gemini_model,
        })
        seen_gemini_lenses.add(candidate)
    return _apply_reviewer_override(reviews, reviewer_override)
