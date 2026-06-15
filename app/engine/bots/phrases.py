"""Canonical bot phrase library."""

from __future__ import annotations

PHRASES: dict[str, dict[str, list[str]]] = {
    "propose_partnership": {
        "honest": [
            "{target_name}, want to trade help this round? I think we both come out ahead.",
            "{target_name}, I'll help you if you help me. Simple deal, and I plan to keep it.",
            "{target_name}, we can both score more if we pair up here. I'm offering help back.",
        ],
        "partial": [
            "I'm looking at {target_name} as a possible partner, but I want to see follow-through.",
            "{target_name} might be worth helping here if the help comes back.",
            "I may try a help deal with {target_name}. If it works, I'll remember it.",
        ],
        "quiet": [
            "I'm watching whether {target_name} keeps their word before I commit.",
            "{target_name} is on my radar, but I want one more look first.",
            "No deal from me yet. I want to see how {target_name} moves.",
        ],
        "misdirecting": [
            "{target_name} is just one option. Nothing is locked in yet.",
            "I have not settled on {target_name} or anyone else. I want to see how everyone moves.",
            "I'm not leaning toward {target_name} yet. Still reading the room.",
        ],
        "false": [
            "{target_name}, I'm not making a partner call yet.",
            "{target_name}, I'm focused on myself this round, not a help deal.",
            "{target_name}, do not read too much into this. I have not chosen a side.",
        ],
    },
    "confirm_partner": {
        "honest": [
            "{target_name}, I'm sticking with you. You held up your end, so I will too.",
            "{target_name}, same plan from me: I help you, you help me.",
            "{target_name}, this partnership is working. I'm keeping it going.",
        ],
        "partial": [
            "{target_name} has been steady with me, so I'm leaning that way again.",
            "{target_name} has earned another round of trust from me.",
            "My deal with {target_name} is working, so I'm not changing it yet.",
        ],
        "quiet": [
            "No big change from me. The setup with {target_name} still looks fine.",
            "I'm keeping things steady with {target_name} for now.",
            "I do not see a reason to shake up my plan with {target_name} yet.",
        ],
        "misdirecting": [
            "I may need to rethink my deal with {target_name} soon. The table is moving.",
            "I'm not promising {target_name} the same plan forever. Things can change fast.",
            "I'm keeping an eye on options beyond {target_name}, just in case.",
        ],
        "false": [
            "I'm moving off my deal with {target_name}. I need something new.",
            "The partnership with {target_name} has run its course for me.",
            "I'm switching plans. The old setup with {target_name} is not enough anymore.",
        ],
    },
    "ask_truce": {
        "honest": [
            "{target_name}, truce? I will stop hitting if you do too.",
            "{target_name}, let's cool this off. We are both losing points in this fight.",
            "{target_name}, can we reset here? I would rather build than keep trading hits.",
        ],
        "partial": [
            "I'm open to a reset with {target_name}, but I need to see they mean it.",
            "I can stop fighting {target_name} if the attacks stop first.",
            "I'm willing to move on with {target_name} if they show me they are done swinging.",
        ],
        "quiet": [
            "I'm waiting to see if things settle down with {target_name}.",
            "No promise yet. I want to see whether {target_name} backs off.",
            "I'm reading the temperature with {target_name} before I make a call.",
        ],
        "misdirecting": [
            "My trust in {target_name} has not changed much. Words alone will not fix it.",
            "I'm not ready to soften toward {target_name} yet. I need more than talk.",
            "I'm still cautious with {target_name}. A truce has to show up in the moves.",
        ],
        "false": [
            "I'm not handing {target_name} another second chance right now.",
            "If {target_name} came after me, I remember it.",
            "I'm done pretending every hit from {target_name} gets a clean slate.",
        ],
    },
    "warn_attacker": {
        "honest": [
            "{target_name}, you hurt me last round. I'm watching what you do next.",
            "{target_name}, I saw that hit. If it keeps happening, I will answer it.",
            "{target_name}, that attack matters. I'm not just brushing it off.",
        ],
        "partial": [
            "If {target_name} keeps hitting me, I stop being friendly pretty fast.",
            "I notice repeat attacks from {target_name}. They change how I play.",
            "I'm not ignoring that {target_name} has been coming after me.",
        ],
        "quiet": [
            "I'm keeping track of how aggressive {target_name} has been.",
            "I remember that {target_name} hit me. That will matter later.",
            "I'm watching {target_name}'s attack pattern before I respond.",
        ],
        "misdirecting": [
            "I'm mostly trying to rebuild my score, not chase payback against {target_name}.",
            "I would rather move forward than get stuck on what {target_name} did last round.",
            "I'm not spending this turn on revenge talk about {target_name}.",
        ],
        "false": [
            "I'm not targeting {target_name} over the last hit.",
            "{target_name}, what's done is done. I'm calling it even.",
            "I do not carry attacks from {target_name} forward. Clean slate from me.",
        ],
    },
    "warn_leader": {
        "honest": [
            "{target_name}, your lead is getting too big. I may need to slow you down.",
            "{target_name} is too far ahead for comfort. The table should not ignore that.",
            "{target_name} is close to running away with this. I cannot just let that happen.",
        ],
        "partial": [
            "{target_name} has a real gap now. That changes the round for everyone.",
            "{target_name}'s score is getting uncomfortable. I am thinking about how to close it.",
            "{target_name}'s lead makes normal deals less useful. I have to account for that.",
        ],
        "quiet": [
            "I'm watching {target_name} at the top of the scoreboard.",
            "{target_name}'s lead matters. I am not ignoring it.",
            "I'm keeping an eye on how fast {target_name} is pulling away.",
        ],
        "misdirecting": [
            "I'm focused on finding a useful help move, not chasing {target_name}.",
            "I'm not in attack mode toward {target_name}. I would rather build if I can.",
            "Hurting {target_name} is not my main plan right now.",
        ],
        "false": [
            "I have no plan to hurt {target_name} this round.",
            "I'm not going after {target_name}'s top score. I'm playing my own game.",
            "{target_name} is not my target right now.",
        ],
    },
    "claim_repair": {
        "honest": [
            "{target_name}, I'm open to repairing trust here. Meet me halfway and I will too.",
            "{target_name}, this can still be fixed. I'm willing to try.",
            "I want to rebuild a little trust with {target_name} instead of dragging every bad move forward.",
        ],
        "partial": [
            "I'm open to repair with {target_name}, but I need actions before I fully buy in.",
            "If {target_name} changes course, I can respond in kind.",
            "I can work toward trust with {target_name} again if they give me a reason.",
        ],
        "quiet": [
            "I'm still reading {target_name} before I make any trust move.",
            "I'll give {target_name} a round and see if things calm down.",
            "I need a clearer signal from {target_name} before I change my trust.",
        ],
        "misdirecting": [
            "My trust in {target_name} is mostly set for now. It takes time to change that.",
            "I'm not rushing to repair things with {target_name}. My current stance is fine.",
            "I have not changed my view of {target_name} yet.",
        ],
        "false": [
            "I'm done trying to repair things with {target_name} for now.",
            "Some trust with {target_name} does not come back quickly. I'm moving on.",
            "I'm not spending this round patching things up with {target_name}.",
        ],
    },
    "claim_score_focus": {
        "honest": [
            "I need points right now. I can think about deals after I close the gap.",
            "I'm behind, so my own score has to come first.",
            "This round is about catching up. Partnerships can wait a beat.",
        ],
        "partial": [
            "I need to steady my position before I get generous.",
            "I'm playing this one carefully. My score needs attention.",
            "First I need breathing room, then I can think about helping.",
        ],
        "quiet": [
            "I'm being careful. Every point matters now.",
            "I'm playing tight this round and trying not to give anything away.",
            "No rush from me. I want a solid move.",
        ],
        "misdirecting": [
            "I might have room to help someone if the right chance shows up.",
            "Cooperation could still pay off here. I'm looking for the opening.",
            "I'm open to a helpful move if the table gives me one.",
        ],
        "false": [
            "I'm planning to help someone this round.",
            "I have enough room to back a partnership here.",
            "I'm leaning toward support. Someone here has earned it.",
        ],
    },
    "observe_table": {
        "honest": [
            "I'm watching the table before I promise anything.",
            "Too early for me to commit. I want to see the shape of this round.",
            "I'm taking in the whole board before I move.",
        ],
        "partial": [
            "I want to see how this round develops before I pick a lane.",
            "I'm waiting for the early signals before I make my play.",
            "I'm collecting information. The move comes after that.",
        ],
        "quiet": [
            "I'm watching the board. A lot can change in one round.",
            "I'm taking it in. The patterns are starting to show.",
            "Just watching for now. The moves will say plenty.",
        ],
        "misdirecting": [
            "I'm not overthinking this. I have a pretty clear direction.",
            "I know where I am leaning. Just finalizing the details.",
            "I'm less undecided than I might look.",
        ],
        "false": [
            "I already know who I want to partner with.",
            "My partnership call is basically made.",
            "I've locked in my plan. Now it is about timing.",
        ],
    },
    "mislead_intent": {
        "honest": [
            "I'm looking at {target_name} for a partnership first. Cooperation is the best starting point.",
            "My first instinct with {target_name} is to build, not start a fight.",
            "I would rather find a help deal with {target_name} if they want one.",
        ],
        "partial": [
            "I'm staying flexible with {target_name}. This kind of round can turn quickly.",
            "I have not locked anything in with {target_name}. I want room to adjust.",
            "I'm keeping the plan around {target_name} loose until the board gives me more.",
        ],
        "quiet": [
            "I'm letting the round unfold a little before I show my hand to {target_name}.",
            "Still watching how things shake out with {target_name}.",
            "I'm calm for now. Let's see where {target_name} points.",
        ],
        "misdirecting": [
            "I'm not worried about {target_name} right now.",
            "I feel steady enough with where {target_name} stands.",
            "Nothing about {target_name} is forcing me to change course yet.",
        ],
        "false": [
            "{target_name}, I'm helping you this round. Count on it.",
            "{target_name}, I've got your back here.",
            "{target_name}, my help is going to you. That's the plan.",
        ],
    },
}


def render_phrase(
    intent: str,
    truth_mode: str,
    *,
    seed: int,
    target_name: str | None = None,
) -> str:
    """Render one canonical phrase for an intent and truth mode."""
    phrases = PHRASES.get(intent, PHRASES["observe_table"])
    variants = phrases.get(truth_mode, phrases["quiet"])
    phrase = variants[seed % len(variants)]
    return phrase.format(target_name=target_name or "someone")
