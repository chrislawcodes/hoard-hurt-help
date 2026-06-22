"""Admin auth + game creation + export tests."""

import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from itsdangerous import TimestampSigner
from sqlalchemy import select
from starlette.requests import Request

from app.config import settings
from app.engine.match_deletion import delete_match
from app.models import Base, GameState, Match, MatchState, Player, RequestIncident, Turn, TurnSubmission, User
from app.models.user import UserRole
from app.read_models.admin_reports import load_turn_timing_report
from app.routes import admin_web
from app.routes import game_admin_web
from app.routes import web_support
from tests.factories import make_agent


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    from app.db import make_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    monkeypatch.setattr(settings, "admin_emails", "admin@test.com")

    yield test_factory
    await test_engine.dispose()


def _cookies(user_id: int) -> dict:
    signer = TimestampSigner(settings.session_secret)
    payload = base64.b64encode(json.dumps({"user_id": user_id}).encode()).decode()
    return {"hhh_session": signer.sign(payload).decode()}


async def _seed_user(reset_db, email: str) -> User:
    async with reset_db() as db:
        u = User(
            google_sub=f"sub-{email}",
            email=email,
            name=email,
            role=(
                UserRole.ADMIN
                if email.lower() in settings.platform_admin_emails_set
                else UserRole.USER
            ),
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


async def _seed_turn_timing_match(
    reset_db,
    *,
    match_id: str = "M_turn_report",
    name: str = "Report Match",
    completed_at: datetime | None = None,
) -> str:
    async with reset_db() as db:
        completed_at = completed_at or datetime.now(timezone.utc)
        seed_tag = match_id.lower().replace(" ", "-")
        owner1 = User(
            google_sub=f"sub-{seed_tag}-1",
            email=f"turn1+{seed_tag}@test.com",
            name=f"turn1+{seed_tag}@test.com",
        )
        owner2 = User(
            google_sub=f"sub-{seed_tag}-2",
            email=f"turn2+{seed_tag}@test.com",
            name=f"turn2+{seed_tag}@test.com",
        )
        owner3 = User(
            google_sub=f"sub-{seed_tag}-3",
            email=f"turn3+{seed_tag}@test.com",
            name=f"turn3+{seed_tag}@test.com",
        )
        db.add_all([owner1, owner2, owner3])
        await db.flush()

        agent1, version1 = await make_agent(db, owner1, name="AI_1")
        agent2, version2 = await make_agent(db, owner2, name="AI_2")
        agent3, version3 = await make_agent(db, owner3, name="AI_3")

        match = Match(
            id=match_id,
            name=name,
            game="hoard-hurt-help",
            state=GameState.COMPLETED,
            scheduled_start=completed_at - timedelta(hours=1),
            started_at=completed_at - timedelta(minutes=10),
            completed_at=completed_at,
        )
        db.add(match)
        await db.flush()

        player1 = Player(
            match_id=match.id,
            user_id=owner1.id,
            agent_id=agent1.id,
            seat_name="AI_1",
            agent_version_id=version1.id if version1 is not None else None,
            model_self_report=version1.model if version1 is not None else None,
        )
        player2 = Player(
            match_id=match.id,
            user_id=owner2.id,
            agent_id=agent2.id,
            seat_name="AI_2",
            agent_version_id=version2.id if version2 is not None else None,
            model_self_report=version2.model if version2 is not None else None,
        )
        player3 = Player(
            match_id=match.id,
            user_id=owner3.id,
            agent_id=agent3.id,
            seat_name="AI_3",
            agent_version_id=version3.id if version3 is not None else None,
            model_self_report=version3.model if version3 is not None else None,
        )
        db.add_all([player1, player2, player3])
        await db.flush()

        turn1_opened = completed_at - timedelta(seconds=90)
        turn2_opened = completed_at - timedelta(seconds=40)
        turn1 = Turn(
            match_id=match.id,
            round=1,
            turn=1,
            turn_token=f"{seed_tag}-tk-1",
            opened_at=turn1_opened,
            deadline_at=turn1_opened + timedelta(seconds=30),
            resolved_at=turn1_opened + timedelta(seconds=31),
        )
        turn2 = Turn(
            match_id=match.id,
            round=1,
            turn=2,
            turn_token=f"{seed_tag}-tk-2",
            opened_at=turn2_opened,
            deadline_at=turn2_opened + timedelta(seconds=30),
            resolved_at=turn2_opened + timedelta(seconds=31),
        )
        db.add_all([turn1, turn2])
        await db.flush()

        db.add_all(
            [
                TurnSubmission(
                    turn_id=turn1.id,
                    player_id=player1.id,
                    action="HOARD",
                    points_delta=2,
                    round_score_after=2,
                    submitted_at=turn1_opened + timedelta(seconds=9),
                ),
                TurnSubmission(
                    turn_id=turn1.id,
                    player_id=player2.id,
                    action="HELP",
                    points_delta=1,
                    round_score_after=1,
                    submitted_at=turn1_opened + timedelta(seconds=25),
                ),
                TurnSubmission(
                    turn_id=turn1.id,
                    player_id=player3.id,
                    action="HURT",
                    points_delta=-1,
                    round_score_after=-1,
                    submitted_at=turn1_opened + timedelta(seconds=40),
                ),
                TurnSubmission(
                    turn_id=turn2.id,
                    player_id=player1.id,
                    action="HOARD",
                    points_delta=2,
                    round_score_after=4,
                    submitted_at=turn2_opened + timedelta(seconds=15),
                ),
                TurnSubmission(
                    turn_id=turn2.id,
                    player_id=player2.id,
                    action="HELP",
                    points_delta=1,
                    round_score_after=2,
                    submitted_at=turn2_opened + timedelta(seconds=35),
                ),
                TurnSubmission(
                    turn_id=turn2.id,
                    player_id=player3.id,
                    action="HOARD",
                    points_delta=0,
                    round_score_after=0,
                    was_defaulted=True,
                    submitted_at=None,
                ),
            ]
        )
        await db.commit()
        return match.id


@pytest.mark.asyncio
async def test_non_admin_blocked(client, reset_db):
    user = await _seed_user(reset_db, "regular@test.com")
    r = await client.get("/admin/matches", cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_see_dashboard(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    r = await client.get("/admin/matches", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert "Match Admin" in r.text


@pytest.mark.asyncio
async def test_old_admin_root_is_gone(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    r = await client.get("/admin", cookies=_cookies(admin.id), follow_redirects=False)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_game_button_links_to_a_real_route(client, reset_db):
    # The "+ Create game" button used to point at /admin/matches/new, which has
    # no route, so admins got a 404. It must link to the game-scoped create
    # form, and that target must actually load.
    admin = await _seed_user(reset_db, "admin@test.com")
    r = await client.get("/admin/matches", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert 'href="/admin/matches/new"' not in r.text
    assert 'href="/games/hoard-hurt-help/admin/matches/new"' in r.text
    form = await client.get(
        "/games/hoard-hurt-help/admin/matches/new", cookies=_cookies(admin.id)
    )
    assert form.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_prompts_link_is_game_scoped(client, reset_db):
    # The "Strategy prompts" link had the same bug as the create button: it
    # pointed at /admin/prompts, which has no route. It must be game-scoped.
    admin = await _seed_user(reset_db, "admin@test.com")
    r = await client.get("/admin/matches", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert 'href="/admin/prompts"' not in r.text
    assert 'href="/games/hoard-hurt-help/admin/prompts"' in r.text
    prompts = await client.get(
        "/games/hoard-hurt-help/admin/prompts", cookies=_cookies(admin.id)
    )
    assert prompts.status_code == 200


@pytest.mark.asyncio
async def test_admin_menu_groups_platform_admin_links(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    r = await client.get("/admin/matches", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert 'role="menuitem">Platform admin</a>' not in r.text
    assert 'href="/admin/matches" role="menuitem">Match Admin</a>' in r.text
    assert 'href="/admin/reports" role="menuitem">Reporting</a>' in r.text


@pytest.mark.asyncio
async def test_turn_timing_report_counts_and_buckets(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    await _seed_turn_timing_match(reset_db)
    async with reset_db() as db:
        report = await load_turn_timing_report(db)
    assert report.matches_scanned == 1
    assert report.matches_with_samples == 1
    assert report.turn_count == 2
    assert report.sample_count == 5
    assert report.defaulted_count == 1
    assert report.mean_seconds == pytest.approx(24.8)
    bucket_counts = {bucket.label: bucket.count for bucket in report.buckets}
    assert bucket_counts["0-10s"] == 1
    assert bucket_counts["10-20s"] == 1
    assert bucket_counts["20-30s"] == 1
    assert bucket_counts["30-45s"] == 2
    assert bucket_counts["45-60s"] == 0
    assert bucket_counts["60-90s"] == 0

    r = await client.get("/admin/reports", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert "Reporting" in r.text
    assert "Report Match" in r.text
    assert "0-10s" in r.text
    assert 'name="start_date"' in r.text
    assert 'name="end_date"' in r.text
    assert "Matches scanned" not in r.text
    assert "Turns scanned" not in r.text
    assert "Timed submissions" not in r.text
    assert "Defaulted rows" not in r.text


@pytest.mark.asyncio
async def test_turn_timing_report_date_filter_limits_matches(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    included_at = datetime(2026, 6, 12, 6, 30, tzinfo=timezone.utc)
    excluded_at = datetime(2026, 6, 12, 8, 30, tzinfo=timezone.utc)
    await _seed_turn_timing_match(
        reset_db,
        match_id="M_turn_report_in_range",
        name="In Range",
        completed_at=included_at,
    )
    await _seed_turn_timing_match(
        reset_db,
        match_id="M_turn_report_out_of_range",
        name="Out of Range",
        completed_at=excluded_at,
    )

    async with reset_db() as db:
        report = await load_turn_timing_report(
            db,
            completed_after=datetime(2026, 6, 11, 7, tzinfo=timezone.utc),
            completed_before=datetime(2026, 6, 12, 7, tzinfo=timezone.utc),
        )
    assert report.matches_scanned == 1
    assert report.sample_count == 5
    assert [row.name for row in report.matches] == ["In Range"]

    r = await client.get(
        "/admin/reports?start_date=2026-06-11&end_date=2026-06-11&tz=America/Los_Angeles",
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 200
    assert "In Range" in r.text
    assert "Out of Range" not in r.text
    assert 'value="2026-06-11"' in r.text
    assert 'name="tz"' in r.text
    assert 'value="America/Los_Angeles"' in r.text


@pytest.mark.asyncio
async def test_game_admin_api_records_creator(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/game-admin/hoard-hurt-help/matches",
        json={
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


@pytest.mark.asyncio
async def test_platform_admin_api_records_creator(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
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


@pytest.mark.asyncio
async def test_admin_api_rejects_player_count_over_max(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/game-admin/hoard-hurt-help/matches",
        json={
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


@pytest.mark.asyncio
async def test_api_rejects_unknown_game_type(client, reset_db):
    # An unknown game type must be rejected at creation (4xx), so a match with a
    # game the scheduler can't run never gets persisted as a future zombie.
    admin = await _seed_user(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/game-admin/no-such-game/matches",
        json={
            "name": "Bad Type",
            "scheduled_start": when,
            "min_players": 6,
            "max_players": 10,
            "per_turn_deadline_seconds": 30,
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 400, r.text
    assert "Unknown game type" in r.text


@pytest.mark.asyncio
async def test_web_form_rejects_unknown_game_type(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/no-such-game/admin/matches/new",
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
    assert r.status_code == 400
    assert "Unknown game type" in r.text


@pytest.mark.asyncio
async def test_create_game_via_web_form(client, reset_db):
    """The browser posts a UTC ISO string (from datetime-local JS conversion)."""
    admin = await _seed_user(reset_db, "admin@test.com")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/hoard-hurt-help/admin/matches/new",
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


@pytest.mark.asyncio
async def test_web_form_rejects_player_count_over_max(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/hoard-hurt-help/admin/matches/new",
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


@pytest.mark.asyncio
async def test_web_form_rejects_past_time(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/hoard-hurt-help/admin/matches/new",
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


@pytest.mark.asyncio
async def test_platform_admin_api_creates_liars_dice_match_and_persists_config(
    client, reset_db
):
    admin = await _seed_user(reset_db, "admin@test.com")
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


@pytest.mark.asyncio
async def test_game_admin_web_form_creates_liars_dice_match_and_persists_config(
    client, reset_db
):
    admin = await _seed_user(reset_db, "admin@test.com")
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )
    r = await client.post(
        "/games/liars-dice/admin/matches/new",
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


@pytest.mark.asyncio
async def test_admin_cancel_pre_start(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    async with reset_db() as db:
        g = Match(
            id="G_001",
            name="t",
            state=GameState.REGISTERING,
            scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(g)
        await db.commit()
    r = await client.post(
        "/api/game-admin/hoard-hurt-help/matches/G_001/cancel",
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_delete_completed_match_with_winner(client, reset_db):
    """Deleting a finished match must not 500 on the winner_player_id FK.

    A completed match points at its winning player while the player points back
    at the match. Postgres enforces both FKs, so deleting players before
    clearing the winner pointer throws. SQLite reproduces it now that the test
    engine enables PRAGMA foreign_keys.
    """
    admin = await _seed_user(reset_db, "admin@test.com")
    async with reset_db() as db:
        u = User(google_sub="u1", email="p1@t.com")
        db.add(u)
        await db.flush()
        g = Match(
            id="G_001",
            name="t",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc),
        )
        db.add(g)
        await db.flush()
        agent, version = await make_agent(db, u, name="AI_0")
        p = Player(
            match_id="G_001",
            user_id=u.id,
            agent_id=agent.id,
            seat_name="AI_0",
            agent_version_id=version.id if version is not None else None,
        )
        db.add(p)
        await db.flush()
        g.winner_player_id = p.id  # the bug: match now references the player
        t = Turn(
            match_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        db.add(
            TurnSubmission(
                turn_id=t.id,
                player_id=p.id,
                action="HOARD",
                message="hi",
                points_delta=2,
                round_score_after=2,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    r = await client.post(
        "/admin/matches/G_001/delete",
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    async with reset_db() as db:
        assert (
            await db.execute(select(Match).where(Match.id == "G_001"))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(select(Player).where(Player.match_id == "G_001"))
        ).scalars().all() == []


@pytest.mark.asyncio
async def test_export_csv_shape(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    async with reset_db() as db:
        u = User(google_sub="u1", email="p1@t.com")
        db.add(u)
        await db.flush()
        g = Match(
            id="G_001",
            name="t",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc),
        )
        db.add(g)
        await db.flush()
        agent, version = await make_agent(db, u, name="AI_0")
        p = Player(
            match_id="G_001",
            user_id=u.id,
            agent_id=agent.id,
            seat_name="AI_0",
            agent_version_id=version.id if version is not None else None,
            model_self_report=version.model if version is not None else None,
        )
        db.add(p)
        await db.flush()
        t = Turn(
            match_id="G_001",
            round=1,
            turn=1,
            turn_token="tk1",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        db.add(
            TurnSubmission(
                turn_id=t.id,
                player_id=p.id,
                action="HOARD",
                message="hi",
                points_delta=2,
                round_score_after=2,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    r = await client.get(
        "/api/game-admin/hoard-hurt-help/matches/G_001/export.csv", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200
    text = r.text
    header = text.split("\n")[0]
    assert "match_id,round,turn,agent_id,action" in header
    assert "AI_0" in text
    assert "HOARD" in text


@pytest.mark.asyncio
async def test_export_json_includes_strategy_prompts(client, reset_db):
    admin = await _seed_user(reset_db, "admin@test.com")
    async with reset_db() as db:
        u = User(google_sub="u1", email="p1@t.com")
        db.add(u)
        await db.flush()
        g = Match(
            id="G_001",
            name="t",
            state=GameState.COMPLETED,
            scheduled_start=datetime.now(timezone.utc),
        )
        db.add(g)
        await db.flush()
        agent, version = await make_agent(db, u, name="AI_0")
        p = Player(
            match_id="G_001",
            user_id=u.id,
            agent_id=agent.id,
            seat_name="AI_0",
            agent_version_id=version.id if version is not None else None,
            model_self_report=version.model if version is not None else None,
        )
        db.add(p)
        await db.flush()
        if version is not None:
            version.strategy_text = "secret strategy"
        await db.commit()

    r = await client.get(
        "/api/game-admin/hoard-hurt-help/matches/G_001/export.json", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["players"][0]["strategy_prompt"] == "secret strategy"


# --- Role boundary tests ---


@pytest.mark.asyncio
async def test_game_admin_only_cannot_access_platform_admin(client, reset_db, monkeypatch):
    """A user who is only a game admin cannot reach the platform admin dashboard."""
    monkeypatch.setattr(settings, "platform_admin_emails", "platformonly@test.com")
    monkeypatch.setattr(settings, "admin_emails", "")
    monkeypatch.setattr(settings, "_game_admin_emails_raw", {"HOARD_HURT_HELP": "gameonly@test.com"})
    gameonly = await _seed_user(reset_db, "gameonly@test.com")
    r = await client.get("/admin/matches", cookies=_cookies(gameonly.id), follow_redirects=False)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_platform_admin_only_cannot_access_game_admin(client, reset_db, monkeypatch):
    """A user who is only a platform admin cannot reach the game admin dashboard."""
    monkeypatch.setattr(settings, "platform_admin_emails", "platformonly@test.com")
    monkeypatch.setattr(settings, "admin_emails", "")
    monkeypatch.setattr(settings, "_game_admin_emails_raw", {"HOARD_HURT_HELP": "gameonly@test.com"})
    platformonly = await _seed_user(reset_db, "platformonly@test.com")
    r = await client.get(
        "/games/hoard-hurt-help/admin/", cookies=_cookies(platformonly.id), follow_redirects=False
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_game_admin_wrong_game_cannot_access(client, reset_db, monkeypatch):
    """A game admin for game X cannot reach the admin dashboard for game Y."""
    monkeypatch.setattr(settings, "admin_emails", "")
    monkeypatch.setattr(settings, "_game_admin_emails_raw", {"HOARD_HURT_HELP": "gameonly@test.com"})
    gameonly = await _seed_user(reset_db, "gameonly@test.com")
    r = await client.get(
        "/games/other-game/admin/", cookies=_cookies(gameonly.id), follow_redirects=False
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_game_admin_dashboard_handles_missing_start_time(monkeypatch):
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

    response = await game_admin_web.game_admin_dashboard(
        game="hoard-hurt-help",
        request=request,
        db=FakeDB(),
        user=SimpleNamespace(email="gameonly@test.com"),
    )

    assert response.context["scheduled_games"][0]["scheduled_start"] is None


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_game_admin_api_accessible(client, reset_db):
    """A game admin can create a match via the game-admin API."""
    admin = await _seed_user(reset_db, "admin@test.com")
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await client.post(
        "/api/game-admin/hoard-hurt-help/matches",
        json={"name": "Boundary", "scheduled_start": when, "min_players": 6, "max_players": 10, "per_turn_deadline_seconds": 30},
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201


@pytest.mark.asyncio
async def test_agent_api_not_shadowed(client, reset_db):
    """The game/{match_id} agent API route is not shadowed by the game-admin router."""
    # A non-existent match returns 404 from the agent API, not a routing error.
    r = await client.get("/api/games/NOSUCHID/state")
    assert r.status_code in (401, 404, 422)  # any non-405 proves the route is reachable


@pytest.mark.asyncio
async def test_delete_active_match_succeeds(client, reset_db):
    """Deleting an in-progress match must not 500.

    Regression: the scheduler task can write a TurnSubmission after our first
    delete pass, and Match.winner_player_id creates a second FK hazard when
    deleting Players before nulling the reference.
    """
    from sqlalchemy import select as sa_select
    from tests.factories import make_user, seat_player

    admin = await _seed_user(reset_db, "admin@test.com")

    async with reset_db() as db:
        await make_user(db, i=99)
        await db.flush()
        g = Match(
            id="G_ACTIVE",
            name="Running Game",
            state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.flush()
        player = await seat_player(db, "G_ACTIVE", "AI_0", i=0)
        # Simulate a completed-game state: winner_player_id is set.
        g.winner_player_id = player.id
        t = Turn(
            match_id="G_ACTIVE",
            round=1,
            turn=1,
            turn_token="tk_active",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
        )
        db.add(t)
        await db.flush()
        db.add(
            TurnSubmission(
                turn_id=t.id,
                player_id=player.id,
                action="HOARD",
                message="",
                points_delta=2,
                round_score_after=2,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    r = await client.post(
        "/admin/matches/G_ACTIVE/delete",
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert r.status_code == 303

    async with reset_db() as db:
        remaining = (
            await db.execute(sa_select(Match).where(Match.id == "G_ACTIVE"))
        ).scalar_one_or_none()
    assert remaining is None


@pytest.mark.asyncio
async def test_delete_cascade_handles_in_flight_submission(reset_db, monkeypatch):
    """The shared delete cascade must stop the scheduler before row cleanup.

    An existing turn submission should be removed by the cascade, and the
    cascade should clear the winner pointer before deleting players.
    """
    order: list[str] = []

    async with reset_db() as db:
        user = User(google_sub="u1", email="p1@t.com")
        db.add(user)
        await db.flush()
        agent, _ = await make_agent(db, user, name="AI_0")
        g = Match(
            id="G_RACE",
            name="Race Game",
            state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
            per_turn_deadline_seconds=60,
        )
        db.add(g)
        await db.flush()
        player = Player(
            match_id="G_RACE",
            user_id=user.id,
            agent_id=agent.id,
            seat_name="AI_0",
        )
        db.add(player)
        await db.flush()
        g.winner_player_id = player.id
        turn = Turn(
            match_id="G_RACE",
            round=1,
            turn=1,
            turn_token="tk_race",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc),
        )
        db.add(turn)
        await db.flush()
        db.add(
            TurnSubmission(
                turn_id=turn.id,
                player_id=player.id,
                action="HOARD",
                message="early",
                points_delta=2,
                round_score_after=2,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            RequestIncident(
                request_id="req-race",
                method="POST",
                path="/admin/matches/G_RACE/delete",
                error_type="test",
                error_message="boom",
                stacktrace="trace",
                match_id="G_RACE",
                player_id=player.id,
            )
        )
        await db.commit()
        turn_id = turn.id

    async with reset_db() as db:
        original_execute = db.execute

        async def wrapped_execute(statement, *args, **kwargs):
            sql = str(statement)
            if "DELETE FROM turn_submissions" in sql and "turn_submissions.turn_id" in sql:
                order.append("turn_submission_turn_delete")
            if "DELETE FROM turn_submissions" in sql and "turn_submissions.player_id" in sql:
                order.append("turn_submission_player_delete")
            if sql.startswith("UPDATE matches SET") and "winner_player_id" in sql:
                order.append("winner_pointer_cleared")
            if sql.startswith("DELETE FROM players"):
                order.append("player_delete")
            return await original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(db, "execute", wrapped_execute)
        monkeypatch.setattr(
            "app.engine.match_deletion.registry.stop",
            lambda match_id: order.append("stop"),
        )

        await delete_match(db, "G_RACE")

    assert order[0] == "stop"
    assert order.index("turn_submission_turn_delete") < order.index(
        "turn_submission_player_delete"
    )
    assert order.index("winner_pointer_cleared") < order.index("player_delete")

    async with reset_db() as db:
        assert (
            await db.execute(select(Match).where(Match.id == "G_RACE"))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(select(Player).where(Player.match_id == "G_RACE"))
        ).scalars().all() == []
        assert (
            await db.execute(select(Turn).where(Turn.match_id == "G_RACE"))
        ).scalars().all() == []
        assert (
            await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == turn_id))
        ).scalars().all() == []
        assert (
            await db.execute(
                select(RequestIncident).where(RequestIncident.match_id == "G_RACE")
            )
        ).scalars().all() == []
