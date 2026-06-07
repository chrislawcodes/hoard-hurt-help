"""Connection key reissue and revoke actions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status
from fastapi.responses import RedirectResponse

from app.deps import DbSession, require_user_with_handle
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.models.connection import Connection
from app.models.user import User

from app.routes.connections_setup import _load_owned_connection

router = APIRouter()


def _issue_new_key(connection: Connection, *, keep_old_overlap: bool) -> str:
    key = generate_connection_key()
    if keep_old_overlap and connection.prev_key_lookup is None:
        connection.prev_key_lookup = connection.key_lookup
    connection.key_lookup = bot_key_lookup(key)
    connection.key_hint = bot_key_hint(key)
    if not keep_old_overlap:
        connection.prev_key_lookup = None
    return key


@router.post("/{connection_id}/reissue")
async def reissue_key(
    connection_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    connection = await _load_owned_connection(db, user, connection_id)
    key = _issue_new_key(connection, keep_old_overlap=True)
    await db.commit()
    request.session[f"fresh_connection_key_{connection.id}"] = key
    return RedirectResponse(
        url=f"/me/connections/{connection.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{connection_id}/revoke")
async def revoke_and_reissue(
    connection_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    connection = await _load_owned_connection(db, user, connection_id)
    key = _issue_new_key(connection, keep_old_overlap=False)
    await db.commit()
    request.session[f"fresh_connection_key_{connection.id}"] = key
    return RedirectResponse(
        url=f"/me/connections/{connection.id}", status_code=status.HTTP_303_SEE_OTHER
    )
