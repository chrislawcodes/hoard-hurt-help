"""Structural guards against duplicate/shadowing top-level CSS selectors.

Audit finding A9: style.css used to define .agent-row, .agent-row-name, and
.provider-name TWICE each (a stale "join picker" block pasted in later, plus
the live one) and .turn-head twice (an old single-line version plus the
current flex layout). CSS lets the later declaration of a tied-specificity
selector win, so the dead, later blocks were silently overriding the live,
earlier ones. The fix deleted the dead blocks; these are cheap regex
tripwires against the next accidental duplicate landing in this 2,800+ line
file — not a full CSS parser, just "did a second top-level block for this
exact selector show up".
"""

from __future__ import annotations

import re
from pathlib import Path

STYLE_CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "style.css"
ANALYSIS_CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "analysis.css"


def _count_top_level_selector(css_text: str, selector: str) -> int:
    """Count declarations of an exact selector opening a rule at column 0.

    Matches ``SELECTOR {`` (optional whitespace before the brace) anchored to
    the start of a line, so a compound/nested selector like ``.foo .agent-row``
    or a pseudo-class variant like ``.agent-row:hover`` does not also count.
    """
    pattern = re.compile(r"(?m)^" + re.escape(selector) + r"\s*\{")
    return len(pattern.findall(css_text))


def test_agent_row_defined_exactly_once() -> None:
    """The /me/agents list styling (bottom border, weight 600, min-width) must
    not be shadowed by a second .agent-row block landing later in the file."""
    css_text = STYLE_CSS.read_text()
    assert _count_top_level_selector(css_text, ".agent-row") == 1


def test_agent_row_name_defined_exactly_once() -> None:
    css_text = STYLE_CSS.read_text()
    assert _count_top_level_selector(css_text, ".agent-row-name") == 1


def test_provider_name_defined_exactly_once() -> None:
    css_text = STYLE_CSS.read_text()
    assert _count_top_level_selector(css_text, ".provider-name") == 1


def test_turn_head_defined_exactly_once() -> None:
    css_text = STYLE_CSS.read_text()
    assert _count_top_level_selector(css_text, ".turn-head") == 1


def test_analysis_css_does_not_shadow_platform_classes() -> None:
    """analysis.css must use its own .an- prefix, not bare names that would
    silently take over style.css's .card/.muted/.row/.chip/.results/.intro/
    .meter the next time either file adds one of its own."""
    css_text = ANALYSIS_CSS.read_text()
    for bare in (".card", ".muted", ".row", ".chip", ".results", ".intro", ".meter"):
        assert _count_top_level_selector(css_text, bare) == 0, (
            f"analysis.css defines a bare {bare!r} selector that would shadow "
            "style.css's platform rule of the same name"
        )
