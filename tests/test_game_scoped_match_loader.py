"""Characterization tests for the game-scoped match-load preamble.

Every route under ``/games/{game}/matches/{match_id}/…`` first loads the match
(404 if missing) and then checks that the URL's ``{game}`` slug matches the
match's real game. These tests pin the *exact* status code and redirect target
for each historical form of that preamble, so the dependency that replaces the
hand-rolled copies cannot change any observable behavior:

- Form A — GET pages: **301** redirect to the corrected ``/games/{real}/…`` URL,
  preserving the path tail (``/analysis`` etc.).
- Form A (POST exception) — the join POST: **308** redirect (a POST keeps its
  method across the move) to the corrected ``/games/{real}/…/join`` URL.
- Form B — POST mutations: a **bare 404** (no redirect) on slug mismatch.
- Form C — the game-admin API copy: a **bare 404** on slug mismatch.

These were authored and run green against the pre-refactor base first, so they
record real behavior, not the refactor's intent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models import GameState, Match
from tests.factories import make_user
from tests.conftest import signed_in_cookies as _cookies

REAL_GAME = "hoard-hurt-help"
WRONG_GAME = "liars-dice"
MATCH_ID = "M_SLUG_1"


async def _seed_match(
    reset_db: async_sessionmaker,
    *,
    state: GameState = GameState.REGISTERING,
) -> None:
    async with reset_db() as db:
        db.add(
            Match(
                id=MATCH_ID,
                name="Slug Test Match",
                game=REAL_GAME,
                state=state,
                scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
                per_turn_deadline_seconds=60,
                max_players=20,
            )
        )
        await db.commit()


async def _seed_match_and_user(
    reset_db: async_sessionmaker,
    *,
    state: GameState = GameState.REGISTERING,
) -> int:
    """Seed the match plus one user in the same fresh DB; return the user id."""
    async with reset_db() as db:
        user = await make_user(db, 1)  # handle "agent1", email "u1@t.com"
        db.add(
            Match(
                id=MATCH_ID,
                name="Slug Test Match",
                game=REAL_GAME,
                state=state,
                scheduled_start=datetime.now(timezone.utc) + timedelta(hours=1),
                per_turn_deadline_seconds=60,
                max_players=20,
            )
        )
        await db.commit()
        return user.id


# --- Form A: GET pages redirect 301 to the corrected URL --------------------


async def test_form_a_get_viewer_wrong_slug_redirects_301(client, reset_db) -> None:
    """GET viewer with a wrong slug: 301 to the canonical viewer URL (no suffix)."""
    await _seed_match(reset_db, state=GameState.ACTIVE)

    r = await client.get(
        f"/games/{WRONG_GAME}/matches/{MATCH_ID}", follow_redirects=False
    )
    assert r.status_code == 301
    assert r.headers["location"] == f"/games/{REAL_GAME}/matches/{MATCH_ID}"


async def test_form_a_get_analysis_wrong_slug_redirects_301_with_suffix(
    client, reset_db
) -> None:
    """GET analysis with a wrong slug: 301 keeping the ``/analysis`` path tail."""
    await _seed_match(reset_db, state=GameState.ACTIVE)

    r = await client.get(
        f"/games/{WRONG_GAME}/matches/{MATCH_ID}/analysis", follow_redirects=False
    )
    assert r.status_code == 301
    assert r.headers["location"] == f"/games/{REAL_GAME}/matches/{MATCH_ID}/analysis"


# --- Form A exception: the join POST redirects 308 (keeps the method) -------


async def test_form_a_post_join_wrong_slug_redirects_308(client, reset_db) -> None:
    """POST join with a wrong slug: 308 (not 301) to the canonical join URL.

    A 308 keeps the POST method across the redirect; the join submit relies on
    this so the re-issued request still posts the form. Pinned separately because
    it is the one Form A site that does not use the default 301.
    """
    user_id = await _seed_match_and_user(reset_db, state=GameState.REGISTERING)

    r = await client.post(
        f"/games/{WRONG_GAME}/matches/{MATCH_ID}/join",
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 308
    assert r.headers["location"] == f"/games/{REAL_GAME}/matches/{MATCH_ID}/join"


# --- Form A edge: coach-note POST redirects 301 to the BARE viewer URL ------


async def test_form_a_post_coach_note_wrong_slug_redirects_301_to_viewer(
    client, reset_db
) -> None:
    """POST coach-note with a wrong slug: 301 to the bare viewer URL.

    Unlike the other Form A sites, the old preamble here passed an empty suffix,
    so the redirect target drops the ``/coach-note`` tail and points at the bare
    ``/games/{real}/matches/{id}`` viewer URL — not the request's own path. This
    deviation must be preserved byte-for-byte by the refactor.
    """
    user_id = await _seed_match_and_user(reset_db, state=GameState.ACTIVE)

    r = await client.post(
        f"/games/{WRONG_GAME}/matches/{MATCH_ID}/coach-note",
        data={"note": "anything"},
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 301
    assert r.headers["location"] == f"/games/{REAL_GAME}/matches/{MATCH_ID}"


# --- Form B: POST mutations 404 on slug mismatch (no redirect) --------------


async def test_form_b_post_play_join_wrong_slug_is_404(client, reset_db) -> None:
    """POST play/join with a wrong slug: 404 with body detail "Match not found.".

    The 404 body is pinned, not just the status: on base these Form B sites
    returned ``{"detail": "Match not found."}``, so the dependency must keep that
    exact body (not FastAPI's default "Not Found").
    """
    user_id = await _seed_match_and_user(reset_db, state=GameState.REGISTERING)

    r = await client.post(
        f"/games/{WRONG_GAME}/matches/{MATCH_ID}/play/join",
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "Match not found."}


async def test_form_b_post_start_wrong_slug_is_404_with_detail(client, reset_db) -> None:
    """POST start with a wrong slug: 404 with body detail "Match not found." too.

    The second Form B site (matches_user.start_match_submit) shares the same
    dependency; pin its body for the same reason.
    """
    user_id = await _seed_match_and_user(reset_db, state=GameState.REGISTERING)

    r = await client.post(
        f"/games/{WRONG_GAME}/matches/{MATCH_ID}/start",
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "Match not found."}


# --- Form C: the game-admin API copy 404s on slug mismatch ------------------


async def test_form_c_admin_export_wrong_slug_is_bare_404(
    client, reset_db, monkeypatch
) -> None:
    """Game-admin export with a wrong slug: a *bare* 404 (Form C behavior).

    The admin callers were bare on base, so the body must stay FastAPI's default
    ``{"detail": "Not Found"}`` — NOT the "Match not found." the Form B sites use.
    This pins the difference between the two 404 families so neither drifts.

    The admin route requires game-admin auth, so a non-admin would 403 before the
    slug check. We grant this user game-admin rights for the WRONG slug so auth
    passes and the request reaches the load+slug-check, isolating the
    404-on-mismatch behavior from the auth gate.
    """
    monkeypatch.setattr(settings, "admin_emails", "")
    # WRONG_GAME slug "liars-dice" → env-key suffix "LIARS_DICE".
    monkeypatch.setattr(
        settings, "_game_admin_emails_raw", {"LIARS_DICE": "u1@t.com"}
    )
    user_id = await _seed_match_and_user(reset_db, state=GameState.ACTIVE)

    r = await client.get(
        f"/api/game-admin/{WRONG_GAME}/matches/{MATCH_ID}/export.json",
        cookies=_cookies(user_id),
        follow_redirects=False,
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}
