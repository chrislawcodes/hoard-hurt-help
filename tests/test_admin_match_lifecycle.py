"""Admin match lifecycle tests: connection-key export, cancel, and delete.

Split from test_admin.py — read-only export via a connection key (and why a
key can never cancel), the cancel gate (pre-start vs. running vs. finished,
default vs. allow_active), and match deletion (the winner_player_id FK
hazard, the CSV/JSON export shapes, and the delete cascade's ordering).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.engine.match_deletion import delete_match
from app.models import GameState, Match, Player, RequestIncident, Turn, TurnSubmission, User
from tests.admin_support import reset_db
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import make_agent, seed_email_user_with_role

# `reset_db` is only ever referenced as a fixture-name parameter below (that's
# how pytest wires a fixture to a test), never called by its imported name at
# module level, so ruff sees the import as unused unless it's listed here --
# the same pattern tests/conftest.py already uses for its own re-exports.
__all__ = ["reset_db"]


async def _seed_key_connection(reset_db, user_id: int) -> str:
    """Give a user a connection, and return its raw key."""
    from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
    from app.models.connection import Connection, ConnectionProvider, ConnectionStatus

    key = generate_connection_key()
    async with reset_db() as db:
        db.add(
            Connection(
                user_id=user_id,
                provider=ConnectionProvider.CLAUDE,
                key_lookup=bot_key_lookup(key),
                key_hint=bot_key_hint(key),
                status=ConnectionStatus.ACTIVE,
            )
        )
        await db.commit()
    return key


async def test_export_accepts_a_platform_admins_connection_key(client, reset_db):
    """The point of the change: a match run with no browser can fetch its own
    results. The export is the only source carrying `was_defaulted` and
    `thinking`, so without this every pooled number comes from whatever a person
    remembered to download."""
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    async with reset_db() as db:
        db.add(
            Match(
                id="G_EXP",
                name="done",
                state=GameState.COMPLETED,
                scheduled_start=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )
        await db.commit()
    key = await _seed_key_connection(reset_db, admin.id)

    r = await client.get(
        "/api/admin/matches/G_EXP/export.json",
        headers={"X-Connection-Key": key},
    )
    assert r.status_code == 200, r.text


async def test_export_refuses_a_non_admins_connection_key(client, reset_db):
    """The role check is the whole gate and is unchanged. Widening HOW you prove
    who you are must not widen WHO may read an unredacted export."""
    await seed_email_user_with_role(reset_db, "admin@test.com")
    player = await seed_email_user_with_role(reset_db, "player@test.com")
    key = await _seed_key_connection(reset_db, player.id)

    r = await client.get(
        "/api/admin/matches/G_EXP/export.json",
        headers={"X-Connection-Key": key},
    )
    assert r.status_code == 403, r.text


async def test_a_key_cannot_cancel_a_match(client, reset_db):
    """Read-only is what makes the wider door acceptable. Cancel, create and
    delete stay session-only so a leaked key cannot destroy a match."""
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    async with reset_db() as db:
        db.add(
            Match(
                id="G_LIVE",
                name="running",
                state=GameState.REGISTERING,
                scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
        await db.commit()
    key = await _seed_key_connection(reset_db, admin.id)

    r = await client.post(
        "/api/admin/matches/G_LIVE/cancel", headers={"X-Connection-Key": key}
    )
    assert r.status_code in (401, 403), r.text
    async with reset_db() as db:
        m = (await db.execute(select(Match).where(Match.id == "G_LIVE"))).scalar_one()
        assert m.state == GameState.REGISTERING


async def test_admin_cancel_pre_start(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
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
        "/api/admin/matches/G_001/cancel",
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 200


async def _seed_running_match(reset_db, match_id: str = "G_ACT") -> str:
    async with reset_db() as db:
        db.add(
            Match(
                id=match_id,
                name="running",
                state=GameState.ACTIVE,
                scheduled_start=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
        )
        await db.commit()
    return match_id


async def test_admin_cannot_cancel_a_running_match_by_default(client, reset_db):
    """The default stays closed. Stopping a live match must be asked for by
    name, so an existing caller cannot start killing running matches because a
    parameter was added."""
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    match_id = await _seed_running_match(reset_db)

    r = await client.post(
        f"/api/admin/matches/{match_id}/cancel", cookies=_cookies(admin.id)
    )
    assert r.status_code == 409, r.text
    assert "already started" in r.text
    async with reset_db() as db:
        m = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
        assert m.state == GameState.ACTIVE


async def test_admin_can_cancel_a_running_match_with_allow_active(client, reset_db):
    """The whole point of the change: before this there was no way to stop a
    match once it started, and a bad run had to be waited out or destroyed with
    a delete that threw away every turn."""
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    match_id = await _seed_running_match(reset_db)

    r = await client.post(
        f"/api/admin/matches/{match_id}/cancel?allow_active=true",
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 200, r.text
    async with reset_db() as db:
        m = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
        assert m.state == GameState.CANCELLED


async def test_a_finished_match_stays_uncancellable_even_with_allow_active(
    client, reset_db
):
    """allow_active lifts one block, not all of them. There is nothing to stop
    on a match that already ended, and cancelling one would rewrite a result."""
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    async with reset_db() as db:
        db.add(
            Match(
                id="G_DONE",
                name="done",
                state=GameState.COMPLETED,
                scheduled_start=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        )
        await db.commit()

    r = await client.post(
        "/api/admin/matches/G_DONE/cancel?allow_active=true",
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 409, r.text
    assert "already ended" in r.text


async def test_a_non_admin_cannot_cancel_a_running_match(client, reset_db):
    """The parameter must not become a way around the admin gate."""
    await seed_email_user_with_role(reset_db, "admin@test.com")
    player = await seed_email_user_with_role(reset_db, "player@test.com")
    match_id = await _seed_running_match(reset_db, "G_ACT2")

    r = await client.post(
        f"/api/admin/matches/{match_id}/cancel?allow_active=true",
        cookies=_cookies(player.id),
    )
    assert r.status_code in (401, 403), r.text
    async with reset_db() as db:
        m = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
        assert m.state == GameState.ACTIVE


async def test_admin_delete_completed_match_with_winner(client, reset_db):
    """Deleting a finished match must not 500 on the winner_player_id FK.

    A completed match points at its winning player while the player points back
    at the match. Postgres enforces both FKs, so deleting players before
    clearing the winner pointer throws. SQLite reproduces it now that the test
    engine enables PRAGMA foreign_keys.
    """
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
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


async def test_export_csv_shape(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
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


async def test_export_json_includes_strategy_prompts(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
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


async def test_delete_active_match_succeeds(client, reset_db):
    """Deleting an in-progress match must not 500.

    Regression: the scheduler task can write a TurnSubmission after our first
    delete pass, and Match.winner_player_id creates a second FK hazard when
    deleting Players before nulling the reference.
    """
    from sqlalchemy import select as sa_select
    from tests.factories import make_user, seat_player

    admin = await seed_email_user_with_role(reset_db, "admin@test.com")

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


