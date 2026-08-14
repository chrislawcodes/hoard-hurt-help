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

This module is the single predicate. The migration backfill and the
account-creation paths both call it, so the two cannot disagree about who is
internal.
"""

from __future__ import annotations

from app.config import settings


def internal_email_domains() -> set[str]:
    """Configured internal domains, lowercased, without empties."""
    raw = settings.internal_email_domains or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def is_internal_email(email: str | None, *, domains: set[str] | None = None) -> bool:
    """True when this address belongs to the platform rather than to a player.

    ``domains`` is injectable so the migration backfill can pass the same set
    without importing app settings into a migration context.
    """
    if not email:
        return False
    resolved = internal_email_domains() if domains is None else domains
    if not resolved:
        return False
    _, _, host = email.strip().lower().rpartition("@")
    if not host:
        return False
    return host in resolved
