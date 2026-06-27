"""Shared helpers for the player web routes (guide, join, connect, dashboard).

These small helpers are used across more than one of the split player route
modules, so they live here to avoid a circular import between siblings:

* ``_hx_redirect`` — an empty 200 that tells HTMX to navigate the whole page.
* ``_seat_name`` — derive a unique public seat name within a match.
* ``_load_user_agents`` — load a user's non-archived agents + current versions.
* ``_seat_provider_readiness`` / ``_seat_provider_label`` — readiness and label
  for the AI a seat was joined with (used by both the join and connect flows).
"""

from __future__ import annotations

from fastapi.responses import HTMLResponse

from app.deps import DbSession
from app.engine.connection_health import (
    ProviderReadiness,
    provider_readiness,
    user_play_readiness,
)
from app.models.agent import Agent
from app.models.agent_version import AgentVersion
from app.models.connection import ConnectionProvider
from app.models.player import Player
from app.provider_labels import provider_label
from app.routes.agents_queries import user_agents_select
from app.routes.web_support import SEAT_NAME_MAX, unique_seat_name


def _hx_redirect(url: str) -> HTMLResponse:
    """An empty 200 that tells HTMX to navigate the whole page to *url*."""
    return HTMLResponse("", headers={"HX-Redirect": url})


def _seat_name(agent_name: str, existing: set[str]) -> str:
    """Derive a public seat name and keep it unique within the match.

    The seat name shown to agents and spectators is the agent's name only —
    never the owning user's handle, name, or email. Identity must not leak to
    competing agents. The human-facing viewer shows the owner's handle as a
    separate byline, sourced from its own query, not from this label.
    """
    return unique_seat_name(agent_name[:SEAT_NAME_MAX], existing)


async def _load_user_agents(
    db: DbSession, user_id: int
) -> list[tuple[Agent, AgentVersion | None]]:
    # Note: unlike the agents-list and connections loaders, this one does NOT
    # filter to AI agents — it returns every non-archived agent (the join screen
    # post-filters to AI itself). Newest-first order, raw (agent, version) tuples.
    rows = (
        await db.execute(
            user_agents_select(user_id, ai_only=False).order_by(
                Agent.created_at.desc(), Agent.id.desc()
            )
        )
    ).all()
    return [(agent, version) for agent, version in rows]


async def _seat_provider_readiness(
    db: DbSession, user_id: int, player: Player
) -> ProviderReadiness:
    """Readiness of the AI a seat was joined with (its chosen provider).

    Legacy seats with no chosen provider fall back to the user's best readiness.
    """
    if player.chosen_provider:
        return await provider_readiness(
            db, user_id, ConnectionProvider(player.chosen_provider)
        )
    return await user_play_readiness(db, user_id)


def _seat_provider_label(player: Player) -> str:
    """Friendly name of the AI a seat was joined with."""
    if player.chosen_provider:
        return provider_label(player.chosen_provider)
    return "your AI"
