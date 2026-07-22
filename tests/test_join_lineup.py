"""The join page as a lineup: row structure, the agent↔AI pairing invariant, and
the agent blurb.

**What this file can and cannot prove.** The suite renders HTML but runs no
JavaScript, and the lineup's interaction (tick a row → its first free AI is
selected → both hidden mirrors switch on together) is entirely client-side. So
the pairing tests below assert the *structure the JavaScript operates on* rather
than the result of operating it: browsers serialize repeated same-named fields in
document order, so "the k-th ``agent_id`` and the k-th ``chosen_provider`` sit in
the same row" IS the positional-pairing contract, and that is checkable here.

That is the ceiling of automated coverage for spec risk R1. A hand-built POST body
(as several existing tests use) proves the *server* pairs correctly but proves
nothing about whether the page can ever produce that body — it passes even with
this template deleted. The clicking itself is verified manually in a browser and
recorded in the PR.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Base, Agent, Connection, GameState, User
from app.models.connection import ConnectionProvider
from app.routes.agents_create import _AGENT_BLURB_MAX
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import make_agent, make_connection, make_match, make_user

GAME = "hoard-hurt-help"
JOIN_URL = f"/games/{GAME}/matches/G_001/join"


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
    yield test_factory
    await test_engine.dispose()


async def _seed_user(reset_db: async_sessionmaker) -> User:
    async with reset_db() as db:
        u = await make_user(db)
        await db.commit()
        await db.refresh(u)
        return u


async def _seed_match(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        await make_match(db, "G_001", state=GameState.REGISTERING, name="Test Match")
        await db.commit()


async def _seed_agent(
    reset_db: async_sessionmaker,
    user: User,
    name: str,
    provider: ConnectionProvider,
    blurb: str | None = None,
) -> Agent:
    """One live agent on its own provider, so each row has a distinct free AI."""
    async with reset_db() as db:
        u = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        connection, _ = await make_connection(db, u, provider=provider)
        agent, _ = await make_agent(db, u, connection=connection, name=name)
        agent.blurb = blurb
        now = datetime.now(timezone.utc)
        existing_mcp = await db.scalar(
            select(Connection.id).where(
                Connection.user_id == u.id,
                Connection.provider == connection.provider,
                Connection.mcp_connected_at.is_not(None),
                Connection.deleted_at.is_(None),
            ).limit(1)
        )
        if existing_mcp is None:
            connection.mcp_connected_at = now
        connection.first_connected_at = now
        connection.last_seen_at = now
        connection.last_polled_at = now
        await db.commit()
        await db.refresh(agent)
        return agent


def _row_chunks(html: str) -> list[str]:
    """Split the rendered page into one string per lineup row."""
    starts = [m.start() for m in re.finditer(r"data-lineup-row", html)]
    bounds = starts[1:] + [len(html)]
    return [html[a:b] for a, b in zip(starts, bounds)]


# ---------------------------------------------------------------- row structure


async def test_lineup_renders_one_row_per_agent_and_no_legacy_cards(client, reset_db):
    """AC1: N agents → N rows, and none of the old card markup survives."""
    user = await _seed_user(reset_db)
    await _seed_match(reset_db)
    await _seed_agent(reset_db, user, "One", ConnectionProvider.CLAUDE)
    await _seed_agent(reset_db, user, "Two", ConnectionProvider.GEMINI)

    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)

    assert r.status_code == 200
    # Positive: the new shape is actually there (a bare "not in" assertion below
    # would also pass on an error page).
    assert r.text.count("data-lineup-row") == 2
    assert 'class="enter-you"' in r.text
    for gone in ("agent-card-hd", "ai-chip", "pick-hint", "pick-row", "pick-sub"):
        assert gone not in r.text, f"legacy markup {gone!r} still rendered"


async def test_lineup_drops_strategy_preview_version_and_record(client, reset_db):
    """AC5: the three per-agent text sources that distinguished nothing are gone."""
    user = await _seed_user(reset_db)
    await _seed_match(reset_db)
    await _seed_agent(reset_db, user, "One", ConnectionProvider.CLAUDE)

    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)

    assert r.status_code == 200
    assert "data-lineup-row" in r.text
    assert "Play to win." not in r.text  # the strategy preview
    assert "rated matches" not in r.text  # the win record
    # The visible per-row picker heading is gone. The radiogroup's accessible
    # name still says "Which AI plays <agent>" — that one is required (A11Y1).
    assert "field-label\">Which AI plays" not in r.text
    assert 'aria-label="Which AI plays One"' in r.text
    assert ">Join<" in r.text  # AC4: the plain label, no dynamic relabel
    assert "Join as" not in r.text


async def test_manual_row_is_first_even_with_agents(client, reset_db):
    """AC8/AC10: "Play manually" leads the page regardless of agent count."""
    user = await _seed_user(reset_db)
    await _seed_match(reset_db)
    await _seed_agent(reset_db, user, "One", ConnectionProvider.CLAUDE)

    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)

    assert r.status_code == 200
    assert r.text.index("Play manually") < r.text.index("data-lineup-row")


async def test_pill_group_starts_hidden(client, reset_db):
    """AC2: an unticked row's AI pills are out of the tab order and a11y tree.

    Anchored to the pill group itself — a bare ``"hidden" in r.text`` would also
    match the two ``<input type="hidden">`` mirrors that every row carries.
    """
    user = await _seed_user(reset_db)
    await _seed_match(reset_db)
    agent = await _seed_agent(reset_db, user, "One", ConnectionProvider.CLAUDE)

    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)

    assert r.status_code == 200
    assert re.search(rf'id="ais-{agent.id}"[^>]*\bhidden\b', r.text), r.text
    assert re.search(rf'id="ais-{agent.id}"[^>]*role="radiogroup"', r.text)


async def test_ready_ais_carry_no_state_word(client, reset_db):
    """A ready pill shows only its name — silence means ready."""
    user = await _seed_user(reset_db)
    await _seed_match(reset_db)
    await _seed_agent(reset_db, user, "One", ConnectionProvider.CLAUDE)

    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)

    assert r.status_code == 200
    assert "ai-pill-name" in r.text
    assert "● ready" not in r.text


# ------------------------------------------------- the pairing invariant (R1)


async def test_each_row_holds_exactly_one_id_and_one_provider_mirror(client, reset_db):
    """R1: the posted lists can only line up if each row contributes one of each.

    Both mirrors ship ``disabled``; the JavaScript enables and disables them as a
    pair. This asserts the structure that guarantee rests on.
    """
    user = await _seed_user(reset_db)
    await _seed_match(reset_db)
    seeded = {
        (await _seed_agent(reset_db, user, "One", ConnectionProvider.CLAUDE)).id,
        (await _seed_agent(reset_db, user, "Two", ConnectionProvider.GEMINI)).id,
        (await _seed_agent(reset_db, user, "Three", ConnectionProvider.OPENAI)).id,
    }

    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    assert r.status_code == 200
    chunks = _row_chunks(r.text)
    assert len(chunks) == 3

    seen = set()
    for chunk in chunks:
        assert chunk.count('name="agent_id"') == 1
        assert chunk.count('name="chosen_provider"') == 1
        # The row's own id, so this holds whatever order the query returns.
        row_id = int(re.search(r'data-agent-id="(\d+)"', chunk).group(1))
        assert f'name="agent_id" value="{row_id}"' in chunk
        seen.add(row_id)
        # Both start switched off, so an untouched page posts no agent at all.
        assert chunk.count("disabled") >= 2
        assert chunk.index("data-agent-id-mirror") < chunk.index("data-provider-mirror")
    assert seen == seeded


async def test_id_and_provider_mirrors_appear_in_the_same_row_order(client, reset_db):
    """R1: the k-th agent_id and the k-th chosen_provider belong to the same row.

    This is the positional-pairing contract the server relies on — browsers
    serialize repeated field names in document order, so an id/provider pair that
    straddles two rows would silently seat each agent on the other's AI.
    """
    user = await _seed_user(reset_db)
    await _seed_match(reset_db)
    await _seed_agent(reset_db, user, "One", ConnectionProvider.CLAUDE)
    await _seed_agent(reset_db, user, "Two", ConnectionProvider.GEMINI)
    await _seed_agent(reset_db, user, "Three", ConnectionProvider.OPENAI)

    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)
    html = r.text
    row_starts = [m.start() for m in re.finditer(r"data-lineup-row", html)]
    ids = [m.start() for m in re.finditer(r'name="agent_id"', html)]
    provs = [m.start() for m in re.finditer(r'name="chosen_provider"', html)]

    assert len(ids) == len(provs) == len(row_starts) == 3

    def row_of(pos: int) -> int:
        return max(i for i, s in enumerate(row_starts) if s <= pos)

    assert [row_of(p) for p in ids] == [0, 1, 2]
    assert [row_of(p) for p in provs] == [0, 1, 2]


def test_template_writes_both_mirrors_the_same_number_of_times():
    """Tripwire: a third writer of one mirror without the other breaks R1.

    Not a proof — a cheap alarm that fires if someone later adds a code path that
    switches one hidden field on without the other.
    """
    src = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "app" / "templates" / "join.html"
    ).read_text()
    assert src.count("data-agent-id-mirror") == src.count("data-provider-mirror")


# ----------------------------------------------------------------- the blurb


def test_blurb_max_is_pinned_at_32():
    """The measured layout headroom assumes 32; widening the column would break it
    silently, so pin the number as well as deriving it."""
    assert _AGENT_BLURB_MAX == 32


async def test_blurb_renders_on_the_row_when_set(client, reset_db):
    user = await _seed_user(reset_db)
    await _seed_match(reset_db)
    await _seed_agent(reset_db, user, "One", ConnectionProvider.CLAUDE, blurb="Forgives once")

    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)

    assert r.status_code == 200
    assert "Forgives once" in r.text
    assert "lineup-blurb" in r.text


async def test_blurb_absent_renders_no_empty_element(client, reset_db):
    """AC15: an agent with no blurb renders name only, with nothing to shift the row."""
    user = await _seed_user(reset_db)
    await _seed_match(reset_db)
    await _seed_agent(reset_db, user, "One", ConnectionProvider.CLAUDE)

    r = await client.get(JOIN_URL, cookies=_cookies(user.id), follow_redirects=False)

    assert r.status_code == 200
    assert "data-lineup-row" in r.text
    assert "lineup-blurb" not in r.text


@pytest.mark.parametrize("raw", ["", "   "])
async def test_create_stores_blank_blurb_as_null(client, reset_db, raw):
    """AC14: empty or whitespace-only is NULL, never "" — the template's
    ``{% if agent.blurb %}`` cannot tell them apart, but the row layout can."""
    user = await _seed_user(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={"name": "Atlas", "strategy_text": "Play to win.", "blurb": raw},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.text
    async with reset_db() as db:
        agent = (await db.execute(select(Agent))).scalars().one()
        assert agent.blurb is None


async def test_create_accepts_exactly_32_characters(client, reset_db):
    """Boundary: an off-by-one in the check would reject a legal blurb."""
    user = await _seed_user(reset_db)
    exactly = "x" * 32
    r = await client.post(
        "/me/agents/new",
        data={"name": "Atlas", "strategy_text": "Play to win.", "blurb": exactly},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.text
    async with reset_db() as db:
        agent = (await db.execute(select(Agent))).scalars().one()
        assert agent.blurb == exactly


async def test_create_rejects_over_long_blurb(client, reset_db):
    """AC13/R2: 33 characters must 400 here, not 500 from Postgres later.

    SQLite ignores VARCHAR length, so without the server-side check this test DB
    would happily store the over-long value and production would be the first
    place it failed.
    """
    user = await _seed_user(reset_db)
    r = await client.post(
        "/me/agents/new",
        data={"name": "Atlas", "strategy_text": "Play to win.", "blurb": "x" * 33},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 400, r.text
    async with reset_db() as db:
        assert (await db.execute(select(Agent))).scalars().all() == []


async def test_set_blurb_route_rejects_over_long_blurb(client, reset_db):
    """The SECOND write path needs the same guard — one cleaned route and one
    unguarded route is exactly how R2 reaches production."""
    user = await _seed_user(reset_db)
    agent = await _seed_agent(reset_db, user, "One", ConnectionProvider.CLAUDE)

    r = await client.post(
        f"/me/agents/{agent.id}/set-blurb",
        data={"blurb": "x" * 33},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 400, r.text

    ok = await client.post(
        f"/me/agents/{agent.id}/set-blurb",
        data={"blurb": "Forgives once"},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert ok.status_code == 303
    async with reset_db() as db:
        refreshed = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
        assert refreshed.blurb == "Forgives once"


async def test_set_blurb_clears_with_empty_submission(client, reset_db):
    user = await _seed_user(reset_db)
    agent = await _seed_agent(
        reset_db, user, "One", ConnectionProvider.CLAUDE, blurb="Forgives once"
    )
    r = await client.post(
        f"/me/agents/{agent.id}/set-blurb",
        data={"blurb": "  "},
        cookies=_cookies(user.id),
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with reset_db() as db:
        refreshed = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
        assert refreshed.blurb is None
