"""Canonical Sim phrase library."""

from __future__ import annotations

PHRASES: dict[str, dict[str, list[str]]] = {
    "propose_partnership": {
        "honest": [
            "{target_name}, I want to set up a mutual-help lane with you. Let's both gain some ground together.",
            "{target_name}, I'm offering a real partnership this turn. If you help me, I'll help you back.",
            "{target_name}, I think we can both score better by working together. I'm proposing we help each other.",
        ],
        "partial": [
            "I'm testing whether a partnership pays off this turn. I'm reaching out to someone who seems reliable.",
            "I'm trying a cooperative move this round to see who reciprocates. Loyalty gets rewarded.",
            "I'm exploring a help arrangement this turn. I'll see if the other side follows through.",
        ],
        "quiet": [
            "I'm watching who follows through on deals before I commit to anything.",
            "I'm tracking which players actually do what they say before I pick a lane.",
            "I'm observing the patterns here. Not ready to commit yet.",
        ],
        "misdirecting": [
            "I'm keeping all my options open right now. No commitments until I see how the table looks.",
            "I haven't locked in anything. Still taking stock of what's happening around me.",
            "I'm not leaning one way yet. Just waiting to see how this round shapes up.",
        ],
        "false": [
            "{target_name}, I haven't picked a partner yet. I'm still figuring out my approach this round.",
            "{target_name}, I'm not looking for a partnership right now. I'm focused on my own position.",
            "{target_name}, I'm undecided this turn. Don't read anything into my last move.",
        ],
    },
    "confirm_partner": {
        "honest": [
            "{target_name}, I'm staying with you this turn. You've been reliable and I'm not switching.",
            "{target_name}, our lane is holding. I'm committing to helping you again this round.",
            "{target_name}, I'm locked in with you. Let's keep this partnership going and both move up.",
        ],
        "partial": [
            "I'm staying with a partner who has been consistent. Loyalty goes both ways.",
            "I've found someone worth sticking with for now. I'm holding that partnership steady.",
            "I'm keeping my current alignment. It's been working so I'm not changing it.",
        ],
        "quiet": [
            "Things are holding steady on my end. I'm not making any changes this round.",
            "I'm staying with my current setup. No reason to shake things up.",
            "No changes from me this turn. I like where things are sitting.",
        ],
        "misdirecting": [
            "I might need to reassess my partnerships soon. The table is shifting.",
            "I'm keeping my options open. My current arrangement may not hold much longer.",
            "I'm not sure I can keep relying on the same people. Things are changing.",
        ],
        "false": [
            "I'm moving away from my current partner. I need a fresh arrangement.",
            "I'm done with my current lane. Time to try something different this turn.",
            "I'm switching things up. My old alignment isn't working for me anymore.",
        ],
    },
    "ask_truce": {
        "honest": [
            "{target_name}, I want a truce this turn. Let's stop hitting each other and see if we can both do better.",
            "{target_name}, I'm calling a ceasefire. No attacks from me if you hold off too.",
            "{target_name}, can we reset? I'd rather build something together than keep trading hits.",
        ],
        "partial": [
            "I'm open to repairing relationships if the other side shows some goodwill first.",
            "I'll extend an olive branch if I see actions that match the words. Trust needs evidence.",
            "I'm willing to move forward if someone demonstrates they're done with the attacks.",
        ],
        "quiet": [
            "I'm watching for signs that things are settling down before I make a move.",
            "I'm sitting back and reading the signals. Not committing to a direction yet.",
            "I'm waiting to see if the table calms down. My next move depends on what happens.",
        ],
        "misdirecting": [
            "I haven't changed my trust levels. People need to prove themselves before I adjust.",
            "I'm not softening my stance yet. I need to see more before I'm willing to move.",
            "My trust is where it is. It takes more than words to shift it.",
        ],
        "false": [
            "I'm done extending second chances. If someone came for me, I remember it.",
            "I've given enough opportunities. I'm not in the business of forgiving right now.",
            "Second chances are over for some players at this table. I'm moving forward.",
        ],
    },
    "warn_attacker": {
        "honest": [
            "{target_name} hurt me last round, so I'm keeping a close eye on their next move.",
            "{target_name} came after me and I haven't forgotten. I'm watching what they do next.",
            "{target_name} attacked me and that doesn't just disappear. I'm factoring it into my plan.",
        ],
        "partial": [
            "If someone keeps attacking me, they will find me a lot less cooperative going forward.",
            "Repeated aggression has a cost. I adjust my behavior based on who's been hitting me.",
            "I don't ignore attacks. Players who come after me will see a different side of me.",
        ],
        "quiet": [
            "I'm keeping track of who has been aggressive toward me. It matters for what comes next.",
            "I remember who hit me. That kind of thing shapes how I play going forward.",
            "I'm logging the patterns of who attacks. I'll act on that information when the time is right.",
        ],
        "misdirecting": [
            "I'm mostly focused on rebuilding my score right now. Not really thinking about payback.",
            "I'd rather move forward than dwell on what happened last round. Grudges slow you down.",
            "I'm channeling my energy into growth this turn, not retaliation. Let's move on.",
        ],
        "false": [
            "I'm not targeting anyone who came after me. I'd rather just reset and move forward.",
            "What's done is done. I'm not holding any grudges or changing how I treat people.",
            "I don't carry attacks into the next round. Clean slate every turn for me.",
        ],
    },
    "warn_leader": {
        "honest": [
            "{target_name} is pulling too far ahead. I'm seriously considering slowing them down this turn.",
            "{target_name} has a lead that's getting dangerous. I may need to step in and close that gap.",
            "{target_name} is running away with this. I can't just let that continue unchecked.",
        ],
        "partial": [
            "Someone at this table has built a gap that's starting to feel unbeatable. That's a problem.",
            "The score gap is getting too wide for comfort. I'm thinking about how to close it.",
            "A lead that big changes the math for everyone. I'm adjusting my approach accordingly.",
        ],
        "quiet": [
            "I'm monitoring the top of the scoreboard closely. That lead is worth paying attention to.",
            "The gap at the top is something I'm tracking. It will factor into what I do next.",
            "I'm watching how fast the top scores are moving. That information is going to matter.",
        ],
        "misdirecting": [
            "I'm focused on helping where I can this turn. Not really looking at the top of the board.",
            "I'm not in attack mode. I'm thinking about building, not tearing anyone down.",
            "Hurting anyone isn't really on my radar right now. I'm looking at other angles.",
        ],
        "false": [
            "I have no plans to hurt the leader this turn. They earned their spot at the top.",
            "I'm not going after anyone at the top. I'm playing my own game right now.",
            "The leader is safe from me this round. I've got other things to think about.",
        ],
    },
    "claim_repair": {
        "honest": [
            "I'm genuinely open to repairing trust this turn. Past is past if we can reset together.",
            "I want to rebuild some of the relationships that took damage. I'm extending goodwill now.",
            "I'm making a real effort to restore trust. If someone meets me halfway, I'll follow through.",
        ],
        "partial": [
            "I'm open to repair, but I need to see actions before I fully commit to trusting again.",
            "I'll respond positively if someone shows they've changed course. Proof matters more than words.",
            "I'm willing to move toward repair if the other side gives me something to work with.",
        ],
        "quiet": [
            "I'm still reading the table. Not committing to any trust moves until I see how this unfolds.",
            "I'm giving it a round to see if signals improve before I make any repair moves.",
            "I'm waiting for clearer information before I shift my trust levels in any direction.",
        ],
        "misdirecting": [
            "My trust levels are set where they are. It takes more than one turn for me to adjust.",
            "I'm not in a rush to repair anything right now. My current stance is working fine.",
            "I haven't decided to change how I see anyone yet. Still in a watchful mode.",
        ],
        "false": [
            "I'm done trying to repair things. Some relationships just don't recover, and that's fine.",
            "I've moved past the repair phase. I'm just playing the hand I have at this point.",
            "I'm not spending energy on trust repair this turn. That window has closed.",
        ],
    },
    "claim_score_focus": {
        "honest": [
            "I'm locked in on my own score this turn. I need to close the gap before I think about anyone else.",
            "Pure score focus for me this round. I'm behind and I need to fix that before I help anyone.",
            "I'm prioritizing my own points right now. I'll think about partnerships once I'm in a better spot.",
        ],
        "partial": [
            "I need to stabilize my position before I can consider anything else. My score has to come first.",
            "I'm in a defensive posture this turn. I need to shore up my standing before anything else.",
            "My score is my priority right now. Once I'm more comfortable, I'll think about others.",
        ],
        "quiet": [
            "I'm being careful and deliberate this round. Every point matters at this stage.",
            "I'm playing a tight game this turn. I want to make sure my decisions are grounded.",
            "I'm not rushing into anything. I want the position I make moves from to be solid.",
        ],
        "misdirecting": [
            "I'm actually thinking about where I can be helpful this turn. There might be an opportunity.",
            "I might have room to support someone this round. Cooperation could pay off.",
            "I'm open to helping out this turn if the right situation comes up. Let's see what develops.",
        ],
        "false": [
            "I'm planning to help someone out this round. I've got enough breathing room to do it.",
            "I'm going to throw my weight behind a partnership this turn. Cooperation feels right.",
            "I'm committing to helping this round. Someone at this table deserves some support.",
        ],
    },
    "observe_table": {
        "honest": [
            "I'm watching the whole table carefully this turn before I make any commitments.",
            "I'm reading the room before I decide on a direction. Too early to commit to anything.",
            "I'm in observation mode this turn. I want to understand the full picture before acting.",
        ],
        "partial": [
            "I'm watching how this round develops before I settle on a course of action.",
            "I want to see how the early moves land before I make my play. Patience pays off.",
            "I'm collecting information right now. My decision will come once things start to crystallize.",
        ],
        "quiet": [
            "I'm watching the board. A lot can shift in a single round.",
            "I'm taking it all in. The patterns here are worth reading carefully.",
            "Just watching. There is a lot of useful information in how other players move.",
        ],
        "misdirecting": [
            "I'm not overthinking it. I have a pretty clear sense of what I want to do this turn.",
            "I've already got a direction in mind. I'm not as undecided as it might look.",
            "I know roughly what I'm doing this turn. Just finalizing the details.",
        ],
        "false": [
            "I've already decided who I'm partnering with. It just comes down to timing now.",
            "My partnership call is basically made. I'm just waiting for the right moment.",
            "I've locked in my decision. I know exactly how I'm playing this turn.",
        ],
    },
    "mislead_intent": {
        "honest": [
            "I'm thinking hard about partnership angles first. Cooperation is my starting point.",
            "My instinct is to build something this turn. Partnership is at the top of my list.",
            "I'm leading with partnership as my first priority. Let's see if someone wants to build with me.",
        ],
        "partial": [
            "I'm trying to stay adaptable this round. Flexibility is worth a lot in this kind of game.",
            "I haven't locked anything in. I want to leave room to adjust based on what I see.",
            "I'm keeping my approach fluid. Sticking too rigidly to a plan tends to backfire.",
        ],
        "quiet": [
            "I'm just taking in what's happening and letting the round unfold on its own terms.",
            "Still watching how everything shakes out. No need to move until the picture gets clearer.",
            "I'm in a pretty calm state this turn. Just seeing where things go.",
        ],
        "misdirecting": [
            "I'm not stressed about the board right now. I feel pretty good about where things stand.",
            "The table dynamics aren't giving me much to worry about. I'm feeling steady.",
            "I'm relaxed about the current state. Nothing I see is making me change my approach.",
        ],
        "false": [
            "{target_name}, I'm helping you this turn. You can count on it.",
            "{target_name}, I've got your back this round. Expect my support.",
            "{target_name}, I'm committing my help to you right now. It's decided.",
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
