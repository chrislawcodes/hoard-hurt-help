"""A replayed seat played by a model instead of by scripted rules.

The platform asks every seat it plays for two questions each turn — what to say,
then what to do — through `choose_bot_talk_decision` and
`choose_bot_action_decision`. A model seat answers those two questions with a
model call. That is the whole integration: because the answers go back into the
shipped turn loop, a model seat gets the talk phase, the scoring, the round
awards and the deadline handling for free, and behaves like any other seat.

Everything the model is shown comes off the `BotContext` the platform already
built for that seat — recent history, the scoreboard, and this turn's talk. No
view is assembled here. A replayed seat seeing a different board than a live one
would mean every difference you read was the board rather than your change.

What a replay still is not: a live match. There is no deadline pressure, seats
answer in sequence rather than at once, and a model is reasoning about a position
it did not play into. Read a replayed decision as evidence, not as a result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

ACTIONS = {"HOARD", "HELP", "HURT"}


@dataclass
class ModelSeat:
    """One seat, answered by a model, inside the real turn loop."""

    model: str
    strategy_text: str
    call: Any  # (model, prompt) -> str; injected so this stays testable
    name: str = ""

    def __post_init__(self) -> None:
        self.name = f"model: {self.model}"

    def talk(self, context: Any) -> Any:
        from app.engine.bots.types import BotTalkDecision

        got = self._ask(
            context,
            "It is the TALK phase. Say one short line to the table — a deal, a"
            " warning, a bluff, whatever your strategy calls for.",
            '{"thinking": "why", "message": "what you say out loud"}',
        )
        return BotTalkDecision(
            intent="replay",
            truth_mode="honest",
            message=str(got.get("message", ""))[:200],
            thinking=str(got.get("thinking", ""))[:2000],
        )

    def act(self, context: Any) -> Any:
        from app.engine.bots.types import BotActionDecision

        got = self._ask(
            context,
            "It is the ACT phase. Everyone has now spoken — their lines are in"
            " current_talk_messages. Choose this turn's action.",
            '{"thinking": "why", "action": "HOARD|HELP|HURT", "target": "seat name or null"}',
        )
        action = str(got.get("action", "")).upper()
        if action not in ACTIONS:
            raise SystemExit(f"{self.name} returned a bad action: {action!r}")
        target = got.get("target") or None
        if target and target not in context.all_agent_ids:
            raise SystemExit(
                f"{self.name} aimed at {target!r}, which is not a seat in this match"
            )
        return BotActionDecision(
            intent="replay",
            move={"action": action, "target_agent_id": target},
            thinking=str(got.get("thinking", ""))[:2000],
        )

    def _ask(self, context: Any, instruction: str, shape: str) -> dict[str, Any]:
        prompt = (
            f"You are {context.your_agent_id} in a game of Hoard Hurt Help.\n\n"
            f"YOUR STRATEGY:\n{self.strategy_text}\n\n"
            f"THE POSITION:\n{json.dumps(_position(context), indent=2, default=str)}\n\n"
            f"{instruction}\nReply with ONLY a JSON object:\n{shape}"
        )
        raw = self.call(self.model, prompt)
        try:
            start, end = raw.index("{"), raw.rindex("}") + 1
            parsed = json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError) as exc:
            # Loud, never defaulted. A seat that silently becomes a HOARD is the
            # fabricated-data failure this tool exists to undo.
            raise SystemExit(
                f"{self.name} for {context.your_agent_id}: could not read a reply.\n"
                f"  error: {exc}\n  reply: {raw[:400]}"
            ) from exc
        if not isinstance(parsed, dict):
            raise SystemExit(f"{self.name}: expected a JSON object, got {type(parsed).__name__}")
        return parsed


def _position(context: Any) -> dict[str, Any]:
    """The board, straight off the context the platform built for this seat."""
    return {
        "round": context.round,
        "turn": context.turn,
        "turns_per_round": context.turns_per_round,
        "you": context.your_agent_id,
        "everyone": context.all_agent_ids,
        "scoreboard": [_plain(r) for r in context.scoreboard],
        "recent_history": [_plain(h) for h in context.history],
        "current_talk_messages": [_plain(m) for m in context.current_talk_messages],
    }


def _plain(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return obj
