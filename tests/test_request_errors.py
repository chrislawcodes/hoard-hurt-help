from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.request_errors import install_request_error_logging


@pytest.mark.asyncio
async def test_unhandled_exception_is_logged_and_returns_500(caplog) -> None:
    app = FastAPI()
    install_request_error_logging(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with caplog.at_level(logging.ERROR):
            response = await client.get("/boom")

    assert response.status_code == 500
    assert response.headers["x-request-id"]
    assert "UNHANDLED REQUEST ERROR" in caplog.text
    assert "kaboom" in caplog.text
