"""Shared helpers for turning agent records into public-facing display names."""

from __future__ import annotations

import re

from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion

_ARCHIVE_SUFFIX_RE = re.compile(r"\s+\(archived\s[^)]+\)(?:\s*#\d+)?$|\s+#\d+$")


def strip_archive_suffix(name: str) -> str:
    """Remove the archived suffix used in display labels."""
    return _ARCHIVE_SUFFIX_RE.sub("", name)


def _strip_internal_bot_prefix(name: str) -> str:
    """Remove the per-match internal prefix from a bot agent name."""

    return name.split(":", 1)[-1]


def agent_display_name(agent: Agent, version: AgentVersion | None = None) -> str:
    """Return the name we should show publicly for an agent.

    Agents are decoupled from a fixed model, so the display name is just the
    agent's name — the provider that actually played is shown separately as a
    badge (from ``Player.played_provider``). ``version`` is accepted for
    backward-compatible call sites but no longer affects the name.
    """

    if agent.kind == AgentKind.BOT:
        if agent.bot_profile_name:
            return agent.bot_profile_name
        return _strip_internal_bot_prefix(strip_archive_suffix(agent.name))

    return strip_archive_suffix(agent.name)
