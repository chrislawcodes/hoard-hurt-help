"""One home for what `thinking` is: the how-to-play block, and nowhere else.

The description of this one field used to be split across two files — the
submit_action docstring owned "other players never see it", the how-to-play
block owned "what to write in it". A client reads both, and they had already
drifted once: the docstring said to leave the field empty when you have nothing
to add, while the block said to write why you are making the move.

Splitting one field's description in two is a slower version of writing it
twice. This pins it to one place.
"""

from __future__ import annotations

import re
from pathlib import Path

MCP_TOOLS = Path(__file__).resolve().parent.parent / "mcp_server" / "mcp_tools.py"


def test_only_the_how_to_play_block_describes_the_thinking_field():
    """No second description of `thinking` may grow back in the tool docstrings.

    A pointer TO the how-to-play block is fine and expected — that is how the
    next reader finds the one home. What must not come back is a second
    statement of what the field is or what to put in it.
    """
    src = MCP_TOOLS.read_text()
    block_start = src.index("def _mcp_how_to_play_block()")
    block_end = src.index("def ", block_start + 10)
    block, elsewhere = src[block_start:block_end], src[:block_start] + src[block_end:]

    assert "`thinking`" in block, "the how-to-play block no longer describes the field"

    # Outside the block, `thinking` may be named — as a parameter, or in a
    # pointer — but must not be re-explained.
    for line in elsewhere.splitlines():
        if "`thinking`" not in line:
            continue
        assert re.search(r"_mcp_how_to_play_block|said once|one home", line, re.I) or \
            not re.search(r"never (see|visible)|is one|optional and", line, re.I), (
                f"a second description of `thinking` has grown back:\n    {line.strip()}"
            )


def test_the_instruction_asks_the_agent_to_name_its_rule():
    """The diagnostic that made this worth doing at all.

    Turncoat broke its own rule in all five v11 matches. Reading its thinking by
    hand showed it was not disobeying — it could not get anyone to pact with it
    after its first public betrayal, so no line of its strategy applied. It
    explained every attack and never said it was stuck. Naming the rule surfaces
    that in one match instead of five.
    """
    src = MCP_TOOLS.read_text()
    block = src[src.index("def _mcp_how_to_play_block()"):]
    block = block[: block.index("def ", 10)]
    assert "Name the rules" in block, (
        "the request to name the rule is gone — fidelity goes back to being "
        "inferred from actions, which measured the wrong thing four times"
    )
