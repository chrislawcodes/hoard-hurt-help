"""Structural guards for the /admin/reports + /admin/engagement dedup (A8).

Both pages used to carry their own copy-pasted inline timezone-detection
script. Only /admin/engagement's guarded the hidden `tz` field against being
clobbered on reload (fill only when empty, "UTC" fallback in a try/catch);
/admin/reports's unconditionally overwrote it every load, which could wipe
out the timezone the server had just echoed back from a submitted filter.
Both pages now load the same static script, app/static/admin-tz.js, carrying
the guarded version.

These tests pin two things a future template edit could silently break:
neither page reverts to an inline script (the bug this file exists to catch),
and the served static file still carries the guard rather than some other
variant a careless edit could swap in.
"""

from __future__ import annotations

import pytest

from app.models.user import User, UserRole
from tests.conftest import signed_in_cookies
from tests.factories import make_user

_ADMIN_PAGES = ["/admin/reports", "/admin/engagement"]


async def _admin(session_factory) -> User:
    async with session_factory() as db:
        user = await make_user(db, 0, handle="tzdedupadmin")
        user.role = UserRole.ADMIN
        await db.commit()
        return user


@pytest.mark.parametrize("path", _ADMIN_PAGES)
async def test_page_loads_the_shared_tz_script_with_no_inline_fallback(
    client, reset_db, path: str
) -> None:
    """Mutation tried: paste the old clobbering inline script back into
    reports.html (`if (browserTz) tz.value = browserTz;`, no guard) — this
    assertion fails against that mutation, confirmed by hand and reverted."""
    admin = await _admin(reset_db)
    response = await client.get(path, cookies=signed_in_cookies(admin.id))
    assert response.status_code == 200
    body = response.text
    assert '<script src="/static/admin-tz.js' in body
    # "resolvedOptions" only ever appears inside the browser-timezone lookup;
    # its presence in the page body (rather than only in the linked file)
    # means an inline copy of the script survived.
    assert "resolvedOptions" not in body, (
        "an inline timezone-detection script is still embedded in the page"
    )


async def test_admin_tz_js_is_served_and_carries_the_guarded_variant(
    client,
) -> None:
    """Pins the FILE CONTENT, not just the page's reference to it — a page
    could link a correctly-named script that has silently regressed to the
    old clobbering variant. Mutation tried: replace the guard with the old
    unconditional `tz.value = browserTz` (no `!field.value` check, no "UTC"
    fallback) — this assertion fails against that mutation, confirmed by hand
    and reverted."""
    response = await client.get("/static/admin-tz.js")
    assert response.status_code == 200
    js = response.text
    assert "!field.value" in js, "the fill-only-when-empty guard is missing"
    assert "UTC" in js, "the try/catch UTC fallback is missing"
