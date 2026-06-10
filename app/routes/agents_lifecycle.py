"""Agent rename, pause/resume, delete, and version-edit actions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from starlette.responses import Response

from app.config import PROVIDER_MODELS, provider_for_model
from app.deps import DbSession, require_user_with_handle
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import ConnectionProvider
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player
from app.models.user import User
from app.templating import templates

router = APIRouter()


def _sync_agent_provider(agent: Agent, model: str) -> None:
    """Keep ``agent.provider`` in sync with its model on every model change.

    claude/gemini/openai map to a provider uniquely; a freeform model
    (hermes/openclaw) maps to nothing and leaves the stored provider as-is.
    Without this, editing an agent to a cross-provider model would leave the
    stored provider stale and route the agent to the wrong provider.
    """
    derived = provider_for_model(model)
    if derived is not None:
        agent.provider = ConnectionProvider(derived)


async def _load_owned_agent(db: DbSession, user: User, agent_id: int) -> Agent:
    agent = (
        await db.execute(
            select(Agent).where(
                Agent.id == agent_id,
                Agent.user_id == user.id,
                Agent.kind == AgentKind.AI,
            )
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return agent


async def _load_current_version(db: DbSession, agent: Agent) -> AgentVersion:
    version = (
        await db.execute(
            select(AgentVersion).where(AgentVersion.id == agent.current_version_id)
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=400, detail="Agent has no current version.")
    return version


async def _version_has_active_match(db: DbSession, version_id: int) -> bool:
    """True if this version is seated in any active match (rated OR practice)."""
    row = (
        await db.execute(
            select(Player.id)
            .join(Match, Match.id == Player.match_id)
            .where(
                Player.agent_version_id == version_id,
                Player.left_at.is_(None),
                Match.state == GameState.ACTIVE,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def _agent_has_active_match(db: DbSession, agent_id: int) -> bool:
    """True if any seat of this agent is in an active match (rated OR practice)."""
    row = (
        await db.execute(
            select(Player.id)
            .join(Match, Match.id == Player.match_id)
            .where(
                Player.agent_id == agent_id,
                Player.left_at.is_(None),
                Match.state == GameState.ACTIVE,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def _version_has_rated_history(db: DbSession, version_id: int) -> bool:
    row = (
        await db.execute(
            select(Player.id)
            .join(Match, Match.id == Player.match_id)
            .where(
                Player.agent_version_id == version_id,
                Match.match_kind != MatchKind.PRACTICE_ARENA.value,
            )
            .limit(1)
        )
    ).first()
    return row is not None


async def _fork_version(
    db: DbSession,
    *,
    agent: Agent,
    model: str,
    strategy_text: str,
) -> AgentVersion:
    next_version_no = (
        await db.scalar(
            select(func.coalesce(func.max(AgentVersion.version_no), 0)).where(
                AgentVersion.agent_id == agent.id
            )
        )
    ) or 0
    version = AgentVersion(
        agent_id=agent.id,
        version_no=int(next_version_no) + 1,
        model=model,
        strategy_text=strategy_text,
        frozen_at=None,
    )
    db.add(version)
    await db.flush()
    agent.current_version_id = version.id
    return version


async def _apply_version_edit(
    db: DbSession,
    *,
    agent: Agent,
    model: str | None = None,
    strategy_text: str | None = None,
) -> AgentVersion:
    current = await _load_current_version(db, agent)
    if await _version_has_active_match(db, current.id):
        raise HTTPException(status_code=409, detail="That version is mid-match and locked.")
    if model is not None:
        _sync_agent_provider(agent, model)
    current_has_rated_history = await _version_has_rated_history(db, current.id)
    if not current_has_rated_history and current.frozen_at is None:
        if model is not None:
            current.model = model
        if strategy_text is not None:
            current.strategy_text = strategy_text
        return current
    if current.frozen_at is None and current_has_rated_history:
        current.frozen_at = datetime.now(timezone.utc)
    return await _fork_version(
        db,
        agent=agent,
        model=model or current.model,
        strategy_text=strategy_text or current.strategy_text,
    )


@router.post("/{agent_id}/rename")
async def rename_agent(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    name: Annotated[str, Form()],
) -> RedirectResponse:
    agent = await _load_owned_agent(db, user, agent_id)
    clean_name = name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Agent name is required.")
    clash = (
        await db.execute(
            select(Agent).where(
                Agent.user_id == user.id,
                Agent.name == clean_name,
                Agent.id != agent.id,
                Agent.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(status_code=409, detail="You already have an agent with that name.")
    agent.name = clean_name
    await db.commit()
    return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{agent_id}/pause")
async def pause_agent(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    agent = await _load_owned_agent(db, user, agent_id)
    agent.status = AgentStatus.PAUSED
    await db.commit()
    return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{agent_id}/resume")
async def resume_agent(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    agent = await _load_owned_agent(db, user, agent_id)
    # Agents are no longer attached to a connection — resume just makes it
    # ACTIVE. Whether it can actually play depends on provider coverage, shown
    # as the "no live connection runs <provider>" warning, not a hard block.
    agent.status = AgentStatus.ACTIVE
    await db.commit()
    return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{agent_id}/delete")
async def delete_agent(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    agent = await _load_owned_agent(db, user, agent_id)
    if await _agent_has_active_match(db, agent.id):
        raise HTTPException(
            status_code=409,
            detail="That agent is in an active match — wait for it to finish before deleting.",
        )
    has_history = (
        await db.execute(select(Player.id).where(Player.agent_id == agent.id).limit(1))
    ).first() is not None
    if has_history:
        agent.archived_at = datetime.now(timezone.utc)
        agent.status = AgentStatus.PAUSED
        agent.connection_id = None
    else:
        await db.delete(agent)
    await db.commit()
    return RedirectResponse(url="/me/agents", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{agent_id}/set-model")
async def set_model(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    model: Annotated[str, Form()],
) -> RedirectResponse:
    agent = await _load_owned_agent(db, user, agent_id)
    current = await _load_current_version(db, agent)
    clean_model = model.strip()
    if not clean_model:
        raise HTTPException(status_code=400, detail="Model is required.")
    # The model→provider derivation (_apply_version_edit) sets the agent's
    # provider from the chosen model; no connection-based allowlist check.
    current_model = current.model
    if clean_model == current_model:
        return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)
    await _apply_version_edit(db, agent=agent, model=clean_model)
    await db.commit()
    return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{agent_id}/set-strategy")
async def set_strategy(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    strategy_text: Annotated[str, Form()],
) -> RedirectResponse:
    agent = await _load_owned_agent(db, user, agent_id)
    clean_strategy = strategy_text.strip()
    if not clean_strategy:
        raise HTTPException(status_code=400, detail="Strategy text is required.")
    current = await _load_current_version(db, agent)
    if clean_strategy == current.strategy_text.strip():
        return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)
    await _apply_version_edit(db, agent=agent, strategy_text=clean_strategy)
    await db.commit()
    return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{agent_id}/edit", response_class=HTMLResponse)
async def edit_agent_version_page(
    agent_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    agent = (
        await db.execute(
            select(Agent).where(
                Agent.id == agent_id,
                Agent.user_id == user.id,
                Agent.kind == AgentKind.AI,
                Agent.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    version = (
        await db.execute(
            select(AgentVersion).where(AgentVersion.id == agent.current_version_id)
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=400, detail="Agent has no current version.")
    # Model choices come from the agent's stored provider (not a connection).
    provider_models = (
        PROVIDER_MODELS.get(agent.provider.value, []) if agent.provider else []
    )
    max_version_no = await db.scalar(
        select(func.max(AgentVersion.version_no)).where(AgentVersion.agent_id == agent.id)
    )
    current_has_rated_history = await _version_has_rated_history(db, version.id)
    will_fork = current_has_rated_history or version.frozen_at is not None
    next_version_no = int(max_version_no or 0) + 1 if will_fork else int(max_version_no or 1)
    return templates.TemplateResponse(
        request,
        "agents/edit_version.html",
        {
            "user": user,
            "agent": agent,
            "version": version,
            "provider_models": provider_models,
            "next_version_no": next_version_no,
            "will_fork": will_fork,
        },
    )


@router.post("/{agent_id}/save-version")
async def save_version(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    model: Annotated[str, Form()],
    strategy_text: Annotated[str, Form()],
) -> RedirectResponse:
    agent = await _load_owned_agent(db, user, agent_id)
    clean_model = model.strip()
    if not clean_model:
        raise HTTPException(status_code=400, detail="Model is required.")
    clean_strategy = strategy_text.strip()
    if not clean_strategy:
        raise HTTPException(status_code=400, detail="Strategy text is required.")
    current = await _load_current_version(db, agent)
    if clean_model == current.model and clean_strategy == current.strategy_text.strip():
        return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)
    await _apply_version_edit(db, agent=agent, model=clean_model, strategy_text=clean_strategy)
    await db.commit()
    return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{agent_id}/restore-version/{version_id}")
async def restore_version(
    agent_id: Annotated[int, Path()],
    version_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    agent = await _load_owned_agent(db, user, agent_id)
    version = (
        await db.execute(
            select(AgentVersion).where(
                AgentVersion.id == version_id,
                AgentVersion.agent_id == agent.id,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    if version.id == agent.current_version_id:
        return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)
    agent.current_version_id = version.id
    # Restoring an older version may change the model → re-derive the provider.
    _sync_agent_provider(agent, version.model)
    await db.commit()
    return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)
