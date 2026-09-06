"""The one home for keeping a number inside a range."""

from __future__ import annotations

from typing import TypeVar

_Number = TypeVar("_Number", int, float)


def clamp(value: _Number, low: _Number, high: _Number) -> _Number:
    """Push ``value`` back inside ``[low, high]`` if it falls outside.

    This is the one home for this rule — do not write a second version.
    Works on ``int`` or ``float`` (matched consistently, per call, by the
    TypeVar) since real call sites need both.
    """
    return max(low, min(high, value))
