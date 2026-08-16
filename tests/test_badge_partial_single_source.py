"""Structural guard: only one template may build a status badge.

Unifying the badge is worth nothing if the next page to need one hand-rolls its
own span again — which is exactly how ``agents/_status.html`` drifted after
PR #644 had already unified the Python mapping underneath it.

The check is deliberately narrow rather than perfect. It does NOT ban every
``<span class="badge ...">`` in the codebase: the match-state badges (SCHEDULED
/ REGISTERING / ACTIVE …) are a different badge family with hard-coded classes
and are out of scope here. What it bans is any template *other than the partial*
building a badge out of a status presenter — reading ``badge_class`` or
``still_dot``, or emitting the ``dot`` element those fields control. Those
markers appear only where a readiness/health badge is being assembled, so the
rule catches the thing that actually drifted and nothing else.

Jinja comments are stripped before scanning, so a template may still *explain*
the rule (this file's own subjects get named in the partial's docstring comment)
without tripping it. Only emitted markup counts.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = _REPO_ROOT / "app" / "templates"
BADGE_PARTIAL = TEMPLATE_ROOT / "fragments" / "_badge.html"
STYLESHEET = _REPO_ROOT / "app" / "static" / "style.css"

# Markers that appear only where a status presenter is being turned into a badge:
# its two presentation fields, plus the steady-dot class those fields switch on.
#
# The plain animated `<span class="dot">` is deliberately NOT a marker. Five
# templates hard-code it inside a "Live now" match-state badge with no presenter
# involved (home, the admin dashboard, the game-status fragments) — that's the
# separate match-state badge family, out of scope here.
_BADGE_BUILDING_MARKERS = ("badge_class", "still_dot", "dot-still")

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _markup(template: Path) -> str:
    """The template's emitted markup — comments stripped."""
    body = _JINJA_COMMENT.sub("", template.read_text())
    return _HTML_COMMENT.sub("", body)


def _templates() -> list[Path]:
    return sorted(p for p in TEMPLATE_ROOT.rglob("*.html") if p.is_file())


def test_the_badge_partial_exists_and_hard_codes_the_base_class() -> None:
    """The partial is the badge's one definition, and always emits ``badge``.

    ``agents/_status.html`` used to omit the base class, so the detail page's
    badge had no pill styling at all while the list page's did.
    """
    assert BADGE_PARTIAL.is_file(), "the shared badge partial is missing"
    markup = _markup(BADGE_PARTIAL)
    assert '<span class="badge {{ badge.badge_class }}">' in markup
    assert "badge.pulse" in markup and "badge.still_dot" in markup


def test_no_other_template_builds_a_status_badge() -> None:
    """Only ``fragments/_badge.html`` may assemble a badge from a presenter.

    This covers the still-dot rule too: ``state.value == 'ready'`` used to
    decide the steady dot inline in two templates, with a third copy in Python
    as ``CalmConnectionStatus.still_dot``. Banning the steady-dot class outside
    the partial leaves nowhere to put the derivation but the presenter.
    """
    offenders: list[str] = []
    for template in _templates():
        if template == BADGE_PARTIAL:
            continue
        markup = _markup(template)
        for marker in _BADGE_BUILDING_MARKERS:
            if marker in markup:
                offenders.append(f"{template.relative_to(TEMPLATE_ROOT)} ({marker})")
    assert not offenders, (
        "these templates build a status badge themselves instead of including "
        "fragments/_badge.html: {}".format(", ".join(offenders))
    )


def test_no_template_emits_a_class_the_stylesheet_never_defines() -> None:
    """``badge-pulse`` styled nothing — it exists in no stylesheet.

    ``agents/_status.html`` emitted it anyway. The real pulse is the ``.dot``
    element's ``hhh-pulse`` animation, which the partial renders. This pins that
    a second, fictional pulse mechanism does not come back.
    """
    assert "badge-pulse" not in STYLESHEET.read_text(), (
        "badge-pulse is defined now — update this guard, or point the partial at it"
    )
    offenders = [
        str(t.relative_to(TEMPLATE_ROOT))
        for t in _templates()
        if "badge-pulse" in _markup(t)
    ]
    assert not offenders, (
        "these templates emit badge-pulse, which no stylesheet defines: {}".format(
            ", ".join(offenders)
        )
    )
