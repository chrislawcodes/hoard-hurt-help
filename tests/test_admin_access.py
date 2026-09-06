"""Admin dashboard access + gating tests.

Split from test_admin.py — who can reach /admin/*, the per-game dashboard,
and a couple of dashboard-rendering edge cases (a match row with no start
time must not take the page down).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from starlette.requests import Request

from app.config import settings
from app.models import GameState
from app.models.user import UserRole
from app.routes import admin_web, match_manage_web, web_support
from tests.admin_support import reset_db
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import seed_email_user_with_role

# `reset_db` is only ever referenced as a fixture-name parameter below (that's
# how pytest wires a fixture to a test), never called by its imported name at
# module level, so ruff sees the import as unused unless it's listed here --
# the same pattern tests/conftest.py already uses for its own re-exports.
__all__ = ["reset_db"]


async def test_non_admin_blocked(client, reset_db):
    user = await seed_email_user_with_role(reset_db, "regular@test.com")
    r = await client.get("/admin/matches", cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 403


async def test_admin_can_see_dashboard(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    r = await client.get("/admin/matches", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert "Match Admin" in r.text


async def test_old_admin_root_is_gone(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    r = await client.get("/admin", cookies=_cookies(admin.id), follow_redirects=False)
    assert r.status_code == 404


async def test_create_game_button_links_to_a_real_route(client, reset_db):
    # The "+ Create game" button used to point at /matches/new, which has
    # no route, so admins got a 404. It must link to the game-scoped create
    # form, and that target must actually load.
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    r = await client.get("/admin/matches", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert 'href="/admin/matches/new"' not in r.text
    assert 'href="/games/hoard-hurt-help/admin/matches/new"' not in r.text
    assert 'href="/games/hoard-hurt-help/matches/new"' in r.text
    form = await client.get(
        "/games/hoard-hurt-help/matches/new", cookies=_cookies(admin.id)
    )
    assert form.status_code == 200


async def test_dashboard_prompts_link_is_game_scoped(client, reset_db):
    # The "Strategy prompts" link had the same bug as the create button: it
    # pointed at /admin/prompts, which has no route. It must be game-scoped.
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    r = await client.get("/admin/matches", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert 'href="/admin/prompts"' not in r.text
    assert 'href="/games/hoard-hurt-help/admin/prompts"' in r.text
    prompts = await client.get(
        "/games/hoard-hurt-help/admin/prompts", cookies=_cookies(admin.id)
    )
    assert prompts.status_code == 200


async def test_admin_menu_groups_platform_admin_links(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    r = await client.get("/admin/matches", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert 'role="menuitem">Platform admin</a>' not in r.text
    assert 'href="/admin/matches" role="menuitem">Match Admin</a>' in r.text
    assert 'href="/admin/reports" role="menuitem">Reporting</a>' in r.text


async def test_plain_user_cannot_access_platform_admin(client, reset_db, monkeypatch):
    """A plain user cannot reach the platform admin dashboard.

    This is the guard behind every /admin/* page. It used to be written as
    "a game admin cannot reach it"; the role is gone, so the actor is now a
    plain user, but the thing being pinned is the same.
    """
    monkeypatch.setattr(settings, "platform_admin_emails", "platformonly@test.com")
    monkeypatch.setattr(settings, "admin_emails", "")
    plain = await seed_email_user_with_role(reset_db, "plain@test.com")
    assert plain.role == UserRole.USER
    r = await client.get("/admin/matches", cookies=_cookies(plain.id), follow_redirects=False)
    assert r.status_code == 403


async def test_platform_admin_can_access_the_game_dashboard(client, reset_db, monkeypatch):
    """A platform admin reaches the per-game dashboard with no env-var listing.

    Before the two-role change this returned 403: the gate read an env list and
    never looked at users.role, so a platform admin who wasn't listed for that
    game was locked out of it. That trap is what this change removes.
    """
    monkeypatch.setattr(settings, "platform_admin_emails", "platformonly@test.com")
    monkeypatch.setattr(settings, "admin_emails", "")
    platformonly = await seed_email_user_with_role(reset_db, "platformonly@test.com")
    assert platformonly.role == UserRole.ADMIN
    r = await client.get(
        "/games/hoard-hurt-help/admin/", cookies=_cookies(platformonly.id), follow_redirects=False
    )
    assert r.status_code == 200


async def test_plain_user_cannot_access_any_game_dashboard(client, reset_db, monkeypatch):
    """A plain user is locked out of every game's dashboard, not just one."""
    monkeypatch.setattr(settings, "admin_emails", "")
    plain = await seed_email_user_with_role(reset_db, "plain@test.com")
    for game in ("hoard-hurt-help", "other-game"):
        r = await client.get(
            f"/games/{game}/admin/", cookies=_cookies(plain.id), follow_redirects=False
        )
        assert r.status_code == 403, game


async def test_match_dashboard_handles_missing_start_time(monkeypatch):
    """A bad match row should not take the whole dashboard down."""

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class FakeDB:
        async def execute(self, _stmt):
            return FakeResult(
                [
                    SimpleNamespace(
                        id="M_9999",
                        name="Broken row",
                        scheduled_start=None,
                        current_round=0,
                        total_rounds=7,
                        state=GameState.SCHEDULED,
                    )
                ]
            )

    async def _no_counts(_db, _match_ids, **_kwargs):
        # The dashboard batches seated-player counts in one grouped query; the
        # fake match row has no players, so return an empty map (absent → 0).
        return {}

    monkeypatch.setattr(web_support, "count_players_by_match", _no_counts)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/games/hoard-hurt-help/admin/",
            "headers": [],
            "query_string": b"",
        },
        receive,
    )

    response = await match_manage_web.match_dashboard(
        game="hoard-hurt-help",
        request=request,
        db=FakeDB(),
        user=SimpleNamespace(email="admin@test.com", role=UserRole.ADMIN),
    )

    assert response.context["scheduled_games"][0]["scheduled_start"] is None


async def test_platform_admin_dashboard_handles_missing_start_time(monkeypatch):
    """The top-level admin page should also survive a broken timestamp."""

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class FakeDB:
        async def execute(self, _stmt):
            return FakeResult(
                [
                    SimpleNamespace(
                        id="M_9999",
                        game="hoard-hurt-help",
                        name="Broken row",
                        match_kind="manual",
                        scheduled_start=None,
                        min_players=3,
                        max_players=10,
                        state=GameState.SCHEDULED,
                    )
                ]
            )

    async def _no_counts(_db, _match_ids, **_kwargs):
        # The dashboard batches seated-player counts in one grouped query; the
        # fake match row has no players, so return an empty map (absent → 0).
        return {}

    monkeypatch.setattr(web_support, "count_players_by_match", _no_counts)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/matches",
            "headers": [],
            "query_string": b"",
        },
        receive,
    )

    response = await admin_web.admin_dashboard(
        request=request,
        db=FakeDB(),
        user=SimpleNamespace(email="admin@test.com"),
    )

    assert response.context["scheduled_games"][0]["scheduled_start"] is None


async def test_admin_api_accessible(client, reset_db):
    """A platform admin can create a match via the admin API."""
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/admin/matches",
        json={"game_type": "hoard-hurt-help", "name": "Boundary", "scheduled_start": when, "min_players": 6, "max_players": 10, "per_turn_deadline_seconds": 30},
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201


async def test_agent_api_not_shadowed(client, reset_db):
    """The game/{match_id} agent API route is not shadowed by the match-manage router."""
    # A non-existent match returns 404 from the agent API, not a routing error.
    r = await client.get("/api/games/NOSUCHID/state")
    assert r.status_code in (401, 404, 422)  # any non-405 proves the route is reachable


