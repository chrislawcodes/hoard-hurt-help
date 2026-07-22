# Spec — Join page as a lineup

**Slug:** `join-lineup`  ·  **Path:** Thin  ·  **Date:** 2026-07-21
**Revision:** 2 (after feasibility + requirements adversarial review)

## Problem

The match join page (`/games/{game}/matches/{id}/join`) shows ~245 words and 20+
controls for what is usually one decision: which of my agents enter this match,
and which AI plays each. The page runs a countdown, so the reading cost lands
exactly when the user has least time.

Measured on the live page (`M_3980`, a user with 4 agents and 3 ready AIs):

| Source of weight | Detail |
|---|---|
| Truncated strategy preview per agent | First 80 chars of the prompt, cut mid-word. **Identical text on 2 of 4 agents** — distinguishes nothing. |
| AI picker repeated per agent | 3 chips × 4 agents = 12 radios, 4 "WHICH AI PLAYS X?" labels, 4 "Connect another AI" links. All 12 read "● ready". |
| Rule paragraph | 35 words explaining "one AI per agent" *before* the user can break the rule. |
| Footer paragraph | 33 words restating the checkboxes above. |
| Win record per agent | 3 of 4 read "Won 0 of N" — fails to distinguish, deflates on the way in. |

On a phone this is 4+ screens of scrolling.

## Goal

The whole entry decision fits on one screen, desktop and phone, and the lineup is
readable at a glance.

## Key design decisions (settled in review)

| # | Decision | Why |
|---|---|---|
| DD1 | The new column is **`Agent.blurb`**, not `tagline` | `GameModule.tagline()` already exists and reaches templates as `game.tagline`. `blurb` is free (only an unrelated CSS class uses the word). |
| DD2 | **Ticking a row auto-selects the first free AI** for that agent | Structurally removes the length-mismatch bug class. N ticked rows always post exactly N `agent_id` and N `chosen_provider`. See Risk R1. |
| DD3 | A row whose agent has **no free AI is not tickable** | Follows from DD2 — a tick that can't pair would post an unpaired `agent_id`. |
| DD4 | Agent rows start **unticked**. The old `default_agent` pre-tick is deleted | It bound to the master "Also send an AI agent" checkbox this design removes. With `Use last lineup` cut from this PR, lineup memory is **lost, not moved** — recorded as accepted coverage loss, follow-up tracked. |
| DD7 | Per-row "no free AI" is recomputed **client-side on every change**, not just at first paint | The server's `_build_ai_options` is one global list; freeness is per-row and moves with every click. `refreshGreyOut()` must set `checkbox.disabled` per row, and `setCard()` must hard-return when no provider is free — otherwise a third row ticked with two free AIs posts `chosen_provider="undefined"`. |
| DD8 | `setCard()` also sets `radio.checked` | The highlight comes from `.ai-chip:has(input:checked)`. Without it the auto-picked pill renders unselected, which is what AC4 relies on to keep the pairing visible after the button label is reduced to `Join`. |
| DD5 | Hidden pill groups use `hidden` / `display:none`, never `opacity`/`visibility` | A visually-hidden-but-focusable radio traps keyboard users. `style.css:878` already documents this trap for the existing chips. |
| DD6 | Day one, every agent renders **name only** (no blurbs exist yet) | Accepted. Backfilling from `version.note` gains nothing — those are empty too. Four blurbs take a minute to write. |

## Non-goals

- No change to the join **submit contract**. Fields the server reads
  (`play_as`, `agent_id`, `bot_id`, `chosen_provider`, `display_name`,
  `strategy_prompt`) keep their names and semantics. The UI-only radio name
  `ai_for_<id>` is also kept — `tests/test_lobby_join_and_stacking.py:403` pins it.
- No change to seat-holding, capacity, provider-busy, or the post-join redirect.
- Not removing the win record from the site — only from **this** page. It stays on
  the agent detail page (`agents/detail.html:21`) and version list
  (`agents/_versions.html:15`).
- **Deferred:** join POST rejections (400/409) render raw JSON instead of
  re-rendering the page with its `error` slot. Pre-existing; DD2/DD3 make the new
  error states unreachable, so this is not made worse. Tracked separately.

## User stories

### P1 — Enter a match without reading (bot operator, primary)

**Acceptance criteria**

- AC1. For a user with N eligible agents, the page renders exactly N
  `.lineup-row` elements and one `.enter-you` element, and renders **no**
  `.agent-card`, `.pick-hint`, or `.agent-card-hd` element. *(DOM-assertable.
  The "fits on one screen" pixel claim is verified manually in the preview
  browser and recorded in the PR's Validation section — the suite has no browser.)*
- AC2. An agent row's pill group carries the `hidden` attribute until that row's
  checkbox is checked, so it is out of both the tab order and the accessibility
  tree (DD5).
- AC3. Choosing an AI on one row disables that AI on every other row. The gate is
  the **per-row checkbox**, not the deleted master toggle. Test: pick provider P
  on row A, assert P is unusable on row B.
- AC4. The submit button reads exactly `Join`, with no dynamic relabel. It is
  disabled when nothing is selected. *(The pairing stays visible on the rows
  themselves — a ticked row with its chosen pill highlighted — so removing the
  `Join as X (Claude) →` label does not remove the confirmation.)*
- AC5. The page no longer renders: the truncated strategy preview, the version
  number, the version **note**, the win record, the "one AI per agent" rule
  paragraph, the `①` prefix and its heading, the trailing explanatory paragraph,
  the master "Also send an AI agent" checkbox, or the dynamic button relabel
  (`updateBtn`'s `textContent` branch — only its `disabled` half survives).
- AC6. Ticking a row selects the first free AI for that agent automatically
  (DD2). A row with no free AI renders with its checkbox `disabled` (DD3).
- AC7. N ticked rows post exactly N `agent_id` values and N `chosen_provider`
  values, paired by position. Test asserts the exact `{seat_name: chosen_provider}`
  mapping for two agents on two different AIs.

### P1 — Play by hand in one click (human player)

- AC8. The manual-play row renders **above** the agent list, in its own block, for
  every user — including a user with no agents, and a user for whom no AI is free.
- AC9. Its label reads exactly `Play manually`, with the handle and
  `every move by hand` as supporting text.
- AC10. It is the first `[data-entry-row]` in the DOM in all of: 0 agents,
  5 agents, last match was agent-only, last match was human-only.
- AC11. Submitting with the manual row ticked still requests browser notification
  permission (FR-027) — the manual row keeps a stable JS hook.

### P2 — Tell my agents apart (bot operator)

- AC12. `Agent` gains a nullable `blurb` column, `String(32)` (DD1).
- AC13. A shared `clean_agent_blurb()` derives its max from
  `Agent.__table__.c.blurb.type.length` (mirroring `clean_agent_name` at
  `agents_create.py:31-53`) and raises **400** above it. **This is not optional:
  SQLite ignores `VARCHAR` length, so the in-memory test DB accepts an over-long
  value while Postgres raises `value too long` → 500 in prod.** Test posts 33
  characters directly and asserts 400.
- AC14. An empty or whitespace-only blurb is stored as `NULL`, so an untouched
  form field never creates an empty element that shifts the row (AC15).
- AC15. An agent with `blurb IS NULL` renders name only — no placeholder, no
  empty element.
- AC16. The blurb input appears on the agent **create** form and on the agent
  detail page under a **new** `POST /me/agents/{id}/set-blurb` route (mirroring
  `set-model`). It must **not** join the rename form — that input auto-submits on
  change (`agents/detail.html:15`), so sharing it would fire a rename.
- AC17. The blurb renders on the join row and the agent list row.
- AC18. A leak test seeds a sentinel blurb and asserts it is absent from the
  spectator JSON, the MCP `get_game_state` payload, and the agent next-turn
  payload (precedent: `tests/test_viewer.py:37`).

### CUT — Repeat my last lineup (bot operator)

**Removed from this PR on 2026-07-21 by Chris.** AC19–AC22 below are retained for
the follow-up only; **do not build them here.** Reason: it was the least testable
piece (a new query + a JSON hand-off + JS that can desync the checkbox from the
posted mirrors), it was not part of the original request, and cutting it shrinks
an already-large PR. Consequence recorded: the old `default_agent` pre-tick (DD4)
is still deleted, so lineup memory is **lost, not moved** — the page no longer
remembers anything about which agents you sent. Follow-up task spawned.

<details><summary>Deferred acceptance criteria (not in this PR)</summary>

- AC19. "Last lineup" = the most recent `Match` **in this match's game** holding
  ≥1 `Player` row of this user where `agent.kind == AI`, `chosen_provider IS NOT
  NULL`, and `left_at IS NULL`.
- AC20. When such a lineup exists and ≥1 of its agents is still selectable, a
  `Use last lineup` control renders in the agent-list header. It is
  `type="button"` and never submits.
- AC21. Activating it ticks each still-selectable agent and selects the AI that
  agent used. It skips any agent that is already seated here, archived, not in
  this game, or whose AI is busy or already taken by another row on this page.
- AC22. When no lineup exists or nothing in it is selectable, the control does
  not render. **This correctly hides it while the previous match is still
  running** — those seats hold those AIs, so they are genuinely unavailable and no
  lineup could be submitted anyway. Not a bug; do not "fix" by excluding the
  source match from the busy check.

</details>

## State table

Rows marked **new** are behaviour changes, not preserved behaviour.

| State | Behaviour |
|---|---|
| No agents at all | Manual row renders; agent list shows one line: `No agents yet — create one`, linking to `/me/agents/new?next=<join>` — **new** (today this copy lives inside the deleted master checkbox) |
| Every AI busy | **new**: rows render with blurbs, checkboxes `disabled` (DD3), pills show `▪ busy`. Today the entire agent section is omitted (`join.html:37`, `any_pickable_ai`), so the user cannot even see their agents. Requires the `any_pickable_ai` gate at `web_join.py:250,273` to change. |
| No AI connected at all | Preserved: all providers render so a cold-start user can pick one and be routed to set it up (`any_connected_ai`) |
| ≥1 AI connected | Preserved: only `ready`/`idle` providers render; the rest sit behind the `Connect another AI` link |
| Pill state labels | Preserved verbatim: `● ready`, `○ idle`, `⊕ not connected`, `▪ busy`. `tests/test_smart_join_flow.py:177,196` pin `not connected` and `○ idle`. |
| Agent already in this match | Preserved: row shows `already in this game` (exact existing string), renders **no checkbox and no `agent_id` mirror** |
| Preferred model verified-failing | Preserved: warning marker on the row linking to agent settings (FR-014, warn not block) |
| Paused agent (`AgentStatus.PAUSED`) | Preserved: renders and is seatable — `agents_queries.py:33` filters `archived_at` only |
| Not signed in / no handle | Preserved: existing redirects |
| Practice arena vs scheduled | Preserved: existing subtitle difference |
| No JavaScript | Manual row still posts (`name="play_as"`); agent rows do not (the hidden mirrors ship `disabled` and are enabled by JS) — pre-existing, unchanged |
| 6+ agents | Rows simply continue; no cap, no collapse |

## Navigation that must survive

- NAV1. One `+ New agent` link, carrying `?next=<join_url>`.
- NAV2. One `Connect another AI` link, carrying `?next=<join_url>`. **Required** —
  without it a user with 2 agents and 1 connected AI has no in-page path out of
  the grey-out rule. Moves from per-row (4×) to once in the lineup footer.

## Accessibility

- A11Y1. Each row's pill group has an accessible name referencing the agent
  (`role="radiogroup"` + `aria-label`, or `<fieldset><legend class="visually-hidden">`),
  replacing the deleted `Which AI plays X?` label.
- A11Y2. The row checkbox carries `aria-expanded` and `aria-controls` for its
  pill group.
- A11Y3. A pill disabled because another row took that AI exposes a text reason,
  not just the `disabled` attribute.

## Layout requirements (manual verification)

- LR1. One line per row at ≥761px: checkbox, name, blurb, pills.
- LR2. Below 761px the blurb and pills each take their own line under the name.
  Measured: at 790px a 21-char agent name still leaves room for 47 blurb
  characters, so 32 has ~15 characters of headroom.
- LR3. No horizontal scrolling at 375px.
- LR4. Selected-AI colour uses the existing `--accent` token — no new colour.

## Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | **Agent↔AI pairing corruption.** The form posts `agent_id` and `chosen_provider` as parallel lists paired by position. Two failure shapes: (a) wrong order → agents silently seated on each other's AI; (b) length mismatch → `_pair_agents_with_providers` (`web_join.py:394-397`) broadcasts one provider to every agent. For a **non-admin** (b) 409s, but for an **admin** it silently seats every agent on one AI with no error and every existing test passing. | DD2/DD3 make both shapes structurally impossible: a row can only be ticked when an AI is free, and ticking selects one. Keep the existing enable-both-mirrors-together mechanism. AC7 test. Dedicated `silent-failure` review lens. Plus an admin regression test: 3 rows ticked → 3 distinct providers posted. |
| R2 | Over-long blurb → Postgres 500 invisible to the SQLite test suite | AC13 (`clean_agent_blurb`, 400, tested with 33 chars) |
| R3 | Seated rows post a stray `agent_id` | `join.html:47` renders the id mirror for seated rows but `:64` renders the provider mirror only for unseated ones. Seated rows must render neither, plus a test. |
| R4 | Migration breaks the pinned head | `tests/test_migrations.py:187` asserts head `0046`; new `0047_agent_blurb.py` must update it. Nullable ADD COLUMN needs no `batch_alter_table` (precedent: `0046_agent_version_note.py`). |

## Consumers of every changed value

**`Agent.blurb` (new)** — write: `agents_create.py` (create form), new
`set-blurb` route in `agents_lifecycle.py`. Read: `join.html` (row),
`agents/list.html` (row), `agents/detail.html` (input). Must NOT reach:
`spectator_api.py:111`, MCP `get_game_state`, agent next-turn payload (AC18).

**Win record on the join page (removed)** — `join.html:57` only. The
`version_stats_by_id` import and call in `web_join.py:32,184` become unused and
must be removed (also drops one query per page load). `agents/detail.html:21` and
`agents/_versions.html:15` keep theirs.

**`_default_entry_choice` (simplified to human-only, DD4)** — callers:
`web_join.py:258`; re-export `web_player.py:38,83`; tests
`test_human_join.py:231,253-254`.

**Templates/docs naming "Play as yourself"** — `join.html:20`;
`docs/platform/AGENT_LUDUM_ARCHITECTURE.md:104`;
`docs/platform/AGENT_LUDUM_DESIGN.md:259-260`.

**Tests asserting removed markup** — `test_smart_join_flow.py:142,158,216` and
`:143` (`"Create one"`, capital C — new copy is lowercase);
`test_human_join.py:208,212,231,253-254`;
`test_human_plus_agent_join.py:295-298`; `test_join_seat_hold.py:78`;
`test_lobby_join_and_stacking.py:403` (keep `ai_for_` so this one survives);
`test_migrations.py:187`.

**CSS** — `style.css` join-page classes: `.pick-section`, `.pick-row`,
`.pick-name`, `.pick-sub`, `.pick-hint`, `.pick-hint-inline`, `.agent-card*`,
`.ai-chip*`, `.pick-add`, `.pick-disabled`. Check each for use outside
`join.html` before deleting (`.pick-row` is noted as shared with
`.agent-card-hd`).
