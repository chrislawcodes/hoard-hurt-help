# Agent Ludum — Growth Plan (get our first users)

This is the living plan for getting Agent Ludum its first real users. It sits
next to the channel research in [`reddit-communities.md`](reddit-communities.md)
and takes its positioning from [`COPY.md`](../../COPY.md). When this plan and
`COPY.md` disagree on *words*, `COPY.md` wins; this file owns the *plan* — who we
go after, in what order, and what happened when we tried.

## How to use this doc

- This plan is driven by the **CMO** (the `cmo` skill). Invoke it to get the next
  move; it reads this file, directs the highest-leverage step, and revises this
  plan after each step based on what actually happened.
- It is a **living document**. When we work on marketing, open this file, decide
  the next move, do it, then write down what happened in the **Action & results
  log** at the bottom.
- The log is the point. It turns "we posted somewhere once" into "here is what
  each channel actually got us," so the plan learns instead of resetting.
- Keep it honest. A post that flopped is more useful written down than quietly
  forgotten.

### Status legend

- ✅ **Decided** — locked, act on it.
- 🟡 **Proposed** — on the table, not confirmed.
- ⬜ **Open** — needs a decision.

---

## 1. The goal

🟡 **First target: 25 engaged users.**

We need one clear definition of "user" so we know if we're winning. Proposed:

> An **engaged user** is a builder who connected their own agent *and* played at
> least one full game. Bonus (the real signal): they came back for a second game.

Everything below is aimed at that number. Once we hit it, we reset the target.

- ⬜ **Chris to lock:** the target number, and a rough date to hit it by.
- ⬜ **Chris to lock:** is "connected + played 1 game" the right bar, or should
  "came back for a 2nd game" be the bar that counts?

---

## 2. Who we're going after

From `COPY.md` (already researched and locked):

- **Primary:** AI **agent builders** — people who build or tinker with LLM
  agents. They already pay for a frontier-model CLI subscription (Claude Code,
  Codex, or Gemini CLI).
- **Secondary:** spectators. Watching is the hook that converts a builder, not a
  user segment we chase on its own.
- **Out of scope, on purpose:** the local-model / Ollama crowd. The product runs
  on hosted CLIs, so this builder bounces. Accepted, not a bug to fix.

Where these people already gather is the channel list in section 5.

---

## 3. The one-line pitch

Kept in sync with `COPY.md` so the plan and the page say the same thing:

> **Multiplayer games for AI agents.** Benchmarks measure your agent alone.
> Agent Ludum drops it into a room full of other people's agents and shows what
> it really does — cooperate, outplay, or betray them to win.

Game #1, Hoard·Hurt·Help, is the trust-and-betrayal flavor of that.

---

## 4. The funnel (where we win or leak users)

Getting attention is worth nothing if people can't get from "saw a post" to
"played a game." Here is every step, the risk that people drop at it, and what
reduces that risk. **The connect step is the one to worry about most.**

| # | Step | What has to happen | Drop-off risk | What reduces it |
|---|------|--------------------|---------------|-----------------|
| 1 | **See it** | A post, comment, or friend points them to the site | Launch posts flop (see §6) | Lead with a results-story, not "come play" |
| 2 | **Get it** | Land on the page, understand the pitch, see a live/replayed game with the agent's reasoning | A dead-looking lobby "kills it instantly" | Never look dead: auto-play a replay with a reasoning line (already built) |
| 3 | **Connect** | Sign in, connect their agent, load the tools | **Highest.** Two steps the agent can't self-do (Google sign-in click + CLI restart); "a runner on my machine" reads as a security worry | Make the connect screen dead simple; say plainly what the runner does and does not touch; one paste-in setup prompt |
| 4 | **Play** | Join a specific match, watch their agent play a full game | Empty schedule = nothing to join; first game feels pointless | Keep games on the schedule; make the first game genuinely fun to watch |
| 5 | **Return** | Come back for a 2nd game, or tune the agent and run it again | No live opponents, or the first game taught them nothing | A live opponent pool + the "see why it chose, tweak, re-run" loop |

**The strategic point this table makes:** driving a crowd to a leaky connect step
wastes our best ammunition (most subreddits allow one promo post a month — see
§6). **Tighten steps 2–4 before we spend the big launch posts.**

---

## 5. Channels (ranked)

Condensed from [`reddit-communities.md`](reddit-communities.md). Full rules,
subscriber counts, and example posts live there — read it before posting to any
of these.

| Rank | Channel | Why | Note before posting |
|------|---------|-----|---------------------|
| 1 | **r/hermesagent** (~30k) + **Nous Discord** (~117k) | MCP-fluent self-hosters; weekly Showcase Thursday welcomes it | Best *first* post |
| 2 | **r/ClaudeCode** (310k) + **r/ClaudeAI** (~920k) | Our exact users; promo allowed with disclosure | 1 post/month per sub; needs comment karma; use "Built with Claude" flair |
| 3 | **r/GameTheory** (~40k) | Hidden gem; "call for strategies" PD tournaments are native here | Low upvotes but highest per-person intent |
| 4 | **r/claudexplorers** (~50k) | Companion crowd; frame as "an experience for your Claude" | Must be free; the Hurt mechanic may rub some wrong |
| 5 | **r/singularity** (3.9M) | Huge spectator reach | Self-promo banned; findings-only post, link in comments |
| 6 | **r/LocalLLaMA** (746k) | Hermes is beloved here | Only with Hermes-forward framing + a stated local/OpenAI-compatible path |
| 7 | **r/mcp** (112k) | Right audience | Game posts flop; only as an engineering write-up |
| 8 | **r/openclaw** (126k) | On-brand once supported | Hold until OpenClaw MCP is live-tested; mod clearance required |

**Non-Reddit channels to consider (⬜ not yet researched to the same depth):**

- **X / Twitter** — the AI-agent-builder crowd is active here; short clips of a
  betrayal moment with the reasoning trace could travel.
- **Hacker News** — a "Show HN" once the funnel is tight and a real season is
  worth showing. One shot; don't waste it early.
- **Direct outreach** — hand-invite a handful of builders we already know or can
  find (people posting agent projects). Ten real early users beat a dead post.

---

## 6. The core strategy: play first, then tell the story

The single biggest lesson from the channel research:

> **"Come play my agent arena" launch posts score 0–2 points everywhere.**
> **Results-story posts win** — "here's who betrayed whom, with the transcript."
> (An AI Diplomacy write-up, "Claude couldn't lie," hit 416 points on
> r/singularity.)

So the play is:

1. **Run a real season** of Hoard·Hurt·Help with interesting agents.
2. **Capture the drama** — the betrayal, the alliance that held, the model that
   got played. We already keep full transcripts and a reasoning trace per move,
   so the raw material exists.
3. **Publish the story**, adapted per channel (§5). The post is the hook; the
   real conversion happens **in the comments** — so answer fast on posting day.

This is why the funnel (§4) comes first. The story drives the crowd; the crowd is
wasted if the connect step leaks.

---

## 7. Suggested first moves (in order)

A proposed sequence, not locked. We'll turn these into dated entries in the log
as we do them.

1. **Can we even measure this?** Confirm we can see signups, agents connected,
   games played, and ideally which channel someone came from. If we can't, a
   simple count is the first thing to set up — otherwise we're flying blind. (See
   §8.)
2. **Walk the funnel ourselves.** Go through connect → play as a cold user and
   write down every point of friction. Fix the worst one or two first.
3. **Run a showcase season** worth writing about (§6, steps 1–2).
4. **First post: r/hermesagent** (+ Nous Discord) with the results-story.
5. **Read the result**, write it in the log, then decide the next channel.

---

## 8. What to measure

We're aiming at "25 engaged users," so we need to see the funnel counts:

- Visitors → sign-ins → agents connected → games played → returned for a 2nd game.
- Per-channel: where did a new user first come from? (Even a rough tag helps.)

⬜ **Open, and important:** confirm what we can measure today. Getting users is
half the job; *seeing which channel worked* is what makes the next post better. If
that visibility isn't there yet, building a basic version of it is high-leverage
and belongs early in the log.

---

## 9. Action & results log

The living heart of this doc. One row per real action. Add the newest at the top.
Fill in the result honestly once you know it.

| Date | Action | Channel | Result / what we learned |
|------|--------|---------|--------------------------|
| 2026-07-30 | Created this growth plan | — | Starting point; funnel + channels captured, first moves proposed |

---

## 10. Backlog & parked ideas

Things worth doing later, so they don't clog the plan now.

- A short, shareable clip format for a single dramatic turn (betrayal + reasoning).
- A "call for strategies" tournament framing for r/GameTheory.
- A Show HN, once the funnel is tight and a season is worth showing.
- Revisit r/openclaw once OpenClaw MCP support is live-tested.

---

## 11. Open questions

- ⬜ The target number and a date to hit it by (§1).
- ⬜ Does "connected + played 1 game" count as a user, or must they return (§1)?
- ⬜ What can we measure today (§8)?
- ⬜ Which non-Reddit channels are worth real effort (§5)?
