"""Server-Sent Events stream of live game updates."""

from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import StreamingResponse

from app.broadcast import subscribe

router = APIRouter(tags=["web"])


@router.get("/games/{game_id}/stream")
async def game_stream(game_id: Annotated[str, Path()]):
    async def event_gen():
        async for msg in subscribe(game_id):
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
