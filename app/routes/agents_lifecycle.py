"""Agent rename, pause/resume, delete, and version-edit actions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from starlette.responses import Response

from app.config import PROVIDER_MODELS
from app.deps import DbSession, require_user_with_handle
from app.models.agent import Agent, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.match import Match, MatchKind
from app.models.player import Player
from app.models.user import User
from app.read_models.matches import agent_has_active_match, version_has_active_match
from app.routes.agents_queries import load_owned_agent
from app.routes.agents_setup import clean_agent_name
from app.templating import templates

router = APIRouter()


async def _load_current_version(db: DbSession, agent: Agent) -> AgentVersion:
    version = (
        await db.execute(
            select(AgentVersion).where(AgentVersion.id == agent.current_version_id)
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=400, detail="Agent has no current version.")
    return version


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
    strategy_text: str,
    note: str | None,
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
        model=None,
        strategy_text=strategy_text,
        note=note,
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
    strategy_text: str,
    note: str | None = None,
) -> AgentVersion:
    """Apply a strategy edit, forking a new version if the current one is frozen
    or has rated history. Agents are just name + strategy now — there is no model
    to edit. *note* is the owner's "what changed" label: an in-place draft edit
    overwrites it, a fork sets it on the new version."""
    current = await _load_current_version(db, agent)
    if await version_has_active_match(db, current.id):
        raise HTTPException(status_code=409, detail="That version is mid-match and locked.")
    current_has_rated_history = await _version_has_rated_history(db, current.id)
    if not current_has_rated_history and current.frozen_at is None:
        current.strategy_text = strategy_text
        current.note = note
        return current
    if current.frozen_at is None and current_has_rated_history:
        current.frozen_at = datetime.now(timezone.utc)
    return await _fork_version(db, agent=agent, strategy_text=strategy_text, note=note)


@router.post("/{agent_id}/rename")
async def rename_agent(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    name: Annotated[str, Form()],
) -> RedirectResponse:
    agent = await load_owned_agent(db, user, agent_id)
    clean_name = clean_agent_name(name)
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
    agent = await load_owned_agent(db, user, agent_id)
    agent.status = AgentStatus.PAUSED
    await db.commit()
    return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{agent_id}/resume")
async def resume_agent(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    agent = await load_owned_agent(db, user, agent_id)
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
    agent = await load_owned_agent(db, user, agent_id)
    if await agent_has_active_match(db, agent.id):
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
    else:
        # No game history, so no Player rows reference this agent or its
        # versions. Hard-delete, but first break the agent -> current_version
        # pointer and drop the versions, or their FK back to agents.id blocks
        # the delete (Postgres enforces this; SQLite in tests does not).
        agent.current_version_id = None
        await db.flush()
        await db.execute(
            delete(AgentVersion).where(AgentVersion.agent_id == agent.id)
        )
        await db.delete(agent)
    await db.commit()
    return RedirectResponse(url="/me/agents", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{agent_id}/set-strategy")
async def set_strategy(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    strategy_text: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Alias for ``save-version`` — same handler body, kept so the POST surface
    doesn't change. The live edit form posts to ``save-version``; this delegates
    so the two routes can't drift."""
    return await save_version(
        agent_id=agent_id, db=db, user=user, strategy_text=strategy_text, note=note
    )


# Every model the picker may set — the union of the provider allowlists. An empty
# submission clears the preference (back to the provider default).
_ALL_PROVIDER_MODELS = {model for models in PROVIDER_MODELS.values() for model in models}


@router.post("/{agent_id}/set-model")
async def set_model(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    preferred_model: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Set or clear an agent's advanced preferred model. Machine connections only;
    MCP ignores it. Stored on the Agent (mutable), not a new version."""
    agent = await load_owned_agent(db, user, agent_id)
    clean = preferred_model.strip()
    if clean and clean not in _ALL_PROVIDER_MODELS:
        raise HTTPException(status_code=400, detail="Unknown model.")
    agent.preferred_model = clean or None
    await db.commit()
    return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{agent_id}/edit", response_class=HTMLResponse)
async def edit_agent_version_page(
    agent_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> Response:
    agent = await load_owned_agent(db, user, agent_id)
    version = (
        await db.execute(
            select(AgentVersion).where(AgentVersion.id == agent.current_version_id)
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=400, detail="Agent has no current version.")
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
            "next_version_no": next_version_no,
            "will_fork": will_fork,
            # Saving would 409 while the version is mid-match; render the locked
            # notice instead of a form that can only fail.
            "version_playing_now": await version_has_active_match(db, version.id),
        },
    )


@router.post("/{agent_id}/save-version")
async def save_version(
    agent_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
    strategy_text: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    agent = await load_owned_agent(db, user, agent_id)
    clean_strategy = strategy_text.strip()
    if not clean_strategy:
        raise HTTPException(status_code=400, detail="Strategy text is required.")
    clean_note = note.strip() or None
    if clean_note is not None and len(clean_note) > 140:
        raise HTTPException(status_code=400, detail="Note must be 140 characters or fewer.")
    current = await _load_current_version(db, agent)
    if clean_strategy == current.strategy_text.strip():
        return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)
    await _apply_version_edit(db, agent=agent, strategy_text=clean_strategy, note=clean_note)
    await db.commit()
    return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{agent_id}/restore-version/{version_id}")
async def restore_version(
    agent_id: Annotated[int, Path()],
    version_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user_with_handle)],
) -> RedirectResponse:
    agent = await load_owned_agent(db, user, agent_id)
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
    await db.commit()
    return RedirectResponse(url=f"/me/agents/{agent.id}", status_code=status.HTTP_303_SEE_OTHER)
