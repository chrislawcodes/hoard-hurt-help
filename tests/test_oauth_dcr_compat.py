"""Tests for the DCR registration-compatibility shim (app/oauth_dcr_compat.py).

The MCP SDK rejects client registrations whose grant_types omit refresh_token,
which blocks standards-compliant clients (e.g. Antigravity). The shim normalizes
the registration body so those clients get through. See fastmcp#2460.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.oauth_dcr_compat import _normalize_registration_body


def _decode(body: bytes) -> dict[str, object]:
    return json.loads(body)


def test_adds_missing_refresh_token() -> None:
    body = json.dumps({"grant_types": ["authorization_code"]}).encode()
    out = _normalize_registration_body(body)
    assert _decode(out)["grant_types"] == ["authorization_code", "refresh_token"]


def test_adds_missing_response_type_code() -> None:
    body = json.dumps(
        {"grant_types": ["authorization_code", "refresh_token"], "response_types": []}
    ).encode()
    out = _normalize_registration_body(body)
    assert _decode(out)["response_types"] == ["code"]


def test_empty_grant_types_list_gets_both() -> None:
    out = _normalize_registration_body(json.dumps({"grant_types": []}).encode())
    assert _decode(out)["grant_types"] == ["authorization_code", "refresh_token"]


def test_complete_body_is_returned_unchanged() -> None:
    body = json.dumps(
        {
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        }
    ).encode()
    # Returns the *same object* so the middleware can skip the content-length rewrite.
    assert _normalize_registration_body(body) is body


def test_omitted_grant_types_is_left_alone() -> None:
    # An omitted field defaults to a valid value inside the SDK, so we must not
    # touch it (touching it would change a body we did not need to change).
    body = json.dumps({"client_name": "x"}).encode()
    assert _normalize_registration_body(body) is body


def test_non_json_body_is_left_alone() -> None:
    body = b"not json at all"
    assert _normalize_registration_body(body) is body


def test_non_dict_json_is_left_alone() -> None:
    body = json.dumps(["a", "list"]).encode()
    assert _normalize_registration_body(body) is body


@pytest.mark.asyncio
async def test_registration_without_refresh_token_succeeds() -> None:
    """End to end: a client that registers only the auth-code flow is accepted.

    Without the shim the MCP SDK answers 400 invalid_client_metadata
    ("grant_types must be authorization_code and refresh_token"). With it, the
    request is accepted and the stored client records both grant types.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/register",
            json={
                "client_name": "antigravity-like-client",
                "redirect_uris": ["http://localhost:8765/callback"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data.get("client_id")
    assert "refresh_token" in data["grant_types"]
