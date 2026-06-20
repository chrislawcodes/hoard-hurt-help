# Joining a Game — Real-World Test Stories

A manual test checklist for the join-a-game flow, ordered the way a brand-new
user actually hits each step. Use it to tick off behavior in the live app.

The key thing to know about this flow: an **agent carries no AI**. When you join,
you pick **an agent AND which connected AI plays it** for that game (recorded on
the seat as `chosen_provider`). The same agent can be played by Claude in one game
and Gemini in another. **One AI plays one seat at a time** — an AI already chosen
for any unfinished seat (playing now or upcoming, including in this same game) is
**busy** and can't be picked; to field several agents in one game, pick a different
AI for each.

Two timing facts to know:

- A **held seat** gives you **15 minutes** to bring the chosen AI online
  (`SEAT_HOLD_SECONDS`, `app/engine/seat_hold.py`). The connect screen polls and
  auto-locks the seat the instant that AI starts playing — there is no visible
  countdown; after ~45s still-offline it surfaces a "reconnect" CTA.
- A provider counts as **"live"** only if its client was seen in the
  **last 90 seconds** (`LIVE_WINDOW_SECONDS`, `app/engine/connection_health.py`).

Stories **5, 6, 8, 15, 16** exercise per-AI status and the single-provider rule
(an MCP connection enables only the provider whose client actually connected).

Old labels from the first draft are kept in parentheses for cross-reference.

---

## 1. First time getting in (in the order it happens)

The straight-line journey for someone who just found the site and clicked Join.
The Join button lives on a match page and links to `…/matches/{id}/join`.

- [ ] **1 (A1) — Signed out.** Click Join while logged out → sent to Google sign-in, then dropped back on the join screen.
- [ ] **2 (A2) — Pick a username.** No handle yet → sent to choose one, then back to join.
- [ ] **3 (A3) — Make an AI agent.** No agent yet → sent to the "create an agent" page, then back to join.
- [ ] **4 (A5) — The form appears.** Now you have an agent, so the join form shows. Backing out here leaves no half-join — no seat taken yet.
- [ ] **5 (E4) — Nothing's connected yet, and the form says so honestly.** You haven't started an AI client, so in the "which AI plays it?" picker **no AI shows "ready,"** they read "not connected — set it up next." (Nothing is falsely marked connected.)
- [ ] **6 (D1) — Pick an agent + an AI that isn't live → seat is held.** Choose an agent and an AI that isn't running → you get a **"one step left"** hold page (not a visible countdown), not the match.
- [ ] **7 (D4) — Click Connect.** The hold page's Connect button takes you to your connections page to start the AI you picked.
- [ ] **8 (E1) — Start one client, only its provider lights up.** Connect, say, Claude Code → **only Claude** goes "ready." Other AIs stay not-connected.
- [ ] **9 (D2) — Auto-confirm.** The hold page is watching; the instant the AI you picked starts playing, your seat locks and you're sent into the match — no refresh.
- [ ] **10 (C2) — Practice match starts on its own.** If it's a practice game and your seat is live, it begins the moment you're in.

## 2. Once you have a live AI (the next few times)

- [ ] **11 (C1) — Instant seat.** The AI you pick is already running → you go straight into the match, no hold.
- [ ] **12 (B1) — Status tags make sense.** Each AI shows **ready / connected-not-playing / not-connected / busy**, and ready ones float to the top.
- [ ] **13 (B2) — Already-in agents are hidden** from the list, so you can't double-add.
- [ ] **14 (D3) — Hold can run out.** Hold a seat but don't connect the chosen AI in time → the seat is released and you're offered a link to rejoin.
- [ ] **15 (E2) — Pick a not-yet-live AI → held, not faked.** With only Claude running, pick **Gemini** for the seat → you get the held-seat hold page, because Gemini really isn't live yet.
- [ ] **16 (E3) — Add a second client, both stay live.** Now also start Gemini CLI → Gemini goes ready **and** Claude stays ready. They work side by side.
- [ ] **17 (G1) — Leave before it starts.** Leave a not-yet-started match → your seat is freed and you can rejoin.

## 3. Bumps you might hit

- [ ] **18 (F2) — Same agent twice.** Add an agent that's already in the match → "already in this game."
- [ ] **19 (F1) — Too late to join.** Match already started or finished → "not open for registration."
- [ ] **20 (F3) — That AI is busy (one AI = one seat).** Pick an AI already chosen for another unfinished seat → blocked ("… is already in a game"). To run a second agent here, pick a different AI for it.
- [ ] **21 (G2) — Can't leave mid-game.** Try to leave after the game starts → blocked.

## 4. Admin-only (rare, you testing)

- [ ] **22 (A4) — Hidden games.** A normal user opening an admin-only game → "Game not found."
- [ ] **23 (H1) — Multi-seat.** An admin can add several agents at once; a normal user can't.
- [ ] **24 (H2) — One-AI-one-seat bypass.** An admin can reuse an AI that's already busy in another seat — but it still has to be genuinely live to lock in.
