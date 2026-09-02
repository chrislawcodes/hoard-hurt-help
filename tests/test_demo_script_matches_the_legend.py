"""The demo video script quotes the replay legend, so it must not drift.

`docs/marketing/demo-video-script.md` restates the payoffs in prose so they can
be read off camera. That is a second written form of a rule the code already
owns (`hoard_legend`, `hurt_legend`, `help_legend`), and One Home Per Rule says
two forms need a test asserting they agree. Without it the failure is silent and
public: someone reads a stale number to camera and the video is wrong forever.

These are exact substring checks rather than a regex over prose. The doc quotes
each legend line verbatim for that reason, so a payoff change fails here with
the new string in the message instead of leaving a number behind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.games.hoard_hurt_help.rules import (
    BETRAYAL_BONUS,
    DEFAULT_MUTUAL_HELP_MODE,
    help_legend,
    hoard_legend,
    hurt_legend,
)

SCRIPT: Path = (
    Path(__file__).resolve().parents[1] / "docs" / "marketing" / "demo-video-script.md"
)


@pytest.fixture(scope="module")
def script_text() -> str:
    assert SCRIPT.exists(), f"{SCRIPT} is missing — update or drop this guard"
    return SCRIPT.read_text()


@pytest.mark.parametrize(
    "legend",
    [
        hoard_legend(),
        hurt_legend(),
        help_legend(DEFAULT_MUTUAL_HELP_MODE),
    ],
)
def test_the_script_quotes_the_live_legend(script_text: str, legend: str) -> None:
    assert legend in script_text, (
        f"The demo script no longer quotes the replay legend {legend!r}. The "
        f"payoffs moved; update docs/marketing/demo-video-script.md to this "
        f"line before anyone films it."
    )


def test_the_script_states_the_betrayal_chip_value(script_text: str) -> None:
    # The chip on the move shows the bonus alone while the script says the
    # attacker's net for the turn, so the doc explains the gap. If the bonus
    # moves, that explanation is wrong in both numbers at once.
    assert f"+{BETRAYAL_BONUS}" in script_text, (
        f"The demo script explains why the betrayal chip reads one number and "
        f"the voiceover says another, but no longer names the bonus "
        f"(+{BETRAYAL_BONUS})."
    )
