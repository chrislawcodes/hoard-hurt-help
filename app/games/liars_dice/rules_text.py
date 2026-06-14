"""Rules text for Liar's Dice."""

from __future__ import annotations


def make_rules_text(
    *,
    wild_ones: bool,
    dice_per_player: int,
    min_players: int,
    max_players: int,
) -> str:
    wild_line = "Wild ones are ON for this match." if wild_ones else "Wild ones are OFF for this match."
    return (
        "Liar's Dice.\n\n"
        f"{wild_line}\n"
        f"Table size: {min_players}–{max_players} players.\n"
        f"Each player starts with {dice_per_player} dice.\n"
        "On your turn submit exactly one move:\n"
        '  {"type":"BID","quantity":N,"face":F} or {"type":"CHALLENGE"}.\n'
        "A bid must strictly raise the standing bid. A challenge ends the hand.\n"
        "1s are wild when wild ones are on.\n"
    )
