"""Connection key actions — rotating a key, and where it is allowed to be used."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.responses import RedirectResponse

from app.deps import DbSession, require_user_with_handle
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.models.connection import Connection
from app.models.user import User

from app.routes.connections_queries import _load_owned_connection

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


@router.post("/{connection_id}/rotate")
async def rotate_key(
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


@router.post("/{connection_id}/mcp-key-signin")
async def set_mcp_key_signin(
    connection_id: Annotated[int, Path()],
    enabled: Annotated[bool, Query()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    """Turn key sign-in on /mcp on or off for one connection.

    The owner's explicit choice is the entire gate on the non-OAuth way into
    /mcp, so it is only ever set from here — never inferred from a client's
    self-reported name, which anything can copy.
    """
    connection = await _load_owned_connection(db, user, connection_id)
    connection.mcp_key_signin_enabled = enabled
    await db.commit()
    return RedirectResponse(
        url=f"/me/connections/{connection.id}", status_code=status.HTTP_303_SEE_OTHER
    )
