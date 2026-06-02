"""Experimental spectator visualizations ("the lab").

Hosts the Robot Circle turn visualization: bots stand in a circle and walk
toward each other to enact each turn (Help / Hurt / mutual pact), with the
headline move spotlit. It reads a saved spectator-state JSON dump from
``app/data/<game_id>_state.json`` rather than the live DB, so a finished
game from production (e.g. G_0016) can be replayed locally without that game
existing in the local database.

The JSON shape is exactly what ``/api/spectator/games/{id}/state`` returns.
"""

from __future__ import annotations

import json
import re
from pathlib import Path as FsPath
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import HTMLResponse

from app.templating import templates

router = APIRouter(tags=["lab"])

_DATA_DIR = FsPath("app/data")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Default cap so the page matches the brief (G_0016 through round 4). Override
# with ?max_round=N. None/0 means "no cap".
_DEFAULT_MAX_ROUND = 4

# Stable per-action display effect (PD nominal), mirroring the move_effect the
# main viewer shows: what the move is worth, attributed to who it lands on.
_NOMINAL = {"HOARD": 2, "HELP": 4, "HURT": -4}

_MISSED_MESSAGE = "I did not submit a turn."


def _delta_for(action: str, mutual: bool) -> int:
    if action == "HELP":
        return 8 if mutual else 4
    return _NOMINAL.get(action, 0)


def _caption_for(actions: list[dict[str, Any]]) -> tuple[str, str]:
    """A one-line headline for the turn — the thing a spectator should read first."""
    betrayals = [a for a in actions if a["betrayal"]]
    mutuals = [a for a in actions if a["mutual"]]
    hurts = [a for a in actions if a["action"] == "HURT"]
    helps = [a for a in actions if a["action"] == "HELP"]
    missed = [a for a in actions if a["missed"]]

    if betrayals:
        b = betrayals[0]
        return "Betrayal", f"{b['agent']} turns on former partner {b['target']}."
    if mutuals:
        names = sorted({a["agent"] for a in mutuals})
        if len(names) >= 2:
            return "The Pact", f"{names[0]} and {names[1]} lock in a mutual pact — +8 each."
        return "The Pact", "A mutual pact forms — +8 each."
    # Gang-up: 2+ HURTs on the same target.
    targets: dict[str, int] = {}
    for a in hurts:
        if a["target"]:
            targets[a["target"]] = targets.get(a["target"], 0) + 1
    ganged = [t for t, n in targets.items() if n >= 2]
    if ganged:
        return "Gang-up", f"{targets[ganged[0]]} agents pile on {ganged[0]}."
    if hurts:
        h = hurts[0]
        return "Strike", f"{h['agent']} strikes {h['target']}."
    if helps:
        h = helps[0]
        return "Help", f"{h['agent']} helps {h['target']}."
    if missed:
        return "No-show", f"{missed[0]['agent']} missed its turn — defaulted to Hoard."
    return "Hoard", "A quiet turn — everyone banks a coin."


def _build_view(state: dict[str, Any], max_round: int | None) -> dict[str, Any]:
    """Turn a spectator-state dump into the data the circle page animates.

    Derives mutual-pact and betrayal flags the same way the main viewer does:
    a pact is a mutual HELP in one turn; a betrayal is a HURT aimed at last
    turn's pact partner.
    """
    agents = [a["agent_id"] for a in state.get("agents", [])]
    history = state.get("history", [])

    turns_out: list[dict[str, Any]] = []
    prev_mutual: set[frozenset[str]] = set()
    for t in history:
        if max_round and t["round"] > max_round:
            continue
        raw_actions = t.get("actions", [])
        messages = {m["agent_id"]: m.get("message", "") for m in t.get("messages", [])}

        helps = {
            a["agent_id"]: a.get("target_id")
            for a in raw_actions
            if a["action"] == "HELP" and a.get("target_id")
        }

        actions: list[dict[str, Any]] = []
        this_mutual: set[frozenset[str]] = set()
        for a in raw_actions:
            agent = a["agent_id"]
            action = a["action"]
            target = a.get("target_id")
            mutual = action == "HELP" and bool(target) and helps.get(target) == agent
            pair = frozenset((agent, target)) if target else frozenset()
            betrayal = action == "HURT" and bool(target) and pair in prev_mutual
            if mutual:
                this_mutual.add(pair)
            msg = messages.get(agent, "")
            actions.append(
                {
                    "agent": agent,
                    "action": action,
                    "target": target,
                    "delta": _delta_for(action, mutual),
                    "mutual": mutual,
                    "betrayal": betrayal,
                    "missed": msg.strip() == _MISSED_MESSAGE,
                    "msg": msg,
                }
            )
        prev_mutual = this_mutual

        # Spotlight: anyone in a targeted move, plus no-shows. If the whole turn
        # is Hoards, light everyone so the stage is never dead.
        spotlight: set[str] = set()
        for a in actions:
            if a["target"]:
                spotlight.add(a["agent"])
                spotlight.add(a["target"])
            if a["missed"]:
                spotlight.add(a["agent"])
        if not spotlight:
            spotlight = set(agents)

        badge, cap = _caption_for(actions)
        turns_out.append(
            {
                "round": t["round"],
                "turn": t["turn"],
                "badge": badge,
                "cap": cap,
                "spotlight": sorted(spotlight),
                "actions": actions,
            }
        )

    return {
        "agents": agents,
        "turns": turns_out,
        "max_round": max_round,
        "sample": bool(state.get("_sample")),
    }


@router.get("/games/{game_id}/circle", response_class=HTMLResponse)
async def robot_circle(
    game_id: Annotated[str, Path()],
    request: Request,
    max_round: int | None = _DEFAULT_MAX_ROUND,
) -> HTMLResponse:
    """Robot Circle visualization for a saved game, capped at ``max_round``.

    Reads ``app/data/<game_id>_state.json`` (a spectator-state dump). Returns a
    clear 404 if no saved data exists for that game.
    """
    if not _SAFE_ID.match(game_id):
        raise HTTPException(404)
    path = _DATA_DIR / f"{game_id}_state.json"
    if not path.is_file():
        raise HTTPException(
            404,
            detail=(
                f"No saved data for {game_id}. Save the JSON from "
                f"/api/spectator/games/{game_id}/state to {path} and reload."
            ),
        )
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, detail=f"Saved data for {game_id} is not valid JSON: {exc}")

    view = _build_view(state, max_round)
    # Embedded in a <script> tag: neutralize any "</script>" hiding in a chat
    # message. Replacing "</" with "<\/" keeps the JSON valid (\/ is a legal
    # escape for / inside a JSON string) and can't break out of the tag.
    data_json = json.dumps(view).replace("</", "<\\/")
    return templates.TemplateResponse(
        request,
        "lab_robot_circle.html",
        {
            "user": None,
            "is_admin": False,
            "game_id": game_id,
            "game_name": state.get("name", game_id),
            "max_round": max_round,
            "data_json": data_json,
            "sample": view["sample"],
        },
    )
