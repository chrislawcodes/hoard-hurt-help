"""Server-Sent Events stream of live game updates."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import StreamingResponse

from app.broadcast import subscribe
from app.engine.match_id_rewrite import to_match_id

router = APIRouter(tags=["web"])


def sse_response(channel: str) -> StreamingResponse:
    """Build the standard `text/event-stream` response for a broadcast channel.

    Subscribes to *channel* and streams each message as-is, with the four headers
    every SSE endpoint here needs (no caching, keep-alive, no proxy buffering).
    Shared by every SSE route so the response/header block lives in one place.
    """

    async def event_gen() -> AsyncIterator[str]:
        async for msg in subscribe(channel):
            yield msg

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/games/{game}/matches/{match_id}/stream")
async def game_stream(
    game: Annotated[str, Path()], match_id: Annotated[str, Path()]
) -> StreamingResponse:
    return sse_response(to_match_id(match_id))


@router.get("/games/{match_id}/stream", include_in_schema=False)
async def legacy_game_stream(match_id: Annotated[str, Path()]) -> StreamingResponse:
    return sse_response(to_match_id(match_id))
