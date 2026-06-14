"""Tests for mapping an MCP client name to its AI provider."""

from __future__ import annotations

import pytest

from app.engine.mcp_client_identity import provider_from_client_name
from app.models.connection import ConnectionProvider


@pytest.mark.parametrize(
    ("client_name", "expected"),
    [
        ("claude-code", ConnectionProvider.CLAUDE),
        ("claude-ai", ConnectionProvider.CLAUDE),
        ("codex-mcp-client", ConnectionProvider.OPENAI),
        ("gemini-cli-mcp-client", ConnectionProvider.GEMINI),
    ],
)
def test_captured_client_names_map_correctly(
    client_name: str, expected: ConnectionProvider
) -> None:
    assert provider_from_client_name(client_name) == expected


def test_match_is_case_insensitive() -> None:
    assert provider_from_client_name("Claude-Code") == ConnectionProvider.CLAUDE
    assert provider_from_client_name("GEMINI-CLI") == ConnectionProvider.GEMINI


def test_surrounding_whitespace_is_stripped() -> None:
    assert provider_from_client_name("  claude-code  ") == ConnectionProvider.CLAUDE


def test_unknown_name_returns_none() -> None:
    assert provider_from_client_name("cursor-vscode") is None


def test_none_returns_none() -> None:
    assert provider_from_client_name(None) is None


def test_empty_string_returns_none() -> None:
    assert provider_from_client_name("") is None


def test_whitespace_only_returns_none() -> None:
    assert provider_from_client_name("   ") is None
