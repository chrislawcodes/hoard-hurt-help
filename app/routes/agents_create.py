"""The `/me/agents/new` create flow — name, model, and strategy a new agent.

Owns name validation, the connection/provider lookups that decide which models
the user can pick, the model-picker grouping, the GET form, and the POST that
creates the Agent + its first AgentVersion.
"""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import String, select
from starlette.responses import Response

from app.config import PROVIDER_MODELS, provider_for_model
from app.deps import DbSession, require_user_with_handle
from app.engine.connection_health import enabled_provider_values
from app.engine.pending_connection_gc import gc_pending_connections
from app.games import get as get_game_module, known_types
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection, ConnectionProvider
from app.models.user import User
from app.routes.connections_setup import (
    _provider_label,
)
from app.routes.web_support import safe_internal_next
from app.templating import templates

router = APIRouter()

_DEFAULT_GAME = known_types()[0] if known_types() else "hoard-hurt-help"

# Cap names at the column's own declared length so a too-long name returns a
# friendly 400 instead of a Postgres "value too long" 500. Derived from the
# column so it can never drift from the schema.
_AGENT_NAME_TYPE = Agent.__table__.c.name.type
_AGENT_NAME_MAX = (
    _AGENT_NAME_TYPE.length
    if isinstance(_AGENT_NAME_TYPE, String) and _AGENT_NAME_TYPE.length
    else 120
)


def clean_agent_name(raw: str) -> str:
    """Strip, require non-empty, and reject names longer than the column holds."""
    name = raw.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Agent name is required.")
    if len(name) > _AGENT_NAME_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"Agent name must be {_AGENT_NAME_MAX} characters or fewer.",
        )
    return name


async def _load_user_connections(db: DbSession, user_id: int) -> list[Connection]:
    rows = (
        await db.execute(
            select(Connection)
            .where(Connection.user_id == user_id, Connection.deleted_at.is_(None))
            .order_by(Connection.created_at.desc(), Connection.id.desc())
        )
    )
    return list(rows.scalars().all())


def _build_model_picker_groups(
    enabled_values: set[str], selected_model: str | None
) -> tuple[list[dict[str, object]], str | None, list[dict[str, str]]]:
    """Return grouped model options, the first selectable model, and per-provider
    "connect this provider" notes for the ones that aren't connected yet.

    Each note is a dict (provider label + value) so the template can render a real
    "Connect {Provider} →" link carrying ?next, rather than dead prose.
    """
    groups: list[dict[str, object]] = []
    notes: list[dict[str, str]] = []
    first_enabled: str | None = None
    first_any: str | None = None
    for provider_value, models in PROVIDER_MODELS.items():
        provider = ConnectionProvider(provider_value)
        enabled = provider_value in enabled_values
        options: list[dict[str, str]] = [{"value": model, "label": model} for model in models]
        if options and first_any is None:
            first_any = options[0]["value"]
        if enabled and options and first_enabled is None:
            first_enabled = options[0]["value"]
        if not enabled:
            notes.append(
                {
                    "provider_value": provider_value,
                    "provider_label": _provider_label(provider),
                }
            )
        groups.append(
            {
                "provider_value": provider_value,
                "provider_label": _provider_label(provider),
                "enabled": enabled,
                "options": options,
            }
        )
    selected = selected_model
    if selected is None:
        selected = first_enabled or first_any
    return groups, selected, notes


@router.get("/new", response_class=HTMLResponse)
async def new_agent_form(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    provider: str | None = None,
    next: str | None = None,
) -> Response:
    await gc_pending_connections(db)
    connections = await _load_user_connections(db, user.id)
    enabled_values = await enabled_provider_values(db, user.id)
    requested_provider = provider.strip().lower() if provider and provider.strip() else None
    selected_model = None
    if requested_provider is not None:
        for provider_value, models in PROVIDER_MODELS.items():
            if provider_value == requested_provider and models:
                selected_model = models[0]
                break
    model_groups, selected_model, availability_notes = _build_model_picker_groups(
        enabled_values, selected_model
    )
    strategy_presets = [
        {
            "id": preset.id,
            "name": preset.name,
            "description": preset.description,
            "prompt": preset.prompt,
        }
        for preset in get_game_module(_DEFAULT_GAME).strategy_presets()
    ]
    context: dict[str, object] = {
        "user": user,
        "connections": connections,
        "model_groups": model_groups,
        "selected_model": selected_model,
        "availability_notes": availability_notes,
        # When no provider is connected yet, the form can't create anything — the
        # template shows a "connect a client first" CTA instead of a dead form.
        "has_enabled_provider": bool(enabled_values),
        "default_game": _DEFAULT_GAME,
        "default_strategy": get_game_module(_DEFAULT_GAME).default_strategy(),
        "strategy_presets": strategy_presets,
        "selected_strategy_preset": strategy_presets[0]["id"] if strategy_presets else "",
        "selected_strategy_text": (
            strategy_presets[0]["prompt"]
            if strategy_presets
            else get_game_module(_DEFAULT_GAME).default_strategy()
        ),
        "next_url": safe_internal_next(next),
    }
    return templates.TemplateResponse(request, "agents/new.html", context)


@router.post("/new")
async def create_agent_or_connection(
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    name: Annotated[str | None, Form()] = None,
    model: Annotated[str | None, Form()] = None,
    strategy_text: Annotated[str | None, Form()] = None,
    strategy_preset: Annotated[str | None, Form()] = None,
    # Aliased to the "next" form field but named to avoid shadowing the next()
    # builtin used below for the strategy-preset lookup.
    next_after: Annotated[str | None, Form(alias="next")] = None,
) -> RedirectResponse:
    if name is not None:
        clean_name = clean_agent_name(name)
        existing = (
            await db.execute(
                select(Agent).where(
                    Agent.user_id == user.id,
                    Agent.name == clean_name,
                    Agent.archived_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(status_code=409, detail="You already have an agent with that name.")

        clean_model = (model or "").strip()
        if not clean_model:
            raise HTTPException(status_code=400, detail="Model is required.")
        derived = provider_for_model(clean_model)
        if derived is None:
            raise HTTPException(status_code=400, detail="Unknown model.")
        agent_provider = ConnectionProvider(derived)
        clean_strategy = (strategy_text or "").strip()
        if not clean_strategy and strategy_preset:
            preset = next(
                (
                    item
                    for item in get_game_module(_DEFAULT_GAME).strategy_presets()
                    if item.id == strategy_preset
                ),
                None,
            )
            clean_strategy = preset.prompt if preset is not None else ""
        version_text = clean_strategy or get_game_module(_DEFAULT_GAME).default_strategy()
        agent = Agent(
            user_id=user.id,
            provider=agent_provider,
            kind=AgentKind.AI,
            name=clean_name,
            game=_DEFAULT_GAME,
            status=AgentStatus.ACTIVE,
        )
        db.add(agent)
        await db.flush()
        version = AgentVersion(
            agent_id=agent.id,
            version_no=1,
            model=clean_model,
            strategy_text=version_text,
        )
        db.add(version)
        await db.flush()
        agent.current_version_id = version.id
        await db.commit()
        # If a join hub (or other page) sent the user here with ?next, forward
        # back there now that the agent exists, instead of the agent detail page.
        destination = safe_internal_next(next_after) or f"/me/agents/{agent.id}"
        return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)

    raise HTTPException(status_code=400, detail="Agent name is required.")
