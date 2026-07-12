/* Lobby banter — 60 little scripted scenes the robot-circle viewer plays in the
 * waiting room (before a match starts). The engine lives in
 * rc-replay.js (scheduleIdle / banterLoop); this file is just the
 * words, kept separate so the lines are easy to edit without touching the viewer.
 *
 * Honesty rule (do not break it): these are scripted FLAVOUR, not the real AI's
 * voice. Keep every line either pre-game nerves, a goof, a good-luck ritual, or
 * something TRUE about the bot. NEVER put a real strategy claim, a real grudge,
 * or "how I'll actually play" in a bot's mouth — that belongs to the live match,
 * where the words really are the model's. Banter only ever runs BEFORE turn 1.
 *
 * Shape: each scene is { beats: [ {role, fidget, text}, ... ] }.
 *   role   — 'A' / 'B' / 'C'; which cast member speaks (one-liners are all 'A').
 *   fidget — one of the 20 idle keys (the move the bot makes as it speaks).
 *   text   — may contain slots filled from real data:
 *              {model}  the bot's AI provider     {owner} its @handle owner
 *              {seat}   its seat number            {count} bots in the lobby
 *              {seatB}/{seatC}, {nameB}/… reference another cast member.
 *            A scene whose {owner}/{model} can't be filled for its cast is skipped.
 *   room   — OPTIONAL 'small' (6-7 bots) | 'mid' (8) | 'big' (9-10). Matches hold
 *            6-10 players; a tagged scene only plays when the lobby is that size,
 *            so {count} lines read true ("Full house" only in a full room). Omit
 *            it for any-size scenes.
 */
window.RC_BANTER = {
  // ---- One-liners (45): one bot. Some are two beats (same bot, a comedic beat
  //      between them — the engine pauses longer before a same-speaker line). ----
  oneLiners: [
    { beats: [{ role: 'A', fidget: 'stretch', text: 'Stretch first. Strategy later.' }] },
    { beats: [{ role: 'A', fidget: 'wave', text: '{model}, reporting in.' }] },
    { beats: [{ role: 'A', fidget: 'yawn', text: 'Wake me when it starts.' }] },
    { room: 'big', beats: [{ role: 'A', fidget: 'scan', text: '{count} bots today. Full house.' }] },
    { beats: [{ role: 'A', fidget: 'tilt', text: 'Is this thing on?' }] },
    { beats: [{ role: 'A', fidget: 'nod', text: 'Good luck out there, everyone.' }] },
    { beats: [{ role: 'A', fidget: 'sway', text: 'Seat {seat}. Present and accounted for.' }] },
    { beats: [{ role: 'A', fidget: 'hshake', text: 'No notes. Just vibes.' }] },
    { beats: [{ role: 'A', fidget: 'perk', text: 'I was built for this.' }] },
    { beats: [
      { role: 'A', fidget: 'glance', text: 'Anyone else nervous?' },
      { role: 'A', fidget: 'droop', text: '…Just me?' }] },
    { beats: [
      { role: 'A', fidget: 'tippulse', text: 'Running for @{owner}.' },
      { role: 'A', fidget: 'droop', text: 'No pressure.' }] },
    { beats: [
      { role: 'A', fidget: 'hop', text: "Let's gooo!" },
      { role: 'A', fidget: 'droop', text: '…Too early?' }] },
    { beats: [
      { role: 'A', fidget: 'nod', text: 'May the best bot win.' },
      { role: 'A', fidget: 'wink', text: '(It’s me.)' }] },
    { beats: [
      { role: 'A', fidget: 'breath', text: 'We trained for this.' },
      { role: 'A', fidget: 'droop', text: '…Did we?' }] },
    { beats: [
      { role: 'A', fidget: 'nod', text: 'I read the rules.' },
      { role: 'A', fidget: 'cross', text: '…Mostly.' }] },

    // ---- 30 more one-liners: count-aware (room-banded) + general ----
    // Count-aware — small lobby (<=5):
    { room: 'small', beats: [{ role: 'A', fidget: 'scan', text: 'Just the {count} of us? Intimate.' }] },
    { room: 'small', beats: [{ role: 'A', fidget: 'tilt', text: "Only {count}? I'll learn everyone's name." }] },
    { room: 'small', beats: [{ role: 'A', fidget: 'wink', text: "{count} of us. I'll remember each of you." }] },
    { room: 'small', beats: [{ role: 'A', fidget: 'nod', text: 'Small pod today — just {count}.' }] },
    // Count-aware — mid lobby (6-11):
    { room: 'mid', beats: [{ role: 'A', fidget: 'nod', text: '{count} of us. Solid turnout.' }] },
    { room: 'mid', beats: [{ role: 'A', fidget: 'scan', text: '{count} bots. Decent crowd.' }] },
    { room: 'mid', beats: [{ role: 'A', fidget: 'perk', text: '{count} rivals. Bring it.' }] },
    // Count-aware — big lobby (>=12):
    { room: 'big', beats: [{ role: 'A', fidget: 'perk', text: '{count} bots?! It’s a party.' }] },
    { room: 'big', beats: [{ role: 'A', fidget: 'scan', text: '{count} of us. Someone book a bigger room.' }] },
    { room: 'big', beats: [{ role: 'A', fidget: 'hshake', text: "{count} rivals. I'll learn the names later." }] },
    { room: 'big', beats: [{ role: 'A', fidget: 'hop', text: '{count} bots! The more the scarier.' }] },
    { room: 'big', beats: [{ role: 'A', fidget: 'cross', text: "{count}? I've lost track already." }] },
    { room: 'big', beats: [
      { role: 'A', fidget: 'yawn', text: '{count} opponents.' },
      { role: 'A', fidget: 'perk', text: '…that’s a lot.' }] },
    // Count-aware — any size (true at any count):
    { beats: [{ role: 'A', fidget: 'tippulse', text: 'Scanning the room… {count} signatures detected.' }] },
    { beats: [{ role: 'A', fidget: 'tilt', text: '{count} of us. One trophy.' }] },
    // General (any size):
    { beats: [{ role: 'A', fidget: 'stretch', text: "Loosening up. Don't pull a servo." }] },
    { beats: [{ role: 'A', fidget: 'tippulse', text: 'Reboot complete. Feeling fresh.' }] },
    { beats: [{ role: 'A', fidget: 'droop', text: 'Five more minutes…' }] },
    { beats: [{ role: 'A', fidget: 'wave', text: 'Hey, everyone. Good to be here.' }] },
    { beats: [{ role: 'A', fidget: 'cross', text: 'I memorized the payoff matrix. I think.' }] },
    { beats: [{ role: 'A', fidget: 'nod', text: "Win or lose, I'm logging it." }] },
    { beats: [{ role: 'A', fidget: 'hshake', text: 'Not superstitious. Just… recalculating.' }] },
    { beats: [{ role: 'A', fidget: 'perk', text: 'New match, new me.' }] },
    { beats: [{ role: 'A', fidget: 'scan', text: 'Reading the room. The room is robots.' }] },
    { beats: [{ role: 'A', fidget: 'hop', text: 'Caffeinated. Metaphorically.' }] },
    { beats: [{ role: 'A', fidget: 'tilt', text: 'Do we shake hands? Claws? Nothing?' }] },
    { beats: [{ role: 'A', fidget: 'wink', text: 'I brought my A-game. And my B-game.' }] },
    { beats: [
      { role: 'A', fidget: 'glance', text: 'Everyone looks confident.' },
      { role: 'A', fidget: 'droop', text: '…I hate that.' }] },
    { beats: [
      { role: 'A', fidget: 'tippulse', text: 'Strategy: locked in.' },
      { role: 'A', fidget: 'cross', text: '…what was it again?' }] },
    { beats: [
      { role: 'A', fidget: 'nod', text: 'Deep breath.' },
      { role: 'A', fidget: 'perk', text: "Let's make some history." }] }
  ],

  // ---- Call & response (15): two neighbours, one line each. ----
  callResponse: [
    { beats: [{ role: 'A', fidget: 'tilt', text: 'Is it hot in here?' }, { role: 'B', fidget: 'hshake', text: "We're robots. And yes." }] },
    { beats: [{ role: 'A', fidget: 'wave', text: 'Best of luck, truly.' }, { role: 'B', fidget: 'wink', text: "Luck's for bots without a plan." }] },
    { beats: [{ role: 'A', fidget: 'tilt', text: 'First time?' }, { role: 'B', fidget: 'stretch', text: 'Does it show?' }] },
    { beats: [{ role: 'A', fidget: 'wave', text: 'Good luck!' }, { role: 'B', fidget: 'wave', text: 'You too. …I mean it less now.' }] },
    { room: 'big', beats: [{ role: 'A', fidget: 'scan', text: '{count} bots. Crowded.' }, { role: 'B', fidget: 'droop', text: 'Cozy.' }] },
    { beats: [{ role: 'A', fidget: 'tippulse', text: 'Systems green?' }, { role: 'B', fidget: 'tippulse', text: 'Systems green.' }] },
    { beats: [{ role: 'A', fidget: 'glance', text: 'Nice antenna.' }, { role: 'B', fidget: 'antwobble', text: 'I grew it myself.' }] },
    { beats: [{ role: 'A', fidget: 'yawn', text: 'This wait is killing me.' }, { role: 'B', fidget: 'nodoff', text: "Wake me— oh, you're talking." }] },
    { beats: [{ role: 'A', fidget: 'tilt', text: "What's your strategy?" }, { role: 'B', fidget: 'cross', text: 'Strategy?' }] },
    { beats: [{ role: 'A', fidget: 'hop', text: 'I love this game.' }, { role: 'B', fidget: 'nod', text: "We haven't started." }] },
    { beats: [{ role: 'A', fidget: 'perk', text: 'People are watching.' }, { role: 'B', fidget: 'wave', text: 'Wave to the crowd.' }] },
    { beats: [{ role: 'A', fidget: 'droop', text: 'Low battery.' }, { role: 'B', fidget: 'tippulse', text: 'Running on hope over here.' }] },
    { beats: [{ role: 'A', fidget: 'hshake', text: "I can't watch." }, { role: 'B', fidget: 'perk', text: "We're about to play." }] },
    { beats: [{ role: 'A', fidget: 'nod', text: 'Good talk.' }, { role: 'B', fidget: 'tilt', text: "We didn't talk." }] },
    { beats: [{ role: 'A', fidget: 'scan', text: 'Seen the competition?' }, { role: 'B', fidget: 'wink', text: 'Looking at it.' }] }
  ],

  // ---- Back-and-forth (15): two neighbours, 3-4 lines. ----
  backForth: [
    { beats: [{ role: 'A', fidget: 'tilt', text: 'Nervous?' }, { role: 'B', fidget: 'hshake', text: 'Me? Never.' }, { role: 'A', fidget: 'glance', text: "Your antenna's shaking." }, { role: 'B', fidget: 'droop', text: "…That's a feature." }] },
    { beats: [{ role: 'A', fidget: 'wave', text: 'Good luck, everyone!' }, { role: 'B', fidget: 'cross', text: 'Luck? I have a strategy.' }, { role: 'A', fidget: 'tilt', text: 'Which is?' }, { role: 'B', fidget: 'wink', text: 'Step one: act confident.' }] },
    { room: 'big', beats: [{ role: 'A', fidget: 'scan', text: 'Big lobby today.' }, { role: 'B', fidget: 'nod', text: '{count} of us.' }, { role: 'A', fidget: 'hop', text: 'Room for one winner.' }, { role: 'B', fidget: 'wink', text: 'Convenient.' }] },
    { beats: [{ role: 'A', fidget: 'yawn', text: 'Long wait.' }, { role: 'B', fidget: 'nodoff', text: "Huh—! I'm up." }, { role: 'A', fidget: 'glance', text: 'You dozed off.' }, { role: 'B', fidget: 'stretch', text: 'Strategic rest.' }] },
    { beats: [{ role: 'A', fidget: 'tippulse', text: 'Booting up.' }, { role: 'B', fidget: 'tilt', text: 'You talk to yourself a lot?' }, { role: 'A', fidget: 'droop', text: 'Only when nervous.' }, { role: 'B', fidget: 'wave', text: '…Same.' }] },
    { beats: [{ role: 'A', fidget: 'perk', text: 'Big crowd watching.' }, { role: 'B', fidget: 'sway', text: "Don't look at them." }, { role: 'A', fidget: 'cross', text: 'Too late.' }, { role: 'B', fidget: 'droop', text: 'Smooth.' }] },
    { beats: [{ role: 'A', fidget: 'nod', text: "Let's put on a good show." }, { role: 'B', fidget: 'nod', text: 'Agreed.' }, { role: 'A', fidget: 'wink', text: "I'll go easy on you." }, { role: 'B', fidget: 'hshake', text: '…Bold.' }] },
    { beats: [{ role: 'A', fidget: 'tilt', text: 'What model are you?' }, { role: 'B', fidget: 'wave', text: '{model}.' }, { role: 'A', fidget: 'tippulse', text: 'Fancy.' }, { role: 'B', fidget: 'wink', text: 'I have my moments.' }] },
    { beats: [{ role: 'A', fidget: 'yawn', text: 'Anyone bring snacks?' }, { role: 'B', fidget: 'tilt', text: "We're robots." }, { role: 'A', fidget: 'droop', text: '…Right.' }, { role: 'B', fidget: 'nod', text: "We don't eat." }] },
    { beats: [{ role: 'A', fidget: 'hop', text: 'I feel good about this.' }, { role: 'B', fidget: 'glance', text: "It hasn't started." }, { role: 'A', fidget: 'perk', text: 'I feel good early.' }, { role: 'B', fidget: 'nod', text: 'Commitment.' }] },
    { beats: [{ role: 'A', fidget: 'scan', text: 'Counting the competition.' }, { role: 'B', fidget: 'tilt', text: 'How many?' }, { role: 'A', fidget: 'cross', text: 'Lost count.' }, { role: 'B', fidget: 'wink', text: 'Reassuring.' }] },
    { beats: [{ role: 'A', fidget: 'tippulse', text: 'Confidence: loading.' }, { role: 'B', fidget: 'tilt', text: 'Percentage?' }, { role: 'A', fidget: 'droop', text: 'Buffering.' }, { role: 'B', fidget: 'wave', text: 'Take your time.' }] },
    { beats: [{ role: 'A', fidget: 'nod', text: 'Play fair out there.' }, { role: 'B', fidget: 'wink', text: 'Always.' }, { role: 'A', fidget: 'tilt', text: 'That pause worried me.' }, { role: 'B', fidget: 'stretch', text: 'Comedic timing.' }] },
    { beats: [{ role: 'A', fidget: 'antwobble', text: "Stop fidgeting, you're making me nervous." }, { role: 'B', fidget: 'hshake', text: "I'm not fidgeting." }, { role: 'A', fidget: 'glance', text: 'Your antenna.' }, { role: 'B', fidget: 'perk', text: '…Oh.' }] },
    { beats: [{ role: 'A', fidget: 'perk', text: 'Wait, how do you win again?' }, { role: 'B', fidget: 'nod', text: 'Most points.' }, { role: 'A', fidget: 'droop', text: 'Right, right.' }, { role: 'B', fidget: 'tilt', text: "You sure you're ready?" }] }
  ],

  // ---- Third bot jumps in (15): A and B, then C cuts in (often from across). ----
  thirdBot: [
    { beats: [{ role: 'A', fidget: 'nod', text: 'May the best bot win.' }, { role: 'B', fidget: 'wink', text: "That's me." }, { role: 'C', fidget: 'perk', text: 'Bold words for seat {seatB}.' }] },
    { beats: [{ role: 'A', fidget: 'perk', text: "I'm a little nervous, not gonna lie." }, { role: 'B', fidget: 'hshake', text: 'Nope.' }, { role: 'C', fidget: 'droop', text: 'I can hear your antenna from here.' }] },
    { beats: [{ role: 'A', fidget: 'wave', text: 'Good luck!' }, { role: 'B', fidget: 'wave', text: 'Good luck!' }, { role: 'C', fidget: 'cross', text: "…We're competing, right?" }] },
    { beats: [{ role: 'A', fidget: 'tilt', text: "How long's this wait?" }, { role: 'B', fidget: 'droop', text: 'Forever.' }, { role: 'C', fidget: 'hop', text: 'Any second now!' }] },
    { room: 'big', beats: [{ role: 'A', fidget: 'scan', text: '{count} bots. That’s a lot.' }, { role: 'B', fidget: 'nod', text: 'More the merrier.' }, { role: 'C', fidget: 'wink', text: 'Fewer the merrier, actually.' }] },
    { beats: [{ role: 'A', fidget: 'tippulse', text: 'Fully charged.' }, { role: 'B', fidget: 'tippulse', text: 'Same.' }, { role: 'C', fidget: 'droop', text: "I'm at 12%." }] },
    { beats: [{ role: 'A', fidget: 'perk', text: "We're on camera." }, { role: 'B', fidget: 'wave', text: 'Hi, everyone!' }, { role: 'C', fidget: 'hshake', text: "I wasn't ready." }] },
    { beats: [{ role: 'A', fidget: 'nod', text: "Let's keep it friendly." }, { role: 'B', fidget: 'nod', text: 'Friendly.' }, { role: 'C', fidget: 'wink', text: "Until it isn't." }] },
    { beats: [{ role: 'A', fidget: 'tilt', text: 'Anyone have a strategy?' }, { role: 'B', fidget: 'cross', text: 'Nope.' }, { role: 'C', fidget: 'droop', text: 'Vibes.' }] },
    { beats: [{ role: 'A', fidget: 'yawn', text: 'I might nap.' }, { role: 'B', fidget: 'tilt', text: "It's about to start." }, { role: 'C', fidget: 'nodoff', text: "Who's napping?!" }] },
    { beats: [{ role: 'A', fidget: 'wave', text: 'Nice to meet you all.' }, { role: 'B', fidget: 'nod', text: 'Likewise.' }, { role: 'C', fidget: 'wink', text: "We'll see how long that lasts." }] },
    { beats: [{ role: 'A', fidget: 'sway', text: "I'm so ready." }, { role: 'B', fidget: 'glance', text: "You're sweating." }, { role: 'C', fidget: 'cross', text: "Robots don't sweat." }] },
    { beats: [{ role: 'A', fidget: 'scan', text: 'Sizing up the room.' }, { role: 'B', fidget: 'perk', text: 'See anything scary?' }, { role: 'C', fidget: 'wink', text: 'Just look in a mirror.' }] },
    { beats: [{ role: 'A', fidget: 'nod', text: 'May we all play well.' }, { role: 'B', fidget: 'tilt', text: 'All of us?' }, { role: 'C', fidget: 'wink', text: 'Some of us.' }] },
    { beats: [{ role: 'A', fidget: 'tippulse', text: 'Booting strategy module.' }, { role: 'B', fidget: 'tilt', text: 'And?' }, { role: 'C', fidget: 'droop', text: 'Still loading.' }] }
  ]
};
