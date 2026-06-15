"""Map an MCP client's self-reported identity to the AI provider it represents.

When an MCP client connects to our server it sends a ``clientInfo.name`` in the
initialize handshake. Each client speaks for exactly one AI provider, so one MCP
client == one provider. This module turns that reported name into a
:class:`ConnectionProvider`.

Captured real client names from live handshakes:

- Claude Code     -> "claude-code"
- Claude Desktop  -> "claude-ai"
- Codex (OpenAI)  -> "codex-mcp-client"
- Gemini CLI      -> "gemini-cli-mcp-client"

Matching is a case-insensitive substring check. Codex is OpenAI's CLI, so a
"codex" name maps to ``OPENAI``. Unknown or missing names return ``None`` so the
caller enables nothing (fail safe).
"""

from __future__ import annotations

from app.models.connection import ConnectionProvider

# Ordered substring -> provider rules. Order matters: the first substring found
# in the (lowercased) client name wins. "codex" is checked before "claude" so a
# hypothetical "codex" client is never misread, and because Codex is OpenAI's CLI
# it maps to OPENAI rather than CLAUDE.
_SUBSTRING_RULES: tuple[tuple[str, ConnectionProvider], ...] = (
    ("gemini", ConnectionProvider.GEMINI),
    ("codex", ConnectionProvider.OPENAI),
    ("claude", ConnectionProvider.CLAUDE),
    ("hermes", ConnectionProvider.HERMES),
    ("openclaw", ConnectionProvider.OPENCLAW),
)


def provider_from_client_name(name: str | None) -> ConnectionProvider | None:
    """Return the AI provider an MCP client's ``clientInfo.name`` stands for.

    One MCP client speaks for exactly one provider, so this maps the client's
    self-reported name to a single :class:`ConnectionProvider`.

    The match is a case-insensitive substring check applied in a fixed order
    (gemini, codex, claude, hermes, openclaw). Surrounding whitespace is
    stripped first. A missing, empty, or unrecognized name returns ``None`` so
    the caller enables nothing — failing safe rather than guessing a provider.
    """
    if name is None:
        return None
    cleaned = name.strip().lower()
    if not cleaned:
        return None
    for substring, provider in _SUBSTRING_RULES:
        if substring in cleaned:
            return provider
    return None
