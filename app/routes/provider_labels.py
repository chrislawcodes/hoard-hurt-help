"""Human-friendly display names for AI providers, shared across web routes."""

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
