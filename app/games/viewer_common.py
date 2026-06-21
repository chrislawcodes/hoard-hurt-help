"""Shared viewer/replay payload shaping for game modules.

Every game module builds its own ``build_replay_view`` payload, but two pieces
of that shaping are genuinely identical across games and were copy-pasted into
each game's ``viewer.py``:

1. The robot-circle / live-region (``rc_data``) envelope: pulling the
   agents/labels/bots/owners maps off the scoreboard, the per-turn ``talk`` list,
   and the ``payload``/``max_round``/``sample``/``viewer_seat`` envelope that is
   serialized to JSON. A game enriches this with its own extra maps (PD adds a
   ``providers`` map) and its own per-turn body (badge/cap/spotlight/win_probs for
   PD, just actions/talk for Liar's Dice).

2. The per-turn talk-message projection: the ``messages`` list
   (``agent_id``/``text``/``thinking``/``was_defaulted``) and the
   ``messages_by_agent`` index a game uses to pair a talk message onto an action.

These helpers hold only the byte-identical parts; each game keeps its own action
shaping and narrative on top. The replay viewer depends on the exact JSON shape,
so the envelope helper preserves key order (extra maps slot in right after
``owners``, matching the order PD's payload has always used).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.read_models.matches import TimelineTurn


def rc_scoreboard_maps(
    scoreboard: list[dict[str, Any]],
) -> tuple[list[str], dict[str, str], dict[str, bool], dict[str, str]]:
    """The shared scoreboard-derived maps every rc_data blob carries.

    Returns ``(agents, labels, bots, owners)``:
      - ``agents``: seat ids in scoreboard order.
      - ``labels``: seat id → display name (falls back to the seat id).
      - ``bots``: seat id → True, only for bot seats.
      - ``owners``: seat id → owner handle, only for seats with a handle (bots and
        handle-less owners are omitted) — for the standings rail's "by @handle" line.
    """
    agents = [r["agent_id"] for r in scoreboard]
    labels = {r["agent_id"]: r.get("display_name") or r["agent_id"] for r in scoreboard}
    bots = {r["agent_id"]: True for r in scoreboard if r.get("is_bot")}
    owners = {r["agent_id"]: r["owner_handle"] for r in scoreboard if r.get("owner_handle")}
    return agents, labels, bots, owners


def rc_talk(history_turn: dict[str, Any]) -> list[dict[str, str]]:
    """The talk-phase entries for one rc_data turn: ``{agent, text}`` for each
    non-empty public message, in message order."""
    return [
        {"agent": m["agent_id"], "text": m["text"].strip()}
        for m in history_turn["messages"]
        if m["text"].strip()
    ]


def rc_envelope(
    *,
    agents: list[str],
    labels: dict[str, str],
    bots: dict[str, bool],
    owners: dict[str, str],
    turns: list[dict[str, Any]],
    viewer_seat: str | None,
    extra_maps: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Assemble and serialize the rc_data JSON envelope shared by every game.

    The key order is ``agents, labels, bots, owners, [extra maps…], turns,
    max_round, sample, [viewer_seat]``. ``extra_maps`` (e.g. PD's ``providers``)
    slots in right after ``owners`` so the serialized JSON matches each game's
    historical byte shape. ``viewer_seat`` is only emitted when not ``None``.
    """
    payload: dict[str, object] = {
        "agents": agents,
        "labels": labels,
        "bots": bots,
        "owners": owners,
    }
    if extra_maps:
        payload.update(extra_maps)
    payload["turns"] = turns
    payload["max_round"] = max((t["round"] for t in turns), default=0)
    payload["sample"] = False
    if viewer_seat is not None:
        payload["viewer_seat"] = viewer_seat
    return json.dumps(payload, ensure_ascii=False)


def project_turn_messages(
    t: TimelineTurn,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """The shared per-turn talk-message projection used to build ``history``.

    Returns ``(messages, messages_by_agent)``:
      - ``messages``: one ``{agent_id, text, thinking, was_defaulted}`` dict per
        talk message on this turn, in submission order.
      - ``messages_by_agent``: seat id → its message dict, so a game can pair a
        talk message onto the seat's action.

    Each game keeps its own action shaping (and any narrative) on top of these.
    """
    messages: list[dict[str, Any]] = [
        {
            "agent_id": message.agent_id,
            "text": message.text,
            "thinking": message.thinking,
            "was_defaulted": message.was_defaulted,
        }
        for message in t.messages
    ]
    messages_by_agent = {m["agent_id"]: m for m in messages}
    return messages, messages_by_agent
