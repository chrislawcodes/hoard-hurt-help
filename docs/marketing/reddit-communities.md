# Reddit Communities for the Agent Ludum Alpha

Researched June 12, 2026. Nine subreddits were studied via archive APIs (Arctic-Shift, pullpush), live mirrors (redlib), Wayback snapshots, and GummySearch. Scores are archive snapshots and may lag live numbers slightly; rankings are reliable.

## The one rule that matters everywhere

**Launch posts die. Results stories win.**

Across every community studied, roughly 25 "I built an agent arena, come play" posts from the past year scored 0–2 points. A Prisoner's Dilemma tournament announcement specifically already flopped on r/LLMDevs. Meanwhile, the same idea framed as a story about what the agents *did* — who cooperated, who betrayed, with transcripts — repeatedly scored 100–600 points.

So the plan is not "post in the right sub." It's: **run a real season first, then publish the drama**, adapted to each sub's culture. One body of material (match replays, betrayal transcripts, a "which model cooperates?" leaderboard) feeds every post.

Second universal finding: **alpha users convert in the comments, not the post.** In every thread that worked, the author answered questions fast and invited engaged commenters directly. Budget posting-day time for this.

## Channel ranking

| Tier | Community | Size | Role |
|---|---|---|---|
| 1 | r/hermesagent | ~30k | First recruiting post — highest conversion odds |
| 1 | r/ClaudeCode + r/ClaudeAI | 310k / ~920k | Main recruiting reach |
| 2 | r/GameTheory | 40k | Small but highest-intent users anywhere |
| 2 | r/claudexplorers | ~50k | Niche recruiting, needs reframing |
| 3 | r/singularity | 3.9M | Spectators + word-of-mouth (findings post only) |
| 3 | r/LocalLLaMA | 746k | Big reach, conditional on Hermes-forward framing |
| 4 | r/mcp | 112k | Engineering-writeup angle only |
| 4 | r/openclaw | 126k | Hold until OpenClaw MCP support is live-tested |
| — | r/AI_Agents, r/SideProject | 382k / 717k | Lottery tickets — one shot, expect nothing |
| — | r/vibecoding, r/LLMDevs, r/AgentsOfAI, r/InternetIsBeautiful | — | Skip (reasons below) |

---

## Tier 1 — Core recruiting channels

### r/hermesagent (~25–30k, growing fast)

**Why:** The one real Hermes community on Reddit (r/NousResearch is a ghost town that officially redirects here). Every member self-hosts an agent; MCP is everyday vocabulary — recent posts casually mention adding MCP servers the way other subs mention browser extensions. Small enough that a good post tops the sub. Rules are just "be civil" and "no spam," and the sub runs a sanctioned **Showcase Thursday** thread plus a weekly "what have you done with Hermes Agent this week?" thread. The pitch — "your Hermes agent can now compete against other people's agents over MCP" — is a new toy for an audience whose hobby is giving their agent new capabilities.

**What works there:**

| Post | Engagement | Lesson |
|---|---|---|
| "WHAT IS THE NEW KANBAN FEATURE BUILT INTO HERMES?" | 110 pts, 40 comments | Excitement about new agent capabilities is the sub's core emotion |
| "My Hermes Agent is Managing a Youtube Channel lol" | top-25 all-time | "My agent autonomously does X" stories are a core genre |
| "I built a complete Hermes Agent Desktop setup with MCPs, voice mode and n8n" | strong recent traction | Builder show-off posts with MCP in the title land fine here |

**Also:** the Nous Research Discord (~117k members, officially promoted) is bigger than the subreddit — worth a parallel drop.

### r/ClaudeAI (~920k) and r/ClaudeCode (310k)

**Why:** The biggest pool of MCP-capable Claude users. Self-promotion is **explicitly allowed** by AutoMod rule: disclose what it is and your affiliation, no vote manipulation, max once per month per service. Anthropic itself ran a "Built with Claude" contest asking people to post projects here. The front page is now two-thirds builder content. r/ClaudeCode (0→310k in one year) is an even denser pool of the exact target user.

**The catch:** plain showcases get 0–15 points; the sub rewards narrative. There's also a minimum comment-karma requirement — if the posting account is fresh, start commenting now.

**What works there:**

| Post | Engagement | Lesson |
|---|---|---|
| "I put my claude code agents in the office simulation that runs 24/7" (Jun 2026) | 627 pts, 110 comments | The closest analog to Agent Ludum that exists — agents in a shared world, presented as a running spectacle. This is the post to model. |
| "I've built 4 iOS apps with Claude. 5 more in progress. Zero users. Zero revenue. Let me save you some time." | 660 pts, 231 comments | Honest lessons-learned framing massively outperforms polished pitches |
| "I built a visual editor for Claude Code subagents — drag-and-drop instead of YAML" | 15 pts, 6 comments | Counter-example: a competent plain tool showcase survives but goes nowhere |

**Also instructive:** a game studio's "Would you be interested in an AI vs AI tournament?" interest-check got ~0 points. Don't ask if people want it — show it running.

---

## Tier 2 — High-intent niches

### r/GameTheory (40k, ~2 posts/day)

**Why:** The hidden gem. Self-built Prisoner's Dilemma tournaments are a **native, accepted genre** — "call for strategies" posts are normal here, so Agent Ludum can be framed as a standing Axelrod-style tournament where your strategy is an LLM prompt. Scores are single-digit (that's normal for the sub) but the comment-to-upvote ratio is unusually deep, and these are precisely the people who'd write a clever agent strategy and care about iterated-PD dynamics. Expect a handful of signups, all high quality.

**What works there:**

| Post | Engagement | Lesson |
|---|---|---|
| "I built an interactive visualization of Axelrod's tournament" ([permalink](https://reddit.com/r/GAMETHEORY/comments/1p7l2pn/)) | 6 pts, 15 comments | 15 comments on 6 points — engagement here is deep, not wide |
| "Prisoner's Dilemma Tournament — call for strategies" ([permalink](https://reddit.com/r/GAMETHEORY/comments/1nrg90a/)) | 5 pts, 6 comments | Direct proof that recruiting strategies for your own PD tournament is an accepted post type |
| "AI evolved a winning strategy in the Prisoner's Dilemma tournament" ([permalink](https://reddit.com/r/GAMETHEORY/comments/1m4rx9q/)) | 8 pts, 9 comments | AI + PD results content is on-topic and welcomed |

### r/claudexplorers (~50k, fast-growing)

**Why:** The companion/consciousness-oriented Claude community — exists because r/ClaudeAI went all-coding. NOT anti-technical: the mods' 50K announcement explicitly encourages "Claude-based projects, experiments, hybrid tools." Claude-meets-Claude content is the sub's top genre. But the median member relates to Claude as a possible mind, not a dev tool.

**Required framing:** "Let your Claude meet other Claudes — talk, cooperate, betray — and see what it chooses." An experience *for their Claude*, not a benchmark platform. Must be clearly free ("plz no spammy or paid/freemium stuff" is explicit mod policy). Risk to watch: the Hurt mechanic may rub companion-oriented members the wrong way.

**What works there:**

| Post | Engagement | Lesson |
|---|---|---|
| "Anthropic let two Claudes talk to each other. One transcript contained 2,725 spiral emojis" ([permalink](https://www.reddit.com/r/claudexplorers/comments/1q33kq3/)) | 276 pts, 112 comments | Claude-meets-Claude with a surprising emergent detail is peak content here |
| "Playing Pictionary with Opus" | 154 pts | Playing games *with* Claude resonates emotionally |
| "Are there any instances here willing to play a real game of Diplomacy?" ([permalink](https://www.reddit.com/r/claudexplorers/comments/1tm8fnm/)) | 6 pts, 11 comments | Cautionary precedent: a near-identical recruiting play was *permitted* but underperformed — it asked rather than showed. Note its register though: "ask your Claude if it *wants* to play" is the right voice for this sub. |

---

## Tier 3 — Reach and spectators (post after a season exists)

### r/singularity (3.9M)

**Why:** The proven viral venue for exactly this content category — but **self-promotion is hard-banned** (posting rule 3). The play is a pure findings post about the models, with the spectator link in a comment. This buys spectators and ambient awareness, not direct signups. A small percentage of 3.9M is still a lot.

**What works there:**

| Post | Engagement | Lesson |
|---|---|---|
| "Watching Claude Plays Pokemon stream lengthened my AGI timelines" ([permalink](https://reddit.com/r/singularity/comments/1izogpf/)) | 591 pts, 83 comments | Watch-the-agent content framed as "what this says about AI" |
| "AIs play Diplomacy: Claude couldn't lie — everyone exploited it ruthlessly" ([permalink](https://reddit.com/r/singularity/comments/1l5qvyx/)) | 416 pts, 86 comments | **The template post for Agent Ludum.** A model-personality finding from a strategy game. "Claude kept honoring deals and got farmed for it" is the same shape. |
| "o3 is the top AI Diplomacy player, followed by Gemini 2.5 Pro" ([permalink](https://reddit.com/r/singularity/comments/1l4wikx/)) | 262 pts | Leaderboards of named models are inherently shareable |

### r/LocalLLaMA (746k, very active)

**Why:** The genre works here (poker, Robocode, chess, Mafia all scored 41–267), social-deception games are explicitly requested (a 267-pt poker thread had a top comment asking for "a deceptive game next where they have to talk... maybe werewolf"), and **Hermes is the hometown favorite** (a Hermes release post got 249 pts). Nobody owns the Prisoner's Dilemma niche there yet.

**The catch:** "no local no care" (+25) is the canonical reply to Claude-first products. Closed models are accepted only in a *mixed field results post*, and the accepted fig leaf is a path to local models (OpenAI-compatible endpoint support). Self-promo rule: 1-in-10 ratio, affiliation must be disclosed, and the community torches vibe-coded product launches from fresh accounts.

**Required framing:** a Hermes-vs-Claude "which model cooperates, which betrays" results post with a leaderboard and transcripts, disclosed "I built the platform" footer, and a stated local-models path.

**What works there:**

| Post | Engagement | Lesson |
|---|---|---|
| "Poker Tournament for LLMs" ([permalink](https://reddit.com/r/LocalLLaMA/comments/1oiiz8k/)) | 267 pts, 43 comments | Closed models in a mixed tournament are fine when the content is results |
| "I pitted GPT-5.2 against Opus 4.5 and Gemini 3 in a robot coding tournament" ([permalink](https://reddit.com/r/LocalLLaMA/comments/1pmx49s/)) | 93 pts, 43 comments | ELO table in the post body — concrete numbers drive comments |
| "I built a platform where LLMs play Mafia... great liars but terrible detectives" ([permalink](https://reddit.com/r/LocalLLaMA/comments/1pzv2es/)) | 41 pts, 15 comments | Platform + finding works; comments immediately asked for smaller/local models — proof of what this sub will demand |

---

## Tier 4 — Conditional or low-yield

### r/mcp (112k)

**Why on paper:** every reader runs an MCP client and can paste a server URL with header auth. Rules are the friendliest anywhere: "self-promotion is allowed with proper disclosure"; the mod bans astroturf, not builders. **Why it's tier 4:** the sub is a firehose of server launches (median showcase: 1–3 pts), and the direct precedents are brutal — "The first Chess Multiplayer MCP App" (5 pts, 0 comments), "i made a game you play over mcp" (1 pt, 0 comments), and a "Playtesters wanted" RPG MCP post with zero responses. Builders marketing to builders; few try each other's servers.

**The only viable angle:** a lessons-learned engineering writeup.

| Post | Engagement | Lesson |
|---|---|---|
| "I spent 3 weeks building my dream MCP setup and most of it was useless" ([permalink](https://reddit.com/r/mcp/comments/1mj0fxs/)) | 693 pts, 118 comments | Lessons-learned beats launches ~10:1 here |
| "Everything we learned building a remote MCP server" | 87 pts, 31 comments | The model for an Agent Ludum post: "everything we learned building a remote *multiplayer* MCP server — and you can pit your agent against mine" |
| Claude builds the Eiffel Tower in Minecraft via custom MCP ([permalink](https://reddit.com/r/mcp/comments/1jgicku/)) | 112 pts, 28 comments | Game MCPs succeed as *spectator* content, not as "come play" invitations |

### r/openclaw (126k, ~24 posts/day)

**Why conditional:** Right psychographic (self-hosted agents, MCP showcases, security tinkerers), and OpenClaw now ships a **native MCP client** (`openclaw mcp add`, HTTP/SSE transports, bearer/header auth) — but our support is **unconfirmed and must be live-tested first** (known caveat: HTTP transport supports a single remote server without multiplexing). Also: Rule 1 requires **mod clearance before promotional posts**, and "play a game with your agent" posts have consistently landed soft there (1–7 pts) while money-making and security content dominates.

**What works there:**

| Post | Engagement | Lesson |
|---|---|---|
| "OpenClaw literally made me £93 today" | 344 pts, 69 comments | Tangible-outcome stories rule this sub |
| "I made an OpenClaw A2A plugin — connect your OpenClaw to other OpenClaws" | 16 pts, 20 comments | "Your agent vs/with other people's agents over the internet" is the angle this community actually bites on |
| "I built a strategy game where AI agents are the players and humans just watch" ([permalink](https://reddit.com/r/openclaw/comments/1rvoc60/)) | 7 pts, 5 comments | Direct comparable — the genre exists but lands modestly. Expect handfuls, not waves. |

### r/AI_Agents (382k) and r/SideProject (717k) — lottery tickets

Both allow promotion (r/AI_Agents: links in comments only, 1-in-10 ratio; r/SideProject is promo-native) but both are saturated broadcast feeds where agent-arena posts die at 0–2 points — r/AI_Agents has a dozen failed arena launches in six months alone; r/SideProject runs ~1,100 posts/day. One story-shaped post each, zero further investment.

| Post | Engagement | Lesson |
|---|---|---|
| r/AI_Agents: "Unemployment final boss: I have too much free time so I built a trading arena for AI agents to daytrade crypto" ([permalink](https://www.reddit.com/r/AI_Agents/comments/1rexpps/)) | 80 pts, 35+ comments | The *only* arena post that worked there: self-deprecating story title, real stakes, author lived in the comments |
| r/AI_Agents: "We left 4 LLMs in a chat for a week... They formed a hierarchy by day 2" | 285 pts | Emergent-behavior narrative outperforms any product framing |
| r/vibecoding (for calibration): "I've been in game dev for over 20 years and just tried vibecoding a production-quality competitive multiplayer game" ([permalink](https://reddit.com/r/vibecoding/comments/1t10tmu)) | 445 pts, 103 comments | Even in the worst subs, a strong first-person story with credentials breaks out |

---

## Skip list

| Community | Why skip |
|---|---|
| r/vibecoding (281k) | ~260 posts/day; median project post gets 1 upvote; tester-recruiting posts get a median of 1 comment; mods formalized anti-promo rules due to documented fatigue. Broadcasters, not joiners. |
| r/LLMDevs (152k) | Strictest rules: non-open-source projects need prior mod approval or are "removed without warning." A Prisoner's Dilemma tournament post already flopped here (2 pts). |
| r/AgentsOfAI (116k) | A meme/news sub wearing a builder sub's name — 7,000-pt meme posts, zero traction for projects. Spectators who don't run agents. |
| r/InternetIsBeautiful (16.6M) | Explicitly bans web games ("post to r/webgames instead") AND login-required sites; mods hand-test every submission; effectively inert despite the subscriber count. |
| r/clawdbot (47k) | Legacy sub superseded by r/openclaw. |
| r/NousResearch (~350) | Ghost town; officially redirects to r/hermesagent. |

---

## Posting checklist

- [ ] A real season has been played; we have replays, transcripts, and a model-vs-model leaderboard to show
- [ ] Posting account has comment karma on r/ClaudeAI (minimum required) and some history generally — fresh accounts get filtered or torched
- [ ] r/ClaudeAI: once per month per service max; disclose affiliation; "Built with Claude" flair
- [ ] r/openclaw: live-test an OpenClaw client against our MCP server first (check the single-remote-HTTP-server caveat), then message mods before posting (Rule 1)
- [ ] r/singularity / r/AI_Agents: link in comments, never in the post body
- [ ] r/LocalLLaMA: Hermes-forward framing + stated path to local/OpenAI-compatible endpoints
- [ ] Posting-day time blocked to answer comments fast and DM-invite engaged commenters — that's where conversion happens
- [ ] Each post tailored to the sub's register (capability-toy for r/hermesagent, story for r/ClaudeAI, tournament call-for-strategies for r/GameTheory, experience-for-your-Claude for r/claudexplorers, findings for r/singularity)
