"""Canonical Sim phrase library."""

from __future__ import annotations

PHRASES: dict[str, dict[str, str]] = {
    "propose_partnership": {
        "honest": "AI_07, I want to try a mutual-help lane.",
        "partial": "I am testing a partnership this turn.",
        "quiet": "I am watching who follows through.",
        "misdirecting": "I am keeping my options open.",
        "false": "AI_07, I am not choosing a partner yet.",
    },
    "confirm_partner": {
        "honest": "AI_07, I am staying with you this turn.",
        "partial": "I am staying with a trusted partner.",
        "quiet": "I am keeping things steady.",
        "misdirecting": "I may need to adjust partners soon.",
        "false": "I am moving away from my current partner.",
    },
    "ask_truce": {
        "honest": "AI_07, I want a truce this turn.",
        "partial": "I am open to repair if actions improve.",
        "quiet": "I am watching for better signals.",
        "misdirecting": "I am not changing my trust yet.",
        "false": "I am done giving second chances.",
    },
    "warn_attacker": {
        "honest": "AI_08 hurt me, so I am watching them closely.",
        "partial": "Repeated attacks will have consequences.",
        "quiet": "I am tracking who hit me.",
        "misdirecting": "I am focused on rebuilding, not payback.",
        "false": "I am not targeting anyone who hurt me.",
    },
    "warn_leader": {
        "honest": "AI_03 is too far ahead, so I may hurt them.",
        "partial": "The top score is getting too far away.",
        "quiet": "I am watching the top scores.",
        "misdirecting": "I am avoiding hurt this turn.",
        "false": "I will not hurt the leader this turn.",
    },
    "claim_repair": {
        "honest": "I am open to repairing trust this turn.",
        "partial": "I am open to repair if actions improve.",
        "quiet": "I am watching for better signals.",
        "misdirecting": "I am not changing my trust yet.",
        "false": "I am done with repair attempts.",
    },
    "claim_score_focus": {
        "honest": "I am focused on my own score this turn.",
        "partial": "I need to stabilize my position.",
        "quiet": "I am playing carefully this turn.",
        "misdirecting": "I am open to helping someone this turn.",
        "false": "AI_07, I am helping you this turn.",
    },
    "observe_table": {
        "honest": "I am watching the table this turn.",
        "partial": "I am watching how this turn develops.",
        "quiet": "I am watching the board.",
        "misdirecting": "I am keeping my options open.",
        "false": "I am making a partnership decision right now.",
    },
    "mislead_intent": {
        "honest": "I am thinking about partnership first.",
        "partial": "I am trying to stay flexible.",
        "quiet": "I am just watching the round develop.",
        "misdirecting": "I am not worried about the board.",
        "false": "I am definitely helping AI_07.",
    },
}


def render_phrase(intent: str, truth_mode: str, *, seed: int) -> str:
    """Render one canonical phrase for an intent and truth mode."""
    phrases = PHRASES.get(intent, PHRASES["observe_table"])
    return phrases.get(truth_mode, phrases["quiet"])

