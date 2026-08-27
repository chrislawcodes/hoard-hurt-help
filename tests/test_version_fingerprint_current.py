"""The pooled report's version fingerprint must know the shipped payoffs.

`scripts/match_runner/pooled_report.py` works out which rules a match ACTUALLY
ran on, rather than trusting the `rules_version` stamped at creation — a match
created in the gap between a deploy reporting success and the new process
serving gets the old label while playing the new payoffs. M_7508 did exactly
that.

It fingerprints on one number: what a HURT against a HOARDing target pays the
attacker. That number lives in `rules.py` and is copied into a map in the
report, which is a second home for a rule that already has one. When the next
version changes it, the map goes stale — and it fails SILENTLY, labelling every
new match as the previous version and pooling it with matches whose payoffs it
does not share. That is the exact mixing the fingerprint exists to prevent.

So: this asserts the map knows the shipped value. It cannot check the map is
*complete* for versions that no longer exist in the code, and it does not try —
the failure it guards is a new version arriving unnoticed.

If this fails after a payoff change, add the new (take, version) pair to
`HOARDER_TAKE_BY_VERSION`. If the change did NOT move HURT_TAKE_HOARDER, the
fingerprint can no longer tell the two versions apart and needs a second signal
— which this test cannot detect for you, and is worth a comment there when it
happens.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.games.hoard_hurt_help.rules import HURT_TAKE_HOARDER, RULES_VERSION

REPORT = Path(__file__).resolve().parents[1] / "scripts" / "match_runner" / "pooled_report.py"


def _load_report():
    """Import the report as a module. It is a script, not a package member."""
    spec = importlib.util.spec_from_file_location("pooled_report", REPORT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["pooled_report"] = module
    spec.loader.exec_module(module)
    return module


def test_the_fingerprint_knows_the_shipped_payoff() -> None:
    """The shipped HURT_TAKE_HOARDER must map to the shipped RULES_VERSION."""
    report = _load_report()
    mapping = report.HOARDER_TAKE_BY_VERSION
    assert HURT_TAKE_HOARDER in mapping, (
        f"HURT_TAKE_HOARDER is {HURT_TAKE_HOARDER} and the pooled report's "
        f"fingerprint does not know that value: {mapping}. Every {RULES_VERSION} "
        f"match will be labelled as an older version and pooled with matches "
        f"whose payoffs it does not share. Add {HURT_TAKE_HOARDER}: "
        f'"{RULES_VERSION}" to HOARDER_TAKE_BY_VERSION.'
    )
    assert mapping[HURT_TAKE_HOARDER] == RULES_VERSION, (
        f"The fingerprint maps a take of {HURT_TAKE_HOARDER} to "
        f'"{mapping[HURT_TAKE_HOARDER]}", but the shipped version is '
        f'"{RULES_VERSION}".'
    )


def test_each_take_maps_to_one_version() -> None:
    """Two versions sharing a take would make the fingerprint ambiguous.

    A dict cannot hold a duplicate key, so this catches the shape that WOULD
    express it — a version appearing twice under different takes, which means
    one of them is wrong.
    """
    report = _load_report()
    versions = list(report.HOARDER_TAKE_BY_VERSION.values())
    assert len(versions) == len(set(versions)), (
        f"A rules version appears under more than one take: "
        f"{report.HOARDER_TAKE_BY_VERSION}. The fingerprint cannot then tell "
        f"which payoffs a match ran on."
    )
