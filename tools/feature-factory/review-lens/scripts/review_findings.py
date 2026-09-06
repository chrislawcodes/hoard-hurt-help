#!/usr/bin/env python3
"""Review-findings contract: structured JSON block, legacy prose shapes, classification.

This module is the single source of truth for how the Feature Factory decides
what findings an adversarial review raised. There are two supported formats:

1. **Structured (required for new reviews).** Every review response must end
   with exactly one fenced JSON block:

       ```json
       {"reviewed": true, "findings": [{"severity": "HIGH", "title": "...", "detail": "..."}]}
       ```

   A clean review is an *affirmative* clean bill: ``{"reviewed": true,
   "findings": []}``. When this block is present and valid it is the source of
   truth for finding count and severities — the legacy prose regex is not
   consulted at all.

2. **Legacy prose shapes (fallback for review files already on disk).** Older
   reviews carry findings only as markdown prose; ``_ACTIONABLE_FINDING_RE``
   recognizes the shapes reviewers have actually used. This path exists so
   past runs keep verifying; new reviews must use the JSON block.

Fail-closed rules (the whole point of this contract):

- A **malformed** JSON block (present but invalid) makes the review
  UNPARSEABLE. It never silently falls through to the regex.
- No JSON block, no regex match, and a **non-trivial** review body (more than
  ``NONTRIVIAL_BODY_MIN_CHARS`` chars of reviewer-authored text) also makes the
  review UNPARSEABLE — a substantial review whose findings we cannot read must
  never auto-accept as clean.
- Auto-accept ("no findings") is only allowed for an affirmative clean JSON
  block, or for a legacy-format file with zero regex matches AND a trivial
  body (e.g. "No findings returned.").

Stdlib-only on purpose: this module is imported by both the review-lens
scripts (verify/repair/runners, same directory) and the feature-factory engine
(``factory_review_specs`` re-exports it), so it must not depend on either.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Severity vocabulary
# ---------------------------------------------------------------------------

SEVERITY_LEVELS: tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

# Back-compat alias — factory_review_specs historically exported this name.
_SEVERITY_ORDER = SEVERITY_LEVELS


def _zero_counts() -> dict[str, int]:
    return {severity: 0 for severity in SEVERITY_LEVELS}


# ---------------------------------------------------------------------------
# Structured findings JSON block — the contract every new review must satisfy
# ---------------------------------------------------------------------------

# Substring that marks a fenced block as a findings-contract candidate. Fenced
# blocks without it (code samples, quoted diffs) are ignored entirely.
_REVIEWED_MARKER = '"reviewed"'

FINDINGS_JSON_CLEAN_EXAMPLE = '{"reviewed": true, "findings": []}'

# Canonical prompt wording, shared by every reviewer prompt builder (Gemini,
# Codex, Claude lenses) so all three state the identical contract.
FINDINGS_JSON_CONTRACT_LINES: tuple[str, ...] = (
    "End your review with exactly one fenced JSON block — the machine-readable findings summary:",
    "```json",
    '{"reviewed": true, "findings": [{"severity": "HIGH", "title": "<short title>", "detail": "<one-sentence detail>"}]}',
    "```",
    'Severity must be one of: CRITICAL, HIGH, MEDIUM, LOW. Include one entry per finding in your "## Findings" section.',
    "If you found no issues, the block must be the affirmative clean bill exactly: "
    + FINDINGS_JSON_CLEAN_EXAMPLE,
    "This JSON block is required, is machine-parsed, and must be the last thing in your response.",
)

JSON_BLOCK_VALID = "valid"
JSON_BLOCK_ABSENT = "absent"
JSON_BLOCK_MALFORMED = "malformed"


@dataclass(frozen=True)
class StructuredFinding:
    severity: str  # always uppercased, one of SEVERITY_LEVELS
    title: str
    detail: str = ""


@dataclass(frozen=True)
class FindingsJsonBlock:
    """Result of looking for the structured findings block in review text."""

    status: str  # JSON_BLOCK_VALID | JSON_BLOCK_ABSENT | JSON_BLOCK_MALFORMED
    findings: tuple[StructuredFinding, ...] = ()
    error: str = ""


def _fenced_blocks(text: str) -> tuple[list[str], str | None]:
    """Return (closed fenced-block contents, unclosed trailing fence content).

    Line-based scan: any line whose stripped form starts with ``` toggles a
    fence. The second element is the content of a fence that was opened but
    never closed (None when all fences are balanced) — an unterminated
    findings block must read as malformed, not silently vanish.
    """
    blocks: list[str] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append("\n".join(current))
                current = None
            continue
        if current is not None:
            current.append(line)
    unclosed = "\n".join(current) if current is not None else None
    return blocks, unclosed


def _validate_findings_payload(
    payload: object,
) -> tuple[tuple[StructuredFinding, ...], str]:
    """Validate the parsed JSON payload against the contract schema.

    Returns (findings, "") on success or ((), error) on any schema violation.
    ``reviewed`` must be exactly ``true`` — ``"reviewed": false`` is an explicit
    signal the review did not happen and is treated as a violation.
    """
    if not isinstance(payload, dict):
        return (), "top-level JSON value must be an object"
    if payload.get("reviewed") is not True:
        return (), 'the "reviewed" key must be exactly true'
    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        return (), 'the "findings" key must be a list (use [] for a clean review)'
    findings: list[StructuredFinding] = []
    for index, item in enumerate(raw_findings):
        if not isinstance(item, dict):
            return (), f"findings[{index}] must be an object"
        severity = item.get("severity")
        if not isinstance(severity, str) or severity.upper() not in SEVERITY_LEVELS:
            return (), (
                f"findings[{index}].severity must be one of "
                f"{', '.join(SEVERITY_LEVELS)}"
            )
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            return (), f"findings[{index}].title must be a non-empty string"
        detail = item.get("detail", "")
        if not isinstance(detail, str):
            return (), f"findings[{index}].detail must be a string when present"
        findings.append(
            StructuredFinding(severity=severity.upper(), title=title.strip(), detail=detail)
        )
    return tuple(findings), ""


def parse_findings_json(text: str) -> FindingsJsonBlock:
    """Find and validate the structured findings block in review text.

    Candidate blocks are fenced blocks containing the ``"reviewed"`` marker;
    the contract puts the block at the very end of the response, so when
    several candidates exist (e.g. the reviewer quoted the example before the
    real block) the LAST one wins. A last candidate that fails to parse or
    validate is MALFORMED — it never falls back to an earlier candidate or to
    the legacy regex.
    """
    blocks, unclosed = _fenced_blocks(text)
    if unclosed is not None and _REVIEWED_MARKER in unclosed:
        return FindingsJsonBlock(
            status=JSON_BLOCK_MALFORMED,
            error="findings JSON block's code fence is never closed",
        )
    candidates = [block for block in blocks if _REVIEWED_MARKER in block]
    if not candidates:
        return FindingsJsonBlock(status=JSON_BLOCK_ABSENT)
    last = candidates[-1].strip()
    try:
        payload = json.loads(last)
    except json.JSONDecodeError as exc:
        return FindingsJsonBlock(
            status=JSON_BLOCK_MALFORMED, error=f"invalid JSON in findings block: {exc}"
        )
    findings, error = _validate_findings_payload(payload)
    if error:
        return FindingsJsonBlock(status=JSON_BLOCK_MALFORMED, error=error)
    return FindingsJsonBlock(status=JSON_BLOCK_VALID, findings=findings)


# ---------------------------------------------------------------------------
# Legacy prose shapes (fallback for review files already on disk)
# ---------------------------------------------------------------------------

# Every pattern below is matched against text that has already been lowercased
# by the legacy scan pipeline. All patterns anchor to start-of-line (after
# optional whitespace) to avoid matching prose mentions of severity words inside
# sentences. ACTIONABLE_FINDING_SHAPES documents the supported forms. This
# regex is FROZEN as the legacy fallback: new reviewer output styles are
# handled by the structured JSON block above, not by growing this list.
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

_SEVERITY_EXTRACT_RE = re.compile(r"\b(critical|high|medium|low)\b")


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


def _count_findings_by_severity(text: str) -> dict[str, int]:
    """Count legacy prose findings by severity in pre-processed review text.

    Uses _ACTIONABLE_FINDING_RE to find genuine finding lines, then extracts
    the severity word from each match. Returns a dict with counts for CRITICAL,
    HIGH, MEDIUM, LOW (uppercase keys). Lines that match the finding shape but
    contain no severity word are ignored.

    Callers should pass pre-processed text (lowercased, non-finding markdown
    stripped) — the same pipeline classify_review_text uses.
    """
    counts = _zero_counts()
    for match in _ACTIONABLE_FINDING_RE.finditer(text):
        sev_match = _SEVERITY_EXTRACT_RE.search(match.group(0))
        if sev_match:
            counts[sev_match.group(0).upper()] += 1
    return counts


# ---------------------------------------------------------------------------
# Classification — JSON first, legacy fallback, fail closed
# ---------------------------------------------------------------------------

FINDINGS_SOURCE_JSON = "json"
FINDINGS_SOURCE_LEGACY = "legacy-regex"
FINDINGS_UNPARSEABLE = "unparseable"

# A review body with more reviewer-authored text than this cannot be assumed
# clean just because nothing matched: a real finding phrased in an
# unrecognized shape would otherwise silently auto-accept. Trivial bodies
# (e.g. "No findings returned." plus a one-line residual) stay on the legacy
# clean path so review files from past runs keep verifying.
NONTRIVIAL_BODY_MIN_CHARS = 400

# Sections written by the runner, not the reviewer. Excluded from the
# authored-text measure so runner boilerplate (token stats, resolution
# bookkeeping, failure evidence) can never push a genuinely clean legacy
# review over the non-trivial threshold.
_BOILERPLATE_SECTIONS = frozenset(
    {"runner stats", "token stats", "resolution", "failure evidence", "quota evidence"}
)


@dataclass(frozen=True)
class ReviewFindingsClassification:
    """How a review's findings were determined, and what they are."""

    source: str  # FINDINGS_SOURCE_JSON | FINDINGS_SOURCE_LEGACY | FINDINGS_UNPARSEABLE
    counts: dict[str, int]
    findings: tuple[StructuredFinding, ...] = ()  # populated for the JSON source
    detail: str = ""  # operator-facing reason when unparseable

    @property
    def has_findings(self) -> bool:
        return any(self.counts.values())

    @property
    def is_unparseable(self) -> bool:
        return self.source == FINDINGS_UNPARSEABLE


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block if present; otherwise no-op."""
    if not text.startswith("---\n"):
        return text
    _, _, rest = text.partition("---\n")
    body_split = rest.split("\n---\n", 1)
    if len(body_split) != 2:
        return text
    return body_split[1]


def _reviewer_authored_text(body: str) -> str:
    """The review text the reviewer wrote, minus runner boilerplate sections."""
    kept: list[str] = []
    in_boilerplate = False
    for line in body.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            in_boilerplate = heading.group(1).strip().lower() in _BOILERPLATE_SECTIONS
            if in_boilerplate:
                continue
        if not in_boilerplate:
            kept.append(line)
    return "\n".join(kept).strip()


def unparseable_classification(detail: str) -> ReviewFindingsClassification:
    return ReviewFindingsClassification(
        source=FINDINGS_UNPARSEABLE, counts=_zero_counts(), detail=detail
    )


def classify_review_text(text: str) -> ReviewFindingsClassification:
    """Classify a review's findings. Accepts a full review file or a bare body.

    Precedence:
      1. Valid structured JSON block → source of truth (counts + severities).
      2. Malformed JSON block → UNPARSEABLE (never falls through to the regex).
      3. No JSON block → legacy regex over the prose.
      4. No JSON, no regex match, non-trivial authored body → UNPARSEABLE.
      5. No JSON, no regex match, trivial body → legacy clean (auto-accept OK).
    """
    body = _strip_frontmatter(text)
    block = parse_findings_json(body)
    if block.status == JSON_BLOCK_MALFORMED:
        return unparseable_classification(
            f"structured findings JSON block is malformed: {block.error}"
        )
    if block.status == JSON_BLOCK_VALID:
        counts = _zero_counts()
        for finding in block.findings:
            counts[finding.severity] += 1
        return ReviewFindingsClassification(
            source=FINDINGS_SOURCE_JSON, counts=counts, findings=block.findings
        )
    scan_text = _strip_non_finding_markdown(_findings_scan_text(body)).lower()
    counts = _count_findings_by_severity(scan_text)
    if any(counts.values()):
        return ReviewFindingsClassification(source=FINDINGS_SOURCE_LEGACY, counts=counts)
    authored = _reviewer_authored_text(body)
    if len(authored) > NONTRIVIAL_BODY_MIN_CHARS:
        return unparseable_classification(
            "no structured findings JSON block and no recognizable finding lines in a "
            f"non-trivial review body ({len(authored)} chars of reviewer text); the "
            "review cannot be proven clean"
        )
    return ReviewFindingsClassification(source=FINDINGS_SOURCE_LEGACY, counts=counts)
