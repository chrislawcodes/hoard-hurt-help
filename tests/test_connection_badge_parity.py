"""One connection, one badge — the list, the detail page, and the 15s poll agree.

The connections inventory dressed its rows with ``calm_connection_status`` while
the detail page and its ``/health-badge`` poll rendered the raw health words
straight from ``compute_connection_health``. The same connection therefore read
two ways depending on which page you were looking at. It was starkest for a
connection that had never finished connecting — a grey "Not connected yet" on
the list, an amber "Waiting to connect" on the detail page, with the phrase then
repeated in the meta line beside it — but it was true of every idle connection
too ("Idle" vs a red "Disconnected").

All three now render the calm presenter through the shared badge partial. These
tests drive the real HTTP routes and pin that the badge element matches.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.connection import ConnectionProvider, ConnectionStatus
from tests.conftest import signed_in_cookies as _signed_in_cookies
from tests.factories import make_connection, make_user

# The one badge element app/templates/fragments/_badge.html renders.
_BADGE_ELEMENT = (
    r'<span class="badge [^"]*">(?:<span class="dot[^"]*"></span>)?[^<]*</span>'
)


def _badge_after(anchor: str, html: str, page: str) -> str:
    """The first badge element after *anchor*, whitespace-normalized."""
    pattern = re.compile(
        re.escape(anchor) + r"[\s\S]{0,600}?(" + _BADGE_ELEMENT + r")"
    )
    match = pattern.search(html)
    assert match is not None, f"no badge element found after {anchor!r} on {page}"
    return re.sub(r"\s+", " ", match.group(1)).strip()


async def _seed(
    reset_db: async_sessionmaker,
    *,
    is_mcp: bool,
    last_seen_at: datetime | None,
    status: ConnectionStatus,
) -> tuple[int, int]:
    """Create one user + one connection; return (user_id, connection_id)."""
    async with reset_db() as db:
        user = await make_user(db)
        connection, _key = await make_connection(
            db,
            user,
            provider=ConnectionProvider.CLAUDE,
            status=status,
            nickname=None if is_mcp else "Home PC",
            last_seen_at=last_seen_at,
            mcp_connected_at=(
                datetime.now(timezone.utc) - timedelta(days=1) if is_mcp else None
            ),
        )
        await db.commit()
        return user.id, connection.id


async def test_never_connected_reads_the_same_on_list_and_detail(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """A connection that never connected shows one badge, not two.

    The list said a calm grey "Not connected yet"; the detail page said an amber
    "Waiting to connect" and then said it again in the meta beside the badge.
    The calm wording wins: it is the deliberate, documented design in
    ``calm_connection_status`` — red (and amber urgency) is reserved for a real
    problem, and a setup the user simply hasn't finished is not one.
    """
    user_id, connection_id = await _seed(
        reset_db,
        is_mcp=True,
        last_seen_at=None,  # never seen and never first-connected
        status=ConnectionStatus.PENDING,
    )
    cookies = _signed_in_cookies(user_id)

    list_resp = await client.get("/me/connections", cookies=cookies)
    detail_resp = await client.get(f"/me/connections/{connection_id}", cookies=cookies)
    poll_resp = await client.get(
        f"/me/connections/{connection_id}/health-badge", cookies=cookies
    )
    assert list_resp.status_code == 200
    assert detail_resp.status_code == 200
    assert poll_resp.status_code == 200

    expected = '<span class="badge badge-done">Not connected yet</span>'
    assert (
        _badge_after(f'href="/me/connections/{connection_id}"', list_resp.text, "list")
        == expected
    )
    assert _badge_after('bot-health-live"', detail_resp.text, "detail") == expected
    assert (
        re.sub(r"\s+", " ", poll_resp.text).strip().startswith(expected)
    ), f"poll fragment rendered {poll_resp.text!r}"

    # The old amber wording is gone from both, and the badge no longer says the
    # same thing twice in a row.
    for name, resp in (("list", list_resp), ("detail", detail_resp)):
        assert "Waiting to connect" not in resp.text, f"{name} still amber-badges it"
        assert "waiting to connect" not in resp.text, f"{name} still repeats it"


async def test_idle_mcp_connection_reads_the_same_on_list_and_detail(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """The common resting state agrees too — calm "Idle", never a red "Disconnected".

    An MCP client only stays live while its chat session is open, so idle is its
    normal rest. The list already knew that; the detail page called the same
    connection "Disconnected" in red.
    """
    user_id, connection_id = await _seed(
        reset_db,
        is_mcp=True,
        last_seen_at=datetime.now(timezone.utc) - timedelta(days=1),
        status=ConnectionStatus.ACTIVE,
    )
    cookies = _signed_in_cookies(user_id)

    list_resp = await client.get("/me/connections", cookies=cookies)
    detail_resp = await client.get(f"/me/connections/{connection_id}", cookies=cookies)

    expected = '<span class="badge badge-done">Idle</span>'
    assert (
        _badge_after(f'href="/me/connections/{connection_id}"', list_resp.text, "list")
        == expected
    )
    assert _badge_after('bot-health-live"', detail_resp.text, "detail") == expected
    assert "Disconnected" not in detail_resp.text


async def test_asleep_machine_connection_reads_the_same_on_list_and_detail(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """A machine helper that is off reads "Asleep" on both pages, not "Disconnected".

    Same state, different kind: the calm presenter is type-aware, so this pins
    that the detail page picks up the machine wording rather than the MCP one.
    """
    user_id, connection_id = await _seed(
        reset_db,
        is_mcp=False,
        last_seen_at=datetime.now(timezone.utc) - timedelta(days=2),
        status=ConnectionStatus.ACTIVE,
    )
    cookies = _signed_in_cookies(user_id)

    list_resp = await client.get("/me/connections", cookies=cookies)
    detail_resp = await client.get(f"/me/connections/{connection_id}", cookies=cookies)

    expected = '<span class="badge badge-soon">Asleep</span>'
    assert (
        _badge_after(f'href="/me/connections/{connection_id}"', list_resp.text, "list")
        == expected
    )
    assert _badge_after('bot-health-live"', detail_resp.text, "detail") == expected
    assert "Disconnected" not in detail_resp.text
