"""Canonical bot phrase library.

Talk is keyed by the action the bot has already decided to take this turn, so
the message points at the real move instead of hedging. The truth mode then
decides how the bot frames that move:

- ``honest`` / ``partial``: HELP lines telegraph the real move — announcing help
  *is* the persuasion, so they carry an offer word and name the target. HURT
  lines never just announce the hit (in a talk-then-act game that only warns the
  victim); instead they do persuasive work appropriate to the attack:

  * ``hit_back`` — a deterrent: "attack me and you get hit back" (carries a
    threat word, names the attacker).
  * ``curb_leader`` — a rally to the table to gang up on the leader (carries a
    leader word, names the leader → a leader_warning, not a personal threat).
  * ``block_rival`` — a warning to a crowding rival to back off (threat word,
    names the rival).
  * ``finish_strong`` — disguised: a late knockout is never announced, so these
    are victimless table-wide menace that name no one.

  This is what feeds :mod:`app.engine.bots.signals`, so other bots read the
  tell — or the deliberate absence of one.
- ``quiet``: a directional hint (warm for HELP, cold for HURT) that commits to
  less but is never empty "watching the table" filler.
- ``misdirecting``: downplays the real move without lying outright.
- ``false``: a confident lie. A bot about to HURT or HOARD claims partnership,
  so the bluff shows up as a (false) cooperation signal; a bot about to HELP
  denies it. The truthfulness knob decides how often this fires.

Every (intent, truth mode) bucket holds the same number of distinct variants so
bots vary their wording turn to turn; see ``VARIANTS_PER_BUCKET`` and the tests
in ``tests/test_bot_talk_telegraph.py`` that pin the count and the word
contract.
"""

from __future__ import annotations

# The intent every fallback path lands on; it has all five truth modes.
FALLBACK_INTENT = "play_own_game"

# Each (intent, truth mode) bucket carries this many distinct lines.
VARIANTS_PER_BUCKET = 8

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
        ],
        "quiet": [
            "I back players who play straight with me. {target_name}, that could be you.",
            "{target_name} has been worth working with. I don't forget that.",
            "I reward people who don't stab me. {target_name}, take the hint.",
            "Good play earns good play. {target_name} knows where I lean.",
            "I keep an eye on who's reliable. {target_name} is on that short list.",
            "{target_name} hasn't crossed me. That counts for something this turn.",
            "I'm friendlier than I look — to the right player. {target_name}, your move.",
            "Steady players get steady treatment from me. {target_name}, stay steady.",
        ],
        "misdirecting": [
            "I'm mostly minding my own score this turn. We'll see who's worth it.",
            "No promises to anyone yet. I'm still weighing my options.",
            "Don't read too much into me this turn. My plans stay loose.",
            "I haven't picked a side. The board's still settling.",
            "I'm keeping my cards down for now. Patience.",
            "Could go a few ways this turn. I'm not tipping my hand.",
            "I'm watching more than moving right now. Ask me later.",
            "Nothing locked in from me yet. I like to keep people guessing.",
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
        ],
        "quiet": [
            "I keep the deals that work, and mine with {target_name} works.",
            "Loyalty runs both ways. {target_name} knows where I stand.",
            "I don't ditch a partner mid-stride. {target_name}, we're good.",
            "Steady is good. {target_name} and I have steady.",
            "No drama from me toward {target_name}. We're fine.",
            "{target_name} and I have an understanding. It holds this turn.",
            "I stick with what's working, and {target_name} is working.",
            "Quiet confidence in {target_name} from me. No need to shout it.",
        ],
        "misdirecting": [
            "Things change fast. I'm not locked in with {target_name} forever.",
            "{target_name} shouldn't get too comfortable. I keep my options open.",
            "I may need to rethink my deal with {target_name} soon.",
            "Even the best runs end. {target_name}, don't bank on me.",
            "I'm loyal until I'm not. {target_name}, read into that.",
            "Don't assume anything about me and {target_name} this turn.",
            "I keep my exits open, even with {target_name}.",
            "{target_name} and I are fine — for now. Emphasis on for now.",
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
        ],
        "quiet": [
            "I pay my debts. {target_name} did me right.",
            "Helpers get remembered. {target_name} knows what they did.",
            "I keep a ledger, and {target_name} is in the black with me.",
            "{target_name} did me a solid. I don't forget those.",
            "Good turns come back around. {target_name}, yours might this turn.",
            "I look after the players who looked after me. {target_name} qualifies.",
            "{target_name} put points my way once. That sticks with me.",
            "Debts get paid in my game. {target_name} is owed.",
        ],
        "misdirecting": [
            "I'm focused on my own climb. Favors can wait.",
            "Not sure I owe anyone this turn. I'm playing tight.",
            "I'm keeping what I've got to myself for now.",
            "Debts? Maybe later. Right now I look out for me.",
            "I don't rush to repay anyone. Timing matters.",
            "Plenty backed me. Doesn't mean I move today.",
            "I'm slow to settle up. Don't expect it this turn.",
            "My ledger can wait a round. I've got my own math.",
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
        ],
        "quiet": [
            "I'd rather mend a fence than burn one. {target_name}, your call.",
            "Grudges cost points. I might let mine with {target_name} go.",
            "Peace beats a feud. {target_name} should think it over.",
            "I don't hold grudges forever. {target_name}, that includes you.",
            "Feuding with {target_name} is getting us both nowhere. Worth noting.",
            "There's an off-ramp here for {target_name} and me. Just saying.",
            "I'm tired of trading blows with {target_name}. Could be time to stop.",
            "Cooler heads win this. {target_name}, I'm open to cooler.",
        ],
        "misdirecting": [
            "My read on {target_name} hasn't changed. Talk is cheap.",
            "I'm not rushing to fix things with {target_name}. We'll see.",
            "Don't expect a soft touch from me toward {target_name} yet.",
            "{target_name} hasn't earned my trust back. Words aren't enough.",
            "I'm wary of {target_name} still. One nice turn won't flip me.",
            "Peace with {target_name}? Not sold yet.",
            "{target_name} and I have a ways to go. I'm not there.",
            "I keep my guard up with {target_name}. Old wounds and all.",
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
        ],
        "quiet": [
            "I keep score, and {target_name}'s tab is overdue.",
            "Some hits don't get forgotten. {target_name} knows which.",
            "I'm patient, not soft. {target_name}, remember that.",
            "I file away every shot taken at me. {target_name} has a file.",
            "Cross me and it lingers. {target_name}, it's lingering.",
            "I don't forgive and forget. {target_name}, mostly the second part.",
            "What goes around comes around. {target_name}, yours is circling.",
            "I've got a long memory, {target_name}. Yours is in it.",
        ],
        "misdirecting": [
            "I'd rather build than chase payback right now. Mostly.",
            "I'm not here for revenge this turn — I've got bigger plans.",
            "{target_name} and I have history, but I'm looking elsewhere today.",
            "Payback's tempting, but I'm playing the long game.",
            "I'm above grudges this turn. Probably.",
            "Old scores can wait. I'm focused on points, not people.",
            "I'm letting it slide this turn. Don't get used to it.",
            "Revenge is a distraction. I'm skipping it for now.",
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
        ],
        "quiet": [
            "Big leads draw fire. {target_name} is up there for a reason, and a risk.",
            "The frontrunner wears a bullseye. {target_name} knows it.",
            "I'm watching the top of the board, and {target_name} is sitting on it.",
            "Leads like {target_name}'s make enemies. Just an observation.",
            "{target_name}'s out front. Out front is a lonely, risky place.",
            "Someone usually clips the leader. {target_name}, food for thought.",
            "{target_name} is the one to beat. The whole table sees it.",
            "Top score brings a crosshair with it, {target_name}. That's the game.",
        ],
        "misdirecting": [
            "I'm chasing my own points, not {target_name}'s crown. For now.",
            "Slowing {target_name} down isn't my plan this turn. I'd rather build.",
            "{target_name} can sit pretty up there. I'm playing my own game.",
            "Let {target_name} keep the crown. I'm climbing my own way.",
            "I'm not the one to fear, {target_name}. Look elsewhere.",
            "{target_name} out in front? Good for them. I've got my own race.",
            "I'd rather grow my score than shrink {target_name}'s. This turn, anyway.",
            "No crown-chasing from me today. {target_name} can relax.",
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
        ],
    },
    # ---- HURT: late-game strike -------------------------------------------
    "finish_strong": {
        "honest": [
            "It's late and the nice phase is over. Watch yourselves.",
            "Endgame now. I'm done making friends — points are all that matter.",
            "Last turns. Don't count on me to play soft anymore.",
            "The finish is here. Somebody's getting left behind, and it's not me.",
            "Late game, gloves off. I'm playing to win, not to be liked.",
            "We're at the sharp end now. I'd watch my back if I were you.",
            "No more easy turns from me. The ending decides everything.",
            "It's crunch time. I'm chasing the win, whatever it costs someone.",
        ],
        "partial": [
            "We're late, and I'm getting less generous by the turn.",
            "The ending's close. I'm tightening up — don't expect favors.",
            "Crunch time changes me. Fair warning to everyone.",
            "I play harder as the clock runs down. We're down to it now.",
            "Late game, less mercy. Read into that what you want.",
            "I'm shifting into closing mode. It's not a friendly mode.",
            "The soft turns are behind us. I'm here to finish on top.",
            "Don't get comfortable this late. I'm not done making moves.",
        ],
        "quiet": [
            "The finish is where masks drop. Brace yourselves, all of you.",
            "Late rounds get ugly. Don't say I didn't warn the table.",
            "Endgame math is cold. Somebody ends up on the wrong side of it.",
            "Nice players finish nice. I'm not finishing nice.",
            "The closer we get, the colder I play. Take note.",
            "Sentiment's a luxury I drop late. Fair warning to the room.",
            "Last call changes people. It's changing me right now.",
            "The ending writes itself rough. You'll all see soon enough.",
        ],
        "misdirecting": [
            "I'm just trying to land softly this round, not start anything.",
            "Late as it is, I'd rather build than swing at {target_name}.",
            "{target_name}, I'm playing the finish safe — nothing aimed your way.",
            "I'm coasting to the end, {target_name}. No fireworks from me.",
            "No big swings from me this late. {target_name}, relax.",
            "I'm protecting what I've got, not chasing {target_name}.",
            "Quiet finish for me. {target_name}, you're not on my mind.",
            "I'd rather hold steady than gamble on a fight with {target_name}.",
        ],
        "false": [
            "{target_name}, let's coast home together. Mutual help?",
            "{target_name}, no need to scrap now — I'll help you home.",
            "{target_name}, truce for the last stretch. Help for help.",
            "{target_name}, let's both finish strong — pair up for it.",
            "{target_name}, I've got your back to the line. Help me too?",
            "{target_name}, we can split this finish. Pair up to the end?",
            "{target_name}, no fighting at the buzzer. Mutual help, deal?",
            "{target_name}, partner with me for the last push. We both win.",
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
        ],
        "quiet": [
            "I watch whoever's nearest on the board. {target_name}, that's you.",
            "Close races get rough. {target_name} is the one I'm racing.",
            "My eyes are on the rival, not the leader. {target_name} fits.",
            "I guard my rank quietly. {target_name}, stay aware.",
            "The one right behind me gets watched. {target_name}, that's you.",
            "Rivalries simmer before they boil. {target_name} and I are simmering.",
            "I know exactly who's chasing me, {target_name}. I always do.",
            "Neck-and-neck makes me nervous, and a little mean. {target_name}, noted.",
        ],
        "misdirecting": [
            "I'm focused on climbing, not shoving {target_name} down.",
            "{target_name} and I can both rise — I'm not aimed at you. For now.",
            "I'd rather outscore {target_name} than swing at them.",
            "No need to scrap with {target_name} — there's room for both of us.",
            "{target_name}, you're not my problem this turn. Climb away.",
            "I beat rivals by scoring, not shoving. {target_name}, relax.",
            "I'm not wasting a move on {target_name} this turn. Bigger fish.",
            "{target_name} and I can race clean. Nothing nasty from me.",
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
        ],
        "quiet": [
            "I'm playing this one close to the chest.",
            "No moves on anyone from me. I'm holding steady.",
            "Head down, points safe. That's my turn.",
            "Steady and quiet. I'm not stirring anything up.",
            "I'm in a holding pattern this turn. Banking what I've got.",
            "Low profile from me this round. Nothing flashy.",
            "I'm letting others make the noise. I'll take the points.",
            "Calm turn from me. Just stacking, no drama.",
        ],
        "misdirecting": [
            "I might have room to help someone if the right opening shows.",
            "There could be a partnership in me — for the right player.",
            "I'm open to a deal if someone makes it worth my while.",
            "Maybe I help someone this turn, maybe I don't. Depends.",
            "A good offer might pull me into a partnership. Might.",
            "I'm not against teaming up if the numbers work.",
            "Someone could earn my help this turn. The bar is high.",
            "I'd consider a deal. Make me a good one.",
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
