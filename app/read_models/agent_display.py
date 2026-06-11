"""Shared helpers for turning agent records into public-facing display names."""

from __future__ import annotations

import re

from app.models.agent import Agent, AgentKind
from app.models.agent_version import AgentVersion

_ARCHIVE_SUFFIX_RE = re.compile(r"\s+\(archived\s[^)]+\)(?:\s*#\d+)?$|\s+#\d+$")


def _strip_archive_suffix(name: str) -> str:
    return _ARCHIVE_SUFFIX_RE.sub("", name)


def _strip_internal_bot_prefix(name: str) -> str:
    """Remove the per-match internal prefix from a bot agent name."""

    return name.split(":", 1)[-1]


def agent_display_name(agent: Agent, version: AgentVersion | None = None) -> str:
    """Return the name we should show publicly for an agent."""

    if agent.kind == AgentKind.BOT:
        if agent.bot_profile_name:
            return agent.bot_profile_name
        return _strip_internal_bot_prefix(_strip_archive_suffix(agent.name))

    base_name = _strip_archive_suffix(agent.name)
    if version is not None:
        return f"{base_name} · {version.model}"
    return base_name
