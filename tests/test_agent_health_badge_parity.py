"""The agent list page and the agent detail page must show the same badge.

Before PR #644, ``app/routes/agents_list.py`` and ``app/routes/agents_detail.py``
each hand-built their own readiness-to-badge mapping, and the two had drifted:
the list page called every rung above ``NO_MCP_CONNECTION`` "Ready", while the
detail page called ``CONNECTED_NOT_LIVE`` "No live connection". So the same
agent, at the same moment, showed a green "Ready" badge on ``/me/agents`` and a
red "No live connection" badge on ``/me/agents/{id}``.

#644 unified the *Python* mapping (``readiness_health_status``). One layer out,
the two pages still built the badge markup themselves, and had drifted again:
``agents/_status.html`` — which is also what the detail page's 15s
``/health-badge`` poll re-serves — emitted the badge WITHOUT the ``badge`` base
class, so none of the pill styling applied, and added a ``badge-pulse`` class
that has never existed in ``app/static/style.css``. The detail page showed bare
text where the list showed a pill, for every state.

So this test pins the rendered MARKUP, not just the mapping: it drives both real
HTTP routes for the same agent and asserts the badge *element* — classes, dot,
and label — comes out identical at every readiness rung. It fails against the
pre-fix ``agents/_status.html``, which does not render a ``badge`` element at
all.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine.connection_health import (
    ConnectionHealth,
    PlayVerdict,
    ProviderReadiness,
    play_verdict,
    providers_busy_for_user,
    user_play_readiness,
)
from app.models.agent import AgentStatus
from app.models.connection import ConnectionProvider, ConnectionStatus
from app.routes.agents_health_presenter import readiness_health_status
from app.routes.web_join import _build_ai_options
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import make_agent, make_connection, make_user

# The one badge element every page renders through app/templates/fragments/_badge.html:
# `<span class="badge {class}">[<span class="dot[ dot-still]"></span>]{label}</span>`.
# Both pages are matched with the SAME pattern on purpose — the old test used a
# different regex per page, which is how it kept passing while one page rendered
# a pill and the other rendered bare text.
_BADGE_ELEMENT = (
    r'<span class="badge [^"]*">(?:<span class="dot[^"]*"></span>)?[^<]*</span>'
)
# Anchored so an unrelated badge elsewhere on either page can't match: the list
# badge sits inside the agent row link, the detail badge inside the polled
# `agent-health-live` wrapper.
_LIST_BADGE = re.compile(r'class="agent-row"[\s\S]{0,400}?(' + _BADGE_ELEMENT + r")")
_DETAIL_BADGE = re.compile(
    r'agent-health-live"[\s\S]{0,600}?(' + _BADGE_ELEMENT + r")"
)


def _badge(pattern: re.Pattern[str], html: str, page: str) -> str:
    """Return the badge ELEMENT the page rendered for the agent, whitespace-normalized."""
    match = pattern.search(html)
    assert match is not None, f"no health badge element found on the {page} page"
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _ago(**kwargs: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kwargs)


# Each case names the rung it means to exercise, and the test asserts the seeded
# data really resolves to it — otherwise a factory default could quietly move a
# case onto a different rung and the parity claim would cover nothing.
_CASES = [
    # The rung that drifted in #644: an MCP sign-in inside the 90-day window (so
    # the provider is set up) that has not been seen live.
    ("connected_not_live", ProviderReadiness.CONNECTED_NOT_LIVE, ConnectionStatus.ACTIVE, _ago(days=5), None, AgentStatus.ACTIVE),
    # A paused connection with no MCP sign-in leaves nothing usable set up.
    ("no_mcp_connection", ProviderReadiness.NO_MCP_CONNECTION, ConnectionStatus.PAUSED, None, None, AgentStatus.ACTIVE),
    # Set up and seen inside the live window, but no play loop polling yet.
    ("seen_not_polling", ProviderReadiness.SEEN_NOT_POLLING, ConnectionStatus.ACTIVE, _ago(days=5), _ago(seconds=5), AgentStatus.ACTIVE),
    # A paused agent overrides readiness on both pages.
    ("paused_agent", ProviderReadiness.SEEN_NOT_POLLING, ConnectionStatus.ACTIVE, _ago(days=5), _ago(seconds=5), AgentStatus.PAUSED),
]


@pytest.mark.parametrize(
    (
        "case",
        "expected_readiness",
        "connection_status",
        "mcp_connected_at",
        "last_seen_at",
        "agent_status",
    ),
    _CASES,
    ids=[case[0] for case in _CASES],
)
async def test_list_and_detail_render_the_same_badge_markup(
    client: AsyncClient,
    reset_db: async_sessionmaker,
    case: str,
    expected_readiness: ProviderReadiness,
    connection_status: ConnectionStatus,
    mcp_connected_at: datetime | None,
    last_seen_at: datetime | None,
    agent_status: AgentStatus,
) -> None:
    """Both agent pages render byte-identical badge markup for one agent.

    Not just the same class and label — the same element, base class included.
    A page that drops ``badge`` (as the detail fragment used to) or adds a class
    of its own fails here even when the underlying mapping agrees.
    """
    async with reset_db() as db:
        user = await make_user(db, i=900, handle="paritycheck")
        connection, _key = await make_connection(
            db,
            user,
            provider=ConnectionProvider.CLAUDE,
            status=connection_status,
            mcp_connected_at=mcp_connected_at,
            last_seen_at=last_seen_at,
        )
        agent, _version = await make_agent(
            db,
            user,
            connection=connection,
            name="Parity",
            status=agent_status,
        )
        await db.commit()
        readiness = await user_play_readiness(db, user.id)

    assert readiness == expected_readiness, (
        f"{case}: seeded data resolves to {readiness}, not the "
        f"{expected_readiness} rung this case means to cover"
    )

    cookies = _cookies(user.id)
    list_resp = await client.get("/me/agents", cookies=cookies)
    detail_resp = await client.get(f"/me/agents/{agent.id}", cookies=cookies)
    # The detail page's badge is re-served on its own every 15s. Poll it too:
    # this fragment is where the two pages drifted, and a page-load-only check
    # would miss a fragment that renders differently from the page that embeds it.
    poll_resp = await client.get(
        f"/me/agents/{agent.id}/health-badge", cookies=cookies
    )

    assert list_resp.status_code == 200
    assert detail_resp.status_code == 200
    assert poll_resp.status_code == 200

    list_badge = _badge(_LIST_BADGE, list_resp.text, "list")
    detail_badge = _badge(_DETAIL_BADGE, detail_resp.text, "detail")
    poll_badge = _badge(re.compile("(" + _BADGE_ELEMENT + ")"), poll_resp.text, "poll")
    assert list_badge == detail_badge, (
        f"{case}: /me/agents rendered {list_badge!r} but /me/agents/{agent.id} "
        f"rendered {detail_badge!r} for the same agent at the same moment"
    )
    assert poll_badge == detail_badge, (
        f"{case}: the /health-badge poll rendered {poll_badge!r}, which is not "
        f"what the page it replaces rendered ({detail_badge!r})"
    )


async def test_no_page_emits_the_phantom_badge_pulse_class(
    client: AsyncClient,
    reset_db: async_sessionmaker,
) -> None:
    """``badge-pulse`` must not appear in any rendered badge.

    The detail fragment used to add it. It has never been defined in
    ``app/static/style.css``, so it styled nothing — the animated-dot pulse is
    the ``.dot`` element's ``hhh-pulse`` animation, which the shared partial
    renders. This pins that a second, fictional pulse mechanism does not come
    back.
    """
    async with reset_db() as db:
        user = await make_user(db, i=902, handle="nopulse")
        connection, _key = await make_connection(
            db,
            user,
            provider=ConnectionProvider.CLAUDE,
            mcp_connected_at=_ago(days=5),
            last_seen_at=_ago(seconds=5),
        )
        connection.last_polled_at = _ago(seconds=5)  # LIVE — the state that pulses
        agent, _version = await make_agent(
            db, user, connection=connection, name="Pulsey"
        )
        await db.commit()

    cookies = _cookies(user.id)
    for url in (
        "/me/agents",
        f"/me/agents/{agent.id}",
        f"/me/agents/{agent.id}/health-badge",
    ):
        resp = await client.get(url, cookies=cookies)
        assert resp.status_code == 200
        assert "badge-pulse" not in resp.text, f"{url} still emits badge-pulse"


async def test_connected_not_live_is_not_advertised_as_ready(
    client: AsyncClient,
    reset_db: async_sessionmaker,
) -> None:
    """A set-up-but-offline agent must not be badged "Ready" on either page.

    Agreeing on the wrong answer would still be a bug, so this pins the side the
    disagreement had to resolve to: no live client can pick up a turn for this
    agent right now, and both the seat-hold path
    (``app/routes/web_seat_connect.py``) and the join path
    (``app/routes/web_join.py``) already refuse to treat this rung as ready.
    """
    async with reset_db() as db:
        user = await make_user(db, i=901, handle="notready")
        connection, _key = await make_connection(
            db,
            user,
            provider=ConnectionProvider.CLAUDE,
            mcp_connected_at=_ago(days=5),  # set up...
            last_seen_at=None,  # ...but not seen live → CONNECTED_NOT_LIVE
        )
        agent, _version = await make_agent(
            db, user, connection=connection, name="Offline"
        )
        await db.commit()
        assert (
            await user_play_readiness(db, user.id)
        ) == ProviderReadiness.CONNECTED_NOT_LIVE

    cookies = _cookies(user.id)
    list_resp = await client.get("/me/agents", cookies=cookies)
    detail_resp = await client.get(f"/me/agents/{agent.id}", cookies=cookies)

    expected = '<span class="badge badge-alert">No live connection</span>'
    assert _badge(_LIST_BADGE, list_resp.text, "list") == expected
    assert _badge(_DETAIL_BADGE, detail_resp.text, "detail") == expected


async def test_seen_not_polling_is_not_advertised_as_ready(
    client: AsyncClient,
    reset_db: async_sessionmaker,
) -> None:
    """A signed-in-but-not-looping agent must not be badged a green "Ready".

    This rung used to be the second half of the split #644 left behind: the
    agent pages called ``SEEN_NOT_POLLING`` "Ready" while the join picker ranked
    the same provider below ready as "idle". The platform settles it — a turn is
    only handed out inside a get-next-turn poll, and ``last_polled_at`` (the
    stamp ``LIVE`` keys on) is the only signal that a play loop is running, so at
    this rung nothing picks the agent's turn up. ``confirm_seat_if_live`` in
    ``app/engine/seat_hold.py`` says the same thing in code: a seat joined at
    this rung is held, not confirmed, and is deleted when the hold expires.

    The badge is amber rather than the red pair's "No live connection", because
    the client genuinely IS connected at this rung — the connect page says so
    and moves the user on. What it isn't, is playing.
    """
    async with reset_db() as db:
        user = await make_user(db, i=903, handle="notplaying")
        connection, _key = await make_connection(
            db,
            user,
            provider=ConnectionProvider.CLAUDE,
            mcp_connected_at=_ago(days=5),  # signed in...
            last_seen_at=_ago(seconds=5),  # ...and seen just now...
        )
        # ...but never polled for a turn → SEEN_NOT_POLLING (factory default).
        assert connection.last_polled_at is None
        agent, _version = await make_agent(
            db, user, connection=connection, name="Idle"
        )
        await db.commit()
        assert (
            await user_play_readiness(db, user.id)
        ) == ProviderReadiness.SEEN_NOT_POLLING

    cookies = _cookies(user.id)
    list_resp = await client.get("/me/agents", cookies=cookies)
    detail_resp = await client.get(f"/me/agents/{agent.id}", cookies=cookies)

    expected = '<span class="badge badge-soon">Not playing yet</span>'
    assert _badge(_LIST_BADGE, list_resp.text, "list") == expected
    assert _badge(_DETAIL_BADGE, detail_resp.text, "detail") == expected


@pytest.mark.parametrize("rung", list(ProviderReadiness), ids=lambda r: r.value)
def test_the_badge_and_the_join_picker_cannot_disagree(rung: ProviderReadiness) -> None:
    """One verdict per rung: green badge ⇔ ``PlayVerdict.READY`` ⇔ picker "ready".

    This is the invariant the whole unification exists for. The agent pages and
    the join picker used to hold separate opinions, and ``SEEN_NOT_POLLING``
    landed on opposite sides of them — "Ready" in green on ``/me/agents``,
    ranked below ready as "idle" on the join screen. Both now reduce through
    ``play_verdict``, so a rung cannot be ready on one surface and not the other.
    """
    verdict = play_verdict(rung)
    status = readiness_health_status(rung, AgentStatus.ACTIVE)
    badge_says_ready = status.state is ConnectionHealth.READY
    assert badge_says_ready is (verdict is PlayVerdict.READY), (
        f"{rung.value}: badge state {status.state} disagrees with verdict {verdict}"
    )
    # Only LIVE clears the bar — the rung whose meaning is "an AI is polling".
    assert badge_says_ready is (rung is ProviderReadiness.LIVE)


async def test_join_picker_calls_a_seen_not_polling_ai_idle_not_ready(
    reset_db: async_sessionmaker,
) -> None:
    """The join picker reads the shared verdict, not its own copy of the ladder.

    Pinned at ``SEEN_NOT_POLLING`` because that is the rung the two surfaces
    disagreed about. The picker's answer here is unchanged by the unification —
    it was already the correct one — but it is now the SAME answer the badge
    gives, and this fails if the shared mapping is edited to say otherwise.
    """
    async with reset_db() as db:
        user = await make_user(db, i=904, handle="pickerparity")
        await make_connection(
            db,
            user,
            provider=ConnectionProvider.CLAUDE,
            mcp_connected_at=_ago(days=5),  # signed in...
            last_seen_at=_ago(seconds=5),  # ...seen just now, never polled
        )
        await db.commit()
        assert (
            await user_play_readiness(db, user.id)
        ) == ProviderReadiness.SEEN_NOT_POLLING
        options = await _build_ai_options(
            db, user.id, await providers_busy_for_user(db, user.id)
        )

    claude = next(o for o in options if o["provider"] == "claude")
    assert claude["state"] == PlayVerdict.IDLE.value
    assert claude["state"] != PlayVerdict.READY.value
