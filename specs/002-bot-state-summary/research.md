# Research: heuristics for the bot-state summary

These are the non-obvious design calls. All must be **deterministic** (so tests
aren't flaky) and **action-only** (no message text — FR-015).

## Q1: How to define "reciprocity" cheaply and unambiguously

**Context**: `returned_help` / `returned_hurt` are the trust-relevant signals, but
"did they return it?" can mean many things.

**Options**:
1. **Next-turn mirror**: after you HELP X on turn T, did X HELP you on turn T+1? (and same for HURT). Boolean.
2. **Ever-returned**: did X ever HELP you after any of your HELPs? Boolean.
3. **Ratio**: returned / opportunities, as a percentage.

**Decision**: Option 1 (next-turn mirror), most recent occurrence. **Rationale**:
cheapest to compute, easy to reason about, matches how Tit-for-Tat thinks, and is
unambiguous for tests. Ratio (option 3) is richer but invites tuning debates and
is better suited to the v2 tier. Document the definition in the prompt so bots
interpret it correctly.

## Q2: Alliance / help-ring detection

**Context**: the marquee "server sees the whole board" signal (US4).

**Options**:
1. **Mutual-help graph + connected components** over the current round.
2. Frequent-itemset / clustering over who-helps-whom.
3. Co-scoring correlation.

**Decision**: Option 1. Build directed help edges over the current round; keep
edges where both directions ≥ `ALLY_MIN_HELPS`; report connected components of
that mutual graph as alliances with a `strength` = total mutual helps. **Rationale**:
O(edges) and fully deterministic; explainable to bot authors; no ML. Options 2–3
are heavier and add tuning surface for marginal gain. Cap at `MAX_ALLIANCES`.

**Window choice**: current round only (scores reset per round, so alliances are
round-scoped). Reconsider a rolling window in v2 if needed.

## Q3: "Surging" definition

**Options**:
1. Rank improved by ≥ `SURGE_RANK_JUMP` over the last `SURGE_WINDOW` turns.
2. Top round-score gainer over the window.
3. Highest points_delta last turn.

**Decision**: Option 1 primary, with Option 2 as the tiebreak/fallback when ranks
are flat early in a round. **Rationale**: rank movement is what a competitor cares
about; pure points_delta (option 3) is noisy. Report top `MAX_SURGING`.

## Q4: "Pattern break" flag

**Context**: a cheap pointer telling the bot "X did something unusual — consider
pulling its history."

**Decision**: compare an opponent's action **this** resolved turn to its dominant
style so far (the mode of HOARD/HELP/HURT). If this turn's action differs from a
dominant style that previously held ≥ 60% share, flag it. Deterministic, action-only.
Only computed for opponents already on the short-list (bounds cost).

## Q5: Read-time cost at 100 bots

**Context**: we moved cost from tokens to server CPU; don't recreate the blow-up
on the server.

**Decision**: aggregate with SQL `GROUP BY` (per-opponent helped/hurt counts,
style counts, per-round help/hurt totals) in a few queries rather than Python
loops over 10k rows; compute full `OpponentStat` detail only for the short-list.
If a profile at 100 bots still shows hotspots, the v2 path is resolve-time
denormalized counters (plan Decision 2) — explicitly out of scope here, but the
API shape is chosen so that swap needs no contract change.

## Q6: Pull rate-limiting

**Decision**: reuse the existing 1 Hz-per-key throttle pattern as a small shared
dependency applied to the pull endpoints (separate bucket from `/turn` so a pull
doesn't starve polling). Over-limit → `RATE_LIMITED` envelope. Keeps behavior
consistent with today's poll throttle and prevents a bot from hammering history.
