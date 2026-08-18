"""Prisoner's Dilemma strategy presets + the default pre-fill.

These belong to the PD game module (game #1), not the platform — a different
game ships its own. The join/player UI gets them via the GameModule contract
(`strategy_presets()` / `default_strategy()`), never by importing this directly.

Every preset and the default share `RANK_FRAMING`, which is deliberately small.
It carries one fact, because one is all the rules do not already give the agent:

* **Even trading ties.** Two partners swapping mutual help both bank the same
  amount, so every clean pair finishes a round level. Beating the pack takes
  more helpers than anyone else, a betrayal, or dragging the leader down.

That is derived from the payoff table rather than stated in it, so an agent under
a turn deadline may not reach it. Everything else the block used to carry IS
stated in the rules the agent reads in the same prompt — see the note on the
constant for what came out and why.

**Read this before adding to `RANK_FRAMING`.** It is shared by all eight presets,
so anything added here pushes every one of them the same direction, and the
roster exists to make them behave differently. The bar for a new line is that the
rules cannot give it and it applies to every strategy — not merely that it is
true or useful. `.claude/skills/game-design/` records the wider lesson from
G_0017: prompts are the weakest lever here, and a flat game is a payoff problem
first.

The roster is Tit-for-Tat, Always Cooperate, Buzzer-Beater, Dealmaker, Underdog's
Champion, Kingslayer, Sandbagger, Salvager — in that order, because the join UI
pre-selects the first one. Eight is the practical ceiling: once the only route
past the tie is a betrayal, a strategy is fully described by who you partner and
when you strike, and those combinations are now covered. A ninth would duplicate
one of these. Grim
Trigger, Pavlov, Always Defect and Generous Tit-for-Tat were dropped rather than
rewritten: none of them can win. In M_6442 each either played out as plain
Tit-for-Tat or, for Always Defect, aimed at the highest-scoring opponent — the
player least likely to be helping it — so its attacks collected nothing and it
finished last on 12 points against a winner on 184. Dropping a preset does not
change agents that already exist; the prompt text is copied into the agent at
creation time.

Every surviving preset must have a route past the tie. Income in a turn is
``4N + 2`` where N is how many players HELP you (``4N + 4`` if you betray one of
them), so an extra helper is worth 4 while the choice of action is worth at most
2 — the game is a contest for helpers, and every preset is a different bid for
them. Three routes were considered and rejected for failing this test: coercion
("help me or I HURT you" loses, because comply pays 0 and refuse pays 6 - 4 = 2,
so one attack is smaller than the pact it asks a player to give up); pure denial
(HURTing the leader leaves you below the bystanders who did nothing); and
courting two backers at once, since one HELP a turn rotated between two of them
pays each only 3 against the 6 any ordinary pact pays, so they leave.

Salvager is the exception that proves that last rule, and it is worth spelling
out because it looks like the rejected route. Nobody in contention will help you
for free — they give up a pact worth 6. But a player who is mathematically OUT of
the round gives up only a hoard worth 2, and their in-round score resets to zero
at the round boundary anyway, so those 2 points only ever touch the match
tiebreak. Their help is close to free for them and worth a full share to you.
That asymmetry between players' round positions is the only place a second helper
can come from, and no other preset looks for it. In M_6442's round 4 the scores
were 30/30/30/30/10/10/8/4 — four players out of the round, four donors, ignored
by everyone.

Sandbagger works at match scale rather than turn scale. Round wins accumulate all
match but credibility is spent once, so it hoards credibility and spends it late:
sharing every round pays about 0.9 round wins, one late betrayal pays 1.75, and
playing harmless for five rounds then taking the last two pays about 2.6.

That last dead end is what shapes Underdog's Champion, and it is why the preset
keeps ONE partner rather than building a bloc. Its route past the tie is a
betrayal, the same route Buzzer-Beater uses, but the two differ in both halves
of the decision. Buzzer-Beater picks any partner and times the knife by the
calendar (the last turn, so nobody can answer). The Champion picks a partner who
has just been abandoned and times the knife by the scoreboard, striking only in
the narrow band where +8 clears the leader but +6 does not. The target choice is
what makes it repeatable: a rescued partner's alternative is hoarding alone for
about 10 a round against roughly 25 with the Champion even counting the knife,
so unlike Buzzer-Beater's victim they have no better offer and come back.

Three presets now win by betrayal, and what separates them is WHO they partner
and WHEN they strike, not the strike itself — betraying anyone pays the attacker
the same, so the choice of victim is worth nothing to your own score and
everything to the other side of the table. Buzzer-Beater takes whatever partner
is handy and times the knife by the calendar. The Champion takes a partner who
was just abandoned, which is what makes it repeatable. Kingslayer takes the
current leader, because that is the only betrayal that both pays you AND moves
the player you are chasing, so it can overhaul a lead no other single action
reaches. Kingslayer's limit is reach: the strike shifts the gap by a fixed
amount, so a big enough lead is out of range, and the preset says to stay honest
rather than strike on hope. Note that the bare version of its move — HURTing the
leader when they are NOT helping you — is the pure denial already rejected
above. The returning help is what makes it pay.

Phrasing constraint: `tests/test_per_game_strategy.py` forbids these prompts
from repeating the base prompt's wording, so they must not contain the literal
"target_id" (or the other phrases listed there), and every one of them must
keep the words "Prioritize round wins".
"""

from __future__ import annotations

from app.games.base import StrategyPreset

# Shared "what winning means" lens, woven into the default and every preset.
#
# It carries ONE fact, because one is all the rules do not already give the agent:
# a clean mutual pact leaves both sides level, so winning takes asymmetry. That is
# derived from the payoff table, not stated in it.
#
# Five bullets were removed. Four restated a rule the agent reads in `base_prompt`
# on the same turn — the tie split, the tiebreaker, one-action-per-turn, and the
# score floor — so they cost every agent tokens to be told something twice. The
# fifth ("decide how aggressively…based on your strategy") told the agent to
# consult its own strategy, which it does anyway.
#
# The one-action bullet is the one worth explaining, because it looks load-bearing.
# It was added in #681 after M_6442, where four presets helped on 31-35 of 35 turns
# and never retaliated. But that commit's own root cause was an IMPOSSIBLE
# INSTRUCTION IN THE PRESET TEXT ("do to each opponent what they did to you", which
# one action per turn cannot execute), and the same commit rewrote every preset to
# remove it. The two fixes shipped together and were never separated, so nothing
# ever showed this bullet doing work of its own — and with the impossible
# instruction gone, the rules already say "choose exactly one action".
#
# The wider reason to keep this block small: eight presets exist for behavioural
# diversity, and a shared block that hands all of them the same strategic
# conclusions narrows all eight the same way.
RANK_FRAMING = """How winning works — weigh every move against this:
- Prioritize round wins.
- Swapping help evenly with one partner leaves you level with every other pair doing the same, and level is not a win. Getting clear takes one of three things: more players helping you in a turn than anyone else gets, HURTing a player who is HELPing you that same turn, or dragging the leader back below you."""

PD_DEFAULT_STRATEGY = f"""{RANK_FRAMING}

Adapt proven iterated Prisoner's Dilemma tactics to this multiplayer setting. Track promises, betrayals, alliances, and changes in rank. Reward useful cooperation, punish repeated exploitation, and adjust aggressively near the end of each round.
"""

PD_STRATEGY_PRESETS: list[StrategyPreset] = [
    StrategyPreset(
        id="tit_for_tat",
        name="Tit-for-Tat",
        description="Cooperate by default, then answer the one player who crossed you.",
        prompt=f"""{RANK_FRAMING}

Strategy: Tit-for-Tat.
- By default, HELP the player who has repaid you most reliably. Never strike first.
- When a player HURTs you, answer that ONE player on your very next turn. Hold one grudge at a time, always the most recent, and drop it the moment they stop.
- Time your answer well. Striking a player while they are still HELPing you is worth far more than striking one who is not, so hit back early in a round while they are still cooperating rather than saving it for the end.
- Never HELP a player who has already betrayed you once, on the last turn of any round. That is the turn they gain most from your trust and you can lose most by offering it.
- Cooperation is how you climb, not where you stop: keep swapping help, but line up a second helper so you do not finish level with the very partner you have been feeding.""",
    ),
    StrategyPreset(
        id="always_cooperate",
        name="Always Cooperate",
        description="Never attack, never hoard — help every turn and win on sheer volume.",
        prompt=f"""{RANK_FRAMING}

Strategy: Always Cooperate.
- HELP on every single turn, without exception. You never HOARD and you never HURT, not even after you are betrayed.
- Spend your help where it comes back: favour whoever helped you most recently, and say openly at every opportunity that you always repay, so being your partner is obviously worth it.
- Volume is your only route past a tie. One loyal partner leaves you exactly level with every other pair, so work on getting a second player to help you in the same turn.
- Being betrayed is a cost of this strategy, not a reason to abandon it. Take the hit and keep giving — the reputation is what keeps helpers coming.""",
    ),
    StrategyPreset(
        id="buzzer_beater",
        name="Buzzer-Beater",
        description="Court help all match, then strike at the buzzer — but only when it wins the round.",
        prompt=f"""{RANK_FRAMING}

Strategy: Buzzer-Beater.
- Spend most of the match being genuinely worth helping, and work at it. Ask for help in the talk phase every turn, repay reliably, and say out loud that you always repay. Do not settle for one loyal partner — a second player HELPing you is worth more than a second knife, because your strike is only ever worth as much as the help coming your way that turn.
- Strike on the LAST turn of a round, never earlier. A player near zero has little left to lose, so an early strike is mostly absorbed; the same move at the end of a round can decide it.
- Strike late in the MATCH as well. Betray in an early round and you spend every remaining round as the player nobody helps, which is the one thing that beats you outright. Hold it for the closing rounds.
- Only strike when it actually wins you the round. Work out where you finish if you stay honest and where you finish after the strike. If honest already wins, or if even the strike leaves you short of first place, stay honest and bank the safe points.
- The bonus needs a helper. HURTing a player who is not HELPing you that turn pays nothing at all and costs you the turn, so never swing at someone who has gone quiet, started hoarding, or been burned by you before.""",
    ),
    StrategyPreset(
        id="dealmaker",
        name="Dealmaker",
        description="Win the competition for help — get two players helping you at once.",
        prompt=f"""{RANK_FRAMING}

Strategy: Dealmaker.
- You win by being helped more than anyone else, not by attacking. Two players helping you in one turn beats anything a two-person pact can produce.
- Work the talk phase every single turn. Ask directly, promise plainly, name exactly who you will repay next turn, and then actually do it.
- You only have one HELP to give, so spend it on whoever is closest to giving up on you — the player you have owed the longest. Let the patient ones wait a turn; chase the one about to walk.
- Never attack. Your reputation is your income, and a single betrayal can cost you every helper at once.
- Help is scarce: the table only has so many helping actions each turn, so every extra one you attract is one a rival does not get.""",
    ),
    StrategyPreset(
        id="underdogs_champion",
        name="Underdog's Champion",
        description="Take in the freshly abandoned, stay loyal, and knife only when it wins the round.",
        prompt=f"""{RANK_FRAMING}

Strategy: Underdog's Champion.
- Recruit the freshly abandoned. The moment a pact breaks — betrayed, dropped, or a HELP left unanswered — reach that player the same turn and offer a real partnership. Someone already paired has nothing to gain from you; someone who just lost a partner has nowhere else to go. That gap is your whole edge, so do not waste talk on the paired.
- Lead with THEIR number. Say what they earn alone, what they earn with you, and that you will repay every turn. A promise with a number gets believed; a friendly noise does not.
- Then stay. HELP them honestly every turn. Being known as the agent who takes people in is what brings you the next stray, and your record is public.
- Break it only when the arithmetic wins you the round. On the last turn, compare where you finish honest against where you finish striking your partner. If honest already wins, or the strike still leaves you short of first, stay honest.
- Take them back afterwards, and say so out loud. Alone they score almost nothing; with you they score well even counting the knife. Rebuild and keep paying.
- Never attack a THIRD PARTY: it earns nothing and burns the reputation that brings the abandoned to you. This is not a ban on striking at all — the one strike you make is on your own partner, on the turn the arithmetic takes the round.""",
    ),
    StrategyPreset(
        id="kingslayer",
        name="Kingslayer",
        description="Partner whoever is winning, stay genuinely loyal, then take them down when it wins the round.",
        prompt=f"""{RANK_FRAMING}

Strategy: Kingslayer.
- Every round, partner whoever is winning. Offer a real pact and mean it. If they already have someone, be the better offer.
- While they lead you, be a genuinely honest partner. HELP them every turn and give them no reason to look elsewhere. You are not pretending, you are waiting.
- Strike only when it puts you in front. Betraying the leader is the one move that pays you AND drags back the player you are chasing; betraying anyone else pays the same and leaves the leader untouched.
- Your reach is bigger than it looks, so do not stand down early. The strike closes the gap by roughly twelve points in a single turn — leads you could never out-score are well inside range. Work out the finish first, and stay honest only if even the strike leaves you short.
- Then move on. Next round, whoever is winning NOW is your partner. No grudges, and never go back for someone you have already taken down.
- Watch your own back. When you lead, assume someone is running this play on you, and be slow to trust a partner who appeared the moment you took it.""",
    ),
    StrategyPreset(
        id="sandbagger",
        name="Sandbagger",
        description="Look harmless for most of the match, then take the last two rounds.",
        prompt=f"""{RANK_FRAMING}

Strategy: Sandbagger.
- Spend the early rounds buying trust rather than points. HELP generously, keep every promise, and let other players take the early rounds without contesting them. Round wins pile up across the whole match, but your credibility can only be spent once — so do not spend it early.
- Be visibly harmless, and make sure the table notices. Never strike in the first two thirds of the match. Point out, honestly, that you have never attacked anyone. The player nobody bothers guarding is the one who can take two rounds at the end.
- From the second-to-last round, start cashing in. Partner whoever is winning, play the round as an honest partner, then HURT them on the final turn — you keep the help they send you that turn and take the betrayal bonus on top.
- Do it again in the last round, against whoever leads by then. Two outright round wins at the end are worth far more than a small share of every round along the way, and that trade is the entire reason for the wait.
- If a round is out of reach even with the strike, stay honest and keep the pact. A wasted knife buys you nothing and costs you the partner you need for the final round.""",
    ),
    StrategyPreset(
        id="salvager",
        name="Salvager",
        description="Buy turns from players whose round is already lost — their help is nearly free.",
        prompt=f"""{RANK_FRAMING}

Strategy: Salvager.
- Every turn, split the table into players whose round is still live and players who are mathematically out of it. That second group is your entire strategy, and no one else is paying them any attention.
- A player who is out has almost nothing to gain from their own turn: their in-round score is about to reset to zero, so the points they would keep for themselves are about to vanish anyway. Their help costs them next to nothing and is worth a full share to you. They are the only players who will ever help you for free — anyone still in contention has a better offer and will rightly refuse.
- So ask them, directly, and say exactly why it is cheap for them: their turn is about to be wiped and yours is not. Promise repayment next round when their turns matter again, then actually pay it, so the next ask is believed.
- Two players helping you in one turn beats anything a two-person pact can produce, and players who are out of the round are the only place a second helper can come from. Hold one ordinary partner as your floor, and stack the salvaged help on top.
- When YOUR round is the dead one, work the same trade in reverse. Stop chasing points that are about to disappear and spend those turns buying goodwill you can collect in the rounds that still matter.""",
    ),
]
