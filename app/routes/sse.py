"""Server-Sent Events stream of live game updates."""

from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import StreamingResponse

from app.broadcast import subscribe
from app.engine.match_id_rewrite import to_match_id

router = APIRouter(tags=["web"])


@router.get("/games/{game}/matches/{match_id}/stream")
async def game_stream(game: Annotated[str, Path()], match_id: Annotated[str, Path()]):
    async def event_gen():
        async for msg in subscribe(to_match_id(match_id)):
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


@router.get("/games/{match_id}/stream", include_in_schema=False)
async def legacy_game_stream(match_id: Annotated[str, Path()]):
    async def event_gen():
        async for msg in subscribe(to_match_id(match_id)):
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
