"""Who plays each seat in a replay, and how.

A replay is only interesting because a seat can play differently than it did.
This module holds the four ways a seat can be filled and nothing else; the
replay driver in replay_match.py handles turns, and the payoffs stay in the
game's own scoring module where they belong.

The four drivers, cheapest first:

  keep          replay the move that was actually recorded. Free. This is the
                default for every seat, so a replay with no overrides should
                reproduce the original match exactly — which is what makes the
                replay trustworthy enough to change something.
  always:X      play X every turn. Free. A crude control: it answers "would
                anyone have stopped a seat that just hoarded?" without a model.
  bot:STRATEGY  one of the repo's scripted bots. Free, and it reacts to the
                table, so it is a fair stand-in for a live seat when you care
                about the shape of the position rather than the prose.
  model:ID      a real model call, given the same view the live game sends.
                The only expensive one, and the only one that can answer "would
                a better model have followed this preset?".

`keep` past the end of the log is an error, not a default. A replay that
quietly invents moves would produce exactly the kind of fabricated round that
made this tool necessary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Move:
    action: str
    target: str | None = None
    thinking: str = ""
    message: str = ""


class SeatDriver(Protocol):
    """Decides one seat's move for one turn."""

    name: str

    def move(self, seat: str, view: dict[str, Any]) -> Move: ...


@dataclass
class KeepDriver:
    """Replay what this seat actually did."""

    recorded: dict[tuple[int, int], Move]
    name: str = "as recorded"

    def move(self, seat: str, view: dict[str, Any]) -> Move:
        key = (view["round"], view["turn"])
        if key not in self.recorded:
            raise SystemExit(
                f"seat {seat!r} has no recorded move for R{key[0]}T{key[1]} — the log "
                f"ends before this turn. Give it a driver (--seat '{seat}=bot:tit_for_tat'), "
                f"or replay a turn that is in the log."
            )
        return self.recorded[key]


@dataclass
class AlwaysDriver:
    """Play one action every turn, targeting the current leader when a target is needed."""

    action: str
    name: str = ""

    def __post_init__(self) -> None:
        self.action = self.action.upper()
        if self.action not in {"HOARD", "HELP", "HURT"}:
            raise SystemExit(f"always: wants HOARD, HELP or HURT, got {self.action!r}")
        self.name = f"always {self.action}"

    def move(self, seat: str, view: dict[str, Any]) -> Move:
        target = None
        if self.action in {"HELP", "HURT"}:
            others = [r["agent_id"] for r in view["standings"] if r["agent_id"] != seat]
            if not others:
                return Move("HOARD", thinking="no one else to act on")
            # Leader for HURT, trailer for HELP — a scripted seat still has to
            # pick someone, and picking at random would add noise to a control.
            target = others[0] if self.action == "HURT" else others[-1]
        return Move(self.action, target, thinking=f"scripted: {self.action} every turn")


@dataclass
class BotDriver:
    """A seat played by one of the repo's scripted bots.

    This driver decides nothing itself. The seat is seeded into the replay
    database as a real BOT agent, and the platform's own `auto_submit_bot_phase`
    fills it in after the other drivers have submitted — that function skips any
    seat that already has a submission, so the two paths compose without either
    knowing about the other. Reusing the shipped bot runtime is the point: a
    second implementation of a strategy would drift from the one the live game
    plays, and then the replay would be measuring the copy.
    """

    strategy: str
    name: str = ""

    def __post_init__(self) -> None:
        self.name = f"bot: {self.strategy}"

    def move(self, seat: str, view: dict[str, Any]) -> Move:
        raise AssertionError(
            "BotDriver.move should never be called — bot seats are filled by the "
            "platform's auto_submit_bot_phase, not by the replay loop."
        )


@dataclass
class ModelDriver:
    """A live model call, given the same view the live game sends an agent.

    The view comes from the game's own `build_turn_static_dict`, so there is one
    description of what a player can see rather than a replay-flavoured copy of
    it. What differs from a live match is real and worth stating: no deadline
    pressure, no other agents moving concurrently, and the model is answering a
    reconstructed position rather than one it played into. Treat a model seat as
    evidence about a decision, not as a match result.
    """

    model: str
    strategy_text: str
    call: Any  # (model, prompt) -> str; injected so the loop stays testable
    name: str = ""

    def __post_init__(self) -> None:
        self.name = f"model: {self.model}"

    def move(self, seat: str, view: dict[str, Any]) -> Move:
        import json

        prompt = (
            f"You are {seat} in a game of Hoard Hurt Help.\n\n"
            f"YOUR STRATEGY:\n{self.strategy_text}\n\n"
            f"THE RULES AND THE CURRENT POSITION:\n{json.dumps(view, indent=2)}\n\n"
            "Choose this turn's action. Reply with ONLY a JSON object:\n"
            '{"thinking": "why", "action": "HOARD|HELP|HURT", "target": "seat name or null"}'
        )
        raw = self.call(self.model, prompt)
        try:
            start, end = raw.index("{"), raw.rindex("}") + 1
            got = json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError) as exc:
            # Loud, not defaulted. A model seat that silently becomes a HOARD is
            # the same fabricated-data failure the tool exists to undo.
            raise SystemExit(
                f"seat {seat!r} on {self.model}: could not read a move from the reply.\n"
                f"  error: {exc}\n  reply: {raw[:400]}"
            ) from exc
        action = str(got.get("action", "")).upper()
        if action not in {"HOARD", "HELP", "HURT"}:
            raise SystemExit(f"seat {seat!r} on {self.model}: bad action {action!r}")
        target = got.get("target") or None
        return Move(action, target, thinking=str(got.get("thinking", "")))


def build_driver(spec: dict[str, str], *, recorded, strategy_text, call) -> SeatDriver:
    """Turn one parsed --seat override into the driver it names."""
    (kind, value), = spec.items()
    if kind == "always":
        return AlwaysDriver(value)
    if kind == "bot":
        return BotDriver(value)
    if kind == "model":
        return ModelDriver(value, strategy_text, call)
    raise SystemExit(f"unknown seat driver {kind!r}")
