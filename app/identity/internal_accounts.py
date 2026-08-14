"""Decide, once, whether an account belongs to the platform rather than a player.

House test rigs, harness accounts, the bots service user and local dev logins all
create real ``users`` rows. Counting them on the engagement dashboard would mean
the page mostly measures our own testing: in the dev database they own more than
500 of 646 player rows.

Filtering on agent kind does not touch them. Only one of those accounts owns
agents marked ``bot``; the rest run agents marked as real AI and are
indistinguishable from a genuine player by anything except who owns them.

**The answer is stored, never recomputed.** Both inputs you would naturally key on
change after the fact — ``sync_google_user`` rewrites ``users.email`` on a later
login, and ``promote_user``/``demote_user`` change the role — so a rule evaluated
at read time would let an account drift in and out of the excluded group between
two page loads, and last month's numbers would stop reproducing.

This module holds the rule used **at account creation**. The migration that
flagged accounts existing before the feature deliberately does NOT call it: a
migration has to produce the same result whenever it runs, and one that reads a
setting is not reproducible. So the rule genuinely exists twice, and the two must
be kept in step by hand — creation is domain-or-admin, and
``migrations/versions/0053_backfill_is_internal.py`` is domain-or-admin-or-bots.

An earlier version of this docstring claimed the migration called this function
and that the two therefore could not disagree. Neither half was true, and while
that claim sat here the two rules had in fact already diverged: creation checked
the domain only, so no admin account created after the deploy was ever flagged.
"""

from __future__ import annotations

from app.config import settings


def internal_email_domains() -> set[str]:
    """Configured internal domains, lowercased, without empties."""
    raw = settings.internal_email_domains or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def is_internal_email(email: str | None) -> bool:
    """True when this address is on one of the configured internal domains.

    This is only half of "is this account ours" — a platform admin on an ordinary
    address is internal too, and the caller applies that. See the module docstring
    for why the migration keeps its own copy of the rule.
    """
    if not email:
        return False
    resolved = internal_email_domains()
    if not resolved:
        return False
    _, _, host = email.strip().lower().rpartition("@")
    if not host:
        return False
    return host in resolved
