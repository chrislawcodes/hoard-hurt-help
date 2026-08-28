"""One rule decides whether a ``?next`` target is safe to redirect to.

``safe_internal_next`` (``app/routes/web_support.py``) is that rule: a target is
accepted only when it is a same-site absolute path. ``//host`` and ``/\\host``
are protocol-relative — browsers treat them as external — and anything carrying
a scheme is external outright.

``app/routes/dev_login.py`` used to re-implement the check instead of calling it.
The two agreed, so nothing was broken; the hazard was the next hardening. Add a
rejection to ``safe_internal_next`` and the hand-rolled copy would silently keep
accepting what the canonical rule had just started refusing.

**These tests take the canonical rule as the oracle rather than hard-coding a
list of bad targets.** A fixed list only ever checks the cases whoever wrote it
thought of: the first version of this file listed thirteen hostile strings, and
when the canonical rule was mutated to also reject a backslash anywhere, the
hand-rolled copy diverged on ``/a\\b`` and all thirteen still passed. So each
wrapper is asserted equal to ``safe_internal_next(target) or <its default>``,
evaluated at test time. Any rejection added to the canonical rule is then
inherited by this test for free, and a wrapper that does not delegate fails.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.routes.dev_login import _DEFAULT_NEXT as DEV_LOGIN_DEFAULT
from app.routes.dev_login import _safe_next as dev_login_safe_next
from app.routes.handle_web import _DEFAULT_NEXT as HANDLE_DEFAULT
from app.routes.handle_web import _safe_next as handle_safe_next
from app.routes.web_support import safe_internal_next


def _corpus() -> list[str]:
    """Targets spanning the shapes a redirect guard has to rule on.

    Generated rather than listed so the set is wider than anyone's imagination
    of "hostile", and so a future rejection is likely already covered.
    """
    prefixes = ["", "/", "//", "/\\", "\\", "///", "/./", "/../", " /", "\t/"]
    bodies = ["", "me/agents", "evil.com", "a\\b", "a b", "a%2fb", "a\nb", "é"]
    suffixes = ["", "?q=1", "#frag", "?a=1#f"]
    schemes = ["http://evil.com", "https://evil.com", "javascript:alert(1)",
               "data:text/html,<script>", "mailto:a@b.c", "//evil.com@good.com"]
    out = {p + b + s for p in prefixes for b in bodies for s in suffixes}
    out.update(schemes)
    out.update({"", "/", "/games/abc?tab=replay", "/a/b/c#frag", "/path//nested"})
    return sorted(out)


CORPUS = _corpus()

WRAPPERS = [
    pytest.param(dev_login_safe_next, DEV_LOGIN_DEFAULT, id="dev_login"),
    pytest.param(handle_safe_next, HANDLE_DEFAULT, id="handle_web"),
]


@pytest.mark.parametrize(("wrapper", "default"), WRAPPERS)
def test_wrapper_answers_exactly_what_the_canonical_rule_answers(
    wrapper: Callable[[str | None], str], default: str
) -> None:
    """A wrapper picks its own landing page, never its own verdict."""
    disagreements = [
        (target, wrapper(target), safe_internal_next(target) or default)
        for target in CORPUS
        if wrapper(target) != (safe_internal_next(target) or default)
    ]
    assert not disagreements, (
        f"{len(disagreements)} target(s) where this wrapper does not match "
        f"safe_internal_next — it is re-implementing the rule instead of calling "
        f"it. First few: {disagreements[:5]}"
    )


def test_the_corpus_actually_exercises_both_verdicts() -> None:
    """Guard the guard: a corpus that is all-safe or all-hostile proves nothing."""
    refused = sum(1 for t in CORPUS if safe_internal_next(t) is None)
    assert refused > 20, f"only {refused} refused targets — corpus too permissive"
    assert len(CORPUS) - refused > 5, "corpus has too few accepted targets"


@pytest.mark.parametrize(
    "target",
    ["//evil.com", "/\\evil.com", "http://evil.com", "javascript:alert(1)", ""],
)
def test_named_escape_routes_stay_refused(target: str) -> None:
    """The specific shapes this guard exists for, named so a regression reads clearly."""
    assert safe_internal_next(target) is None
    assert dev_login_safe_next(target) == DEV_LOGIN_DEFAULT
    assert handle_safe_next(target) == HANDLE_DEFAULT
