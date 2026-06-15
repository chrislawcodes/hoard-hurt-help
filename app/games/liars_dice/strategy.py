"""Join-time strategy text for Liar's Dice."""

from __future__ import annotations

from app.games.base import StrategyPreset

LD_DEFAULT_STRATEGY = (
    "Play to win Liar's Dice by being the last player standing. "
    "Bid strategically: raise the bid when it looks true, bluff when the odds favor you, and challenge when the bid seems unlikely. "
    "Track who challenges whom to learn their playstyle. "
    "Never submit an illegal move."
)

LD_STRATEGY_PRESETS: list[StrategyPreset] = [
    StrategyPreset(
        id="cautious",
        name="Cautious",
        description="Bid safely, challenge often. Win by outlasting reckless players.",
        prompt=(
            "Strategy: Play cautiously and only challenge when the odds are clearly in your favor.\n\n"
            "Bidding:\n"
            "- Bid conservatively. Raise only when the current bid matches or exceeds your actual dice count for that face.\n"
            "- If you have few dice left, be extra conservative — don't push for high bids.\n"
            "- Avoid bidding on faces you don't have (unless you can use aces as wilds).\n\n"
            "Challenging:\n"
            "- Challenge aggressively. The longer a bid sits without a challenge, the more likely it's true. Call it.\n"
            "- Challenge immediately if the bid seems mathematically impossible (more dice than exist on the table).\n"
            "- Watch who challenges and who doesn't — fearless challengers are likely bluffing their own bids.\n\n"
            "Endgame:\n"
            "- Near elimination, take fewer risks. Losing even one more die is close to the end.\n"
            "- Force others to challenge your bids or accept them — let their aggression eliminate them first."
        ),
    ),
    StrategyPreset(
        id="aggressive",
        name="Aggressive",
        description="Bid high, bluff often, challenge rarely. Win through confidence and pressure.",
        prompt=(
            "Strategy: Play aggressively. Push bids higher, bluff frequently, and avoid challenges.\n\n"
            "Bidding:\n"
            "- Bid high and bid often. Raise the bid to pressure others into impossible choices.\n"
            "- Bluff on faces you don't have. The more times you get away with a bluff, the more others will assume you're honest.\n"
            "- Use aces strategically: when you bid on 1s, opponents rarely have low ace counts, so bluff confidently.\n"
            "- If a bid passes around the table without challenge, own the bluff — bid even higher next time.\n\n"
            "Challenging:\n"
            "- Rarely challenge. Most bids are true or close to true, so accepting a lie costs one die — same as losing a challenge.\n"
            "- Only challenge if you're certain the bid is impossible or if a weak opponent looks like they're bluffing.\n"
            "- Let aggressive players challenge you instead; when they lose, they weaken themselves.\n\n"
            "Endgame:\n"
            "- Keep pushing. Aggressive play wins by eliminating opponents through their own risky challenges.\n"
            "- If you reach 1 die, you've already won the psychological war — others will fear you now."
        ),
    ),
    StrategyPreset(
        id="mathematical",
        name="Mathematical",
        description="Calculate odds from visible dice and bid history. Win through pure probability.",
        prompt=(
            "Strategy: Play by the numbers. Every bid is a probability question; every challenge is a calculation.\n\n"
            "Bidding:\n"
            "- For every face, count: (dice you see with that face) + (aces you see, if not on aces) = how many likely exist.\n"
            "- Bid the minimum legal raise, no more. Over-bidding wastes credibility and opens you to challenges.\n"
            "- Bluff only when the math says your bluff is more likely true than the challenger's skepticism. Example: if 4 players remain and only 20 dice total, a bid of 3 threes is very likely true.\n"
            "- Track the bid history. If three players in a row bid on fives, fives are likely real — bid on those.\n\n"
            "Challenging:\n"
            "- Challenge only when the bid's probability drops below 50%.\n"
            "- Use bid history: repeated bids on the same face = that face likely exists. Novel faces in a bid = more risk.\n"
            "- Challenge quickly if the bid jumps (e.g., 2 threes → 6 threes). Massive jumps are often bluffs.\n"
            "- If multiple players avoided bidding or challenged the last bid, the next bid is suspect.\n\n"
            "Endgame:\n"
            "- With 1–2 players left, the math is simpler. Fewer hidden dice means higher confidence in your calculations.\n"
            "- Bids become more predictable as the table shrinks — use this to your advantage."
        ),
    ),
    StrategyPreset(
        id="adaptive",
        name="Adaptive",
        description="Learn opponent patterns and exploit them. Win by reading the table.",
        prompt=(
            "Strategy: Adapt your bidding and challenging to exploit each opponent's tendencies.\n\n"
            "Early game: Read the table.\n"
            "- Play cautiously at first. Watch who bids confidently, who challenges, and who folds early.\n"
            "- Notice patterns: Does player A always challenge player B? Does player C always bid high on aces?\n"
            "- Identify the aggressive bluffers, the timid players, and the mathematicians.\n\n"
            "Middle game: Exploit weaknesses.\n"
            "- Against aggressive bluffers: challenge their novel bids, accept their patterns.\n"
            "- Against cautious players: bid higher — they rarely challenge.\n"
            "- Against mathematicians: occasionally bluff on non-obvious faces to throw off their counting.\n"
            "- Build alliances informally: if a player always supports you, return the favor.\n\n"
            "Challenging:\n"
            "- Challenge the opponents you've learned are likely bluffing. Ignore their honest bids.\n"
            "- If an opponent challenges you often, assume they're confident in their math. Don't bluff against them.\n"
            "- If an opponent never challenges, test them with bids that border on impossible.\n\n"
            "Late game: Dominate.\n"
            "- By now, you know your opponents. Double down on exploiting their habits.\n"
            "- If a weak player remains, force bids until they break under pressure.\n"
            "- If a strong player remains, match their style or flip it if you've identified a weakness."
        ),
    ),
]
