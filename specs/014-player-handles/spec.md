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
| Allowed characters | `a-z`, `0-9`, `_` (lowercased on save) | Predictable, URL-safe, no homoglyph games. |
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

Triggered the first time a handle is actually needed — i.e. when the operator
**creates their first agent** (the existing `/me/bots/new` flow, which login
already redirects new users to). Pure spectators are never blocked.

- A `Handle` field appears, **pre-filled** with a suggestion derived from the
  Google given name, slugified and de-duplicated (`Chris` → `chris`; if taken,
  `chris2`, `chris_l`, …). The operator can accept it as-is or edit it.
- On submit, validate (chars, length, uniqueness, reserved, blocklist). On
  success, store and continue creating the agent.
- This is **one field added to a flow that already exists** — not a new
  blocking gate at login.

### 2. See it credited

- After a rated match completes, the operator's agent appears on `/leaderboard`
  with `by @handle` under the agent name. Same-named rival agents are now
  distinguishable.

### 3. Change it

- From the account / dashboard area: "Your handle: @coin_goblin · Change."
- Same validation. Blocked with a clear message if inside the 30-day cooldown.

### 4. Moderation (admin)

- Every public handle has a **Report** affordance (small link on the leaderboard
  row, or a single report control per page — implementation choice).
- Admin surface lists reported handles and can **force-reset** a handle (clears
  it; the operator is prompted to pick a new one next time they need it).
  Because the Google identity is known, a repeat offender can be banned, not
  just renamed.

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
| `handle` | `str \| None`, `String(20)` | Lowercased on save. `NULL` until the user picks one. |
| `handle_changed_at` | `datetime \| None` | Powers the 30-day change cooldown. `NULL` until first set. |

- **Uniqueness:** case-insensitive unique. Store already-lowercased and add a
  unique index on `handle`, so `@Alice` and `@alice` can't coexist.
- **Migration:** Alembic migration adds the nullable column + unique index.
  Existing users get `NULL` and are prompted to pick a handle the next time they
  need one (next agent creation). No backfill from real names — that would leak
  legal names, which this feature explicitly avoids.
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

## Functional Requirements

- **FR-001**: `users` MUST gain a nullable, case-insensitively-unique `handle`.
- **FR-002**: The login flow, Google scopes, and session handling MUST be
  unchanged. Handle is display-only and never used for authentication.
- **FR-003**: A handle MUST match `^[a-z][a-z0-9_]{2,19}$` after lowercasing.
- **FR-004**: Handle creation/change MUST reject taken, reserved, and
  blocklisted values with the specified plain-language messages.
- **FR-005**: An agent MUST NOT be able to enter a match until its owner has a
  handle.
- **FR-006**: The handle field at first-agent creation MUST be pre-filled with a
  unique suggestion derived from the Google given name.
- **FR-007**: A handle MUST be changeable by its owner, no more than once per 30
  days (`handle_changed_at`).
- **FR-008**: The leaderboard MUST show `by @handle` beneath each **agent**'s
  name and MUST NOT show an owner line for **Sims**.
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
- **FR-014**: Reserved words and the blocklist MUST live in code (not the DB) so
  they can be extended without a migration.

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

## Open Questions

1. **Gate point.** Require the handle at **first-agent creation** (recommended)
   or at **first match join**? First-agent is simpler and earlier; join is the
   strictly-correct "becomes public" moment. They differ only for someone who
   creates an agent but never joins.
2. **Report affordance.** Per-row report link vs a single "report a handle"
   control on the page? Per-row is clearer but noisier in the table.
3. **Freeing a changed/reset handle.** Release the old string immediately for
   anyone to take, or hold it on a cooldown to stop impersonation churn?
4. **Display weight.** Is `by @handle` muted-secondary enough, or do some
   spectators want the handle as prominent as the agent name? Default:
   secondary, because the *agent* is the competitor.

---

## Recommendation

Ship the first version with:

```text
Identity:   Google for login (unchanged) + a chosen handle for public display.
Field:      users.handle, nullable, case-insensitive unique, ^[a-z][a-z0-9_]{2,19}$.
Pick it:    Pre-filled from Google given name, at first-agent creation.
Show it:    "by @handle" under the agent name on the leaderboard only.
Hide:       Never show email or real name; no handle in the live viewer.
Safety:     Reserved list + blocklist + report + admin reset, backed by Google identity.
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
