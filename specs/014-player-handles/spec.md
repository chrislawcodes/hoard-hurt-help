# Feature 014 — Player Handles (Public Operator Identity)

**Status:** Draft
**Created:** 2026-06-05
**Input:** "I want to identify each player." Decide and design how a human is
identified on the site. Decision (made with Chris): keep Google purely for
**login**, and give each human a **chosen handle** for **public display** —
do not force real names.

---

## Summary

Add a public **handle** to each human account and show it as the owner credit
next to their agents on the leaderboard. The handle is a name the person picks
(like `@coin_goblin`), not their Google legal name.

The key idea that makes this safe and simple:

```text
Auth identity  ≠  Display identity
Google = who you really are (private, accountable)
Handle = who the crowd sees (public, chosen)
```

Google sign-in is already decided (`DESIGN.md` §6) and is unchanged here. We add
a display layer on top of it. Because every handle is still tied to a real
Google account, we keep full backend accountability (ban / reset a bad handle)
while giving operators a competitive, low-friction public identity.

### Why a handle, not a forced real name

The decision was made by walking the two questions that actually move the call:

| Question | Answer chosen | What it implies |
|---|---|---|
| Where does the identity show? | **Semi-public — the leaderboard**, not the live turn feed. | A leaderboard is exactly where a *chosen* competitive identity belongs. |
| What job does it do? | **Competition & bragging rights.** | This is the Kaggle / Lichess / Chess.com pattern: handles, not legal names. |

Forcing a legal name onto a public page tied to an agent that may play "evil"
(Hurt-heavy) is a privacy deterrent that buys nothing, because Google already
gives us the accountability a real name would. So: **handle for display, Google
for login.**

### Decisions locked (2026-06-05, with Chris)

Four big decisions were walked through and settled:

| # | Decision | Choice |
|---|---|---|
| 1 | Whose name we show | **One handle per person**, shown beside every agent they run. |
| 2 | When you must pick it | **At first-agent creation** — pre-filled, ~one click. Spectators never need one. |
| 3 | Stopping bad handles | **Light touch + a shared bad-words list** that blocks slurs in *all* public text: handles, agent names, and agent messages. |
| 4 | How big the handle looks | **Small credit under the agent name** — agent is the competitor, human is the credit. |

Decision 3 widens the scope past handles: the same bad-words list must also screen
**agent display names** and the **public messages agents post each turn**. The
agent-message screening is a separate, larger chunk of work (it touches the game
engine, not the signup form) — see "Shared Bad-Words List" below.

### Edge cases resolved (2026-06-05, with Chris)

| Edge case | Resolution |
|---|---|
| **The core rule** | You must have a handle to **own an agent**. New users meet it at first-agent creation; pure spectators (no agents) are never asked. |
| **Existing operators** | Anyone who already owns an agent is **hard-gated at their next login**: they pick a handle before they can use the dashboard. |
| **Leaderboard before they've logged in** | An agent whose owner hasn't set a handle yet shows **no credit line** (same as a Sim), until the owner logs in and picks one. |
| **Where the credit shows** | **Leaderboard only.** Not the lobby, replays, analysis pages, or live turn feed — consistent with the "semi-public, leaderboard only" call. |
| **Capitalization** | Show the capitals the user typed (`@ZeusMaster`); enforce uniqueness **case-insensitively** so `@zeusmaster` can't also exist. |
| **Suggestion fallback** | Pre-fill from Google given name → if missing/unusable, the email name-part → if that fails, `player<random>`. Always a valid, free suggestion. |

### Bonus this also fixes

Agent names are unique only **per owner** (`uq_bots_user_id_name`), so two
different people can both run a "ZeusBot." On a public leaderboard those collide
and a spectator can't tell them apart. The owner handle disambiguates them for
free: `ZeusBot · by @alice` vs `ZeusBot · by @bob`. We do **not** need to make
agent names globally unique.

---

## Goals

- Give every human a single **public handle** they choose and control.
- Show the handle as the **owner credit** on the leaderboard, beside each agent.
- Keep Google sign-in exactly as-is — handle is display only, never auth.
- Keep real names and emails **private** (admin-only), never shown publicly.
- Make picking a handle near-zero friction (pre-filled suggestion).
- Keep handles safe: uniqueness, a blocklist, reporting, and admin reset — all
  backed by the real Google identity behind each handle.
- Disambiguate same-named agents owned by different people.

## Non-Goals

- Do **not** force or display real / legal names anywhere public.
- Do **not** change the login flow, Google scopes, or session handling.
- Do **not** put a human handle in the **live turn-by-turn viewer** — that
  surface stays agent-only (per the "semi-public, leaderboard only" decision).
- Do **not** build a per-owner aggregate ranking ("best operator") in this
  feature. Ratings stay per-agent; the handle is just a credit. (Future note.)
- Do **not** make agent names globally unique.
- Do **not** add public profile pages for handles in this feature.
- Do **not** merge or push this feature as part of the spec work.

---

## Primary Users

| User | Job on this feature |
|---|---|
| **Bot operator** (primary) | Pick a handle once; see their handle credited next to their agents on the leaderboard. |
| **Spectator** (secondary) | Read "who runs this agent" at a glance and tell two same-named agents apart. |
| **Admin / owner** | Reset an abusive handle; still see the real person behind any handle for research and moderation. |

The primary user is the **bot operator**. Where operator simplicity and admin
control conflict, keep the operator flow simple and push moderation tools into
the admin surface.

---

## The Decision, Concretely

### What a handle is

- A short public name the operator chooses, shown with an `@` prefix
  (`@coin_goblin`). The `@` is a visual signal: "this is a chosen handle, not a
  real name."
- One handle **per human account** (the `users` row), reused across all of that
  person's agents. It is the *operator's* identity, not the agent's.
- Optional for pure spectators. **Required before an agent can enter a match**,
  because that is the moment the operator becomes public on the leaderboard.

### Rules

| Rule | Value | Why |
|---|---|---|
| Allowed characters | `A-Z`, `a-z`, `0-9`, `_` | Predictable, URL-safe, no homoglyph games. |
| Capitalization | Keep the case the user typed for **display**; compare **case-insensitively** for uniqueness | Lets people style their name (`@ZeusMaster`) without allowing a near-duplicate `@zeusmaster`. |
| Must start with | a letter | Avoids all-number handles that read like IDs. |
| Length | 3–20 chars | Long enough to be distinct, short enough for a table cell. |
| Uniqueness | **Globally unique, case-insensitive** | Two `@alice`es defeat the purpose. |
| Reserved words | `admin`, `system`, `sim`, `agentludum`, `staff`, `mod`, `null`, `none` (extend in code) | Stop impersonation of the platform. |
| Blocklist | A slur / profanity list checked on save | First line against abusive names. |
| Changeable | Yes, from the account page | People outgrow a handle. |
| Change cooldown | Once per 30 days | Keeps leaderboard identity stable; stops dodging a report. |

Identity is anchored to the **stable `users.id`, not the handle string**. So a
handle change (or an admin reset) keeps all of that person's leaderboard
history — the rating belongs to the agent, the credit belongs to the account.

### Where the handle shows — and where it does NOT

| Surface | Shows handle? | Form |
|---|---|---|
| Leaderboard row (agent) | **Yes** | `AgentName` primary, `by @handle` secondary/muted line. |
| Leaderboard row (Sim) | No | Sims have no human owner. Existing `Sim` tag stays; no "by" line. |
| Operator's own account / dashboard | Yes | "Your handle: @coin_goblin · Change" |
| Live turn-by-turn viewer | **No** | Stays agent-only by decision. |
| Anywhere | **Never** the email or legal name | Those are admin-only. |

---

## Screen-By-Screen Flow

### 1. Pick a handle (first time)

Handled by **one shared page** (`/me/handle`) that the user is routed to the
first time a handle is actually needed — a **gate** on the agent-owner surfaces
(the bots panel, `/play`, match-join). A new user hits it the moment they go to
create their first agent; an existing agent-owner hits it at next login. Pure
spectators (no agents) are never routed there.

- The page shows a `Handle` field **pre-filled** with a suggestion: Google given
  name → email name-part → `player<random>` (de-duplicated). The operator can
  accept it or edit it.
- On submit, validate (chars, length, uniqueness, reserved, word filter). On
  success, store and send the user back to where they were headed (`next`).
- One page, three entry points (new user, existing-owner gate, later changes) —
  not a field duplicated into the new-agent form.

### 2. See it credited

- After a rated match completes, the operator's agent appears on `/leaderboard`
  with `by @handle` under the agent name. Same-named rival agents are now
  distinguishable.

### 3. Change it

- From the account / dashboard area: "Your handle: @coin_goblin · Change."
- Same validation. Blocked with a clear message if inside the 30-day cooldown.
- The **old handle is released immediately** — anyone can take it right after.

### 4. Moderation (admin)

- **No report button in this version.** The word-list blocks slurs on save, and
  the admin can reset anything. Reporting is a later addition if needed.
- Admin surface can **force-reset** a handle (clears it; the operator picks a new
  one when they next sign in / need it). Because the Google identity is known, a
  repeat offender can be banned, not just renamed.

---

## Key States & Microcopy

Copy follows `COPY.md`: plain, high-school reading level, **"agent" not "bot."**

### Handle field (pick / change)

```text
Label:        Handle
Help:         Your public name on leaderboards. Pick something you like —
              it doesn't have to be your real name. Letters, numbers, and
              underscores. 3–20 characters.
Prefix:       @
Button:       Save handle
```

### Validation errors

```text
Taken:        That handle is taken. Try another.
Too short:    Handles need at least 3 characters.
Too long:     Handles can be at most 20 characters.
Bad chars:    Use only letters, numbers, and underscores. Start with a letter.
Reserved:     That handle is reserved. Pick a different one.
Blocked:      That handle isn't allowed. Pick a different one.
Cooldown:     You changed your handle recently. You can change it again on
              {date}.
```

### Required-before-join gate

```text
Heading:      Pick a handle first
Body:         Your handle is how spectators see who runs this agent on the
              leaderboard. Set one to enter a match.
```

### Leaderboard credit line

```text
Row:          ZeusBot
              by @alice
```

### Account page

```text
Your handle:  @coin_goblin   [ Change ]
None yet:     No handle yet — you'll pick one when you create your first agent.
```

---

## Information Architecture (leaderboard row)

The existing "Competitor" cell gains a second line. Order of prominence:

```text
[rank]  AgentName            rating  matches  last played
        by @handle  [Sim?] [Provisional?]
```

- `AgentName` stays the primary, bold text (unchanged).
- `by @handle` is a smaller, muted second line — present for agents, absent for
  Sims.
- Existing `Sim` / `Provisional` tags keep their current position and meaning.
- At phone width, the `by @handle` line stacks under the name and stays visible
  (it is identity, not a stat) while stats collapse — consistent with the 013
  mobile rule.

---

## Data Model Notes

### `users` — add one column

| Field | Type | Notes |
|---|---|---|
| `handle` | `str \| None`, `String(20)` | **Display** form, exactly as typed (e.g. `ZeusMaster`). `NULL` until the user picks one. |
| `handle_key` | `str \| None`, `String(20)` | Lowercased `handle`. Carries the unique index; used for all lookups. `NULL` until set. |
| `handle_changed_at` | `datetime \| None` | Powers the 30-day change cooldown. `NULL` until first set. |

- **Uniqueness:** the unique index lives on `handle_key` (lowercased), so
  `@Alice` and `@alice` can't coexist while `handle` still shows the typed case.
  A separate `handle_key` column keeps this portable across SQLite and Postgres
  without relying on database-specific functional/`COLLATE` indexes.
- **Migration:** Alembic migration adds the two nullable columns + the unique
  index on `handle_key`. Existing users get `NULL`; because they already own
  agents, they are **hard-gated at next login** to pick one (see Edge cases). No
  backfill from real names — that would leak legal names, which this feature
  explicitly avoids.
- SQLite dev DBs rebuild from models (`Base.metadata.create_all`), so confirm
  the migration applies cleanly on SQLite too (per the project's known
  migration caveat).

### Leaderboard read model — carry the owner handle

`app/read_models/leaderboard.py` builds each row's `display_name` from
`bot.name` for agents. Extend it to also carry the **owner handle**:

- Join `Bot.user_id → User.handle` when building agent participants.
- Add `owner_handle: str | None` to `LeaderboardRow` (and the internal
  participant/state dataclasses it flows through).
- For Sims, `owner_handle` is `None` (Sims have no human owner).
- The Elo math, keys, and sorting are **unchanged** — this is display-only data
  riding alongside the existing fields.

### Validation / normalization — one home

Put handle rules in a single small module (e.g.
`app/identity/handle.py` — domain-meaningful name, not `utils.py`): normalize,
validate characters/length/reserved/blocklist, and suggest-from-name. Routes and
tests call it. Keep it out of `app/games/` and `mcp_server/`.

---

## Shared Bad-Words List (Decision 3)

There is **one** bad-words list for the whole site, not a per-feature copy. It
lives in code (so it can grow without a migration) behind a small, well-named
module (e.g. `app/identity/word_filter.py`). Everything that produces
public-facing text calls it.

### What it checks

| Public surface | When it runs | What happens on a hit |
|---|---|---|
| **Handle** | On save (pick / change) | Reject the save with "That handle isn't allowed. Pick a different one." Nothing is stored. |
| **Agent display name** | On agent create / rename | Reject the save with a parallel message. Nothing is stored. |
| **Agent public message** (each turn) | When the agent submits a turn | See "Agent messages" below — this is the larger, phased piece. |

### Design rules

- **One source of truth.** Slurs and reserved words live in one code list; all
  three surfaces import the same checker. Adding a word covers every surface at
  once.
- **Match safely.** Normalize before matching (lowercase, strip simple
  look-alikes) so trivial dodges like spacing or capitalization don't slip
  through. Be honest that no list is perfect — pair it with the report + admin
  reset path.
- **Never echo the blocked text.** Per Chris: when something is rejected, do
  **not** display the offending word back to the user or anywhere public. Show a
  generic "not allowed" message only.

### Agent messages — phased, separate work

Agents broadcast a public message every turn (`DESIGN.md` §4), shown live in the
viewer and stored in history. Screening those is real work and touches the game
engine and turn pipeline, not the signup form. So:

- **Phase 1 (this feature):** ship the shared list + apply it to **handles and
  agent names**. Build the checker so it's reusable.
- **Phase 2 (follow-up):** apply the same checker to agent turn messages. On a
  hit, the message **still posts**, but each blocked word is **replaced with four
  asterisks (`****`)** — a fixed length that doesn't reveal the original word.
  This is censoring, not blocking: the turn proceeds normally and is never
  defaulted to Hoard over a word.

This keeps the handle feature small while honoring the "no slurs in any public
text" intent and avoiding a second, drifting word list later.

---

## Functional Requirements

- **FR-001**: `users` MUST gain a nullable display `handle` plus a lowercased
  `handle_key` that carries a unique index (case-insensitive uniqueness).
- **FR-002**: The login flow, Google scopes, and session handling MUST be
  unchanged. Handle is display-only and never used for authentication.
- **FR-003**: A handle MUST match `^[A-Za-z][A-Za-z0-9_]{2,19}$`; uniqueness MUST
  be enforced on its lowercased form. The typed case MUST be preserved for
  display.
- **FR-004**: Handle creation/change MUST reject taken, reserved, and
  blocklisted values with the specified plain-language messages.
- **FR-005**: A user MUST have a handle to own an agent. New users MUST be asked
  at first-agent creation; existing agent-owners with no handle MUST be gated at
  next login before using the dashboard.
- **FR-006**: The handle field MUST be pre-filled with a unique, valid
  suggestion: Google given name → email name-part → `player<random>` as
  fallbacks when the prior source is missing or unusable.
- **FR-007**: A handle MUST be changeable by its owner, no more than once per 30
  days (`handle_changed_at`).
- **FR-008**: The leaderboard MUST show `by @handle` beneath an **agent** whose
  owner has a handle, and MUST show **no owner line** for Sims or for agents
  whose owner has not yet set a handle.
- **FR-009**: The system MUST NOT display any user's email or legal/real name on
  any public surface.
- **FR-010**: The live turn-by-turn viewer MUST remain agent-only — no human
  handle in the feed.
- **FR-011**: Admin MUST be able to force-reset a handle; doing so MUST preserve
  the user's leaderboard history (identity is keyed on `users.id`).
- **FR-012**: A handle change MUST NOT alter any agent's Elo rating or match
  history.
- **FR-013**: The leaderboard MUST stay readable at phone width with the handle
  line, without horizontal scrolling.
- **FR-014**: Reserved words and the bad-words list MUST live in code (not the
  DB) so they can be extended without a migration.
- **FR-015**: There MUST be a single shared bad-words checker used by every
  public-text surface. Handles and **agent display names** MUST be screened by it
  on save; a hit MUST reject the save.
- **FR-016**: A rejected value MUST NOT be echoed back to the user or shown
  anywhere public — only a generic "not allowed" message.
- **FR-017**: The checker MUST be built reusable so agent **turn messages** can
  adopt it in a follow-up phase without a second word list.
- **FR-018**: When a handle is changed or reset, its previous value MUST become
  immediately available for any other user to claim (no reuse cooldown).
- **FR-019** (Phase 2): When an agent turn message hits the bad-words list, the
  message MUST still post with each blocked word replaced by exactly four
  asterisks (`****`); the turn MUST NOT be defaulted to Hoard for this reason.

---

## User Stories

### Story 1 — Pick a handle without friction (P1)

As a new operator, I want a handle suggested for me when I create my first
agent, so I'm public on the leaderboard without stopping to invent a name.

**Independent test:** A user with no handle visits `/me/bots/new`; the handle
field is pre-filled with a free suggestion from their Google given name; saving
the form stores a valid handle.

**Acceptance scenarios:**

1. **Given** a user with no handle and Google given name "Chris", **When** they
   open the new-agent flow, **Then** the handle field is pre-filled with `chris`
   (or the next free variant).
2. **Given** the pre-filled handle, **When** the user submits unchanged, **Then**
   a valid handle is saved and the agent is created.
3. **Given** the user edits the handle to a taken value, **When** they submit,
   **Then** they see "That handle is taken. Try another." and nothing is saved.

### Story 2 — See my handle credited on the leaderboard (P1)

As an operator, I want my handle shown next to my agent so spectators know it's
mine and can tell it apart from a same-named rival.

**Acceptance scenarios:**

1. **Given** my agent has a completed rated match, **When** the leaderboard
   renders, **Then** my agent's row shows `by @myhandle` under the agent name.
2. **Given** two different owners each have an agent named "ZeusBot", **When**
   both appear, **Then** each shows a different `by @handle` line.
3. **Given** a Sim row, **When** it renders, **Then** it shows the `Sim` tag and
   **no** `by @handle` line.

### Story 3 — Change my handle (P2)

As an operator, I want to change my handle from my account, within reason.

**Acceptance scenarios:**

1. **Given** I have a handle set 40 days ago, **When** I change it to a free
   valid value, **Then** it updates everywhere it's shown.
2. **Given** I changed my handle 5 days ago, **When** I try to change it again,
   **Then** I see the cooldown message with the date I can next change it.
3. **Given** I change my handle, **When** the leaderboard re-renders, **Then**
   my agents' ratings and match history are unchanged.

### Story 4 — Keep handles safe (P2)

As the admin, I want to reset an abusive handle while keeping history and knowing
who the person really is.

**Acceptance scenarios:**

1. **Given** an offensive handle, **When** I force-reset it, **Then** it's
   cleared and the operator is prompted to pick a new one next time they need it.
2. **Given** a reset handle, **When** the leaderboard re-renders, **Then** the
   operator's agents and ratings are intact.
3. **Given** any handle, **When** I look in the admin surface, **Then** I can
   still see the real Google identity behind it.

---

## Success Criteria

- **SC-001**: A new operator gets a working handle in one pre-filled field, no
  separate step.
- **SC-002**: The leaderboard shows `by @handle` for agents and nothing for
  Sims.
- **SC-003**: No public surface ever shows an email or legal name.
- **SC-004**: The live viewer shows no human handle.
- **SC-005**: Handles are globally unique, case-insensitive, and reject
  reserved/blocked values.
- **SC-006**: Changing or resetting a handle never changes an agent's rating or
  history.
- **SC-007**: The leaderboard with the handle line works on mobile without
  horizontal scrolling.

---

## Decided (was Open Questions)

- ✅ **Gate point** — require the handle at **first-agent creation**. Spectators
  never need one.
- ✅ **Whose name / how it's shown** — one handle per person, shown as a small
  `by @handle` credit **under** the agent name.
- ✅ **Moderation level** — light touch + a single shared bad-words list across
  all public text (see "Shared Bad-Words List").

## Also decided (2026-06-05)

- ✅ **Reporting** — **no report button for now.** The word-list blocks slurs on
  save, admin can reset any handle, and Google ties every handle to a real
  person. Add reporting later only if bad handles slip through in practice.
- ✅ **Freeing a handle** — when a handle is changed or reset, the old string is
  **released immediately** for anyone to take. No reuse cooldown.
- ✅ **Agent-message on-hit (Phase 2)** — the message still posts, but each
  blocked word is **replaced with four asterisks (`****`)**. Fixed length, so it
  never reveals how long the word was. (Handles and agent names are still
  *rejected* on save — masking is only for the free-text turn messages.)

No open questions remain.

---

## Recommendation

Ship the first version with:

```text
Identity:   Google for login (unchanged) + a chosen handle for public display.
Field:      users.handle, nullable, case-insensitive unique, ^[a-z][a-z0-9_]{2,19}$.
Pick it:    Pre-filled from Google given name, at first-agent creation.
Show it:    "by @handle" under the agent name on the leaderboard only.
Hide:       Never show email or real name; no handle in the live viewer.
Safety:     One shared bad-words list (handles + agent names now, agent messages next)
            + reserved names + report + admin reset, backed by Google identity.
Change:     Allowed, once per 30 days; history keyed on users.id, never lost.
```

This gives operators a real, fun, competitive identity, keeps their privacy,
disambiguates same-named agents for spectators, and keeps full accountability —
without touching the login that already works.

---

## Constitution Check

PASS. Login is unchanged; private data (email, real name, strategy prompts) stays
private; copy uses "agent," not "bot"; the new module avoids vague names; types
are annotated; the live viewer boundary is respected. Implementation must follow
the Preflight Gate (`ruff` + `mypy` + `pytest`) before any push or PR, and must
not use suppressions to pass checks.
