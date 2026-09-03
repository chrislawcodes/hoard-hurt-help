"""Docs that state a rule must be regenerated from the code, not retyped.

This replaces a weaker guard. The first attempt asserted "this exact payoff
string appears in the doc" — which turns a silent drift into a loud one, but
still leaves a human retyping the number by hand, and a human who retypes it
into the wrong sentence passes. The doc was still a second copy of the rule.

Now the doc holds a GENERATED block: `scripts/render_generated_docs.py` renders
it from the constants and this test asserts the file matches what the renderer
produces. Change a payoff and the fix is to re-run the script, never to edit
prose. The rules block is not even a doc-specific rendering — it is
`semantic_rules_text()`, the same text served to agents, so there is one
rendering of the constants with two readers.

What this does NOT cover, on purpose: the hand-written prose around each block.
That is the doc's job — the *why* — and it is written without repeating any
number so it has nothing to drift. `tests/test_rules_docs_current.py` still
guards the match length across files with no markers (README, the game-design
skill), and it also covers the hand-written halves of these two docs, which is
why both stay listed there.
"""

from __future__ import annotations

import pytest

from scripts.render_generated_docs import BLOCKS, Block, current_and_expected


@pytest.mark.parametrize("block", BLOCKS, ids=lambda b: f"{b.doc}:{b.block_id}")
def test_the_generated_block_matches_the_code(block: Block) -> None:
    have, want = current_and_expected(block)
    assert have == want, (
        f"{block.doc} block {block.block_id!r} is out of date with the code.\n"
        f"Do not edit it by hand — run:\n"
        f"    python scripts/render_generated_docs.py --write"
    )


def test_every_block_carries_the_do_not_edit_banner() -> None:
    # A reader who lands on a block mid-file must be able to tell it is
    # generated without going looking for the script. Losing the banner turns a
    # generated block back into something that looks hand-editable.
    from scripts.render_generated_docs import BANNER

    for block in BLOCKS:
        have, _ = current_and_expected(block)
        assert BANNER in have, f"{block.doc}:{block.block_id} lost its banner"


def test_a_missing_marker_fails_loudly_rather_than_skipping() -> None:
    # A doc that loses its markers has silently stopped being generated, which
    # is the exact failure this machinery exists to prevent — so the renderer
    # must raise, not shrug and move on.
    from scripts.render_generated_docs import _marker_span

    with pytest.raises(ValueError, match="missing the begin marker"):
        _marker_span("a doc with no markers at all", BLOCKS[0])
