"""The MCP play prompt: one wording, two places, and the guard rails that earn it.

``app/routes/connections_connect_guide.py`` serves the prompt to players and
``docs/setup-mcp.md`` documents it. They are supposed to be the same text. They
had already drifted (the doc grew three sentences the served copy never got, and
kept a stale "~25s" hold), which is exactly the failure a copy-paste pair invites,
so the first test here pins them together.

The rest pin the two phrases that were *measured* to change client behaviour — see
the comment above ``_PLAY_PROMPT`` for the Codex 0.146.1 numbers.

The prompt and doc also drifted from a THIRD place: the live
``agent_long_poll_hold_seconds`` setting (app/config.py), whose actual value is
40 in dev/test and 90 in production. The prompt said "about 90 seconds"
unconditionally, so every non-prod client read a number that was simply false.
The fix removes the hand-typed figure rather than chasing it with a fourth
place to update — the get_next_turn tool description already interpolates the
live setting (see tests/test_mcp_poll_tool_descriptions.py), so the prompt only
needs to say a wait happens, not how long it is. The structural tests below make
that permanent: they fail on ANY hold-length-shaped literal, not just the ones
that already bit us.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.routes.connections_connect_guide import (
    ANTIGRAVITY_KEY_PLACEHOLDER,
    _PLAY_PROMPT,
    antigravity_config_file,
    antigravity_config_fragment,
)

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
SETUP_DOC: Path = REPO_ROOT / "docs" / "setup-mcp.md"

# Matches a hand-typed hold-length figure: digits immediately followed by
# "second(s)" (with an optional separator, so "90 seconds" and "90-second" both
# match) or by a bare "s" unit ("90s", "40s", "25s"). Deliberately broad rather
# than a fixed list of bad strings — the bug this guards against is a NEW number
# getting typed in, not a specific old one recurring, so the check has to catch
# shapes, not values.
_HOLD_LENGTH_LITERAL_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:-|to)?\s*seconds?\b|\b\d+(?:\.\d+)?s\b", re.IGNORECASE
)


def test_play_prompt_matches_docs() -> None:
    """The served prompt appears verbatim in ``docs/setup-mcp.md``.

    Both copies are pasted by real players, so a doc that has drifted is a player
    running a different prompt than the site handed them.
    """
    doc = SETUP_DOC.read_text(encoding="utf-8")
    assert _PLAY_PROMPT in doc, (
        "docs/setup-mcp.md no longer contains the served play prompt verbatim. "
        "Update the ```text block in the 'MCP connection' section to match "
        "_PLAY_PROMPT in app/routes/connections_connect_guide.py."
    )


PROD_MCP_URL = "https://agentludum.com/mcp"


def test_antigravity_config_fragment_matches_docs() -> None:
    """``docs/setup-mcp.md`` carries the Antigravity config fragment verbatim.

    Same reasoning as the play prompt above, and it had drifted the same way: the
    doc's continuation line was indented 18 spaces where the served prompt used
    19. A one-character difference no reviewer catches by eye, in a block users
    paste into a JSON file.
    """
    doc = SETUP_DOC.read_text(encoding="utf-8")
    assert antigravity_config_fragment(PROD_MCP_URL) in doc, (
        "docs/setup-mcp.md no longer contains the served Antigravity config "
        "fragment verbatim. Update the Antigravity block to match "
        "antigravity_config_fragment() in app/routes/connections_connect_guide.py."
    )


def test_one_placeholder_name_across_every_antigravity_surface() -> None:
    """Prompt, page and doc must name the SAME blank for the user to replace.

    They did not: the connection page said YOUR_CONNECTION_KEY while the prompt
    and the doc said MY_CONNECTION_KEY, so a user reading one and following the
    other hunted for a placeholder that was not in front of them. Both config
    shapes now build from ``ANTIGRAVITY_KEY_PLACEHOLDER``; this pins the doc and
    forbids the retired name anywhere.
    """
    doc = SETUP_DOC.read_text(encoding="utf-8")
    template = (
        REPO_ROOT / "app" / "templates" / "connections" / "detail.html"
    ).read_text(encoding="utf-8")

    for shape in (antigravity_config_fragment(PROD_MCP_URL), antigravity_config_file(PROD_MCP_URL)):
        assert ANTIGRAVITY_KEY_PLACEHOLDER in shape

    assert ANTIGRAVITY_KEY_PLACEHOLDER in doc
    # The page must not hand-type a config block again — it renders the shared one.
    assert "YOUR_CONNECTION_KEY" not in doc
    assert "YOUR_CONNECTION_KEY" not in template
    assert "mcpServers" not in template, (
        "app/templates/connections/detail.html is hand-typing an Antigravity "
        "config block again. Render antigravity_config_file() instead so the "
        "page cannot drift from the prompt and the doc."
    )


def test_play_prompt_sends_the_poll_loop_to_the_holding_tool() -> None:
    """The prompt must name ``get_next_turn`` and rule ``get_next_turns`` out.

    ``get_next_turns`` is the fan-out endpoint and the one lane that never holds
    (``pace_idle(can_hold=False)``). A client that polls it opts out of the long
    poll without knowing it, and falls into the hot loop the hold exists to
    prevent — measured at 30 calls in 100 seconds on Codex 0.146.1.
    """
    assert "get_next_turn" in _PLAY_PROMPT
    assert "Do NOT poll `get_next_turns`" in _PLAY_PROMPT


def test_play_prompt_says_the_call_itself_is_the_wait() -> None:
    """The prompt must say the call blocks.

    Nothing in the tool list tells a client that ``get_next_turn`` holds the
    request open, so without this sentence it has no reason to treat one call as
    one wait. Adding it took the same client to a 90.4s hold, then a fresh call
    2.2s later.
    """
    assert "blocking call" in _PLAY_PROMPT
    assert "the call itself IS your wait" in _PLAY_PROMPT


def test_play_prompt_has_no_hardcoded_hold_length() -> None:
    """The served prompt must not hand-type a hold length.

    The number lives in ONE place — ``agent_long_poll_hold_seconds`` in
    app/config.py — and the get_next_turn tool description already advertises
    the live value (see tests/test_mcp_poll_tool_descriptions.py). A figure
    typed into this prompt is a second copy that silently goes stale the next
    time someone tunes the setting, exactly like the "~90s" that used to sit
    here unconditionally while dev/test actually held 40s. Structural (a regex
    over any digits-plus-seconds shape) rather than a list of banned strings, so
    the NEXT hand-typed number fails this too, not just the ones we already
    caught.
    """
    matches = _HOLD_LENGTH_LITERAL_RE.findall(_PLAY_PROMPT)
    assert not matches, (
        f"_PLAY_PROMPT hand-types a hold-length figure: {matches}. State that "
        "get_next_turn blocks, not how long it blocks for — the tool's own "
        "description (mcp_server/mcp_tools.py) already advertises the live "
        "agent_long_poll_hold_seconds setting."
    )


def test_setup_doc_has_no_hardcoded_hold_length_outside_the_prompt() -> None:
    """``docs/setup-mcp.md`` must not hand-type a hold length anywhere, not just
    inside the mirrored play-prompt block.

    The doc's prose (the "MCP connection" section, outside the ```text block
    ``test_play_prompt_matches_docs`` pins) is free text a human edits directly —
    it drifted to "holds open ~90s while waiting" once already, unguarded,
    because the verbatim-prompt test only ever looked inside the code fence.
    This scans the whole file so that line can't quietly regrow a number either.
    ("about 90 days", the OAuth sign-in lifetime, is a different setting and
    does not match this pattern — see the regex comment above.)
    """
    doc = SETUP_DOC.read_text(encoding="utf-8")
    matches = _HOLD_LENGTH_LITERAL_RE.findall(doc)
    assert not matches, (
        f"docs/setup-mcp.md hand-types a hold-length figure: {matches}. Point "
        "readers at the tool's own description instead of a number that can go "
        "stale — see the comment above _PLAY_PROMPT in "
        "app/routes/connections_connect_guide.py for the reasoning."
    )
