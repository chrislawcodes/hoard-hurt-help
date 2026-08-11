"""The agent list page and the agent detail page must show the same badge.

Before the fix, ``app/routes/agents_list.py`` and ``app/routes/agents_detail.py``
each hand-built their own readiness-to-badge mapping, and the two had drifted:
the list page called every rung above ``NO_MCP_CONNECTION`` "Ready", while the
detail page called ``CONNECTED_NOT_LIVE`` "No live connection". So the same
agent, at the same moment, showed a green "Ready" badge on ``/me/agents`` and a
red "No live connection" badge on ``/me/agents/{id}``.

Both routes now build the badge through the single ``readiness_health_status``
mapping in ``app/routes/agents_health_presenter.py``. This test drives both real
HTTP routes for the same user and asserts the rendered badges match — at every
readiness rung, not just the one that drifted.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine.connection_health import ProviderReadiness, user_play_readiness
from app.models.agent import AgentStatus
from app.models.connection import ConnectionProvider, ConnectionStatus
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import make_agent, make_connection, make_user

# The list page badge: `<span class="badge {{ badge_class }}">[dot]{{ label }}</span>`
# (app/templates/agents/list.html).
_LIST_BADGE = re.compile(
    r'<span class="badge (badge-[a-z]+)">(?:<span class="dot[^"]*"></span>)?([^<]+)</span>'
)
# The detail page badge, scoped to the live-health span that wraps
# `agents/_status.html`, so an unrelated badge elsewhere on the page can't match.
_DETAIL_BADGE = re.compile(
    r'agent-health-live"[\s\S]{0,600}?<span class="(badge-[a-z]+)[^"]*">([^<]+)</span>'
)


def _badge(pattern: re.Pattern[str], html: str, page: str) -> tuple[str, str]:
    """Return the (badge_class, label) the page rendered for the agent."""
    match = pattern.search(html)
    assert match is not None, f"no health badge found on the {page} page"
    return match.group(1), match.group(2).strip()


def _ago(**kwargs: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kwargs)


# Each case names the rung it means to exercise, and the test asserts the seeded
# data really resolves to it — otherwise a factory default could quietly move a
# case onto a different rung and the parity claim would cover nothing.
_CASES = [
    # The rung that drifted: an MCP sign-in inside the 90-day window (so the
    # provider is set up) that has not been seen live.
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
async def test_list_and_detail_agree_on_the_health_badge(
    client: AsyncClient,
    reset_db: async_sessionmaker,
    case: str,
    expected_readiness: ProviderReadiness,
    connection_status: ConnectionStatus,
    mcp_connected_at: datetime | None,
    last_seen_at: datetime | None,
    agent_status: AgentStatus,
) -> None:
    """Both agent pages render the same badge class and label for one agent."""
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

    assert list_resp.status_code == 200
    assert detail_resp.status_code == 200

    list_badge = _badge(_LIST_BADGE, list_resp.text, "list")
    detail_badge = _badge(_DETAIL_BADGE, detail_resp.text, "detail")
    assert list_badge == detail_badge, (
        f"{case}: /me/agents showed {list_badge} but /me/agents/{agent.id} "
        f"showed {detail_badge} for the same agent at the same moment"
    )


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

    assert _badge(_LIST_BADGE, list_resp.text, "list") == (
        "badge-alert",
        "No live connection",
    )
    assert _badge(_DETAIL_BADGE, detail_resp.text, "detail") == (
        "badge-alert",
        "No live connection",
    )
