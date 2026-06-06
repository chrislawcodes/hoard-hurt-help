#!/usr/bin/env python3
"""Review spec definitions, actionable-finding detection, and lens selection.

Pure helpers with no filesystem or workflow-state side effects. Extracted from
factory_review.py to keep each module under the 400-line source limit.
"""
import re
from pathlib import Path

from factory_io import read_text

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

SMALL_TASK_SET_THRESHOLD = 15
_AUTO_ACCEPT_NOTE = "No HIGH/MEDIUM/LOW/CRITICAL findings detected — auto-accepted"

# Every pattern below is matched against text that has already been lowercased
# by detect_actionable_findings(). All patterns anchor to start-of-line (after
# optional whitespace) to avoid matching prose mentions of severity words inside
# sentences. ACTIONABLE_FINDING_SHAPES documents the supported forms; when a
# reviewer starts using a new shape, update this regex and add a test.
#
# Supported shapes (each example drawn from a real review):
#   1. "- high: ..."                           bullet + severity + colon
#   2. "- [tag] high: ..."                     bullet + bracket tag + severity
#   3. "- HIGH [CODE-CONFIRMED]: ..."          bullet + bare severity + bracket tag
#   4. "- **HIGH**: ..."                       bullet + bold severity + colon
#   5. "| **HIGH** | ..."                      table cell with bold severity
#   6. "1. **HIGH**: ..."                      numbered list + bold severity
#   7. "### HIGH: ..."                         heading with severity word
#   8. "### 1. Finding title"                  heading with rank prefix (matched via next line)
#   9. "**HIGH**: ..."                         bold-prefix at paragraph start
#  10. "**HIGH [CODE-CONFIRMED]**: ..."        bold-prefix with tag
#  11. "**Severity**: HIGH"                    inline-field form (Gemini style)
#  12. "Severity: HIGH"                        inline-field without bold
ACTIONABLE_FINDING_SHAPES = (
    "bullet-colon",
    "bullet-bracket-tag-colon",
    "bullet-bare-plus-bracket-tag",
    "bullet-bold-severity",
    "table-bold-severity",
    "numbered-bold-severity",
    "heading-severity",
    "paragraph-bold-prefix",
    "paragraph-bold-prefix-bracket",
    "inline-severity-field-bold",
    "inline-severity-field-plain",
)

_SEV = r"(?:critical|high|medium|low)"

_ACTIONABLE_FINDING_RE = re.compile(
    r"(?:"
    # 1-2. Bullet + severity + colon: "- high:" or "- [tag] high:"
    r"^\s*-\s+(?:\[[^\]]+\]\s+)?" + _SEV + r":"
    r"|"
    # 3. Bullet + bare severity + bracket tag: "- high [code-confirmed]:"
    r"^\s*-\s+" + _SEV + r"\s+\[[^\]]+\]\s*:"
    r"|"
    # 4. Bullet + bold severity (with optional inner bracket tag):
    # "- **high**:" or "- **high [code-confirmed]**:" or
    # "- **HIGH [CODE-CONFIRMED]** rest-of-line" (no colon, as some lenses emit)
    r"^\s*-\s+\*\*" + _SEV + r"(?:\s*\[[^\]]+\])?\*\*(?:\s*:|\s+)"
    r"|"
    # 5. Table cell with bold severity: "| **high** |"
    r"^\|\s*\*\*" + _SEV + r"\*\*"
    r"|"
    # 6a. Numbered list + bold severity: "1. **high**:"
    r"^\s*\d+\.\s+\*\*" + _SEV + r"\*\*\s*:"
    r"|"
    # 6b. Numbered list + plain severity + colon (no bold): "1. high:" or "1. HIGH [tag]:"
    r"^\s*\d+\.\s+" + _SEV + r"(?:\s+\[[^\]]+\])?\s*:"
    r"|"
    # 7. Heading with severity word followed by colon or end-of-line.
    # Must be `### HIGH:` or `### HIGH` on its own line — NOT `### HIGH availability
    # target` (false-positive from section titles). Colon is the only delimiter
    # allowed since `-` or `--` can appear in compound words like `MEDIUM-term`.
    # Adversarial-review finding: allow non-word chars (emoji, bullet, etc.)
    # between `#+ ` and the rank prefix / severity — `### 🚨 HIGH:` is common.
    r"^#+\s+(?:\W+\s*)*(?:\d+\.\s+)?(?:\W+\s*)*" + _SEV + r"\s*(?::|$)"
    r"|"
    # 9-10. Paragraph start with bold prefix: "**high**:", "**high [code-confirmed]**:",
    # or "**high** - something" (adversarial review: dash delimiter after closing **).
    r"^\s*\*\*" + _SEV + r"(?:\s*\[[^\]]+\])?\*\*\s*(?::|-\s)"
    r"|"
    # 10b. Bracket-tag-first bold prefix: "**[HIGH SEVERITY]**:" — the severity
    # word lives inside the brackets, not before them (adversarial-review find).
    r"^\s*\*\*\[\s*" + _SEV + r"[^\]]*\]\*\*\s*:"
    r"|"
    # 11. Inline Severity field bold: "**severity**: high" or "**severity:** high"
    r"^\s*\*\*severity(?:\*\*)?:\*?\*?\s*" + _SEV + r"\b"
    r"|"
    # 12. Inline Severity field plain: "severity: high"
    r"^\s*severity:\s*" + _SEV + r"\b"
    r")",
    re.MULTILINE,
)


def _strip_non_finding_markdown(text: str) -> str:
    """Remove common quoted/example Markdown so severity examples do not match."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", "", text)
    kept: list[str] = []
    in_indented = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if line.startswith("    "):
            in_indented = True
            continue
        if in_indented and not stripped:
            continue
        in_indented = False
        if stripped.startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _findings_scan_text(text: str) -> str:
    lines = text.splitlines()
    starts = [
        idx
        for idx, line in enumerate(lines)
        if re.match(r"^##\s+Findings\s*$", line, flags=re.IGNORECASE)
    ]
    if not starts:
        return text
    first = starts[0] + 1
    return "\n".join(lines[first:])


# ---------------------------------------------------------------------------
# Review helpers
# ---------------------------------------------------------------------------


def detect_actionable_findings(review_path: Path) -> bool:
    """Return True if the review contains any HIGH or MEDIUM severity findings.

    Lowercases the full text once so mixed-case headings (High, HIGH, high) all match.
    Returns False if the file cannot be read, treating unreadable files as non-blocking.
    """
    try:
        text = _strip_non_finding_markdown(_findings_scan_text(read_text(review_path))).lower()
    except OSError:
        return False
    return bool(_ACTIONABLE_FINDING_RE.search(text))


_SEVERITY_EXTRACT_RE = re.compile(r"\b(critical|high|medium|low)\b")

_SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


def _count_findings_by_severity(text: str) -> dict[str, int]:
    """Count actionable findings by severity in review text.

    Uses _ACTIONABLE_FINDING_RE to find genuine finding lines, then extracts
    the severity word from each match. Returns a dict with counts for CRITICAL,
    HIGH, MEDIUM, LOW (uppercase keys). Lines that match the finding shape but
    contain no severity word are ignored.

    Callers should pass pre-processed text (lowercased, non-finding markdown
    stripped) to match the contract used by detect_actionable_findings.
    """
    counts: dict[str, int] = {sev: 0 for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    for match in _ACTIONABLE_FINDING_RE.finditer(text):
        sev_match = _SEVERITY_EXTRACT_RE.search(match.group(0))
        if sev_match:
            counts[sev_match.group(0).upper()] += 1
    return counts


def trim_detail(text: str, limit: int = 240) -> str:
    stripped = " ".join(text.split())
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 3] + "..."


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


def required_reviews(
    stage: str,
    sensitive: bool,
    large_structural: bool,
    performance_sensitive: bool,
    extra_gemini: list[str],
    fast: bool = False,
    small_task_set: bool = False,
) -> list[dict[str, str]]:
    if fast:
        return [
            {"reviewer": "codex", "lens": "correctness-adversarial", "model": DEFAULT_CODEX_MODEL},
            {"reviewer": "gemini", "lens": "regression-adversarial", "model": DEFAULT_GEMINI_MODEL},
        ]

    # Bundle 2: tasks, diff, and closeout stages have no default adversarial
    # reviews. Reasoning: tasks is mostly a mechanical translation of plan
    # (caught by failed implementation if wrong); diff is caught by CI;
    # closeout is documentation. Spec and plan reviews carry the leverage.
    # Operators can still pass --extra-codex-lens or --extra-gemini-lens to
    # add reviews to any stage.
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
    elif stage in {"tasks", "diff", "closeout"}:
        pass
    else:
        raise ValueError(f"Unsupported stage: {stage}")

    if small_task_set and stage in ("tasks", "closeout") and not extra_gemini:
        return []

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
    return reviews
