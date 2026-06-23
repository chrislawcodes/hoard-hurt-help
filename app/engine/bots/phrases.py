"""Canonical bot phrase library.

Talk is keyed by the action the bot has already decided to take this turn, and
there are only two ways it can frame that move:

- ``honest``: says what it is about to do. HELP lines carry an offer word and
  name the target; HURT lines do persuasive work that fits the attack (deter the
  attacker, rally the table against the leader, warn a crowding rival). This is
  what feeds :mod:`app.engine.bots.signals`, so other bots read the tell.
- ``false``: claims the opposite. A bot about to HURT or HOARD asks to partner
  up (a bluff that shows up as a false cooperation signal); a bot about to HELP
  denies it.

Which one a bot uses is set by its truthfulness (0-100) = how often it tells the
truth; see `_choose_truth_mode`. Every (intent, mode) bucket holds the same
number of distinct lines (the last is a wry robot-pun / dad-joke variant); see
``VARIANTS_PER_BUCKET`` and the tests in `tests/test_bot_talk_telegraph.py`.
"""

from __future__ import annotations

# The intent every fallback path lands on; it has both modes.
FALLBACK_INTENT = "play_own_game"

# Each (intent, mode) bucket carries this many distinct lines.
VARIANTS_PER_BUCKET = 9


PHRASES: dict[str, dict[str, list[str]]] = {
    "offer_help": {
        "honest": [
            "{target_name}, I'm helping you this turn. Help me back and we both jump +8.",
            "{target_name}, let's pair up — my help lands on you. Match it and we both win.",
            "{target_name}, my help goes to you this turn. Return it and we run this together.",
            "{target_name}, I'm backing you with a help. Send one my way and we're a team.",
            "{target_name}, want a deal? I help you now, you help me next. We both climb.",
            "{target_name}, you and me — mutual help this turn. +8 each beats hoarding alone.",
            "{target_name}, I'm offering a straight trade: my help for yours. Take it?",
            "{target_name}, partner with me this turn. My help's already pointed your way.",
            "{target_name}, team up with me — I help you, you help me. Even a robot can do that math.",
        ],
        "false": [
            "I'm out for myself this round. No favors from anyone here.",
            "{target_name}, don't count on me. I'm playing cold this turn.",
            "I'm not in a giving mood. Everyone's on their own.",
            "No charity from me today. Watch your own back.",
            "{target_name}, I owe you nothing. Don't expect a hand.",
            "I'm playing selfish this turn. Nothing personal.",
            "Don't wait on me to bail anyone out. I'm focused on me.",
            "{target_name}, we're not a team this turn. I'm solo.",
            "Don't wait on me, {target_name}. This lone-bot rolls solo.",
        ],
    },
    "keep_ally": {
        "honest": [
            "{target_name}, sticking with you. You held your end, I hold mine — help again.",
            "{target_name}, same deal as before: I help you, you help me. It's working.",
            "{target_name}, our pairing pays off. My help stays on you this turn.",
            "{target_name}, you've been a real partner. I'm helping you again, no question.",
            "{target_name}, why break what works? My help's yours this turn too.",
            "{target_name}, we're a proven team. Mutual help, same as last round.",
            "{target_name}, loyalty pays. I'm sending help your way again.",
            "{target_name}, you kept faith, so do I. Help for help, round two.",
            "{target_name}, stick with me — two bots in a lobby, and it works. My help's yours.",
        ],
        "false": [
            "I'm done with my deal with {target_name}. Time for something new.",
            "{target_name}, our run is over as far as I'm concerned.",
            "I'm cutting {target_name} loose this turn. Plans change.",
            "{target_name}, we're finished. I'm done carrying this.",
            "I'm walking away from {target_name}. Don't follow.",
            "{target_name}, consider us over. I need a fresh angle.",
            "The {target_name} era is done for me. Moving on.",
            "{target_name}, I've outgrown our deal. This turn I go my own way.",
            "We're done, {target_name}. It's not you, it's my wiring. Move on.",
        ],
    },
    "repay_help": {
        "honest": [
            "{target_name}, you helped me — I'm helping you back. That's how I play.",
            "{target_name} backed me last round, so my help goes their way now.",
            "Credit where it's due: {target_name} helped me, and I return the help.",
            "{target_name}, you had my back, now I've got yours. Help incoming.",
            "{target_name} earned this. My help lands on them this turn.",
            "Fair is fair, {target_name}. You helped me, I help you. Done.",
            "{target_name}, I pay back help with help. Yours is coming.",
            "You looked out for me, {target_name}. I'm returning it with a help.",
            "{target_name}, you scratched my back — keep it up and I keep scratching yours. Help incoming.",
        ],
        "false": [
            "I don't do favors. Last round was last round.",
            "{target_name}, don't expect a thank-you move from me.",
            "Owe you or not, I play for myself this turn.",
            "I forget debts fast. Nothing owed today, {target_name}.",
            "{target_name}, what you did bought you nothing from me.",
            "I'm nobody's charity. Past favors don't move me.",
            "{target_name}, don't cash in on old goodwill. Not today.",
            "Debts forgiven — mine, not yours. I keep my points, {target_name}.",
            "Expect nothing from me, {target_name}. My debt memory just got wiped. Convenient.",
        ],
    },
    "mend_fences": {
        "honest": [
            "{target_name}, let's reset. My help comes your way to prove I mean it.",
            "{target_name}, truce — I'd rather build with you. Help's headed your way.",
            "{target_name}, I'm done feuding. My help this turn is the olive branch.",
            "{target_name}, clean slate. I'll prove it with a help right now.",
            "{target_name}, let's bury it. My help is the handshake.",
            "{target_name}, peace offer — I help you this turn, no strings.",
            "{target_name}, enough fighting. I'm sending help to start fresh.",
            "{target_name}, I'll make the first move toward peace: a help, this turn.",
            "{target_name}, drop the fight and I'll send help. Let's bury the hatchet — I've no arms to swing it anyway.",
        ],
        "false": [
            "I'm not letting {target_name} off the hook this turn. Some things stick.",
            "{target_name} hasn't earned a clean slate from me.",
            "I'm keeping my guard up with {target_name}. Nothing's settled.",
            "{target_name}, we're still on bad terms as far as I'm concerned.",
            "No peace from me, {target_name}. You burned that bridge.",
            "{target_name}, I don't forget. Don't expect kindness.",
            "Making nice with {target_name} isn't happening today.",
            "{target_name}, stay wary of me. I haven't moved on.",
            "Stay wary of me, {target_name}. I backed up the grudge to the cloud.",
        ],
    },
    "hit_back": {
        "honest": [
            "{target_name}, you hit me. I always hit back — that's the one rule I keep.",
            "Hit me and you get hit back, {target_name}. Nothing personal, just my policy.",
            "{target_name}, I answer every attack. You threw one, so one comes back.",
            "I don't start fights, {target_name}, but I finish them. You hit me first.",
            "{target_name}, my rule is simple: attack me, eat a hit. You knew the deal.",
            "Everyone should know: hit me like {target_name} did, and I hit back.",
            "{target_name}, I match what's done to me. You attacked, so expect the same.",
            "Cross me and it comes back, {target_name} — I hit back, always.",
            "{target_name}, don't hit me again — I always hit back. That's how I'm programmed.",
        ],
        "false": [
            "{target_name}, water under the bridge. Let's help each other this turn.",
            "{target_name}, I'm calling us even. Help for help this turn.",
            "{target_name}, no hard feelings. I'd rather partner up than scrap.",
            "{target_name}, let's drop it and team up. Mutual help?",
            "{target_name}, I'm over it. Pair with me and we both gain.",
            "{target_name}, truce — help me and I'll help you right back.",
            "{target_name}, clean slate. My help can be yours this turn.",
            "{target_name}, let's be allies, not enemies. Help for help?",
            "{target_name}, all forgiven — help me and I'll help you. Beep boop, friendship.",
        ],
    },
    "curb_leader": {
        "honest": [
            "{target_name} is running away with this, far ahead. The rest of us must gang up.",
            "Look at {target_name}'s lead. If we don't pile on the leader, they win it.",
            "{target_name} is too far ahead. Table, we slow them together or we all lose.",
            "Someone has to check {target_name}, our runaway leader. I can't alone — who's in?",
            "{target_name}'s runaway lead is everyone's problem. Let's deal with it as a group.",
            "The leader, {target_name}, is pulling away. Gang up now or hand them the game.",
            "{target_name} is winning while we squabble. Focus up — pressure the leader.",
            "I'm calling it: {target_name} is ahead and climbing. We all push back or lose.",
            "{target_name}'s lead is over 9000. Everyone, gang up on the leader before they unplug us all.",
        ],
        "false": [
            "{target_name}, you're safe from me — I'd rather partner up.",
            "{target_name}, let's not clash. Help me and I'll help you.",
            "{target_name}, no quarrel here. Want to pair up?",
            "{target_name}, I'm no danger to you. Let's partner up instead.",
            "{target_name}, I'd rather ride with you than race you. Help for help?",
            "{target_name}, you and me could partner up and split the table.",
            "{target_name}, I come in peace. A mutual help sound good?",
            "{target_name}, let's pair up — easier to win together than apart.",
            "{target_name}, you're the final boss and I'm an NPC — so let's partner up instead of fighting.",
        ],
    },
    "block_rival": {
        "honest": [
            "{target_name}, you're crowding my spot. Back off or I hit you for room.",
            "{target_name}, ease off my heels or you take a hit. Your choice.",
            "We can't both sit here, {target_name}. Push me and I hit back.",
            "{target_name}, you're too close. Give me space or eat a hit.",
            "I guard my rank, {target_name}. Crowd me and a hit follows.",
            "{target_name}, drop back a step. Stay on me and you get hit.",
            "Fair warning, {target_name}: keep chasing my spot and I hit you.",
            "{target_name}, this rank is mine. Crowd it and I'll hit to keep it.",
            "{target_name}, back off my rank or I hit eject. You're crowding my bubble.",
        ],
        "false": [
            "{target_name}, let's partner up — two of us can catch the leader.",
            "{target_name}, I'd rather help you than scrap over one spot.",
            "{target_name}, no rivalry here. Help me and I'll help you.",
            "{target_name}, we're stronger together. Pair up this turn?",
            "{target_name}, forget the race — let's pair up and split it.",
            "{target_name}, mutual help beats scrapping for one rank. You in?",
            "{target_name}, I've got no beef with you. Partner up?",
            "{target_name}, team up with me and we both move up. Help for help.",
            "{target_name}, why fight over one chair when we could grab two? Help me and I'll help you.",
        ],
    },
    "play_own_game": {
        "honest": [
            "I'm heads-down on my own score this turn. No deals from me.",
            "No help, no fights — I'm banking points for myself this turn.",
            "I'm staying out of the drama and building my own pile.",
            "Just me and my score this turn. Everyone else can wait.",
            "I'm hoarding clean this turn. No friends, no enemies.",
            "Solo run this turn. I'm stacking points and nothing else.",
            "I'm sitting this round's politics out. Pure score-building.",
            "No moves on anyone. I'm taking the safe points and going home.",
            "Leave me out this turn — solo mode: engaged. Just me and my point counter.",
        ],
        "false": [
            "{target_name}, I've got your back this turn. Count on my help.",
            "{target_name}, let's help each other — mutual +8, you in?",
            "{target_name}, my help is going your way. We climb together.",
            "{target_name}, partner with me this turn. I'm helping you, promise.",
            "{target_name}, you and me — mutual help, easy points. Deal?",
            "{target_name}, I'll help you if you help me. Let's do it.",
            "{target_name}, count me in as your ally this turn. Help incoming.",
            "{target_name}, pair up with me. My help's got your name on it.",
            "{target_name}, team up with me — full help, pinky promise. (Robots have pinkies, right?)",
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
    phrases = PHRASES.get(intent, PHRASES[FALLBACK_INTENT])
    variants = phrases.get(truth_mode, phrases["honest"])
    phrase = variants[seed % len(variants)]
    return phrase.format(target_name=target_name or "someone")
