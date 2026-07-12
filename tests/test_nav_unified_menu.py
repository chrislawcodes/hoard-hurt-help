"""Unified nav menu: what a visitor can actually reach from the nav bar.

On a phone the nav collapses to one menu in the right corner. Signed out, that
menu offers "Sign in". Signed in, the account menu carries both the wayfinding
links (so the phone keeps a single menu) and the account actions, and the
separate "Sign in" is gone.

These tests assert on what a visitor sees and where the links go — visible link
text and destinations — not on CSS class names or where tags sit in the markup,
so renaming a class or reshuffling the nav structure won't break them while the
nav still works.
"""

import re

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_user
from tests.conftest import signed_in_cookies as _signed_in_cookies


@pytest.fixture(autouse=True)
async def reset_db(reset_db: async_sessionmaker) -> async_sessionmaker:
    """Autouse override of tests/conftest.py's reset_db: every test here touches the DB."""
    return reset_db


# --- Reading the nav the way a visitor does (text + destinations) -----------

_TAG = re.compile(r"<[^>]+>")


def _text(html: str) -> str:
    """Visible text of an HTML fragment: tags stripped, whitespace collapsed."""
    return re.sub(r"\s+", " ", _TAG.sub(" ", html)).strip()


def _links(html: str) -> list[tuple[str, str]]:
    """Every link in a fragment as (visible text, destination href)."""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r'<a\b[^>]*?\bhref="([^"]*)"[^>]*>(.*?)</a>', html, re.S):
        out.append((_text(m.group(2)), m.group(1)))
    return out


def _nav(page: str) -> str:
    """Inner HTML of the <nav> landmark — the navigation bar a visitor sees.

    Scoped on purpose: the footer repeats several of the same links (Sign in,
    Games, Leaderboard…), so a page-wide search would pass even if the nav bar
    itself were broken. We anchor on the <nav> landmark, not a CSS class.
    """
    m = re.search(r"<nav\b[^>]*>(.*?)</nav>", page, re.S)
    assert m is not None, "page is missing its <nav> landmark"
    return m.group(1)


def _account_menu(page: str) -> str | None:
    """Inner HTML of the account menu, or None when the visitor is signed out.

    Located by behavior, not by class: it is the one nav dropdown a visitor can
    sign out from. Nested <details> (the admin submenu) are balanced so the menu
    doesn't end early at the first inner </details>.
    """
    nav = _nav(page)
    starts: list[int] = []
    for m in re.finditer(r"<details\b[^>]*>|</details>", nav, re.S):
        if m.group().startswith("</"):
            if not starts:
                continue
            inner = nav[starts.pop() : m.start()]
            if 'action="/auth/logout"' in inner:
                return inner
        else:
            starts.append(m.end())
    return None


async def test_signed_out_nav_offers_sign_in_and_no_account_menu(client):
    r = await client.get("/games")
    assert r.status_code == 200
    nav = _nav(r.text)
    # Signed out, the nav offers a way to sign in.
    assert ("Sign in", "/auth/google/login") in _links(nav)
    # And there is no account menu — nothing to sign out of, no identity shown.
    assert _account_menu(r.text) is None
    assert "Sign out" not in _text(nav)


async def test_signed_in_account_menu_carries_wayfinding_and_hides_sign_in(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        user_id, handle = user.id, user.handle

    r = await client.get("/games", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200

    acct = _account_menu(r.text)
    assert acct is not None, "a signed-in visitor should get an account menu"
    acct_links = _links(acct)
    # The wayfinding links live inside the account menu, so the phone keeps a
    # single menu instead of two dropdowns at opposite ends of the bar.
    assert ("Games", "/games") in acct_links
    assert ("Leaderboard", "/leaderboard") in acct_links
    # The menu shows who you're signed in as and lets you sign out.
    acct_text = _text(acct)
    assert f"@{handle}" in acct_text
    assert "Sign out" in acct_text
    # Signed in, the nav no longer offers "Sign in".
    nav = _nav(r.text)
    assert "/auth/google/login" not in nav
    assert not any(text == "Sign in" for text, _ in _links(nav))


async def test_marketing_nav_override_follows_into_account_menu(client, reset_db):
    # The marketing home adds "How it works" to nav_links; the account menu
    # renders the same block, so that page override must follow along into it.
    async with reset_db() as db:
        user = await make_user(db)
        await db.commit()
        user_id = user.id

    r = await client.get("/", cookies=_signed_in_cookies(user_id))
    assert r.status_code == 200
    acct = _account_menu(r.text)
    assert acct is not None
    assert ("How it works", "/#how") in _links(acct)
