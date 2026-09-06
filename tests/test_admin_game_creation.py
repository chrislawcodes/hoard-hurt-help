"""Admin game/match creation tests: the API and web-form create paths.

Split from test_admin.py — records the creator, enforces player-count and
game-type limits, the web form vs. admin API for both hoard-hurt-help and
liars-dice, and the per-match mutual-help mode switch (flat/decay/default,
and its typo rejection).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.games.hoard_hurt_help.rules import DEFAULT_MUTUAL_HELP_MODE
from app.models import Match, MatchState
from tests.admin_support import reset_db
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import seed_email_user_with_role

# `reset_db` is only ever referenced as a fixture-name parameter below (that's
# how pytest wires a fixture to a test), never called by its imported name at
# module level, so ruff sees the import as unused unless it's listed here --
# the same pattern tests/conftest.py already uses for its own re-exports.
__all__ = ["reset_db"]


async def test_admin_api_records_creator(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/admin/matches",
        json={
            "game_type": "hoard-hurt-help",
            "name": "QA",
            "scheduled_start": when,
            "min_players": 6,
            "max_players": 10,
            "per_turn_deadline_seconds": 30,
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"].startswith("M_")
    assert body["state"] == "registering"
    async with reset_db() as db:
        match = (await db.execute(select(Match).where(Match.id == body["id"]))).scalar_one()
        assert match.created_by_user_id == admin.id


async def test_platform_admin_api_records_creator(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/admin/matches",
        json={
            "name": "Platform QA",
            "scheduled_start": when,
            "min_players": 6,
            "max_players": 10,
            "per_turn_deadline_seconds": 30,
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    async with reset_db() as db:
        match = (await db.execute(select(Match).where(Match.id == body["id"]))).scalar_one()
        assert match.created_by_user_id == admin.id


async def test_admin_api_rejects_player_count_over_max(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/admin/matches",
        json={
            "game_type": "hoard-hurt-help",
            "name": "Too Big",
            "scheduled_start": when,
            "min_players": 6,
            "max_players": 11,
            "per_turn_deadline_seconds": 30,
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 400
    assert "supports 6-10 players" in r.text


async def test_api_rejects_unknown_game_type(client, reset_db):
    # An unknown game type must be rejected at creation (4xx), so a match with a
    # game the scheduler can't run never gets persisted as a future zombie.
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/admin/matches",
        json={
            "game_type": "no-such-game",
            "name": "Bad Type",
            "scheduled_start": when,
            "min_players": 6,
            "max_players": 10,
            "per_turn_deadline_seconds": 30,
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 400, r.text
    assert "No game module registered" in r.text


async def test_web_form_rejects_unknown_game_type(client, reset_db):
    """An unknown game is a 404, not a rendered form error.

    The removed admin form answered 400 with "Unknown game type X". The one
    surviving create route answers 404 — the same thing every other route says
    about a game that does not exist, and the behaviour the player create path
    already had.
    """
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/no-such-game/matches/new",
        data={
            "name": "Bad Type",
            "scheduled_start": future,
            "min_players": "3",
            "max_players": "10",
            "per_turn_deadline_seconds": "60",
        },
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Game not found."


async def test_create_game_via_web_form(client, reset_db):
    """The browser posts a UTC ISO string (from datetime-local JS conversion)."""
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/hoard-hurt-help/matches/new",
        data={
            "name": "Web Night",
            "scheduled_start": future,
            "min_players": "6",
            "max_players": "10",
            "per_turn_deadline_seconds": "60",
        },
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 303  # redirect on success
    async with reset_db() as db:
        match = (
            await db.execute(select(Match).where(Match.name == "Web Night"))
        ).scalar_one()
        assert match.created_by_user_id == admin.id


async def test_web_form_rejects_player_count_over_max(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/hoard-hurt-help/matches/new",
        data={
            "name": "Too Big",
            "scheduled_start": future,
            "min_players": "6",
            "max_players": "11",
            "per_turn_deadline_seconds": "60",
        },
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "6 to 10" in r.text


async def test_web_form_rejects_past_time(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/hoard-hurt-help/matches/new",
        data={
            "name": "Past",
            "scheduled_start": past,
            "min_players": "3",
            "max_players": "10",
            "per_turn_deadline_seconds": "60",
        },
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "must be in the future" in r.text


async def test_platform_admin_api_creates_liars_dice_match_and_persists_config(
    client, reset_db
):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/admin/matches",
        json={
            "name": "LD API",
            "scheduled_start": when,
            "game_type": "liars-dice",
            "min_players": 3,
            "max_players": 6,
            "per_turn_deadline_seconds": 30,
            "wild_ones": False,
            "dice_per_player": 4,
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    async with reset_db() as db:
        match = (await db.execute(select(Match).where(Match.id == body["id"]))).scalar_one()
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        assert match.game == "liars-dice"
        assert state.state_json["config"] == {"wild_ones": False, "dice_per_player": 4}


async def test_web_form_creates_liars_dice_match_and_persists_config(
    client, reset_db
):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/liars-dice/matches/new",
        data={
            "name": "LD Web",
            "scheduled_start": future,
            "min_players": "3",
            "max_players": "6",
            "per_turn_deadline_seconds": "60",
            "total_rounds": "7",
            "turns_per_round": "7",
            "wild_ones": "on",
            "dice_per_player": "4",
        },
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    async with reset_db() as db:
        match = (await db.execute(select(Match).where(Match.name == "LD Web"))).scalar_one()
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        assert match.game == "liars-dice"
        assert state.state_json["config"] == {"wild_ones": True, "dice_per_player": 4}


# --- Mutual-help decay: the per-match rule switch, settable by an admin ---
#
# The switch shipped as a Match column with no way to set it outside Python, so
# admins could only ever create decay-on matches. These cover both admin entry
# points, and the case that silently breaks things: a form that never renders the
# control must still create the shipped default rather than turning decay off.


async def _create_via_form(client, admin, name, **extra):
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    return await client.post(
        f"/games/{extra.pop('game', 'hoard-hurt-help')}/matches/new",
        data={
            "name": name,
            "scheduled_start": future,
            "min_players": "6",
            "max_players": "10",
            "per_turn_deadline_seconds": "60",
            **extra,
        },
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )


async def _match_named(reset_db, name):
    async with reset_db() as db:
        return (
            await db.execute(select(Match).where(Match.name == name))
        ).scalar_one()


async def test_create_form_offers_the_mode_control_only_for_hoard_hurt_help(
    client, reset_db
):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    hhh = await client.get(
        "/games/hoard-hurt-help/matches/new", cookies=_cookies(admin.id)
    )
    assert hhh.status_code == 200
    assert 'name="mutual_help_mode"' in hhh.text
    other = await client.get(
        "/games/liars-dice/matches/new", cookies=_cookies(admin.id)
    )
    assert other.status_code == 200
    assert 'name="mutual_help_mode"' not in other.text


async def test_web_form_can_create_a_flat_match(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    r = await _create_via_form(client, admin, "Flat Bonus", mutual_help_mode="flat_8")
    assert r.status_code == 303, r.text
    assert (await _match_named(reset_db, "Flat Bonus")).mutual_help_mode == "flat_8"


async def test_web_form_decay_is_the_default(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    r = await _create_via_form(client, admin, "Decaying", mutual_help_mode="decay")
    assert r.status_code == 303, r.text
    assert (await _match_named(reset_db, "Decaying")).mutual_help_mode == "decay"


async def test_web_form_without_the_field_uses_the_platform_default(client, reset_db):
    """A form that never rendered the control must not silently pick a rule.

    An unchecked checkbox and a game whose form omits the control both arrive as
    "absent" — that has to mean "the platform default", not any one named mode.
    """
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    r = await _create_via_form(client, admin, "No Field")
    assert r.status_code == 303, r.text
    assert (
        await _match_named(reset_db, "No Field")
    ).mutual_help_mode == DEFAULT_MUTUAL_HELP_MODE.value


async def test_admin_api_can_create_a_flat_match(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/admin/matches",
        json={
            "name": "API Flat",
            "scheduled_start": when,
            "mutual_help_mode": "flat_8",
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201, r.text
    assert (await _match_named(reset_db, "API Flat")).mutual_help_mode == "flat_8"


async def test_admin_api_omitting_the_mode_uses_the_platform_default(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/admin/matches",
        json={"name": "API Default", "scheduled_start": when},
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201, r.text
    assert (
        await _match_named(reset_db, "API Default")
    ).mutual_help_mode == DEFAULT_MUTUAL_HELP_MODE.value


async def test_web_form_rejects_an_unknown_mutual_help_mode(client, reset_db):
    """A typo must be refused, not quietly stored or defaulted.

    Silently falling back to "decay" would mislabel which rule the match was
    played under — a result that looks fine and means something else.
    """
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    r = await _create_via_form(client, admin, "Typo Mode", mutual_help_mode="flat_5")
    assert r.status_code == 400
    assert "Unknown mutual-help mode" in r.text
    async with reset_db() as db:
        assert (
            await db.execute(select(Match).where(Match.name == "Typo Mode"))
        ).scalar_one_or_none() is None
