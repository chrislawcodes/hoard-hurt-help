from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.request_logging import install_request_logging


@pytest.mark.asyncio
async def test_request_id_is_added_to_success_responses(caplog) -> None:
    app = FastAPI()
    install_request_logging(app)

    @app.get("/ok")
    async def ok() -> dict[str, bool]:
        return {"ok": True}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with caplog.at_level(logging.INFO):
            response = await client.get("/ok")

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert "request start" in caplog.text
    assert "request end" in caplog.text


@pytest.mark.asyncio
async def test_unhandled_exception_is_logged_and_returns_500(caplog) -> None:
    app = FastAPI()
    install_request_logging(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with caplog.at_level(logging.INFO):
            response = await client.get("/boom")

    assert response.status_code == 500
    assert response.headers["x-request-id"]
    assert "Internal Server Error" in response.text
    assert "Request ID:" in response.text
    assert "request error" in caplog.text
    assert "kaboom" in caplog.text
