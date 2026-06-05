"""One shared bad-words + reserved-name list for all public-facing text.

Every surface that shows text a human typed — handles, agent display names, and
(in a later phase) agent turn messages — runs through this one module, so adding
a word covers all of them at once. The lists live in code, not the database, so
they can grow without a migration.

Honest limitation: a word list is a first line of defense, not a guarantee. It
is paired with admin reset and the fact that every handle is tied to a real
Google account. Matching is deliberately simple (normalize, then substring),
which can over-match in rare cases (the "Scunthorpe problem"); when that bites a
real handle the user just picks another, which is an acceptable trade for a small
site.
"""

from __future__ import annotations

import re

# Exact names nobody may take as a handle — they imply platform/staff authority.
# Compared against the *normalized* handle, so casing/spacing don't dodge them.
RESERVED: frozenset[str] = frozenset(
    {
        "admin",
        "administrator",
        "system",
        "sim",
        "agentludum",
        "staff",
        "moderator",
        "mod",
        "support",
        "official",
        "null",
        "none",
    }
)

# Profanity / slur seed list. Intentionally partial — extend here over time; the
# same list backs handles, agent names, and (Phase 2) messages. Kept to clearly
# offensive tokens to limit false positives on innocent words.
BLOCKED: frozenset[str] = frozenset(
    {
        "fuck",
        "shit",
        "bitch",
        "cunt",
        "asshole",
        "bastard",
        "dick",
        "piss",
        "slut",
        "whore",
        "nigger",
        "faggot",
        "retard",
        "rape",
        "nazi",
    }
)

# Common letter/number look-alikes, so "sh1t" / "f@ck" still normalize to the
# blocked word.
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


def normalize(text: str) -> str:
    """Lowercase, fold common look-alikes, and drop everything non-alphanumeric.

    Collapsing to bare letters/digits means spacing and punctuation tricks
    ("f u c k", "f-u-c-k") reduce to the same string we match against.
    """
    lowered = text.lower().translate(_LEET)
    return re.sub(r"[^a-z0-9]", "", lowered)


def contains_blocked(text: str) -> bool:
    """True if any blocked word appears in the normalized text."""
    squashed = normalize(text)
    return any(word in squashed for word in BLOCKED)


def is_reserved(text: str) -> bool:
    """True if the normalized text exactly matches a reserved name."""
    return normalize(text) in RESERVED


def mask(text: str) -> str:
    """Replace each blocked word with four asterisks, preserving the rest.

    Used for agent turn messages (Phase 2): the message still posts, but the
    blocked word is censored to a fixed-length ``****`` that never reveals the
    original length. Matches whole words case-insensitively on the raw text;
    handles and names are *rejected* on save instead of masked.
    """
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(word) for word in BLOCKED) + r")\b",
        re.IGNORECASE,
    )
    return pattern.sub("****", text)
