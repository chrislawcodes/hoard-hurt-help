# Joining a Game — Real-World Test Stories

A manual test checklist for the join-a-game flow, ordered the way a brand-new
user actually hits each step. Use it to tick off behavior in the live app.

Two timing facts to know:

- A **held seat** gives you **90 seconds** to bring your AI online
  (`SEAT_HOLD_SECONDS`, `app/engine/seat_hold.py`).
- A provider counts as **"live"** only if its client was seen in the
  **last 90 seconds** (`LIVE_WINDOW_SECONDS`, `app/engine/connection_health.py`).

Stories **5, 6, 8, 15, 16** directly exercise the single-provider fix
(PR #392 — an MCP connection enables only the provider whose client actually
connected).

Old labels from the first draft are kept in parentheses for cross-reference.

---

## 1. First time getting in (in the order it happens)

The straight-line journey for someone who just found the site and clicked Join.
The Join button lives on a match page and links to `…/matches/{id}/join`.

- [ ] **1 (A1) — Signed out.** Click Join while logged out → sent to Google sign-in, then dropped back on the join screen.
- [ ] **2 (A2) — Pick a username.** No handle yet → sent to choose one, then back to join.
- [ ] **3 (A3) — Make an AI agent.** No agent yet → sent to the "create an agent" page, then back to join.
- [ ] **4 (A5) — The form appears.** Now you have an agent, so the join form shows. Backing out here leaves no half-join — no seat taken yet.
- [ ] **5 (E4) — Nothing's connected yet, and the form says so honestly.** You haven't started an AI client, so **no provider shows "live."** Your new agent shows as offline/unconfigured. (Nothing is falsely marked connected.)
- [ ] **6 (D1) — Pick it anyway → seat is held.** Choose your not-yet-live agent → you get a **90-second countdown** page ("one step left"), not the match.
- [ ] **7 (D4) — Click Connect.** The countdown page's Connect button takes you to your connections page to start your AI.
- [ ] **8 (E1) — Start one client, only its provider lights up.** Connect, say, Claude Code → **only Claude** goes "live." Other providers stay offline. *(This is the bug we just fixed.)*
- [ ] **9 (D2) — Auto-confirm.** The countdown page is watching; the instant your AI connects, your seat locks and you're sent into the match — no refresh.
- [ ] **10 (C2) — Practice match starts on its own.** If it's a practice game and your seat is live, it begins the moment you're in.

## 2. Once you have a live AI (the next few times)

- [ ] **11 (C1) — Instant seat.** Your AI is already running → pick an agent and you go straight into the match, no countdown.
- [ ] **12 (B1) — Status tags make sense.** Each provider shows **live / offline / unconfigured**, and live ones float to the top.
- [ ] **13 (B2) — Already-in agents are hidden** from the list, so you can't double-add.
- [ ] **14 (D3) — Countdown can run out.** Start a held seat but don't connect in time → the seat is released and you're offered a link to rejoin.
- [ ] **15 (E2) — A second-provider agent is held, not faked.** With only Claude running, pick a **Gemini** agent → you get the held-seat countdown, because Gemini really isn't live yet.
- [ ] **16 (E3) — Add a second client, both stay live.** Now also start Gemini CLI → Gemini goes live **and** Claude stays live. They work side by side.
- [ ] **17 (G1) — Leave before it starts.** Leave a not-yet-started match → your seat is freed and you can rejoin.

## 3. Bumps you might hit

- [ ] **18 (F2) — Same agent twice.** Add an agent that's already in the match → "already in this game."
- [ ] **19 (F1) — Too late to join.** Match already started or finished → "not open for registration."
- [ ] **20 (F3) — At capacity.** Your machines are already running their max matches for that provider → "machines at capacity."
- [ ] **21 (G2) — Can't leave mid-game.** Try to leave after the game starts → blocked.

## 4. Admin-only (rare, you testing)

- [ ] **22 (A4) — Hidden games.** A normal user opening an admin-only game → "Game not found."
- [ ] **23 (H1) — Multi-seat.** An admin can add several agents at once; a normal user can't.
- [ ] **24 (H2) — Capacity bypass.** An admin can seat an agent already busy elsewhere — but its provider still has to be genuinely live.
