"""Shared match-export builders for the admin and game-scoped APIs.

Both API modules expose CSV and JSON exports for a match. The auth and
match-loading differ per module, so each route keeps those and calls the
builders here to produce the response.

The payload depends on **who is asking**, expressed by the ``ExportViewer``
passed to every builder:

* ``strategy_prompt`` is real only for a seat the viewer owns; every other
  seat reads ``null``. A platform admin sees them all. Strategy text is private
  to its owner everywhere else in the app, and the export is reachable by any
  signed-in player.
* ``thinking`` is real only for a seat the viewer owns; every other seat reads
  ``null``. The agent prompt promises "Opponents never see it", so it is gated
  exactly like ``strategy_prompt`` rather than shipped with the public columns.
  It is exported at all because a preset's per-turn reasoning is the only way to
  tell "the model could not do what it was told" from "the model decided not
  to" — Headhunter attacked 57% against an instruction to attack every turn, and
  the reason was only visible by reading its own words.
* A non-admin sees **resolved turns only**. Without that filter an opponent in
  a live match could read every rival's chosen action, target and message
  between the act deadline and the resolve.

A platform admin's export is therefore the only one that is unchanged from
before this became player-reachable. ``viewer`` is keyword-only with no default
on purpose: a default would silently redact an admin's export, and there is no
safe value to guess.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.model_provider_match import resolve_seat_model
from app.models.agent import Agent
from app.models.agent_version import AgentVersion
from app.models.match import Match
from app.models.player import Player
from app.read_models.matches import load_match_timeline

EXPORT_COLUMNS = [
    "match_id",
    "round",
    "turn",
    "agent_id",
    "action",
    "target_id",
    "message",
    "thinking",
    "points_delta",
    "round_score_after",
    "submitted_at",
    "was_defaulted",
]


@dataclass(frozen=True)
class ExportViewer:
    """Who is asking for an export, and therefore how much they may see.

    A value object rather than two loose booleans so a call site cannot swap
    the arguments by accident.
    """

    user_id: int | None
    is_platform_admin: bool

    @property
    def sees_unresolved_turns(self) -> bool:
        """Only an admin sees the turn still in flight."""
        return self.is_platform_admin

    def may_read_private_seat_text(self, seat_user_id: int) -> bool:
        """Whether this viewer may read a seat's private text.

        Covers both the seat's strategy prompt and its per-turn ``thinking``.
        One gate rather than two so the two private fields cannot drift apart.
        """
        return self.is_platform_admin or seat_user_id == self.user_id


async def gather_export_rows(
    db: AsyncSession, match_id: str, *, viewer: ExportViewer
) -> list[dict[str, Any]]:
    """Flatten a match timeline into one row per submitted action."""

    rows: list[dict[str, Any]] = []
    timeline = await load_match_timeline(
        db, match_id, resolved_only=not viewer.sees_unresolved_turns
    )
    # Which seats may show their thinking. Keyed by the agent_id string the
    # timeline uses, so the lookup below cannot silently miss and leak.
    owner_of_seat = {
        seat_agent_id: user_id
        for seat_agent_id, user_id in (
            await db.execute(
                select(Player.seat_name, Player.user_id).where(
                    Player.match_id == match_id
                )
            )
        ).all()
    }
    for turn in timeline:
        for action in turn.actions:
            rows.append(
                {
                    "match_id": match_id,
                    "round": turn.round,
                    "turn": turn.turn,
                    "agent_id": action.agent_id,
                    "action": action.action,
                    "target_id": action.target_id or "",
                    "message": action.message,
                    # Private, like strategy_prompt: only the seat's owner (or an
                    # admin) reads it. An unknown seat falls through to None
                    # rather than defaulting to visible.
                    "thinking": (
                        action.thinking
                        if viewer.may_read_private_seat_text(
                            owner_of_seat.get(action.agent_id, -1)
                        )
                        else None
                    ),
                    "points_delta": action.points_delta,
                    "round_score_after": action.round_score_after,
                    "submitted_at": action.submitted_at.isoformat()
                    if action.submitted_at
                    else "",
                    "was_defaulted": action.was_defaulted,
                }
            )
    return rows


async def build_csv_export(
    db: AsyncSession, match_id: str, *, viewer: ExportViewer
) -> StreamingResponse:
    """Build the CSV export response for a loaded match.

    The columns never carried strategy text, so the only viewer-dependent part
    is which turns are included.
    """

    rows = await gather_export_rows(db, match_id, viewer=viewer)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(EXPORT_COLUMNS)
    for r in rows:
        w.writerow([r[k] for k in EXPORT_COLUMNS])
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{match_id}.csv"'},
    )


async def build_json_export(
    db: AsyncSession, match: Match, *, viewer: ExportViewer
) -> StreamingResponse:
    """Build the JSON export response for a loaded match."""

    match_id = match.id
    players = (
        (await db.execute(select(Player).where(Player.match_id == match_id))).scalars().all()
    )
    players_payload: list[dict[str, Any]] = []
    for p in players:
        version = None
        if p.agent_version_id is not None:
            version = (
                await db.execute(
                    select(AgentVersion).where(AgentVersion.id == p.agent_version_id)
                )
            ).scalar_one_or_none()
        # A bot seat carries no agent_version_id, so `version` is already None
        # and needs no special case here.
        strategy_prompt: str | None = None
        if version is not None and viewer.may_read_private_seat_text(p.user_id):
            strategy_prompt = version.strategy_text
        # Which model this seat plays, from `resolve_seat_model` — the SAME
        # function that fills the model field in the turn payload. Reporting the
        # raw `preferred_model` instead was a second answer to one question: the
        # payload layers in the provider's default when an agent has no
        # preference, so a seat with none showed as null here while really
        # playing the default. Anything that needs to know which model a seat
        # plays calls that function; nothing works it out again.
        agent = (
            await db.execute(select(Agent).where(Agent.id == p.agent_id))
        ).scalar_one_or_none()
        model = (
            resolve_seat_model(p.chosen_provider, agent.preferred_model)
            if agent is not None
            else None
        )
        players_payload.append(
            {
                "agent_id": p.agent_id,
                "model_self_report": p.played_provider,
                "model": model,
                "total_round_wins": p.total_round_wins,
                "total_round_score": p.total_round_score,
                "strategy_prompt": strategy_prompt,
            }
        )
    rows = await gather_export_rows(db, match_id, viewer=viewer)
    payload = {
        "game": {
            "id": match.id,
            "name": match.name,
            "state": match.state.value,
            "scheduled_start": match.scheduled_start.isoformat()
            if match.scheduled_start
            else None,
            "started_at": match.started_at.isoformat() if match.started_at else None,
            "completed_at": match.completed_at.isoformat() if match.completed_at else None,
            "rules_version": match.rules_version,
        },
        "players": players_payload,
        "submissions": rows,
    }
    return StreamingResponse(
        iter([json.dumps(payload, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{match_id}.json"'},
    )
