"""Liar's Dice viewer presentation.

The platform viewer route loads the generic skeleton (players, scoreboard,
timeline, messages) and asks each game module to build its own display payload
via ``build_replay_view``. Liar's Dice has no PD pact/betrayal narrative — its
fragments (``fragments/liars_dice_live_region.html`` /
``fragments/liars_dice_turn.html``) render the bid/challenge log and the public
dice/showdown state, so this builder just turns the timeline into the simple
per-turn ``history`` those fragments consume, plus the ``rc_data`` script blob
the live region carries.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.match import Match
    from app.models.player import Player
    from app.read_models.matches import TimelineTurn


def _build_rc_data(
    scoreboard: list[dict[str, Any]],
    history: list[dict[str, Any]],
    viewer_seat: str | None = None,
) -> str:
    """Serialize Liar's Dice history as the live-region replay JSON blob.

    The robot-circle animation is not rendered for Liar's Dice, but the live
    region still embeds a ``#rc-data-live`` blob (the page's generic refresh
    path reads it); this keeps that blob present and well-formed without any PD
    pact/betrayal concepts.
    """
    agents = [r["agent_id"] for r in scoreboard]
    labels = {r["agent_id"]: r.get("display_name") or r["agent_id"] for r in scoreboard}
    bots = {r["agent_id"]: True for r in scoreboard if r.get("is_bot")}
    owners = {r["agent_id"]: r["owner_handle"] for r in scoreboard if r.get("owner_handle")}

    turns = []
    for h in history:
        rc_actions = [
            {
                "agent": a["agent_id"],
                "action": a["action"],
                "target": a["target_id"],
                "missed": a["was_defaulted"],
                "msg": (a.get("message") or "").strip(),
            }
            for a in h["actions"]
        ]
        talk = [
            {"agent": m["agent_id"], "text": m["text"].strip()}
            for m in h["messages"]
            if m["text"].strip()
        ]
        turns.append(
            {
                "round": h["round"],
                "turn": h["turn"],
                "actions": rc_actions,
                "talk": talk,
            }
        )

    payload: dict[str, object] = {
        "agents": agents,
        "labels": labels,
        "bots": bots,
        "owners": owners,
        "turns": turns,
        "max_round": max((t["round"] for t in turns), default=0),
        "sample": False,
    }
    if viewer_seat is not None:
        payload["viewer_seat"] = viewer_seat
    return json.dumps(payload, ensure_ascii=False)


async def build_liars_dice_replay_view(
    db: AsyncSession,
    match: Match,
    players: list[Player],
    scoreboard: list[dict[str, Any]],
    timeline: list[TimelineTurn],
    viewer_seat: str | None,
) -> dict[str, Any]:
    """Build Liar's Dice's display payload: the per-turn ``history`` and ``rc_data``.

    Each history turn carries the bid/challenge fields the LD turn fragment
    renders (action, quantity, face, target, message, missed) and the talk-phase
    messages, with no PD scoring or pact/betrayal tagging.
    """
    history: list[dict[str, Any]] = []
    for seq, t in enumerate(timeline, start=1):
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
        actions: list[dict[str, Any]] = []
        for action in t.actions:
            paired = messages_by_agent.get(action.agent_id)
            actions.append(
                {
                    "agent_id": action.agent_id,
                    "action": action.action,
                    "target_id": action.target_id,
                    "quantity": action.quantity,
                    "face": action.face,
                    "thinking": action.thinking,
                    "was_defaulted": action.was_defaulted,
                    "message": paired["text"] if paired is not None else "",
                }
            )
        history.append(
            {
                "seq": seq,
                "round": t.round,
                "turn": t.turn,
                "messages": messages,
                "actions": actions,
            }
        )

    return {
        "history": history,
        "rc_data": _build_rc_data(scoreboard, history, viewer_seat),
    }
