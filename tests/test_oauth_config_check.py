"""Startup OAuth-config check: fail loud in a real deployment (FR-013).

`_check_oauth_config` must raise before serving when, in a real deployment
(`RAILWAY_ENVIRONMENT_ID` set), the Google credentials or a public `base_url`
are missing — so `/mcp` never starts in a fail-open state with the GoogleProvider
dev-placeholder credentials. In local dev it warns and continues.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.main import _check_oauth_config


def _arrange(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prod: bool,
    client_id: str,
    client_secret: str,
    base_url: str,
    signing_key: str = "a-stable-mcp-signing-key-0123456789",
) -> None:
    # The check short-circuits under pytest; clear the marker so the real logic runs.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    if prod:
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "test-env")
    else:
        monkeypatch.delenv("RAILWAY_ENVIRONMENT_ID", raising=False)
    monkeypatch.setattr(settings, "google_client_id", client_id)
    monkeypatch.setattr(settings, "google_client_secret", client_secret)
    monkeypatch.setattr(settings, "base_url", base_url)
    monkeypatch.setattr(settings, "mcp_jwt_signing_key", signing_key)


def test_prod_missing_google_creds_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _arrange(monkeypatch, prod=True, client_id="", client_secret="", base_url="https://play.example.com")
    with pytest.raises(RuntimeError) as exc:
        _check_oauth_config()
    assert "GOOGLE_CLIENT_ID" in str(exc.value)


def test_prod_localhost_base_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _arrange(
        monkeypatch,
        prod=True,
        client_id="id",
        client_secret="secret",
        base_url="http://localhost:8000",
    )
    with pytest.raises(RuntimeError) as exc:
        _check_oauth_config()
    assert "BASE_URL" in str(exc.value)


def test_prod_fully_configured_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _arrange(
        monkeypatch,
        prod=True,
        client_id="id",
        client_secret="secret",
        base_url="https://play.example.com",
    )
    _check_oauth_config()  # must not raise


def test_prod_missing_signing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Everything else valid, but no MCP_JWT_SIGNING_KEY -> fail loud in prod so the
    # signing/encryption keys never silently ride on GOOGLE_CLIENT_SECRET.
    _arrange(
        monkeypatch,
        prod=True,
        client_id="id",
        client_secret="secret",
        base_url="https://play.example.com",
        signing_key="",
    )
    with pytest.raises(RuntimeError) as exc:
        _check_oauth_config()
    assert "MCP_JWT_SIGNING_KEY" in str(exc.value)


def test_local_dev_missing_creds_warns_not_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _arrange(
        monkeypatch,
        prod=False,
        client_id="",
        client_secret="",
        base_url="http://localhost:8000",
        signing_key="",
    )
    _check_oauth_config()  # warn-but-run in dev; no raise
