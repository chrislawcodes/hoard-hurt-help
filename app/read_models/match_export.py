"""Shared match-export builders for the admin and game-admin APIs.

Both API modules expose byte-identical CSV and JSON exports for a match. The
auth and match-loading differ per module, so each route keeps those and calls
the builders here to produce the response. Keep the output (columns, CSV bytes,
JSON shape) stable — the two routes must stay byte-identical.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    "points_delta",
    "round_score_after",
    "submitted_at",
    "was_defaulted",
]


async def gather_export_rows(db: AsyncSession, match_id: str) -> list[dict[str, Any]]:
    """Flatten a match timeline into one row per submitted action."""

    rows: list[dict[str, Any]] = []
    for turn in await load_match_timeline(db, match_id, resolved_only=False):
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
                    "points_delta": action.points_delta,
                    "round_score_after": action.round_score_after,
                    "submitted_at": action.submitted_at.isoformat()
                    if action.submitted_at
                    else "",
                    "was_defaulted": action.was_defaulted,
                }
            )
    return rows


async def build_csv_export(db: AsyncSession, match_id: str) -> StreamingResponse:
    """Build the CSV export response for a loaded match."""

    rows = await gather_export_rows(db, match_id)
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


async def build_json_export(db: AsyncSession, match: Match) -> StreamingResponse:
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
        players_payload.append(
            {
                "agent_id": p.agent_id,
                "model_self_report": p.played_provider,
                "total_round_wins": p.total_round_wins,
                "total_round_score": p.total_round_score,
                "strategy_prompt": version.strategy_text if version else None,
            }
        )
    rows = await gather_export_rows(db, match_id)
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
