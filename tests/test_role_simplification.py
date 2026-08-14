"""The two-role permission matrix.

The platform has exactly two roles: user, and platform admin. A third one —
game admin, granted by an environment variable and invisible to the database —
was removed. This file is the proof of what each role may now do.

Why a dedicated file: the pre-existing route tests all drive these pages as a
`role=ADMIN` user, which is allowed under both the old model and the new one.
Every one of them stays green whether or not the ownership branch was ever
written, so none of them can pin this change. The negatives have to live here.

Admin users are seeded by setting `User.role` directly rather than through the
`admin_emails` fixture other files use: under the old rules that fixture also
made the user a game admin for every game, which is exactly the confusion this
change removes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.games.hoard_hurt_help.rules import DEFAULT_MUTUAL_HELP_MODE
from app.models import Base, GameState, Match, MatchState
from app.models.user import User, UserRole
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import (
    add_submission,
    make_match,
    make_turn,
    seat_player,
)

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
GAME = "hoard-hurt-help"
HIDDEN_GAME = "liars-dice"  # admin_only=True — under construction


@pytest.fixture
async def reset_db(monkeypatch):
    from app.db import make_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    # No email-based admin grants: role comes from the column, nothing else.
    from app.config import settings

    monkeypatch.setattr(settings, "admin_emails", "")
    monkeypatch.setattr(settings, "platform_admin_emails", "")

    yield test_factory
    await test_engine.dispose()


async def _user(reset_db, tag: str, *, admin: bool = False) -> User:
    async with reset_db() as db:
        u = User(
            google_sub=f"sub-{tag}",
            email=f"{tag}@test.com",
            name=tag,
            handle=tag,
            handle_key=tag,
            role=UserRole.ADMIN if admin else UserRole.USER,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:00.000Z"
    )


async def _post_create(client, user, *, game: str = GAME, **fields):
    data = {"name": fields.pop("name", "A Match"), "scheduled_start": _future()}
    data.update({k: str(v) for k, v in fields.items()})
    return await client.post(
        f"/games/{game}/matches/new",
        data=data,
        cookies=_cookies(user.id),
        follow_redirects=False,
    )


def _code(response) -> str:
    """Pull the error code out of the shared nested envelope."""
    return response.json()["detail"]["error"]["code"]


async def _named(reset_db, name: str) -> Match | None:
    async with reset_db() as db:
        return (
            await db.execute(select(Match).where(Match.name == name))
        ).scalar_one_or_none()


async def _seed_match(reset_db, match_id: str, *, owner_id: int | None) -> None:
    async with reset_db() as db:
        await make_match(
            db,
            match_id,
            state=GameState.REGISTERING,
            created_by_user_id=owner_id,
        )
        await db.commit()


# --------------------------------------------------------------------------
# Create: any signed-in player picks their own settings (AC1.x)
# --------------------------------------------------------------------------


async def test_player_creates_a_match_with_every_setting_they_chose(client, reset_db):
    """Row 1. Every posted value is distinct from every default.

    If the route ignored the new form fields and kept using fixed defaults, the
    posted values would round-trip anyway when they happen to match. They don't.
    """
    player = await _user(reset_db, "player")
    r = await _post_create(
        client,
        player,
        name="Custom",
        min_players=7,
        max_players=9,
        per_turn_deadline_seconds=120,
        total_rounds=4,
        turns_per_round=6,
    )
    assert r.status_code == 303, r.text
    match = await _named(reset_db, "Custom")
    assert match is not None
    assert match.min_players == 7
    assert match.max_players == 9
    assert match.per_turn_deadline_seconds == 120
    assert match.total_rounds == 4
    assert match.turns_per_round == 6
    assert match.created_by_user_id == player.id


async def test_player_count_below_the_games_floor_is_rejected(client, reset_db):
    """Row 2. `player_count_error` is still the gate — the engine's own band is
    1 to 20, so only the per-game check can reject 2."""
    player = await _user(reset_db, "player")
    r = await _post_create(client, player, name="TooSmall", min_players=2, max_players=9)
    assert r.status_code == 400
    assert "Player counts must be 6 to 10." in r.text
    assert await _named(reset_db, "TooSmall") is None


async def test_rounds_below_the_route_band_are_rejected(client, reset_db):
    """Row 3. 2 rounds is inside `create_match`'s 1-20 band, so this passes only
    if the route runs its own 3-20 check."""
    player = await _user(reset_db, "player")
    r = await _post_create(client, player, name="TooFew", total_rounds=2)
    assert r.status_code == 400
    assert "Total rounds must be 3 to 20." in r.text
    assert await _named(reset_db, "TooFew") is None


async def test_turns_below_the_route_band_are_rejected(client, reset_db):
    """Row 4. Same shape as rounds — 2 is inside the engine's band."""
    player = await _user(reset_db, "player")
    r = await _post_create(client, player, name="TooShort", turns_per_round=2)
    assert r.status_code == 400
    assert "Turns per round must be 3 to 20." in r.text
    assert await _named(reset_db, "TooShort") is None


@pytest.mark.parametrize("deadline", [0, 99999])
async def test_out_of_band_turn_deadline_is_rejected(client, reset_db, deadline):
    """Rows 5 and 6. The HTML form bounded this nowhere before; a player could
    have created a match with a zero-second or day-long turn."""
    player = await _user(reset_db, "player")
    r = await _post_create(
        client, player, name=f"Deadline{deadline}", per_turn_deadline_seconds=deadline
    )
    assert r.status_code == 400
    assert "Per-turn deadline must be 5 to 600 seconds." in r.text
    assert await _named(reset_db, f"Deadline{deadline}") is None


async def test_a_players_submitted_mutual_help_mode_is_ignored(client, reset_db):
    """Row 7. Choosing the per-match rule is an admin power. A player's value is
    dropped, not honoured — and the match still gets the platform default.

    "flat_8" here is just a value the platform default is not, so the assertion
    below can only pass by the value being dropped.
    """
    player = await _user(reset_db, "player")
    assert DEFAULT_MUTUAL_HELP_MODE.value != "flat_8"
    r = await _post_create(client, player, name="PlayerMode", mutual_help_mode="flat_8")
    assert r.status_code == 303, r.text
    match = await _named(reset_db, "PlayerMode")
    assert match is not None
    assert match.mutual_help_mode == DEFAULT_MUTUAL_HELP_MODE.value


async def test_an_admin_may_choose_the_mutual_help_mode(client, reset_db):
    """Row 8."""
    admin = await _user(reset_db, "boss", admin=True)
    r = await _post_create(client, admin, name="AdminMode", mutual_help_mode="flat_8")
    assert r.status_code == 303, r.text
    match = await _named(reset_db, "AdminMode")
    assert match is not None
    assert match.mutual_help_mode == "flat_8"


async def test_an_unknown_mutual_help_mode_from_an_admin_is_rejected(client, reset_db):
    """Row 9. A typo silently becoming "decay" would mislabel which rule the
    match was played under."""
    admin = await _user(reset_db, "boss", admin=True)
    r = await _post_create(client, admin, name="BadMode", mutual_help_mode="nonsense")
    assert r.status_code == 400
    assert "nonsense" in r.text
    assert await _named(reset_db, "BadMode") is None


async def test_the_json_api_rejects_an_unknown_mutual_help_mode(client, reset_db):
    """Row 10. The schema field was a bare `str`, so garbage reached the column
    on the platform-admin API too."""
    admin = await _user(reset_db, "boss", admin=True)
    r = await client.post(
        "/api/admin/matches",
        json={
            "game_type": GAME,
            "name": "ApiBadMode",
            "scheduled_start": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201  # sanity: the same body without a mode works

    r = await client.post(
        "/api/admin/matches",
        json={
            "game_type": GAME,
            "name": "ApiWorseMode",
            "scheduled_start": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
            "mutual_help_mode": "garbage",
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 422, r.text
    assert await _named(reset_db, "ApiWorseMode") is None


async def test_the_mode_control_is_admin_only_and_hoard_hurt_help_only(client, reset_db):
    """Rows 11, 12 and 13."""
    player = await _user(reset_db, "player")
    admin = await _user(reset_db, "boss", admin=True)

    as_player = await client.get(f"/games/{GAME}/matches/new", cookies=_cookies(player.id))
    assert as_player.status_code == 200
    assert 'name="mutual_help_mode"' not in as_player.text

    as_admin = await client.get(f"/games/{GAME}/matches/new", cookies=_cookies(admin.id))
    assert as_admin.status_code == 200
    assert 'name="mutual_help_mode"' in as_admin.text

    other_game = await client.get(
        f"/games/{HIDDEN_GAME}/matches/new", cookies=_cookies(admin.id)
    )
    assert other_game.status_code == 200
    assert 'name="mutual_help_mode"' not in other_game.text


async def test_the_admin_create_route_is_gone(client, reset_db):
    """Row 14. Asserted structurally: `/matches/new` still partially matches the
    `/matches/{match_id}` route, so a 404 alone would prove nothing."""
    from app.main import create_app

    paths = {getattr(r, "path", None) for r in create_app().routes}
    assert "/games/{game}/admin/matches/new" not in paths

    admin = await _user(reset_db, "boss", admin=True)
    r = await client.get(
        f"/games/{GAME}/admin/matches/new", cookies=_cookies(admin.id)
    )
    assert r.status_code == 404


async def test_no_template_links_to_the_removed_create_route(client, reset_db):
    """Row 15. A dead link in the admin dashboard is how this route family broke
    twice before."""
    offenders = [
        path
        for path in (REPO_ROOT / "app" / "templates").rglob("*.html")
        if "admin/matches/new" in path.read_text()
    ]
    assert offenders == []


# --------------------------------------------------------------------------
# Export: open to players, but narrowed in content (AC2.x)
# --------------------------------------------------------------------------


async def _seed_export_match(reset_db, *, owner: User, rival: User) -> str:
    """One match, two agent seats with DIFFERENT strategy text, one resolved
    turn and one still in flight."""
    match_id = "M_EXPORT"
    async with reset_db() as db:
        await make_match(
            db, match_id, state=GameState.ACTIVE, created_by_user_id=owner.id
        )
        mine = await seat_player(
            db, match_id, "MyAgent", user=owner, strategy_text="MY-SECRET-PLAN"
        )
        theirs = await seat_player(
            db, match_id, "RivalAgent", user=rival, strategy_text="THEIR-SECRET-PLAN"
        )
        done = await make_turn(db, match_id, round=1, turn=1, resolved=True)
        await add_submission(db, done, mine, action="HOARD", message="resolved-move")
        await add_submission(db, done, theirs, action="HELP", message="resolved-move")
        live = await make_turn(db, match_id, round=1, turn=2, resolved=False)
        await add_submission(db, live, theirs, action="HURT", message="IN-FLIGHT-MOVE")
        await db.commit()
    return match_id


async def test_a_players_json_export_hides_other_peoples_strategies(client, reset_db):
    """Row 16. Their own is real; the rival's is null."""
    owner = await _user(reset_db, "owner")
    rival = await _user(reset_db, "rival")
    match_id = await _seed_export_match(reset_db, owner=owner, rival=rival)

    r = await client.get(
        f"/api/game-admin/{GAME}/matches/{match_id}/export.json",
        cookies=_cookies(owner.id),
    )
    assert r.status_code == 200, r.text
    prompts = {p["strategy_prompt"] for p in r.json()["players"]}
    assert "MY-SECRET-PLAN" in prompts
    assert "THEIR-SECRET-PLAN" not in prompts
    assert None in prompts


async def test_an_admins_json_export_shows_every_strategy(client, reset_db):
    """Row 17 — the game-scoped export."""
    owner = await _user(reset_db, "owner")
    rival = await _user(reset_db, "rival")
    admin = await _user(reset_db, "boss", admin=True)
    match_id = await _seed_export_match(reset_db, owner=owner, rival=rival)

    r = await client.get(
        f"/api/game-admin/{GAME}/matches/{match_id}/export.json",
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 200, r.text
    prompts = {p["strategy_prompt"] for p in r.json()["players"]}
    assert prompts == {"MY-SECRET-PLAN", "THEIR-SECRET-PLAN"}


async def test_the_platform_admin_export_route_is_not_redacted(client, reset_db):
    """Row 18. This route shares a builder with the game-scoped one; a redacting
    default would silently strip it and no other test would notice."""
    owner = await _user(reset_db, "owner")
    rival = await _user(reset_db, "rival")
    admin = await _user(reset_db, "boss", admin=True)
    match_id = await _seed_export_match(reset_db, owner=owner, rival=rival)

    r = await client.get(
        f"/api/admin/matches/{match_id}/export.json", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200, r.text
    prompts = {p["strategy_prompt"] for p in r.json()["players"]}
    assert prompts == {"MY-SECRET-PLAN", "THEIR-SECRET-PLAN"}


async def test_the_csv_export_columns_are_unchanged_for_a_player(client, reset_db):
    """Row 19. The CSV never carried strategy text and still does not."""
    from app.read_models.match_export import EXPORT_COLUMNS

    owner = await _user(reset_db, "owner")
    rival = await _user(reset_db, "rival")
    match_id = await _seed_export_match(reset_db, owner=owner, rival=rival)

    r = await client.get(
        f"/api/game-admin/{GAME}/matches/{match_id}/export.csv",
        cookies=_cookies(owner.id),
    )
    assert r.status_code == 200
    header = r.text.splitlines()[0]
    assert header == ",".join(EXPORT_COLUMNS)


async def test_a_player_cannot_read_the_in_flight_turn(client, reset_db):
    """Row 20. The sharpest leak this change had to close: between the act
    deadline and the resolve, an opponent could have read every rival's chosen
    action, target and message out of the export."""
    owner = await _user(reset_db, "owner")
    rival = await _user(reset_db, "rival")
    match_id = await _seed_export_match(reset_db, owner=owner, rival=rival)

    csv_body = await client.get(
        f"/api/game-admin/{GAME}/matches/{match_id}/export.csv",
        cookies=_cookies(owner.id),
    )
    json_body = await client.get(
        f"/api/game-admin/{GAME}/matches/{match_id}/export.json",
        cookies=_cookies(owner.id),
    )
    assert "resolved-move" in csv_body.text  # sanity: the export is not empty
    assert "IN-FLIGHT-MOVE" not in csv_body.text
    assert "IN-FLIGHT-MOVE" not in json_body.text


async def test_an_admin_still_sees_the_in_flight_turn(client, reset_db):
    """Row 21. The admin export is unchanged from before it became reachable."""
    owner = await _user(reset_db, "owner")
    rival = await _user(reset_db, "rival")
    admin = await _user(reset_db, "boss", admin=True)
    match_id = await _seed_export_match(reset_db, owner=owner, rival=rival)

    r = await client.get(
        f"/api/game-admin/{GAME}/matches/{match_id}/export.csv",
        cookies=_cookies(admin.id),
    )
    assert "IN-FLIGHT-MOVE" in r.text


def test_the_export_docstring_no_longer_claims_byte_identical_output():
    """Row 22. The payload depends on the caller now, so the old promise is
    false — and a comment asserting a rule the code does not keep is worse than
    no comment."""
    import app.read_models.match_export as module

    assert "byte-identical" not in (module.__doc__ or "")


# --------------------------------------------------------------------------
# The role itself is gone (AC3.x)
# --------------------------------------------------------------------------


def test_the_repo_has_no_trace_of_the_removed_role():
    """Row 55. Walks git-tracked files, not the working directory.

    Not a shell-out to `grep -r`: that matches stale `__pycache__` binaries and
    depends on the xdist worker's directory. Not a plain filesystem walk either
    — an untracked scratch file in `app/` or `tests/` would redden the suite for
    a reason that has nothing to do with the code.

    The needle is built from parts so this file does not contain the string it
    forbids and fail on itself.
    """
    import subprocess

    needle = "game" + "_admin"
    self_name = Path(__file__).name
    try:
        listed = subprocess.run(
            ["git", "ls-files", "app", "tests"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("not a git checkout")
    offenders: list[str] = []
    for rel in listed.stdout.splitlines():
        if not rel.endswith((".py", ".html")) or rel.endswith(self_name):
            continue
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        if needle in text or needle.upper() in text:
            offenders.append(rel)
    assert offenders == [], f"the removed role still appears in: {offenders}"


# --------------------------------------------------------------------------
# Gates: who may reach what (AC4.x)
# --------------------------------------------------------------------------


async def test_a_plain_user_cannot_reach_the_prompts_page(client, reset_db):
    """Row 25."""
    player = await _user(reset_db, "player")
    r = await client.get(f"/games/{GAME}/admin/prompts", cookies=_cookies(player.id))
    assert r.status_code == 403
    assert _code(r) == "NOT_PLATFORM_ADMIN"


async def test_a_plain_user_cannot_reach_the_game_dashboard(client, reset_db):
    """Row 26."""
    player = await _user(reset_db, "player")
    r = await client.get(f"/games/{GAME}/admin/", cookies=_cookies(player.id))
    assert r.status_code == 403
    assert _code(r) == "NOT_PLATFORM_ADMIN"


async def test_a_platform_admin_reaches_both_admin_pages(client, reset_db):
    """Row 27. Before this change the gate read an env list and never looked at
    `users.role`, so an unlisted platform admin got a 403 here."""
    admin = await _user(reset_db, "boss", admin=True)
    for path in (f"/games/{GAME}/admin/", f"/games/{GAME}/admin/prompts"):
        r = await client.get(path, cookies=_cookies(admin.id))
        assert r.status_code == 200, path


async def _bots_user(db) -> User:
    user = User(
        google_sub="sub-bots", email="bots@test.com", name="bots",
        handle="bots", handle_key="bots",
    )
    db.add(user)
    await db.flush()
    return user


async def test_an_owner_sees_only_their_own_strategy_on_the_detail_page(
    client, reset_db
):
    """Row 28."""
    owner = await _user(reset_db, "owner")
    rival = await _user(reset_db, "rival")
    match_id = "M_DETAIL"
    async with reset_db() as db:
        await make_match(
            db, match_id, state=GameState.REGISTERING, created_by_user_id=owner.id
        )
        await seat_player(
            db, match_id, "MyAgent", user=owner, strategy_text="MY-SECRET-PLAN"
        )
        await seat_player(
            db, match_id, "RivalAgent", user=rival, strategy_text="THEIR-SECRET-PLAN"
        )
        await db.commit()

    r = await client.get(
        f"/games/{GAME}/admin/matches/{match_id}", cookies=_cookies(owner.id)
    )
    assert r.status_code == 200, r.text
    assert "MY-SECRET-PLAN" in r.text
    assert "THEIR-SECRET-PLAN" not in r.text


async def test_a_seated_bots_strategy_stays_visible_to_its_owner(client, reset_db):
    """Row 29. A bot's preset is not a private prompt — the owner who seated it
    must be able to see what they picked, and the Type column already names it."""
    from app.models.agent import Agent, AgentKind
    from app.models.player import Player

    owner = await _user(reset_db, "owner")
    match_id = "M_BOTS"
    async with reset_db() as db:
        match = await make_match(
            db, match_id, state=GameState.REGISTERING, created_by_user_id=owner.id
        )
        bots_user = await _bots_user(db)
        bot = Agent(
            user_id=bots_user.id,
            name="Grabby",
            kind=AgentKind.BOT,
            bot_strategy="always_hoard",
            bot_truthfulness="honest",
            bot_trust_model="none",
            bot_seed=1,
            bot_version="v1",
        )
        db.add(bot)
        await db.flush()
        db.add(
            Player(
                match_id=match.id,
                user_id=bots_user.id,
                agent_id=bot.id,
                seat_name="Grabby",
            )
        )
        await db.commit()

    r = await client.get(
        f"/games/{GAME}/admin/matches/{match_id}", cookies=_cookies(owner.id)
    )
    assert r.status_code == 200, r.text
    assert "always_hoard" in r.text


async def test_an_admin_sees_every_strategy_on_the_detail_page(client, reset_db):
    """Row 30."""
    owner = await _user(reset_db, "owner")
    rival = await _user(reset_db, "rival")
    admin = await _user(reset_db, "boss", admin=True)
    match_id = "M_DETAIL"
    async with reset_db() as db:
        await make_match(
            db, match_id, state=GameState.REGISTERING, created_by_user_id=owner.id
        )
        await seat_player(
            db, match_id, "MyAgent", user=owner, strategy_text="MY-SECRET-PLAN"
        )
        await seat_player(
            db, match_id, "RivalAgent", user=rival, strategy_text="THEIR-SECRET-PLAN"
        )
        await db.commit()

    r = await client.get(
        f"/games/{GAME}/admin/matches/{match_id}", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200
    assert "MY-SECRET-PLAN" in r.text
    assert "THEIR-SECRET-PLAN" in r.text


async def test_an_owner_can_open_and_use_the_bots_form(client, reset_db):
    """Row 31."""
    owner = await _user(reset_db, "owner")
    await _seed_match(reset_db, "M_OWNED", owner_id=owner.id)

    form = await client.get(
        f"/games/{GAME}/admin/matches/M_OWNED/bots", cookies=_cookies(owner.id)
    )
    assert form.status_code == 200, form.text

    seated = await client.post(
        f"/games/{GAME}/admin/matches/M_OWNED/bots",
        data={"seat_name": "Grabby", "seat_strategy": "coalition_seeker"},
        cookies=_cookies(owner.id),
        follow_redirects=False,
    )
    assert seated.status_code == 303, seated.text
    # The redirect proves the route ran; only the row proves it seated anything.
    from app.models.player import Player

    async with reset_db() as db:
        seats = (
            (await db.execute(select(Player).where(Player.match_id == "M_OWNED")))
            .scalars()
            .all()
        )
    assert [p.seat_name for p in seats] == ["Grabby"]


@pytest.mark.parametrize(
    "method,suffix",
    [("get", ""), ("get", "/bots"), ("post", "/bots")],
)
async def test_a_non_owner_is_refused_on_someone_elses_match(
    client, reset_db, method, suffix
):
    """Row 32. The match has a real, different owner — asserted, because every
    factory match used to come out ownerless, which would collapse this test
    into the NULL-owner one below."""
    owner = await _user(reset_db, "owner")
    stranger = await _user(reset_db, "stranger")
    await _seed_match(reset_db, "M_OWNED", owner_id=owner.id)
    async with reset_db() as db:
        match = (
            await db.execute(select(Match).where(Match.id == "M_OWNED"))
        ).scalar_one()
        assert match.created_by_user_id is not None

    call = getattr(client, method)
    r = await call(
        f"/games/{GAME}/admin/matches/M_OWNED{suffix}",
        cookies=_cookies(stranger.id),
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert _code(r) == "NOT_MATCH_OWNER"


async def test_a_match_with_no_creator_is_admin_only(client, reset_db):
    """Row 33. An auto-scheduled match has no `created_by_user_id`. "Nobody owns
    it" must not read as "anybody owns it"."""
    player = await _user(reset_db, "player")
    await _seed_match(reset_db, "M_ORPHAN", owner_id=None)

    for suffix in ("", "/bots"):
        r = await client.get(
            f"/games/{GAME}/admin/matches/M_ORPHAN{suffix}", cookies=_cookies(player.id)
        )
        assert r.status_code == 403, suffix
        assert _code(r) == "NOT_MATCH_OWNER"


async def test_an_admin_can_open_a_match_with_no_creator(client, reset_db):
    """Row 34."""
    admin = await _user(reset_db, "boss", admin=True)
    await _seed_match(reset_db, "M_ORPHAN", owner_id=None)
    r = await client.get(
        f"/games/{GAME}/admin/matches/M_ORPHAN", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200


@pytest.mark.parametrize("action", ["start", "cancel"])
async def test_force_start_and_cancel_stay_admin_only_even_for_the_owner(
    client, reset_db, action
):
    """Row 35. Force-start skips the seat check, the player floor and the bot
    fill that the player start route runs. Cancel is an organizer's power — a
    player deletes their own pre-start match instead. Neither is "manage your own
    match", so owning it is not enough."""
    owner = await _user(reset_db, "owner")
    await _seed_match(reset_db, "M_OWNED", owner_id=owner.id)
    r = await client.post(
        f"/games/{GAME}/admin/matches/M_OWNED/{action}",
        cookies=_cookies(owner.id),
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert _code(r) == "NOT_PLATFORM_ADMIN"


async def test_the_owners_detail_page_hides_the_force_start_button(client, reset_db):
    """Row 36. A button that always 403s is worse than no button."""
    owner = await _user(reset_db, "owner")
    await _seed_match(reset_db, "M_OWNED", owner_id=owner.id)
    r = await client.get(
        f"/games/{GAME}/admin/matches/M_OWNED", cookies=_cookies(owner.id)
    )
    assert r.status_code == 200
    assert "/admin/matches/M_OWNED/start" not in r.text


@pytest.mark.parametrize("suffix", ["", "/bots"])
async def test_the_owners_pages_show_no_platform_admin_navigation(
    client, reset_db, suffix
):
    """Row 37. Both pages hardcoded `is_admin: True` before, which would have
    shown a player the admin menu."""
    owner = await _user(reset_db, "owner")
    await _seed_match(reset_db, "M_OWNED", owner_id=owner.id)
    r = await client.get(
        f"/games/{GAME}/admin/matches/M_OWNED{suffix}", cookies=_cookies(owner.id)
    )
    assert r.status_code == 200, suffix
    assert 'href="/admin/users"' not in r.text
    assert 'href="/admin/reports"' not in r.text


async def test_an_admin_may_act_on_a_match_they_did_not_create(client, reset_db):
    """Row 38."""
    owner = await _user(reset_db, "owner")
    admin = await _user(reset_db, "boss", admin=True)
    await _seed_match(reset_db, "M_OWNED", owner_id=owner.id)

    detail = await client.get(
        f"/games/{GAME}/admin/matches/M_OWNED", cookies=_cookies(admin.id)
    )
    assert detail.status_code == 200
    bots = await client.get(
        f"/games/{GAME}/admin/matches/M_OWNED/bots", cookies=_cookies(admin.id)
    )
    assert bots.status_code == 200
    cancelled = await client.post(
        f"/games/{GAME}/admin/matches/M_OWNED/cancel",
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert cancelled.status_code == 303, cancelled.text
    async with reset_db() as db:
        after = (
            await db.execute(select(Match).where(Match.id == "M_OWNED"))
        ).scalar_one()
    assert after.state == GameState.CANCELLED


ALL_GATED_PATHS = [
    ("get", "/games/{game}/admin/"),
    ("get", "/games/{game}/admin/prompts"),
    ("get", "/games/{game}/admin/matches/M_ANY"),
    ("post", "/games/{game}/admin/matches/M_ANY/start"),
    ("post", "/games/{game}/admin/matches/M_ANY/cancel"),
    ("get", "/games/{game}/admin/matches/M_ANY/bots"),
    ("post", "/games/{game}/admin/matches/M_ANY/bots"),
    ("get", "/api/game-admin/{game}/matches/M_ANY/export.csv"),
    ("get", "/api/game-admin/{game}/matches/M_ANY/export.json"),
]


@pytest.mark.parametrize("method,template", ALL_GATED_PATHS)
async def test_anonymous_callers_get_401_everywhere(client, reset_db, method, template):
    """Row 39. Authentication runs before any role or ownership test."""
    r = await getattr(client, method)(
        template.format(game=GAME), follow_redirects=False
    )
    assert r.status_code == 401
    assert _code(r) == "NOT_SIGNED_IN"


@pytest.mark.parametrize("method,template", ALL_GATED_PATHS)
async def test_a_hidden_game_answers_404_never_403(client, reset_db, method, template):
    """Row 50. A 403 would confirm the under-construction game exists. The
    visibility check has to run before the role and ownership checks, on every
    route — not just the create form.

    The match is SEEDED, and seeded in the hidden game, so the 404 has to come
    from the visibility check. With an unseeded id the match loader would 404 on
    its own and this would pass with the guard deleted — which is exactly what a
    mutation test showed.
    """
    player = await _user(reset_db, "player")
    async with reset_db() as db:
        match = await make_match(
            db, "M_ANY", state=GameState.REGISTERING, created_by_user_id=player.id
        )
        match.game = HIDDEN_GAME
        await db.commit()

    r = await getattr(client, method)(
        template.format(game=HIDDEN_GAME),
        cookies=_cookies(player.id),
        follow_redirects=False,
    )
    assert r.status_code == 404, r.text


async def test_a_disabled_account_is_bounced_not_served(client, reset_db):
    """Rows 40 to 42. The response shape differs by what the caller accepts, so
    all three are pinned. The trap here is swapping the auth dependency for a
    plain "who is signed in?" lookup, which does not check `disabled_at`."""
    player = await _user(reset_db, "player")
    async with reset_db() as db:
        row = (await db.execute(select(User).where(User.id == player.id))).scalar_one()
        row.disabled_at = datetime.now(timezone.utc)
        await db.commit()

    html = await client.get(
        f"/games/{GAME}/admin/",
        cookies=_cookies(player.id),
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )
    assert html.status_code == 303
    assert html.headers["location"] == "/disabled"

    htmx = await client.get(
        f"/games/{GAME}/admin/",
        cookies=_cookies(player.id),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert htmx.status_code == 200
    assert htmx.headers["HX-Redirect"] == "/disabled"

    api = await client.get(
        f"/api/game-admin/{GAME}/matches/M_ANY/export.csv",
        cookies=_cookies(player.id),
        follow_redirects=False,
    )
    assert api.status_code == 403
    assert _code(api) == "ACCOUNT_DISABLED"


async def test_the_three_match_cap_still_applies_to_players(client, reset_db):
    """Row 43. The cap lived on one route; this change had to keep it there and
    make sure no second create route grew around it."""
    player = await _user(reset_db, "player")
    for n in range(3):
        r = await _post_create(client, player, name=f"Held{n}")
        assert r.status_code == 303, r.text

    r = await _post_create(client, player, name="OneTooMany")
    assert r.status_code == 409
    assert "at most 3" in r.text
    assert await _named(reset_db, "OneTooMany") is None


async def test_the_three_match_cap_does_not_apply_to_admins(client, reset_db):
    """Row 44."""
    admin = await _user(reset_db, "boss", admin=True)
    for n in range(4):
        r = await _post_create(client, admin, name=f"AdminHeld{n}")
        assert r.status_code == 303, r.text
    assert await _named(reset_db, "AdminHeld3") is not None


async def test_deleting_someone_elses_match_is_still_refused(client, reset_db):
    """Row 45. Existing behaviour, pinned so the refactor cannot change it."""
    owner = await _user(reset_db, "owner")
    stranger = await _user(reset_db, "stranger")
    await _seed_match(reset_db, "M_OWNED", owner_id=owner.id)
    r = await client.post(
        "/matches/M_OWNED/delete",
        cookies=_cookies(stranger.id),
        follow_redirects=False,
    )
    assert r.status_code == 403
    assert _code(r) == "NOT_MATCH_OWNER"


async def test_an_admin_may_delete_someone_elses_match(client, reset_db):
    """Row 46."""
    owner = await _user(reset_db, "owner")
    admin = await _user(reset_db, "boss", admin=True)
    await _seed_match(reset_db, "M_OWNED", owner_id=owner.id)
    r = await client.post(
        "/matches/M_OWNED/delete", cookies=_cookies(admin.id), follow_redirects=False
    )
    assert r.status_code == 303


async def test_the_player_start_route_still_requires_a_confirmed_seat(
    client, reset_db
):
    """Row 47. Owning a match is not the same as holding a seat in it. The
    player start route's eligibility rules are untouched by this change."""
    owner = await _user(reset_db, "owner")
    await _seed_match(reset_db, "M_OWNED", owner_id=owner.id)
    r = await client.post(
        f"/games/{GAME}/matches/M_OWNED/start",
        cookies=_cookies(owner.id),
        follow_redirects=False,
    )
    assert r.status_code == 409
    assert _code(r) == "CANNOT_START"


@pytest.mark.parametrize(
    "path",
    [
        "/games/liars-dice",
        "/games/liars-dice/matches/new",
    ],
)
async def test_the_under_construction_game_stays_hidden_from_players(
    client, reset_db, path
):
    """Row 48. `_is_any_admin` used to be true for a game admin, which is what
    made this game visible to someone never promoted in the database."""
    player = await _user(reset_db, "player")
    r = await client.get(path, cookies=_cookies(player.id), follow_redirects=False)
    assert r.status_code == 404, path


async def test_the_under_construction_game_is_absent_from_the_games_catalog(
    client, reset_db
):
    """Row 49, the catalog half. The catalog lists registered games directly, so
    an empty database is enough to make this meaningful — an admin does see the
    slug here. The leaderboard and home-page halves need a seeded section, so
    they live in their own test below."""
    player = await _user(reset_db, "player")
    admin = await _user(reset_db, "boss", admin=True)

    as_admin = await client.get("/games", cookies=_cookies(admin.id))
    assert as_admin.status_code == 200
    assert HIDDEN_GAME in as_admin.text

    as_player = await client.get("/games", cookies=_cookies(player.id))
    assert as_player.status_code == 200
    assert HIDDEN_GAME not in as_player.text


# --------------------------------------------------------------------------
# One creation path (AC5.x)
# --------------------------------------------------------------------------


async def test_an_admin_creates_a_liars_dice_match_with_its_own_config(
    client, reset_db
):
    """Row 51. `wild_ones` is a checkbox, so it is omitted to mean false.

    This pins the CONFIG VALUES, not the row's existence: the game lazily
    fabricates a default `MatchState` when one is missing, so an assertion that
    the row exists would pass while the admin's choices were silently lost.
    """
    admin = await _user(reset_db, "boss", admin=True)
    r = await _post_create(
        client,
        admin,
        game=HIDDEN_GAME,
        name="LD",
        min_players=3,
        max_players=6,
        total_rounds=5,
        turns_per_round=7,
        dice_per_player=3,
    )
    assert r.status_code == 303, r.text
    match = await _named(reset_db, "LD")
    assert match is not None
    async with reset_db() as db:
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
    assert state.state_json["config"] == {"wild_ones": False, "dice_per_player": 3}


async def test_the_liars_dice_form_prefills_values_its_own_route_accepts(
    client, reset_db
):
    """Row 52. The game's own defaults are 3-6 players over 64 rounds of 256
    turns. Rendering those raw would give an admin a form whose default
    submission the very same route rejects."""
    admin = await _user(reset_db, "boss", admin=True)
    r = await client.get(
        f"/games/{HIDDEN_GAME}/matches/new", cookies=_cookies(admin.id)
    )
    assert r.status_code == 200
    assert 'name="min_players" value="3"' in r.text
    assert 'name="max_players" value="6"' in r.text
    assert 'name="total_rounds" value="20"' in r.text
    assert 'name="turns_per_round" value="20"' in r.text


async def test_a_hoard_hurt_help_match_stores_no_other_games_config(
    client, reset_db
):
    """Row 53. Every admin-created match used to get Liar's Dice keys stamped
    onto it, whatever game it was."""
    player = await _user(reset_db, "player")
    r = await _post_create(client, player, name="Plain")
    assert r.status_code == 303
    match = await _named(reset_db, "Plain")
    assert match is not None
    async with reset_db() as db:
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
    assert state.state_json["config"] == {}


async def test_both_creation_paths_agree_on_the_stored_config(client, reset_db):
    """Row 54. The HTML route and the JSON API share one config helper, so they
    cannot drift over what a game's module-owned config looks like."""
    admin = await _user(reset_db, "boss", admin=True)
    r = await client.post(
        "/api/admin/matches",
        json={
            "game_type": GAME,
            "name": "ViaApi",
            "scheduled_start": (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).isoformat(),
        },
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 201, r.text
    match = await _named(reset_db, "ViaApi")
    assert match is not None
    async with reset_db() as db:
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
    assert state.state_json["config"] == {}


async def test_a_player_can_delete_a_match_they_created_through_the_form(
    client, reset_db
):
    """The create route and the delete route must agree on every row a match owns.

    Regression: the merged create route seeds a module-owned `MatchState` row,
    which `delete_match` did not clear. The foreign key then blocked the delete
    and every Delete button on /me/matches answered 500. Every other delete test
    seeds a bare Match row, so none of them could catch it — the match has to be
    created through the real route.
    """
    player = await _user(reset_db, "player")
    created = await _post_create(client, player, name="Doomed")
    assert created.status_code == 303, created.text
    match = await _named(reset_db, "Doomed")
    assert match is not None

    deleted = await client.post(
        f"/matches/{match.id}/delete",
        cookies=_cookies(player.id),
        follow_redirects=False,
    )
    assert deleted.status_code == 303, deleted.text
    assert await _named(reset_db, "Doomed") is None
    async with reset_db() as db:
        leftover = (
            await db.execute(
                select(MatchState).where(MatchState.match_id == match.id)
            )
        ).scalar_one_or_none()
    assert leftover is None


@pytest.mark.parametrize("dice", [0, 99])
async def test_out_of_band_dice_per_player_is_rejected(client, reset_db, dice):
    """The JSON API bounded this 1-20; the HTML form bounded it nowhere.

    A zero-dice Liar's Dice match is a wedged match, not a rejected request, so
    it has to fail before the write. The two create paths share one config
    helper, so they must share the bound too.
    """
    admin = await _user(reset_db, "boss", admin=True)
    r = await _post_create(
        client,
        admin,
        game=HIDDEN_GAME,
        name=f"Dice{dice}",
        min_players=3,
        max_players=6,
        total_rounds=5,
        turns_per_round=7,
        dice_per_player=dice,
    )
    assert r.status_code == 400
    assert "Dice per player must be 1 to 20." in r.text
    assert await _named(reset_db, f"Dice{dice}") is None


async def test_creating_lands_each_role_where_they_started(client, reset_db):
    """An admin creates from the per-game dashboard and belongs back on it. A
    player has no dashboard, so their new match is waiting on /me/matches."""
    player = await _user(reset_db, "player")
    admin = await _user(reset_db, "boss", admin=True)

    as_player = await _post_create(client, player, name="PlayerLanding")
    assert as_player.headers["location"] == "/me/matches"

    as_admin = await _post_create(client, admin, name="AdminLanding")
    assert as_admin.headers["location"] == f"/games/{GAME}/admin"


async def test_the_owner_gets_a_link_to_the_page_they_may_open(client, reset_db):
    """Seating bots is the owner's power, and /me/matches is the only place they
    meet their own match. Without the link the permission is unreachable in the
    product — the page exists but nothing points at it."""
    player = await _user(reset_db, "player")
    created = await _post_create(client, player, name="Mine")
    assert created.status_code == 303
    match = await _named(reset_db, "Mine")
    assert match is not None

    href = f"/games/{GAME}/admin/matches/{match.id}"
    r = await client.get("/me/matches", cookies=_cookies(player.id))
    assert r.status_code == 200
    # The exact href, not a substring: a link with a bogus suffix appended still
    # contains the prefix, so a substring check cannot detect a broken link.
    assert f'href="{href}"' in r.text
    followed = await client.get(href, cookies=_cookies(player.id))
    assert followed.status_code == 200


async def test_a_stranger_may_export_but_sees_nothing_private(client, reset_db):
    """Exports are open to every signed-in player — that is the audience
    decision, and without this test it could be narrowed back to owner-only with
    the whole suite still green. What a stranger gets is the redacted view: no
    strategy prompts at all, and nothing from the turn still in flight."""
    owner = await _user(reset_db, "owner")
    rival = await _user(reset_db, "rival")
    stranger = await _user(reset_db, "stranger")
    match_id = await _seed_export_match(reset_db, owner=owner, rival=rival)

    r = await client.get(
        f"/api/game-admin/{GAME}/matches/{match_id}/export.json",
        cookies=_cookies(stranger.id),
    )
    assert r.status_code == 200, r.text
    assert {p["strategy_prompt"] for p in r.json()["players"]} == {None}
    assert "IN-FLIGHT-MOVE" not in r.text
    # Not an empty payload — the resolved turn is there, which is the point.
    assert "resolved-move" in r.text


async def test_deleting_a_match_clears_its_per_player_state(client, reset_db):
    """`PlayerState` points at both the match and a player, so it has to go
    before the players do. Only Liar's Dice writes it, and only once a round
    starts, so the hoard-hurt-help delete test cannot reach this branch — but an
    admin deleting a Liar's Dice match can."""
    from app.models.game_state import PlayerState

    admin = await _user(reset_db, "boss", admin=True)
    created = await _post_create(
        client,
        admin,
        game=HIDDEN_GAME,
        name="Dicey",
        min_players=3,
        max_players=6,
        total_rounds=5,
        turns_per_round=7,
        dice_per_player=3,
    )
    assert created.status_code == 303, created.text
    match = await _named(reset_db, "Dicey")
    assert match is not None

    async with reset_db() as db:
        player = await seat_player(db, match.id, "Roller", user=admin)
        db.add(
            PlayerState(
                match_id=match.id, player_id=player.id, state_json={"dice": [1, 2, 3]}
            )
        )
        await db.commit()

    deleted = await client.post(
        f"/matches/{match.id}/delete",
        cookies=_cookies(admin.id),
        follow_redirects=False,
    )
    assert deleted.status_code == 303, deleted.text
    async with reset_db() as db:
        leftover = (
            (
                await db.execute(
                    select(PlayerState).where(PlayerState.match_id == match.id)
                )
            )
            .scalars()
            .all()
        )
    assert leftover == []


async def test_a_participant_who_does_not_own_the_match_gets_no_manage_link(
    client, reset_db
):
    """/me/matches lists matches you PLAY IN, not only ones you created. Showing
    a manage link to a participant would hand them a link that 403s — the same
    dead-button problem the force-start row pins."""
    owner = await _user(reset_db, "owner")
    guest = await _user(reset_db, "guest")
    match_id = "M_SHARED"
    async with reset_db() as db:
        await make_match(
            db, match_id, state=GameState.REGISTERING, created_by_user_id=owner.id
        )
        await seat_player(db, match_id, "GuestAgent", user=guest)
        await db.commit()

    r = await client.get("/me/matches", cookies=_cookies(guest.id))
    assert r.status_code == 200
    assert match_id in r.text  # sanity: the guest does see the match
    assert f"/games/{GAME}/admin/matches/{match_id}" not in r.text

    # And the link would indeed have been dead.
    blocked = await client.get(
        f"/games/{GAME}/admin/matches/{match_id}", cookies=_cookies(guest.id)
    )
    assert blocked.status_code == 403


@pytest.mark.parametrize("path", ["/leaderboard", "/"])
async def test_the_leaderboard_pages_hide_an_admin_only_games_section(
    client, reset_db, monkeypatch, path
):
    """Both pages filter on the same narrowed admin flag.

    The section is injected rather than seeded: a real one needs a completed
    match with scored players, and without one the "slug is absent" assertion
    holds for admins too — it would pass with the filter deleted. The admin leg
    proves the injection reaches the page, so the player leg means something.
    """
    from app.read_models import leaderboard_cache
    from app.read_models.leaderboard import LeaderboardRow, LeaderboardSection
    from app.routes import web_front_page, web_leaderboard

    # The two pages render a section differently — /leaderboard shows the game
    # name, the home band shows only rows — so the marker has to be a row.
    marker = "HiddenGameCompetitor"
    section = LeaderboardSection(
        game_type=HIDDEN_GAME,
        game_name="Liar's Dice",
        rows=[
            LeaderboardRow(
                rank=1,
                display_name=marker,
                owner_handle=None,
                rating=1200.0,
                match_count=1,
                last_played_at=None,
                is_bot=False,
                provisional=False,
                is_archived=False,
                archived_at=None,
                provider=None,
            )
        ],
        match_count=1,
        has_bots=False,
    )

    async def _fake_sections(**_kwargs) -> list[LeaderboardSection]:
        return [section]

    for module in (leaderboard_cache, web_front_page, web_leaderboard):
        monkeypatch.setattr(
            module, "load_leaderboard_sections_cached", _fake_sections, raising=False
        )

    player = await _user(reset_db, "player")
    admin = await _user(reset_db, "boss", admin=True)

    as_admin = await client.get(path, cookies=_cookies(admin.id))
    assert as_admin.status_code == 200
    assert marker in as_admin.text, (
        f"injection did not reach {path} — the player leg would be vacuous"
    )

    as_player = await client.get(path, cookies=_cookies(player.id))
    assert as_player.status_code == 200
    assert marker not in as_player.text
