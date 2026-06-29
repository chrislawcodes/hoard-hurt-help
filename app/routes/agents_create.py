"""The `/me/agents/new` create flow — name and strategy a new agent.

Owns name validation, the GET form, and the POST that creates the Agent + its
first AgentVersion. Agents are decoupled from any AI model/provider — an agent
is just a name + a strategy — so there is no model picker here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import String, select
from starlette.responses import Response

from app.deps import DbSession, require_user_with_handle
from app.engine.pending_connection_gc import gc_pending_connections
from app.game_types import DEFAULT_GAME_TYPE
from app.games import get as get_game_module, known_types
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.user import User
from app.routes.web_support import safe_internal_next
from app.templating import templates

router = APIRouter()

_DEFAULT_GAME = known_types()[0] if known_types() else DEFAULT_GAME_TYPE

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


async def _load_existing_strategies(db: DbSession, user_id: int) -> list[dict[str, str]]:
    """Current strategy text of the user's other agents, for the "start from an
    existing agent" picker. Lets a user reuse a strategy they already wrote
    instead of retyping it. Purely a client-side fill — nothing here is stored.
    """
    rows = await db.execute(
        select(Agent.name, AgentVersion.strategy_text)
        .join(AgentVersion, AgentVersion.id == Agent.current_version_id)
        .where(
            Agent.user_id == user_id,
            Agent.kind == AgentKind.AI,
            Agent.archived_at.is_(None),
        )
        .order_by(Agent.name)
    )
    return [{"name": name, "strategy": strategy} for name, strategy in rows.all()]


@router.get("/new", response_class=HTMLResponse)
async def new_agent_form(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    next: str | None = None,
) -> Response:
    # Agents are decoupled from any AI model/provider: an agent is just a name +
    # a strategy. Whatever AI client the user connects plays it, so there is no
    # model to pick here.
    await gc_pending_connections(db)
    existing_strategies = await _load_existing_strategies(db, user.id)
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
        "existing_strategies": existing_strategies,
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
        # No provider/model: the agent is name + strategy. Whatever AI the user
        # has connected plays it.
        agent = Agent(
            user_id=user.id,
            provider=None,
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
            model=None,
            strategy_text=version_text,
        )
        db.add(version)
        await db.flush()
        agent.current_version_id = version.id
        await db.commit()
        # After creating an agent, go to wherever the user came from, else the
        # lobby. We deliberately do NOT route to /me/connections: an agent no
        # longer needs a provider set up to exist, and joining a game from the
        # lobby already walks the user through connecting an AI if they haven't yet.
        next_url = safe_internal_next(next_after)
        destination = next_url or f"/games/{agent.game}"
        return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)

    raise HTTPException(status_code=400, detail="Agent name is required.")
