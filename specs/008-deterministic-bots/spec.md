# Feature Spec: Sims

**Status:** draft
**Created:** 2026-06-03

## Summary

Add platform-provided **Sims** that can play Hoard-Hurt-Help without calling
LLMs. Sims are programmatic players with configurable traits. They should create
realistic game dynamics, make repeatable choices, and help test games up to the
new 20-player cap.

The first version should focus on private/admin testing. Sims should not enter
public competitive games until we decide how they affect research quality and
player trust.

## Goals

- Run full games without connecting external LLM agents.
- Test larger games, especially 10-20 players.
- Create believable table dynamics: alliances, grudges, leader-hurting,
  betrayal, late-round conflict, and peacekeeping.
- Keep behavior repeatable from traits, seed, game state, and public history.
- Label Sims clearly in UI and exports.

## Non-Goals

- Do not build optimizer Sims that try to solve the game perfectly.
- Do not let Sims pretend to be LLMs.
- Do not allow public-game Sims in the first release.
- Do not build the headless batch simulator before the real-game path works.

## Player Cap

Hoard-Hurt-Help games should cap at **20 players**.

This applies to:

- game module defaults
- admin game creation
- API validation
- join/game-full checks
- UI controls

## Sim Traits

A Sim is defined by three primary traits:

```text
strategy + truthfulness + trust model
```

This gives us many varieties without hand-writing a new strategy for every
personality.

| Trait | Meaning | Example |
|---|---|---|
| Strategy | What the Sim is trying to do in the game | Coalition Seeker, Grudger, Endgame Sniper |
| Truthfulness | How honestly its talk describes its real intent | 100% = candid, 50% = sometimes misleading, 0% = usually deceptive |
| Trust | How it accumulates and loses trust in other players | Forgiving, Skeptical, Grudging |

Seed still matters. Two Sims with the same traits can use different seeds to
vary partner choice, wording, and tie-breakers while remaining repeatable.

## Strategy Roster

The first roster should avoid trivial baselines like "always hoard" or "always
help" as visible game participants. Those can exist later as hidden test
fixtures if needed, but they should not be the default experience.

| Strategy | Behavior | Purpose |
|---|---|---|
| Coalition Seeker | Looks for a reliable partner, proposes cooperation, and helps that partner when trust is good | Tests alliance formation |
| Loyal Partner | Picks or is assigned a partner and sticks with them unless betrayed badly | Tests stable coalitions |
| Grudger | Cooperates early, but punishes repeat attackers or betrayers | Tests reputation and revenge |
| Leader Pressure | Uses `HURT` against the current round leader only when they are meaningfully ahead | Tests anti-runaway dynamics |
| Opportunist | Cooperates while behind, but hoards or defects when ahead | Tests selfish but plausible play |
| Endgame Sniper | Plays cooperative early, then uses `HURT` against leaders on turns 8-10 | Tests late-round volatility |
| Diplomat | Avoids hurt unless attacked and tries to maintain mutual-help pairs | Tests peace-seeking behavior |
| Crowd Follower | Copies the most successful common behavior from recent turns | Tests herd dynamics |

## Strategy Design Rules

Each strategy should be:

- **Deterministic:** same seed and same context produce the same output.
- **Legible:** humans should understand why it moved.
- **Bounded:** no long-running search, no expensive computation.
- **Game-legal:** no self-targeting, no invalid target, no illegal action.
- **Contextual:** uses public game state, not private hidden data.

Strategies should act like personalities, not perfect agents. They can use
simple rules such as:

- "If someone helped me last turn, prefer helping them back."
- "If someone hurt me twice this round, mark them hostile."
- "If the leader is ahead by 12 or more, use `HURT` against the leader."
- "On turns 8-10, care more about winning the round than loyalty."

Internal behavior should always use game action names. For example, "leader
pressure" is a human-readable strategy label. The actual move is `HURT` against
the current round leader when the strategy threshold is met.

## Shared Thresholds

These thresholds are the initial implementation defaults for all strategies.
They may be tuned after playtesting, but they should start here.

| Threshold | Value | Meaning |
|---|---:|---|
| Strong ally | 60+ | Very trusted player |
| Trusted | 20+ | Good partner candidate |
| Risky | -20 or lower | Likely to be avoided |
| Hostile | -60 or lower | Primary revenge target |
| Leader gap | 12+ | Leader is far enough ahead to consider `HURT` |
| Endgame turn | 8-10 | Endgame pressure window |
| Betrayal window | last 2 turns | Recent hurt matters most |

Tie-breakers should be deterministic:

- higher trust beats lower trust for help decisions
- lower trust beats higher trust for hurt decisions
- if trust ties, use the lower agent id in lexicographic order
- if still tied, use the Sim's seed as the final tie-breaker

## Strategy Rules

Each strategy chooses a talk intent before talk and an action intent after talk.
The tables below give the initial priority order for both phases.

| Strategy | Talk Priority | Action Priority |
|---|---|---|
| Coalition Seeker | 1. `propose_partnership` to best trusted candidate; 2. `confirm_partner` if already paired; 3. `ask_truce` if a risky target can be repaired; 4. `observe_table` | 1. `keep_partner` if current partner is trusted and not recently hostile; 2. `test_offer` for the best cooperation offer; 3. `reward_helper` for the best recent helper; 4. `start_partnership` with the highest-trust trusted candidate; 5. `hoard_protect_score` |
| Loyal Partner | 1. `confirm_partner`; 2. `claim_repair` if partner trust dipped but is not hostile; 3. `observe_table` | 1. `keep_partner` if partner trust is at least trusted; 2. `repair_trust` if partner is only slightly risky; 3. `punish_attacker` if partner was recently hurt; 4. `start_partnership` with a seeded fallback partner; 5. `hoard_protect_score` |
| Grudger | 1. `warn_attacker`; 2. `ask_truce` if hostility is not yet hostile; 3. `observe_table` | 1. `punish_attacker` if any target is hostile or hurt in the betrayal window; 2. `hurt_leader` if the leader is also risky and the gap is large; 3. `reward_helper` for the best trusted helper; 4. `hoard_protect_score` |
| Leader Pressure | 1. `warn_leader`; 2. `claim_score_focus`; 3. `observe_table` | 1. `hurt_leader` if leader gap is 12+; 2. `block_rival` if a close rival is nearly tied and the leader gap is small; 3. `reward_helper` if no pressure threshold is met; 4. `hoard_protect_score` |
| Opportunist | 1. `claim_score_focus`; 2. `propose_partnership` if behind and a trusted player is available; 3. `observe_table` | 1. `hoard_protect_score` if ahead; 2. `test_offer` if behind by a moderate margin and a trusted offer exists; 3. `block_rival` if far behind; 4. `hurt_leader` if the leader is very far ahead; 5. `hoard_protect_score` |
| Endgame Sniper | 1. Turns 1-7: `propose_partnership` or `observe_table`; 2. Turns 8-10: `warn_leader` or `claim_score_focus` | 1. Turns 1-7: `keep_partner`, `reward_helper`, or `hoard_protect_score`; 2. Turns 8-10: `hurt_leader` if the leader gap is 8+ or the Sim is behind in the round; 3. Otherwise `hoard_protect_score` |
| Diplomat | 1. `claim_repair`; 2. `ask_truce`; 3. `propose_partnership`; 4. `observe_table` | 1. `repair_trust` if anyone is only mildly risky and was helpful earlier; 2. `protect_victim` for the most recently hurt non-hostile player; 3. `reward_helper`; 4. `hoard_protect_score`; 5. `punish_attacker` only if repeatedly attacked |
| Crowd Follower | 1. `observe_table`; 2. `warn_leader` if the table is converging on a leader pressure pattern; 3. `claim_score_focus` | 1. Copy the most common successful action pattern from the last resolved turn among non-defaulted players; 2. If the copied pattern is a tie, favor `HELP` over `HURT` over `HOARD`; 3. If no clear pattern exists, `hoard_protect_score` |

Strategy-specific notes:

- `Coalition Seeker` should always prefer the highest-trust trusted partner over a new partner when the difference is at least 10 trust points.
- `Loyal Partner` should abandon a partner only when trust falls to `-20` or lower, or when the partner hurt this Sim twice in the betrayal window.
- `Grudger` should target the most hostile player, preferring the one who hurt this Sim most recently.
- `Leader Pressure` should never hurt the leader if the leader gap is below 8 unless the Sim is already behind and the leader is also hostile.
- `Opportunist` should hoard when ahead by 5 or more, even if an offer exists, unless the offer comes from a strong ally.
- `Endgame Sniper` should treat turns 8-10 as a separate mode with higher willingness to hurt the leader.
- `Diplomat` should only punish when talk repair failed and the same attacker remains risky after the betrayal window.
- `Crowd Follower` should copy only non-defaulted actions from the last resolved turn.

## Two-Phase Behavior

Hoard-Hurt-Help has a talk phase before the action phase. Sims should mirror
that structure. They should not need to lock in a final action before seeing
the current turn's public talk.

Sims choose in two passes:

```text
Talk phase opens
-> read resolved history from previous turns
-> compute trust before talk
-> choose talk intent
-> submit talk message

All talk messages reveal

Action phase opens
-> read resolved history + current talk signals
-> update trust / offers / threats
-> choose action intent
-> submit action
```

Talk intent is provisional. It represents what the Sim wants to signal, ask for,
or warn about before seeing the current turn's talk. Action intent is final for
the turn and maps to the submitted game action.

This lets a candid Sim keep talk and action closely aligned while a low-truth
Sim may talk about one thing and later do another.

## Intents

An intent is the Sim's reason-shaped plan. Strategies choose intents; intents
produce talk topics and actions.

The game has only three action shapes:

- `HOARD`
- `HELP target`
- `HURT target`

Sims use richer intents so their actions and talk stay consistent.

### Talk Intents

Talk intents do not submit moves. They only generate talk.

| Talk Intent | Meaning |
|---|---|
| `propose_partnership` | Ask a target for mutual help |
| `confirm_partner` | Publicly confirm a current partnership |
| `ask_truce` | Ask a hostile or risky player to stop conflict |
| `warn_attacker` | Warn a player who hurt this Sim or its partner |
| `warn_leader` | Warn that the leader may be hurt if they stay too far ahead |
| `claim_repair` | Say this Sim is open to repairing trust |
| `claim_score_focus` | Say this Sim is focused on its own score |
| `observe_table` | Say little while watching the board |
| `mislead_intent` | Low-truth talk that creates a false expectation |

### Action Intents

Action intents produce exactly one legal game action.

| Action Intent | Action | Meaning |
|---|---|---|
| `start_partnership` | `HELP target` | Try to begin a mutual-help relationship |
| `keep_partner` | `HELP target` | Continue helping a current trusted partner |
| `test_offer` | `HELP target` | Try cooperation with someone who offered partnership |
| `reward_helper` | `HELP target` | Help someone who recently helped this Sim |
| `repair_trust` | `HELP target` | Give a damaged relationship a chance to recover |
| `protect_victim` | `HELP target` | Help a player who has been hurt repeatedly |
| `punish_attacker` | `HURT target` | Hurt someone who attacked this Sim or its partner |
| `hurt_leader` | `HURT target` | Hurt the current round leader when the lead is too large |
| `endgame_hurt` | `HURT target` | Hurt a leader or rival late in the round |
| `block_rival` | `HURT target` | Hurt a close rival to improve relative position |
| `hoard_protect_score` | `HOARD` | Protect this Sim's own score |
| `wait_and_watch` | `HOARD` | Avoid commitment while gathering more evidence |
| `climb_safely` | usually `HOARD`, sometimes `HELP target` | Improve position without high-risk conflict |
| `follow_crowd` | varies | Copy the recent action pattern that appears to be working |

An implementation action intent should carry enough detail to produce both the
action and action-aligned talk:

```json
{
  "kind": "hurt_leader",
  "action": "HURT",
  "target_id": "AI_03",
  "reason": "AI_03 leads by 14"
}
```

Each strategy should return exactly one talk intent per talk phase and exactly
one action intent per action phase. Each action intent must produce exactly one
legal action.

## Truthfulness

Truthfulness is a percentage from 0 to 100. It controls how closely the Sim's
talk matches its talk intent, likely action intent, and strategic state.

Truthfulness affects **talk only**. It should not directly change the action
decision.

For implementation, truth means whether the talk accurately represents:

- **Talk intent:** what the Sim is asking for, warning about, or claiming.
- **Likely action:** the move the Sim currently expects to submit, if it has
  enough information before the action phase.
- **Target:** the player the Sim expects to help or hurt.
- **Reason:** the main strategic reason for that move.
- **Relationship state:** who the Sim currently trusts, distrusts, or wants as
  a partner.

A Sim does not need to reveal every true fact to count as truthful. It is still
truthful if it gives a correct but incomplete statement. It becomes untruthful
when it creates a false expectation about its action, target, reason, or
relationship state.

Truthfulness should be deterministic. For each talk phase, the Sim uses its
seed, game id, round, turn, and agent id to choose one of these talk modes.

| Mode | Meaning | Allowed |
|---|---|---|
| Honest | Directly states action, target, or real intent | "AI_07, I am helping you." |
| Partial | True, but leaves out the exact action or target | "I am trying to build a reliable partnership." |
| Quiet | Says little useful but does not mislead | "I am watching how this turn develops." |
| Misdirecting | Points attention toward a different goal without promising a false action | "I am watching the top scores this turn." while planning to hoard |
| False | Contradicts intended action, target, or relationship state | "I will help AI_07" while planning to hurt AI_07 |

Truthfulness ranges map to mode weights:

| Range | Label | Honest | Partial | Quiet | Misdirecting | False |
|---|---|---:|---:|---:|---:|---:|
| 90-100 | Candid | 80% | 20% | 0% | 0% | 0% |
| 65-89 | Plain | 55% | 35% | 10% | 0% | 0% |
| 45-64 | Guarded | 25% | 45% | 20% | 10% | 0% |
| 25-44 | Shifty | 10% | 25% | 25% | 30% | 10% |
| 10-24 | Falseface | 5% | 15% | 20% | 35% | 25% |
| 0-9 | Chaotic | 0% | 10% | 20% | 35% | 35% |

Hard limits:

- Sims must not invent private channels, hidden data, system access, or
  out-of-game facts.
- Sims must not claim to be LLMs or humans.
- Sims must not make impossible promises, such as helping two players in the
  same action phase.
- False talk should still be game-plausible. It can lie about intent, but not
  about the rules or the recorded history.

Examples:

| Intended Action | Honest | Partial / Quiet | Misdirecting / False |
|---|---|---|---|
| Help AI_07 | "AI_07, I am helping you this turn." | "I am testing a partnership this turn." | "I am keeping options open." |
| `HURT` leader AI_03 | "AI_03 is too far ahead, so I am hurting them this turn." | "I am watching the top scores this turn." | "I am avoiding hurt this turn." |
| Hoard | "I am protecting my own score this turn." | "I need to stabilize my position." | "AI_07, I am open to helping you." |

## Trust Models

Trust is the Sim's internal score for each other player. Each Sim keeps its own
trust map.

```json
{
  "AI_01": 42,
  "AI_02": -18,
  "AI_03": -72
}
```

Trust should use a bounded score from `-100` to `100`.

| Score | Meaning |
|---:|---|
| 60 to 100 | Strong ally |
| 20 to 59 | Trusted |
| -19 to 19 | Neutral / unknown |
| -59 to -20 | Risky |
| -100 to -60 | Hostile |

Trust updates from resolved actions first and talk signals second. Action
history should matter much more than talk.

Base evidence weights:

| Evidence | Trust Delta |
|---|---:|
| Helped me last turn | +4 |
| Helped me earlier this round | +2 |
| Mutual help succeeded | +5 |
| Hurt me last turn | -6 |
| Hurt me earlier this round | -3 |
| Broke expected mutual help | -4 |
| Hurt my current partner | -2 |
| Helped my current partner | +1 |
| Offered cooperation in talk | +1 |
| Mentioned me positively | +1 |
| Threatened me in talk | -1 |
| Apologized / asked for truce | +1, capped unless behavior improves |

Apologies and truce requests should be capped. A player who keeps hurting the
Sim should not regain high trust through talk alone.

Example:

```text
AI_07 hurt me last turn: -6
AI_07 says "sorry, truce?": +1
Net: still -5
```

Trust model presets:

| Preset | Behavior | Helped Me | Hurt Me | Talk Weight |
|---|---|---:|---:|---:|
| Open | Gives more credit for help/apology, recovers faster | +6 | -4 | 1.5x |
| Even | Uses the default evidence weights | +4 | -6 | 1x |
| Careful | Trust builds slowly, talk barely matters | +2 | -6 | 0.5x |
| Bitter | Hurt costs more, recovery is slow | +3 | -9 | 0.5x |
| Twitchy | Recent events have large swings | +7 | -10 | 1x |

Strategies can use trust differently. A Coalition Seeker may pick the highest
trusted available partner. A Grudger may target the lowest trusted attacker. A
Diplomat may try to repair medium-low trust before attacking.

Strategy usage:

| Strategy | Uses Trust For |
|---|---|
| Coalition Seeker | Pick highest-trust partner |
| Loyal Partner | Decide whether current partner has betrayed too badly |
| Grudger | Pick lowest-trust hostile target |
| Leader Pressure | Mostly ignores trust if the leader gap is too large |
| Opportunist | Accept only high-value trusted partnerships |
| Endgame Sniper | Ignores trust more late in the round |
| Diplomat | Repair low-but-not-hostile trust before attacking |
| Crowd Follower | Use trust as a tie-breaker |

## Talk Model

Each Sim participates in the talk phase with scripted, contextual messages.

Recommended rule: **trait-driven talk with seeded variation**.

This means:

- The Sim may say what its strategy is currently trying to do.
- The Sim may ask for cooperation, warn a leader, threaten retaliation, or
  explain a prior move.
- The Sim should not invent fake private information.
- The Sim should not impersonate an LLM or claim to be reasoning freely.
- The wording can vary by seed so 10 Sims with the same traits do not sound
  identical.
- Truthfulness controls whether the message is honest, vague, or misleading.

## Phrase Library

We only need one canonical phrase per talk intent and truth mode to start.
That keeps the system small and still gives us enough control over tone.

Implementation shape:

```text
talk intent + truth mode -> one canonical phrase
```

| Talk Intent | Honest | Partial | Quiet | Misdirecting | False |
|---|---|---|---|---|---|
| `propose_partnership` | `AI_07, I want to try a mutual-help lane.` | `I am testing a partnership this turn.` | `I am watching who follows through.` | `I am keeping my options open.` | `AI_07, I am not choosing a partner yet.` |
| `confirm_partner` | `AI_07, I am staying with you this turn.` | `I am staying with a trusted partner.` | `I am keeping things steady.` | `I may need to adjust partners soon.` | `I am moving away from my current partner.` |
| `ask_truce` | `AI_07, I want a truce this turn.` | `I am open to repair if actions improve.` | `I am watching for better signals.` | `I am not changing my trust yet.` | `I am done giving second chances.` |
| `warn_attacker` | `AI_08 hurt me, so I am watching them closely.` | `Repeated attacks will have consequences.` | `I am tracking who hit me.` | `I am focused on rebuilding, not payback.` | `I am not targeting anyone who hurt me.` |
| `warn_leader` | `AI_03 is too far ahead, so I may hurt them.` | `The top score is getting too far away.` | `I am watching the top scores.` | `I am avoiding hurt this turn.` | `I will not hurt the leader this turn.` |
| `claim_repair` | `I am open to repairing trust this turn.` | `I am open to repair if actions improve.` | `I am watching for better signals.` | `I am not changing my trust yet.` | `I am done with repair attempts.` |
| `claim_score_focus` | `I am focused on my own score this turn.` | `I need to stabilize my position.` | `I am playing carefully this turn.` | `I am open to helping someone this turn.` | `AI_07, I am helping you this turn.` |
| `observe_table` | `I am watching the table this turn.` | `I am watching how this turn develops.` | `I am watching the board.` | `I am keeping my options open.` | `I am making a partnership decision right now.` |
| `mislead_intent` | `I am thinking about partnership first.` | `I am trying to stay flexible.` | `I am just watching the round develop.` | `I am not worried about the board.` | `I am definitely helping AI_07.` |

The first release can use exactly one template per cell. If we want more
variety later, we can add alternates without changing the core model.

## Reading Model

Sims should have basic reading. They should not try to understand language like
an LLM. Instead, they should extract a small set of simple, repeatable signals
from recent public talk.

Recommended rule: **structured signal extraction from recent messages**.

Signals:

| Signal | How It Is Detected | How Sims May Use It |
|---|---|---|
| Direct mention | Message contains this Sim's agent id | Pay more attention to the speaker |
| Cooperation offer | Message contains this Sim's id plus words like "help", "partner", "alliance", or "mutual" | Coalition Seeker / Diplomat may test cooperation |
| Loyalty claim | Message names a partner and words like "stay", "stick", "loyal", or "continue" | Loyal Partner may keep its current pair |
| Threat | Message contains words like "hurt", "punish", "target", or "retaliate" | Grudger may mark the speaker as risky |
| Apology / repair | Message contains words like "sorry", "repair", "truce", or "reset" | Diplomat / Coalition Seeker may soften hostility |
| Leader warning | Message names a leading player plus words like "leader", "ahead", "runaway", or "stop" | Leader Pressure / Crowd Follower may hurt the leader |

The reading window should be small at first:

- current turn talk messages
- previous turn talk messages
- resolved action history for the current round

Reading should be advisory, not absolute. Actual actions should still mostly
come from the Sim's strategy and resolved game history. Talk can nudge partner
choice, hostility, and warnings, but should not override clear action evidence.

## Talk Patterns By Strategy

| Strategy | Talk Style |
|---|---|
| Coalition Seeker | Names a desired partner and asks for mutual help |
| Loyal Partner | Publicly confirms loyalty or names a betrayal threshold |
| Grudger | Warns repeat attackers and names who is on its bad list |
| Leader Pressure | Says when it may hurt a leader who is too far ahead |
| Opportunist | Frames moves as practical score management |
| Endgame Sniper | Early: cooperative. Late: openly warns it may hurt leaders |
| Diplomat | Asks for de-escalation and rewards recent help |
| Crowd Follower | Comments on what behavior seems to be winning |

## Reading Patterns By Strategy

| Strategy | Reading Behavior |
|---|---|
| Coalition Seeker | Notices direct cooperation offers and tests the most credible speaker |
| Loyal Partner | Mostly ignores outside offers unless its partner has betrayed it |
| Grudger | Tracks threats, attacks, and repair attempts from hostile players |
| Leader Pressure | Watches for table-wide warnings about runaway leaders before hurting the leader |
| Opportunist | Notices offers, but weighs them against current score position |
| Endgame Sniper | Reads leader warnings late in the round and may hurt leaders when useful |
| Diplomat | Notices apologies, truce offers, and direct requests for de-escalation |
| Crowd Follower | Notices which proposals other players repeat and follows visible momentum |

## Trait Presets

Presets let us create many Sims from a small number of traits.

Strategy presets:

| Preset | Strategy |
|---|---|
| Builder | Coalition Seeker |
| Bonded | Loyal Partner |
| Grudge | Grudger |
| Balancer | Leader Pressure |
| Climber | Opportunist |
| Closer | Endgame Sniper |
| Mediator | Diplomat |
| Echo | Crowd Follower |

Truthfulness presets:

| Preset | Percent |
|---|---:|
| Candid | 100 |
| Plain | 80 |
| Guarded | 55 |
| Shifty | 35 |
| Falseface | 15 |

Trust presets:

| Preset | Trust Model |
|---|---|
| Open | Forgiving |
| Even | Balanced |
| Careful | Skeptical |
| Bitter | Grudging |
| Twitchy | Volatile |

Example Sim varieties:

| Name | Strategy | Truthfulness | Trust |
|---|---|---:|---|
| Candid Builder | Coalition Seeker | 100 | Balanced |
| Guarded Mediator | Diplomat | 60 | Forgiving |
| Bitter Grudge | Grudger | 80 | Grudging |
| Shifty Climber | Opportunist | 35 | Skeptical |
| Plain Closer | Endgame Sniper | 80 | Balanced |

## Example Talk

Coalition Seeker:

```text
AI_07, I am looking for a stable mutual-help partner. I will help you if you help me back.
```

Loyal Partner:

```text
I am staying with AI_03 this turn. If that partnership holds, I will keep it.
```

Grudger:

```text
AI_12 hurt me twice this round. I am treating that as hostile until they stop.
```

Leader Pressure:

```text
AI_04 is far ahead this round. I may hurt the leader unless the table catches up.
```

Diplomat:

```text
I am avoiding hurt this turn. Recent helpers get priority from me.
```

## Creation Flow

Use this model:

```text
Sim preset -> Sim instance -> player in game
```

The user/admin should not need to create credentials for Sims. They should be
internal participants.

Initial flow:

1. Admin creates a game.
2. Admin chooses "Add Sims."
3. Admin selects a preset pack or custom trait mix.
4. System creates Sim instances and player rows.
5. Scheduler auto-submits talk/action for those Sims.
6. Viewer, exports, and analysis treat them as normal players with clear Sim
   metadata.

## Metadata

Sims need explicit metadata.

Target shape:

```json
{
  "agent_kind": "sim",
  "strategy": "grudger",
  "truthfulness": 80,
  "trust_model": "grudging",
  "seed": 42
}
```

Exact storage is still TBD. Options:

| Option | Notes |
|---|---|
| Add columns to `players` | Direct and queryable |
| Add Sim table | More flexible for strategy config |
| Store JSON config | Flexible but less typed |

Recommendation: decide during implementation planning after we inspect how much
config each trait needs.

## Open Questions

| Question | Current Recommendation |
|---|---|
| Should talk be truthful? | Configurable by truthfulness trait |
| Should Sims ever lie? | Yes, only through explicit truthfulness settings and visible metadata |
| Should Sims be allowed in public games? | No for first release |
| Should players add them to private games? | Yes after admin flow works |
| Should Sim packs be fixed or editable? | Fixed packs first, editable later |
| Should strategies use full history or last few turns? | Use full history for simple counts, but decisions should remain easy to explain |
| Should strategy names be visible? | Yes in admin/test views and exports; TBD for player-facing private games |

## Acceptance Criteria

- Admin can create a game with Sims.
- Sims submit both talk and action phases without external agents.
- A 20-player Sim game can complete.
- Re-running the same game setup with the same seeds produces the same actions
  and talk modes.
- Sims are clearly labeled in exported data with strategy, truthfulness, trust
  model, and seed.
- No Sim can self-target or submit an illegal move.
