"""Public operator handle: validation, normalization, and suggestions.

A handle is the chosen public name shown as "by @handle" on the leaderboard. It
is display-only — Google login stays the auth layer. Uniqueness is enforced on
the lowercased ``key_for(handle)`` (so ``@Alice`` and ``@alice`` can't coexist)
while the typed capitalization is preserved for display.

Callers own the uniqueness check (it needs the database); everything else —
characters, length, reserved words, and the bad-words list — lives here.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Iterator

from app.identity import word_filter

MIN_LEN = 3
MAX_LEN = 20

# Start with a letter, then letters / digits / underscores. Length is checked
# separately so we can give a length-specific message.
_HANDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class HandleError(ValueError):
    """A handle failed validation. The message is safe to show the user.

    It never echoes the rejected input, so a blocked word is not reflected back.
    """


def key_for(handle: str) -> str:
    """The lowercased uniqueness key for a handle."""
    return handle.strip().lower()


def validate(raw: str) -> str:
    """Return the cleaned display handle, or raise HandleError.

    Checks length, characters, reserved names, and the bad-words list. Does NOT
    check uniqueness — the caller does that against ``handle_key``.
    """
    handle = raw.strip()
    if len(handle) < MIN_LEN:
        raise HandleError("Handles need at least 3 characters.")
    if len(handle) > MAX_LEN:
        raise HandleError("Handles can be at most 20 characters.")
    if not _HANDLE_RE.match(handle):
        raise HandleError("Use only letters, numbers, and underscores. Start with a letter.")
    if word_filter.is_reserved(handle):
        raise HandleError("That handle is reserved. Pick a different one.")
    if word_filter.contains_blocked(handle):
        raise HandleError("That handle isn't allowed. Pick a different one.")
    return handle


def _slugify(base: str) -> str:
    """Reduce arbitrary text to a handle-shaped stem (may be empty)."""
    stem = re.sub(r"[^A-Za-z0-9_]", "", base)
    stem = re.sub(r"^[^A-Za-z]+", "", stem)  # must start with a letter
    return stem[:MAX_LEN]


def _candidate_bases(given_name: str | None, email: str | None) -> Iterator[str]:
    if given_name:
        yield given_name
    if email and "@" in email:
        yield email.split("@", 1)[0]


def _acceptable(handle: str, taken: Callable[[str], bool]) -> bool:
    try:
        validate(handle)
    except HandleError:
        return False
    return not taken(key_for(handle))


def _with_suffix(stem: str, suffix: str) -> str:
    """Stem + numeric suffix, trimmed so the whole thing fits MAX_LEN."""
    return f"{stem[: MAX_LEN - len(suffix)]}{suffix}"


def suggest(
    *,
    given_name: str | None,
    email: str | None,
    taken: Callable[[str], bool],
) -> str:
    """Suggest a free, valid handle: given name → email name-part → player####.

    ``taken`` is called with a lowercased handle key and returns whether it is
    already in use, so the suggestion is unique from the start.
    """
    for base in _candidate_bases(given_name, email):
        stem = _slugify(base)
        if not stem:
            continue
        if len(stem) >= MIN_LEN and _acceptable(stem, taken):
            return stem
        for n in range(2, 1000):
            candidate = _with_suffix(stem, str(n))
            if len(candidate) >= MIN_LEN and _acceptable(candidate, taken):
                return candidate

    # Final fallback: a random player handle. The space is large enough that the
    # loop effectively always returns.
    for _ in range(10_000):
        candidate = f"player{secrets.randbelow(900_000) + 100_000}"
        if _acceptable(candidate, taken):
            return candidate
    raise HandleError("Could not generate a handle. Pick one yourself.")
