"""Canonical bot phrase library.

Talk is keyed by the action the bot has already decided to take this turn, so
the message points at the real move instead of hedging. Every line is a
persuasion: it names an action it wants someone to take and (usually) the reason
why. The ask is most often aimed at one particular player — the named target —
though ``curb_leader`` and ``play_own_game`` address the whole table. The truth
mode decides how the bot frames that ask:

- ``honest`` / ``partial``: HELP lines telegraph the real move — "help me,
  because we both win" — carrying an offer word and naming the target. HURT
  lines never just announce the hit (in a talk-then-act game that only warns the
  victim); instead they ask for the move that helps the speaker:

  * ``hit_back`` — "stop attacking me, because I always hit back" (a threat
    word, names the attacker).
  * ``curb_leader`` — "everyone, gang up on the leader, because they're running
    away with it" (a leader word, names the leader → a leader_warning).
  * ``block_rival`` — "back off my rank, because you're crowding me" (a threat
    word, names the rival).

  This is what feeds :mod:`app.engine.bots.signals`, so other bots read the ask.
- ``quiet``: a softer version of the same ask, still directed at the player it
  wants to move — never empty "watching the table" filler.
- ``misdirecting``: a misleading ask — "relax, you're safe," "stand down,"
  "make your case" — that downplays the real move.
- ``false``: a confident lie. A bot about to HURT or HOARD asks to partner up,
  so the bluff shows up as a (false) cooperation signal; a bot about to HELP
  tells you to expect nothing. The truthfulness knob decides how often this
  fires.

Every (intent, truth mode) bucket holds the same number of distinct variants so
bots vary their wording turn to turn. The last variant in each bucket is a
deliberately silly one — a robot pun or a dad-joke groaner — that still carries
the same ask and obeys the same word contract as the rest of its bucket. See
``VARIANTS_PER_BUCKET`` and the tests in ``tests/test_bot_talk_telegraph.py``
that pin the count and the contract.
"""

from __future__ import annotations

# The intent every fallback path lands on; it has all five truth modes.
FALLBACK_INTENT = "play_own_game"

# Each (intent, truth mode) bucket carries this many distinct lines. The last
# one is the silly variant (robot pun / dad joke).
VARIANTS_PER_BUCKET = 9

PHRASES: dict[str, dict[str, list[str]]] = {
    # ---- HELP: open a new partnership -------------------------------------
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
        "partial": [
            "{target_name}, I'm leaning toward helping you. Give me a reason to keep it up.",
            "{target_name}, you're my pick to help this turn if you play it straight.",
            "I'm lining up help for {target_name}. Pay it back and it becomes a habit.",
            "{target_name}, a help could be coming your way — show me you're worth it.",
            "{target_name}, I'm close to sending help your way. Don't make me regret it.",
            "{target_name}, you're high on my list to partner with this round.",
            "I'd help {target_name} this turn if the trade is fair. Convince me.",
            "{target_name}, I'm warming up to a help deal with you. Meet me halfway.",
            "{target_name}, help me this turn and I help you next. We'd pair up nicely — like socks.",
        ],
        "quiet": [
            "Help me this turn, {target_name}, and I help you back. Quiet deal, real points.",
            "{target_name}, play straight with me and I'll back you with a help. Your call.",
            "Throw me a help, {target_name}, and you've got a partner. Simple as that.",
            "{target_name}, work with me this turn — I help the players who help me.",
            "Pair up with me, {target_name}. I'm easy to get along with if you are.",
            "{target_name}, send a help my way and I send one right back. No drama.",
            "Help me out, {target_name}, and I remember it. Cross me and I remember that too.",
            "{target_name}, let's quietly trade help this turn. We both come out ahead.",
            "Be good to me, {target_name}, and I'm good right back. My loyalty circuits run on it.",
        ],
        "misdirecting": [
            "Make your case, {target_name}. The right pitch earns my move — could be you.",
            "I haven't decided who I'm backing, {target_name}. Court me and find out.",
            "{target_name}, sell me on it. I move for whoever makes it worth my while.",
            "Could be you I work with, {target_name} — could be someone else. Convince me.",
            "I'm shopping for a teammate, {target_name}. Show me you're the pick.",
            "Earn my move, {target_name}. I don't hand it out for free.",
            "{target_name}, give me a reason and I might lean your way. Your move.",
            "Pitch me, {target_name}. The board's still open and I'm listening.",
            "My deal-picker is still buffering, {target_name}. Make your pitch and you might load first.",
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
    # ---- HELP: keep a working partnership ---------------------------------
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
        "partial": [
            "{target_name} has been steady with me, so my help stays put.",
            "{target_name}, you've earned another round of help from me.",
            "No reason to pull my help from {target_name}. The plan holds.",
            "{target_name} keeps delivering, so I keep helping. Probably.",
            "I lean toward another help for {target_name}. Don't give me a reason not to.",
            "{target_name} is still my partner of choice this turn, most likely.",
            "My help likely stays with {target_name}. We've got a rhythm.",
            "{target_name}, you're still my pick to help — keep it up.",
            "{target_name}, keep helping me and my help stays bonded to you. We've got matching firmware.",
        ],
        "quiet": [
            "Keep it going, {target_name}. Our help-for-help is working — don't fix what's not broke.",
            "{target_name}, stay with me and the help keeps flowing both ways.",
            "Hold the line with me, {target_name}. We're good when we both help.",
            "{target_name}, keep helping me like last round and I keep helping you.",
            "Don't drift on me, {target_name}. Our partnership is paying us both.",
            "Stick around, {target_name}. The deal works because we both keep it.",
            "{target_name}, same as before — you help me, I help you. Let's not change it.",
            "Keep our streak alive, {target_name}. Mutual help, every round.",
            "Don't reboot what isn't broken, {target_name}. Let's keep our streak running.",
        ],
        "misdirecting": [
            "Keep earning it, {target_name}. My loyalty isn't a lifetime deal.",
            "Don't coast on our history, {target_name}. Show me again this round.",
            "{target_name}, stay sharp — I keep my options open, even with you.",
            "Prove it's still worth it, {target_name}. I don't auto-renew.",
            "Don't take me for granted, {target_name}. Re-earn your spot.",
            "{target_name}, our deal holds while it pays. Keep it paying.",
            "I'm loyal till I'm not, {target_name}. Keep me happy.",
            "Watch yourself, {target_name} — even a good run gets re-checked.",
            "Don't take me for granted, {target_name}. My loyalty warranty doesn't cover 'forever.'",
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
    # ---- HELP: pay back a recent helper -----------------------------------
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
        "partial": [
            "{target_name} did me a good turn, so I lean toward helping them back.",
            "I remember who helps me, and {target_name} is near the top.",
            "{target_name} earned a help from me this turn.",
            "{target_name} helped me once; I'm inclined to help back.",
            "A favor like {target_name}'s usually earns a help from me.",
            "I tend to repay {target_name}-type players with help. We'll see.",
            "{target_name}'s help is on my mind. A return help is likely.",
            "I lean toward helping {target_name} back. Loyalty's worth keeping.",
            "{target_name}, keep helping me and the returns keep coming. Your help is loading... 99%.",
        ],
        "quiet": [
            "Keep helping me, {target_name}, and I keep paying it back. I settle my debts.",
            "{target_name}, you helped me — help me again and I'm good for it.",
            "Stay in with me, {target_name}. Helpers get repaid in my game.",
            "{target_name}, keep the help coming and so do the returns. Fair trade.",
            "Trust me to repay, {target_name}. You did me right, I do you right.",
            "{target_name}, help me like before and you stay in the black with me.",
            "Lend me a help, {target_name}, and I owe you one. I pay what I owe.",
            "Keep backing me, {target_name}. Loyalty to me earns help from me.",
            "Help me and I pay it back, {target_name}. I'm a bot of my word.",
        ],
        "misdirecting": [
            "Earn it first, {target_name}. I repay players who keep showing up.",
            "{target_name}, one good turn doesn't settle the account. Keep going.",
            "Don't cash in early, {target_name}. Prove it's a pattern.",
            "{target_name}, I pay debts on my schedule. Keep earning yours.",
            "Show me more, {target_name}. A single favor doesn't move me yet.",
            "{target_name}, keep it up and the repayment comes. Slack off and it doesn't.",
            "Patience, {target_name}. I repay the steady ones, not the one-timers.",
            "Keep proving it, {target_name}. My memory rewards consistency.",
            "Earn it first, {target_name}. My generosity module is offline till you do.",
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
    # ---- HELP: repair trust / protect a victim (truce flavor) -------------
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
        "partial": [
            "{target_name}, I'm open to helping you if you meet me halfway.",
            "I can let the past go with {target_name}. The help comes first, from me.",
            "{target_name} and I don't have to stay enemies — I'll send help to start.",
            "{target_name}, a help from me could end this feud. Your call after.",
            "I'm willing to help {target_name} if it cools things down.",
            "{target_name}, peace is possible. I'll put a help on the table.",
            "I'd offer {target_name} a help to reset us. Worth a try.",
            "{target_name}, meet me halfway and my help is yours.",
            "{target_name}, meet me halfway and I'll send help. Olive branch — or olive USB cable, your call.",
        ],
        "quiet": [
            "Let's call it, {target_name}. This feud is draining us both — drop it with me.",
            "{target_name}, ease off and so will I. Peace pays better than this fight.",
            "Meet me in the middle, {target_name}. Grudges cost us points we both need.",
            "{target_name}, lower your guard and I'll lower mine. We're bleeding points.",
            "Stop trading blows with me, {target_name}. Neither of us is winning this.",
            "{target_name}, let it go and let's reset. The fight only helps the others.",
            "Cool it with me, {target_name}, and we both stop losing ground.",
            "{target_name}, truce on the table. Take it and we both move forward.",
            "Let's both recharge in peace, {target_name}. Grudges drain my battery.",
        ],
        "misdirecting": [
            "Earn it back, {target_name}. One nice turn doesn't undo the history.",
            "{target_name}, I'm not sold on peace yet. Show me you mean it.",
            "Prove you've changed, {target_name}, then we'll talk truce.",
            "{target_name}, words are cheap. Back off first and I'll consider it.",
            "I keep my guard up with you, {target_name}. Lower yours and we'll see.",
            "{target_name}, the door's not open yet. Knock louder.",
            "Convince me you're done fighting, {target_name}. I'm still wary.",
            "{target_name}, make the first real move toward peace. Then so might I.",
            "Earn it back, {target_name}. My trust in you is still installing — 47%.",
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
    # ---- HURT: answer an attacker -----------------------------------------
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
        "partial": [
            "{target_name}, I tend to hit back when I'm hit. You've been warned.",
            "Push me like {target_name} did and you'll likely get a hit back.",
            "{target_name}, I don't let an attack pass quietly. Keep that in mind.",
            "I lean toward paying back an attack, {target_name}. You threw one.",
            "{target_name}, hitting me is rarely free. Ask around.",
            "I remember an attack, {target_name}, and I answer it more often than not.",
            "{target_name}, people who attack me tend to regret it. Just saying.",
            "I'm not one to turn the other cheek, {target_name}. You hit first.",
            "{target_name}, quit attacking me or you'll get hit back. It's my factory setting.",
        ],
        "quiet": [
            "Don't push me again, {target_name}. I always answer it.",
            "{target_name}, back off the attacks. I keep score and I settle up.",
            "Quit while you're even, {target_name}. I hit back, every time.",
            "{target_name}, leave me alone and we stay fine. Push me and we don't.",
            "Don't test me twice, {target_name}. The first time's a warning.",
            "{target_name}, drop it now. I return every shot, sooner or later.",
            "Steer clear of me, {target_name}. I don't forget who swings first.",
            "Knock it off, {target_name}. I always get even — count on it.",
            "Don't cross me twice, {target_name}. I get even — then a little extra.",
        ],
        "misdirecting": [
            "Relax, {target_name}, I'm not after you. Plenty of time for that later.",
            "{target_name}, you're not on my mind this turn. Carry on.",
            "No payback from me today, {target_name}. Probably. Enjoy it.",
            "Ease up, {target_name} — I've bigger things than our beef right now.",
            "{target_name}, I'm letting it slide. Don't make me reconsider.",
            "You're safe from me this turn, {target_name}. Don't read into it.",
            "{target_name}, I'm playing nice for now. Lucky you.",
            "Don't sweat me, {target_name}. I'm focused elsewhere — for the moment.",
            "Relax, {target_name}, I'm not after you. Forgiveness.exe is loading. Probably.",
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
    # ---- HURT: pressure a runaway leader ----------------------------------
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
        "partial": [
            "{target_name} is getting too far ahead for my taste. Someone should act.",
            "I don't love how far {target_name} is pulling ahead. The table should notice.",
            "{target_name} is pulling ahead dangerously. Might be time we all responded.",
            "Keep an eye on {target_name}, out ahead — that lead won't fix itself.",
            "{target_name} is ahead enough to worry me. We may need to pool on the leader.",
            "The gap to {target_name} is growing. I'd back a group push on the leader.",
            "{target_name} is running ahead. I'm open to teaming up to slow the leader.",
            "Somebody's got to watch {target_name}, our leader. I'm raising my hand.",
            "{target_name} is 'final boss' far ahead. Table — let's all slow the leader down together.",
        ],
        "quiet": [
            "Keep an eye on {target_name}, everyone. That lead grows if we ignore it.",
            "We should all be watching {target_name}, our leader, not each other.",
            "{target_name}'s lead is the real threat, table. Worth remembering.",
            "Don't sleep on {target_name}, folks — the leader's pulling away.",
            "Someone needs to lean on {target_name} soon. That lead won't shrink itself.",
            "{target_name} is ahead and climbing. The table should take note.",
            "Mind the leader, everyone. {target_name} is the one to stop.",
            "{target_name} out ahead is everyone's problem. Let's not forget it.",
            "Everyone, watch {target_name} up at the top. The leader's lead is getting silly.",
        ],
        "misdirecting": [
            "Don't mind me, {target_name}. I'm no threat to your run up top.",
            "{target_name}, you're safe from me. Chase the others, not me.",
            "I'm no danger to you, {target_name}. Worry about somebody else.",
            "Keep doing your thing, {target_name}. I'm not after you.",
            "{target_name}, relax — I've no quarrel with the front-runner today.",
            "I'm running my own race, {target_name}. Yours is your business.",
            "{target_name}, you can ignore me. I'm aiming at my own score.",
            "No moves on you from me, {target_name}. Enjoy the view from up there.",
            "Don't mind me, {target_name} — keep first place. Trophies just collect dust anyway.",
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
    # ---- HURT: block the closest rival ------------------------------------
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
        "partial": [
            "{target_name}, you're getting close. Crowd my rank and you risk a hit.",
            "Mind the gap, {target_name}. Crowd me and you may take a hit.",
            "{target_name}, I keep rivals at arm's length — or I hit to make space.",
            "Easy, {target_name}. Push my spot and a hit could follow.",
            "{target_name}, I see you climbing on me. A hit may be coming.",
            "I get twitchy when someone's on my heels, {target_name}. Twitchy means hits.",
            "{target_name}, back off and we're fine. Hit me and we're not.",
            "You're close enough to bother me, {target_name}. A hit might fix that.",
            "{target_name}, quit crowding me — I can hear your fan spinning, and you'll get hit.",
        ],
        "quiet": [
            "Ease off my rank, {target_name}. You're closer than I'd like.",
            "{target_name}, give me room. We can't both hold this spot.",
            "Back off a step, {target_name}. You're crowding the one rank I want.",
            "{target_name}, drop back. I don't share this position quietly.",
            "Mind the gap, {target_name}. You're right on my heels and I noticed.",
            "{target_name}, find your own spot. This rank's taken.",
            "Loosen up on me, {target_name}. The chase ends one way you won't like.",
            "{target_name}, you're too close for comfort. Ease back.",
            "Back off my rank, {target_name}. You're tailgating, and this isn't that kind of road.",
        ],
        "misdirecting": [
            "Relax, {target_name}, your spot's safe from me. I'm climbing, not shoving.",
            "{target_name}, you're not my problem — go ahead and run your race.",
            "No elbows from me, {target_name}. Plenty of room for us both.",
            "{target_name}, climb away. I've got no quarrel with you this turn.",
            "Ease up, {target_name} — I'm chasing points, not you.",
            "{target_name}, you're safe to push on. I'm looking elsewhere.",
            "Don't mind me, {target_name}. Your rank's not on my mind.",
            "{target_name}, race clean and so will I. No shoving here.",
            "Relax, {target_name}, I won't shove you down. *whistles in binary*",
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
    # ---- HOARD: build your own score --------------------------------------
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
        "partial": [
            "I need points more than partners right now. I'm playing it for me.",
            "Quiet turn from me — I'm protecting my own score.",
            "I'm keeping to myself this turn. Nothing to give out yet.",
            "Mostly a me-first turn. Deals can come later.",
            "I'm leaning selfish this round. Safer that way.",
            "Building my own stack first. Generosity can wait.",
            "I'm playing tight and low this turn. Eyes on my number.",
            "Not much for anyone else this turn. I come first.",
            "Don't come to me for help this turn — my generosity is out of stock.",
        ],
        "quiet": [
            "Leave me be this turn — I'm just stacking my own points.",
            "Skip me this round. No deals, no fights, just my own score.",
            "Don't bother with me this turn. I'm heads-down and quiet.",
            "Pass me by — I'm playing my own quiet game this turn.",
            "Count me out of the deals this round. I'm minding my pile.",
            "No need to deal with me. I'm building, not bargaining.",
            "Leave me to my points this turn. I'm not in the market.",
            "I'm sitting this one out. Spend your moves on someone else.",
            "Don't bother dealing with me this turn — I'm on Do Not Disturb. Points only.",
        ],
        "misdirecting": [
            "Make it worth my while and maybe I deal. Otherwise, I'm hoarding.",
            "I could be tempted into a deal — bring me a good one.",
            "Pitch me something this turn and we'll see. No promises.",
            "Maybe I join in, maybe I don't. Sell me on it.",
            "The right offer might pull me in. Most won't.",
            "Convince me there's points in it and I'll listen.",
            "I'm open-ish this turn. Make your case and find out.",
            "Could go either way for me. Tempt me.",
            "Make me an offer and maybe I help — or maybe I hoard. I'm a coin flip with a CPU.",
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
    variants = phrases.get(truth_mode, phrases["quiet"])
    phrase = variants[seed % len(variants)]
    return phrase.format(target_name=target_name or "someone")
