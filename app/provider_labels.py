"""Human-friendly display names for AI providers, shared across the app.

Lives at the app root (not under ``app/routes/``) because non-route layers —
notably read models like ``app.read_models.leaderboard`` — also need provider
labels, and a read model must not depend on a route.
"""

from __future__ import annotations

# Friendly names for an AI provider, keyed by its provider slug
# (``ConnectionProvider.<X>.value``). Used by the join picker, the connect page,
# and the MCP-connection display name.
PROVIDER_LABELS = {
    "claude": "Claude",
    "gemini": "Gemini",
    "openai": "OpenAI",
    "hermes": "Hermes",
    "openclaw": "OpenClaw",
}


def provider_label(value: str) -> str:
    """Friendly label for a provider slug, title-cased fallback if unknown."""
    return PROVIDER_LABELS.get(value, value.title())
