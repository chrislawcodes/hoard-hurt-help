"""The strategy workspace pages: agent-detail hero, version timeline, edit lock,
save-with-note, and the informed join cards (with the per-game agent filter)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.agent_version import AgentVersion
from app.models.match import GameState, MatchKind
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import (
    make_agent,
    make_match,
    make_user,
    seat_prebuilt_player,
)

_LONG_STRATEGY = ("Open with generosity. " * 30) + "END_OF_STRATEGY_MARKER"


async def _completed_match(db, match_id: str, *, match_kind: str = "manual"):
    now = datetime.now(timezone.utc)
    return await make_match(
        db,
        match_id,
        state=GameState.COMPLETED,
        scheduled_start=now - timedelta(hours=2),
        started_at=now - timedelta(hours=2),
        completed_at=now - timedelta(hours=1),
        match_kind=match_kind,
    )


async def test_agent_detail_shows_full_strategy_note_and_record(client, reset_db):
    """The strategy card carries the whole strategy text in the inline editor, the
    version note, the record line, and the fork-aware Save button."""
    async with reset_db() as db:
        user = await make_user(db, i=0)
        agent, version = await make_agent(
            db, user, name="Hero", strategy_text=_LONG_STRATEGY
        )
        assert version is not None
        version.note = "Sharper endgame"
        # One rated win, one rated loss, one practice game.
        win = await _completed_match(db, "M_WIN")
        winner_seat = await seat_prebuilt_player(
            db, match=win, user=user, agent=agent, version=version, seat_name="Hero"
        )
        win.winner_player_id = winner_seat.id
        loss = await _completed_match(db, "M_LOSS")
        await seat_prebuilt_player(
            db, match=loss, user=user, agent=agent, version=version, seat_name="Hero"
        )
        practice = await _completed_match(
            db, "M_PRACTICE", match_kind=MatchKind.PRACTICE_ARENA.value
        )
        await seat_prebuilt_player(
            db, match=practice, user=user, agent=agent, version=version, seat_name="Hero"
        )
        await db.commit()

    r = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert r.status_code == 200
    assert len(_LONG_STRATEGY) > 600  # long enough to need the big editable box
    assert "END_OF_STRATEGY_MARKER" in r.text  # whole strategy, inline in the editor
    assert 'name="strategy_text"' in r.text  # editable in place, not a link out
    assert "Save as v2" in r.text  # this version has played, so a save forks v2
    assert "Sharper endgame" in r.text
    assert "Won 1 of 2 rated matches" in r.text
    assert "1 practice" in r.text
    # A single version that HAS played shows the timeline (old gate was >1).
    assert "Version history" in r.text
    assert f"/games/{win.game}/matches/M_WIN" in r.text  # recent-match link
    assert "Applies to your next matches." not in r.text  # no Restore on current


async def test_agent_detail_hides_timeline_with_one_unplayed_version(client, reset_db):
    async with reset_db() as db:
        user = await make_user(db, i=1)
        agent, _version = await make_agent(db, user, name="Fresh")
        await db.commit()

    r = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert r.status_code == 200
    assert "Version history" not in r.text


async def test_old_edit_url_redirects_and_detail_locks_mid_match(client, reset_db):
    """The old /edit path now permanently redirects to the agent page, and
    mid-match the inline editor is replaced by the locked notice (no form)."""
    async with reset_db() as db:
        user = await make_user(db, i=2)
        agent, version = await make_agent(db, user, name="Busy")
        assert version is not None
        now = datetime.now(timezone.utc)
        live = await make_match(
            db,
            "M_LIVE",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=10),
            started_at=now - timedelta(minutes=10),
        )
        await seat_prebuilt_player(
            db, match=live, user=user, agent=agent, version=version, seat_name="Busy"
        )
        await db.commit()

    # The retired /edit URL redirects to the agent page — the editor lives there now.
    redirect = await client.get(
        f"/me/agents/{agent.id}/edit",
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert redirect.status_code == 308
    assert redirect.headers["location"].endswith(f"/me/agents/{agent.id}")

    detail = await client.get(f"/me/agents/{agent.id}", cookies=_cookies(user.id))
    assert detail.status_code == 200
    assert "Playing now — editing unlocks when the match ends." in detail.text
    assert 'name="strategy_text"' not in detail.text  # no editable form mid-match
    assert f"/me/agents/{agent.id}/save-version" not in detail.text


async def test_save_version_stores_note_in_place_and_on_fork(client, reset_db):
    """An in-place draft edit overwrites the note; a fork writes the note on the
    new version and leaves the old version's note alone."""
    async with reset_db() as db:
        user = await make_user(db, i=3)
        agent, version = await make_agent(db, user, name="Noted", strategy_text="v1 text")
        assert version is not None
        await db.commit()

    r = await client.post(
        f"/me/agents/{agent.id}/save-version",
        cookies=_cookies(user.id),
        data={"strategy_text": "v1 text improved", "note": "tightened opener"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        stored = (
            await db.execute(select(AgentVersion).where(AgentVersion.id == version.id))
        ).scalar_one()
        assert stored.strategy_text == "v1 text improved"
        assert stored.note == "tightened opener"
        assert stored.version_no == 1

        # Rated history freezes-on-edit: the next save forks v2.
        completed = await _completed_match(db, "M_RATED")
        await seat_prebuilt_player(
            db, match=completed, user=user, agent=agent, version=stored, seat_name="Noted"
        )
        await db.commit()

    r = await client.post(
        f"/me/agents/{agent.id}/save-version",
        cookies=_cookies(user.id),
        data={"strategy_text": "v2 text", "note": "fork note"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        versions = (
            (
                await db.execute(
                    select(AgentVersion)
                    .where(AgentVersion.agent_id == agent.id)
                    .order_by(AgentVersion.version_no)
                )
            )
            .scalars()
            .all()
        )
        assert [v.version_no for v in versions] == [1, 2]
        assert versions[0].note == "tightened opener"  # untouched by the fork
        assert versions[1].note == "fork note"
        assert versions[1].strategy_text == "v2 text"


async def test_join_page_shows_version_line_and_filters_other_games(client, reset_db):
    """Join cards carry the v-line (version, note, record); agents of another
    game don't appear for this match."""
    async with reset_db() as db:
        user = await make_user(db, i=4)
        agent, version = await make_agent(db, user, name="JoinReady")
        assert version is not None
        version.note = "Opening gambit tweak"
        win = await _completed_match(db, "M_JWIN")
        seat = await seat_prebuilt_player(
            db, match=win, user=user, agent=agent, version=version, seat_name="JoinReady"
        )
        win.winner_player_id = seat.id

        other_agent, other_version = await make_agent(db, user, name="OtherGameAgent")
        assert other_version is not None
        other_agent.game = "liars-dice"

        await make_match(db, "G_JOIN", state=GameState.REGISTERING)
        await db.commit()

    r = await client.get(
        "/games/hoard-hurt-help/matches/G_JOIN/join", cookies=_cookies(user.id)
    )
    assert r.status_code == 200
    assert "JoinReady" in r.text
    assert f"v{version.version_no}" in r.text
    assert "Opening gambit tweak" in r.text
    assert "Won 1 of 1 rated match" in r.text
    assert "OtherGameAgent" not in r.text  # different game, filtered out
