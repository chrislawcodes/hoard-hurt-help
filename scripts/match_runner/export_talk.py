"""What a seat said, read out of an export row.

The one home for a reader-side rule with a deadline built into it: exports
written before the `talk` rename carry the same value under `message`, and those
files do not get rewritten. Every saved export on disk, and every export any of
us has already downloaded, is a `message` export. So a reader that knows only
one key silently reports the other era as silent — which is the exact failure
the rename was made to stop, moved one layer out.

Reading both keys is not a compatibility shim to delete later. Old exports stay
old, so this stays.

`talk` wins when both are present. Nothing writes both today, but if something
ever does, the newer name is the one the current code produced.
"""

from __future__ import annotations

from typing import Any

# The current key, then the pre-rename one. Order is the precedence.
TALK_KEYS = ("talk", "message")


def read_talk(row: dict[str, Any]) -> str:
    """The words one seat said on one turn, from one export submission row.

    Raises ``KeyError`` when the row carries neither key. That is a loud failure
    on purpose: a row shaped like neither era is an export this reader does not
    understand, and returning "" for it would print a confident 0% talk figure
    built on nothing. Reading a wrongly-quiet number off this report is how
    M_7360 got called a hoarding convergence.
    """
    for key in TALK_KEYS:
        if key in row:
            return row[key] or ""
    raise KeyError(
        f"export row has no talk column: expected one of {TALK_KEYS}, "
        f"got {sorted(row)}. This is not an export this reader understands."
    )
