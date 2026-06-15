"""Machine setup: minting the pending setup + key, naming, and setup pages.

Owns the one open "set up a machine" record per user — minting it with a stable
key for the inline connector command, the auto-save name action, and the setup
detail/status views polled while a machine comes up.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import String, select
from starlette.responses import Response

from app.deps import DbSession, require_user_with_handle
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.models.connection import ConnectionProvider
from app.models.connection_setup import ConnectionSetup
from app.models.user import User
from app.routes.connections_connect_guide import _provider_label, _setup_message
from app.routes.connections_queries import _load_owned_connection
from app.templating import templates

router = APIRouter()

# Cap nicknames at the column's declared length so a too-long value returns a
# friendly 400 instead of a Postgres "value too long" 500. Derived from the
# column so it can't drift from the schema.
_NICKNAME_TYPE = ConnectionSetup.__table__.c.nickname.type
_NICKNAME_MAX = (
    _NICKNAME_TYPE.length
    if isinstance(_NICKNAME_TYPE, String) and _NICKNAME_TYPE.length
    else 60
)


def _validate_nickname_length(raw: str | None) -> str | None:
    """Reject a nickname longer than the column holds; otherwise pass through.

    Blank/None handling is intentionally left to ``_ensure_pending_setup_and_key``,
    which strips the value and treats an empty string as "clear the name". We only
    guard the length so a too-long value returns a friendly 400 instead of a
    Postgres "value too long for type character varying(60)" 500.
    """
    if raw is not None and len(raw.strip()) > _NICKNAME_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"Nickname must be {_NICKNAME_MAX} characters or fewer.",
        )
    return raw


async def _load_resumeable_pending_setup(
    db: DbSession, user_id: int, provider: ConnectionProvider | None
) -> ConnectionSetup | None:
    """Return the newest pending setup for this provider, if one exists."""
    provider_clause = (
        ConnectionSetup.provider.is_(None)
        if provider is None
        else ConnectionSetup.provider == provider
    )
    return (
        await db.execute(
            select(ConnectionSetup)
            .where(
                ConnectionSetup.user_id == user_id,
                provider_clause,
                ConnectionSetup.completed_at.is_(None),
            )
            .order_by(ConnectionSetup.created_at.desc(), ConnectionSetup.id.desc())
        )
    ).scalar_one_or_none()


def _issue_setup_key(setup: ConnectionSetup) -> str:
    key = generate_connection_key()
    setup.key_lookup = bot_key_lookup(key)
    setup.key_hint = bot_key_hint(key)
    return key


async def _ensure_pending_setup_and_key(
    request: Request,
    db: DbSession,
    user_id: int,
    nickname: str | None = None,
) -> tuple[ConnectionSetup, str]:
    """Reuse the user's one open machine setup (or mint it) and return a STABLE
    plaintext key for the inline setup command.

    A machine is provider-agnostic — the connector auto-detects which AI CLIs are
    installed — so setups are always created with ``provider=None``. The key is
    minted once and stashed in the session so reloads show the SAME command; we
    never silently rotate a key the user may have already copied. The key only
    regenerates if the session no longer carries it (e.g. a new browser session),
    since the raw value is unrecoverable from the stored hash.
    """
    setup = await _load_resumeable_pending_setup(db, user_id, None)
    if setup is None:
        key = generate_connection_key()
        setup = ConnectionSetup(
            user_id=user_id,
            nickname=(nickname.strip() if nickname and nickname.strip() else None),
            provider=None,
            key_lookup=bot_key_lookup(key),
            key_hint=bot_key_hint(key),
        )
        db.add(setup)
        await db.flush()
        request.session[f"fresh_connection_key_setup_{setup.id}"] = key
    else:
        if nickname is not None:
            setup.nickname = nickname.strip() or None
        session_field = f"fresh_connection_key_setup_{setup.id}"
        stored = request.session.get(session_field)
        if stored:
            key = str(stored)
        else:
            key = _issue_setup_key(setup)
            request.session[session_field] = key
    await db.commit()
    return setup, key


@router.post("/name", response_class=HTMLResponse)
async def save_machine_name(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    nickname: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Auto-save the optional machine name (HTMX, no button, no reload).

    Labels the one open setup; never rotates the key or creates a second setup.
    A blank name is cleared — the machine then names itself from its hostname when
    it connects (see report_pid). Returns a tiny status span for the inline tick.
    """
    setup, _ = await _ensure_pending_setup_and_key(
        request, db, user.id, nickname=_validate_nickname_length(nickname)
    )
    label = "Saved ✓" if setup.nickname else ""
    return HTMLResponse(label)


async def _load_owned_connection_setup(
    db: DbSession, user: User, setup_id: int
) -> ConnectionSetup:
    setup = (
        await db.execute(
            select(ConnectionSetup).where(
                ConnectionSetup.id == setup_id,
                ConnectionSetup.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if setup is None:
        raise HTTPException(status_code=404, detail="Connection setup not found.")
    return setup


@router.get("/setup/{setup_id}", response_class=HTMLResponse)
async def connection_setup_detail(
    setup_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    setup = await _load_owned_connection_setup(db, user, setup_id)
    fresh_key = request.session.get(f"fresh_connection_key_setup_{setup.id}")
    connection = None
    if setup.connection_id is not None:
        connection = await _load_owned_connection(db, user, setup.connection_id)
    return templates.TemplateResponse(
        request,
        "connections/setup.html",
        {
            "user": user,
            "setup": setup,
            "connection": connection,
            "provider_label": _provider_label(setup.provider),
            "fresh_key": fresh_key,
            "setup_message": (_setup_message(fresh_key) if fresh_key else None),
        },
    )


@router.get("/setup/{setup_id}/status", response_class=HTMLResponse)
async def connection_setup_status_fragment(
    setup_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    setup = await _load_owned_connection_setup(db, user, setup_id)
    connection = None
    if setup.connection_id is not None:
        connection = await _load_owned_connection(db, user, setup.connection_id)
    return templates.TemplateResponse(
        request,
        "connections/_setup_status.html",
        {
            "setup": setup,
            "connection": connection,
        },
    )
