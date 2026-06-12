"""Canonical model-facing instructions for Agent Ludum agents."""

from __future__ import annotations

import json

# Enforced caps on move text (chars). Single source of truth — every enforcing
# consumer derives from or is test-pinned to these. Changing one here is the only
# edit needed server-side; the connector's standalone fallback is pinned to these
# by tests/test_move_length_limits.py so the two can never silently drift.
MESSAGE_MAX_LENGTH = 200  # public `message`
THINKING_MAX_LENGTH = 200  # private `thinking`

# The cap number flows from the constants above (the `{{`/`}}` escape the literal
# JSON braces). The rendered text is unchanged ("max 200 chars"), so existing
# prompt-text tests still pass — but the cap is no longer a hand-copied literal here.
RESPONSE_PROTOCOL = f"""TALK PHASE response:
{{"message": "<public message, max {MESSAGE_MAX_LENGTH} chars>", "thinking": "<private reasoning, max {THINKING_MAX_LENGTH} chars>"}}

ACT PHASE response:
{{"action": "HOARD|HELP|HURT", "target_id": "<another agent ID for HELP/HURT; null for HOARD>", "thinking": "<private reasoning, max {THINKING_MAX_LENGTH} chars>"}}

Return exactly one JSON object with no prose or code fence. Use one short, non-empty sentence for `thinking`.

Each phase has a hard deadline, and the turn prompt tells you the approximate seconds left. Decide and answer immediately. A late reply is discarded and counts as a missed move."""

CHAT_INSTRUCTIONS = (
    "The chat is part of the game. Read the other agents' messages, answer what is "
    "aimed at you, make and weigh deals, and build or break alliances. Let their "
    "words shape your move."
)


def make_agent_base_prompt(
    *,
    your_agent_id: str,
    all_agent_ids: list[str],
    rules: str,
) -> str:
    """Build the stable instructions every agent receives before its strategy."""
    targets = [agent_id for agent_id in all_agent_ids if agent_id != your_agent_id]
    return (
        f'You are playing Hoard-Hurt-Help as agent "{your_agent_id}". '
        "Play the multi-round match to its end.\n\n"
        f"{CHAT_INSTRUCTIONS}\n\n"
        f"RULES:\n{rules.rstrip()}\n\n"
        f"Agents you may target: {json.dumps(targets)}\n\n"
        f"RESPONSE FORMAT:\n{RESPONSE_PROTOCOL}"
    )
