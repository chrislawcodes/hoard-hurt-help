# Plan — Join page as a lineup

Spec: `./spec.md` (revision 2). Slicing decision: **one slice**. The template,
its JS, and its CSS are one interlocking unit; splitting them ships a broken
page mid-way. No data-critical gate (the migration is a nullable ADD COLUMN with
no backfill). Est. ~600 changed lines — above the ~300 guidance, but the
alternative is a knowingly-broken intermediate state, which the guidance ranks
worse.

## Build order

1. **Model + migration.** `Agent.blurb` `String(32)` nullable.
   `migrations/versions/0047_agent_blurb.py` — plain `op.add_column`, no batch
   (precedent `0046_agent_version_note.py`). Update `tests/test_migrations.py:187`
   head to `0047`.
2. **Validation.** `clean_agent_blurb()` next to `clean_agent_name` in
   `agents_create.py`, derived from the column length. Returns `None` for
   empty/whitespace (AC14), raises 400 above the max (AC13).
3. **Write paths.** Create form field in `agents/new.html` + `agents_create.py`.
   New `POST /me/agents/{id}/set-blurb` in `agents_lifecycle.py`, its own form on
   `agents/detail.html` (never the auto-submitting rename form).
4. **Read paths.** `agents/list.html` row. Then the join page.
5. **Route.** `web_join.py`: drop `version_stats_by_id`; add `blurb` to rows;
   simplify `_default_entry_choice` → `_default_human_choice`; change the
   `any_pickable_ai` gate so rows render even when nothing is free; add
   `last_lineup`.
6. **Template + CSS + JS.** Rewrite `join.html`; add `.enter-you` / `.lineup-*` /
   `.ai-pill*` to `style.css`; delete the join-only dead classes.
7. **Tests.** Update the assertion list from the spec's consumer table; add the
   new tests (AC7 pairing, AC13 over-long, AC18 leak, admin regression, R3
   seated-row).
8. **Docs.** `AGENT_LUDUM_ARCHITECTURE.md:104`, `AGENT_LUDUM_DESIGN.md:259-260`.

## The JS rewrite — the risky part

The current script hangs everything off a master `[data-play-as-agent]`
checkbox. That checkbox is deleted, so `agentOn()`, `setAgentEnabled()`, and the
`#agent-section` show/hide all go. What replaces them, per row:

| Event | Effect |
|---|---|
| Row checkbox **checked** | Un-`hidden` its pill group; call `setCard(row, firstFreeProvider)` — enables **both** hidden mirrors together (unchanged mechanism); `refreshGreyOut()`; `updateBtn()` |
| Row checkbox **unchecked** | `hidden` the pill group; `clearCard(row)` — disables **both** mirrors and clears its radios; `refreshGreyOut()`; `updateBtn()` |
| Pill clicked (row already ticked) | `setCard(row, provider)`; re-grey; the previously chosen pill on this row is replaced, never left dangling |
| Pill clicked (its own, i.e. deselect gesture) | **Removed.** With DD2 a row always has exactly one AI while ticked; unticking the row is the only way to stop sending it. Resolves the two-deselect-gestures ambiguity. |
| `Use last lineup` clicked | For each still-selectable pair, check the row and `setCard(row, provider)`; then one `refreshGreyOut()` + `updateBtn()` |

`refreshGreyOut()` keeps its shape but its gate becomes per-row: a provider is
disabled on row R if it is server-disabled, or chosen on some other row.

**Invariant to hold (R1):** `setCard` and `clearCard` are the only writers of the
two hidden mirrors, and each always writes both. This is what guarantees N ticked
rows post N ids and N providers.

## Server-side `last_lineup`

```
# most recent match in THIS game holding one of my AI seats with a provider
last_match_id = (
    select(Player.match_id)
    .join(Agent, Agent.id == Player.agent_id)
    .join(Match, Match.id == Player.match_id)
    .where(Player.user_id == uid,
           Player.left_at.is_(None),
           Player.chosen_provider.is_not(None),
           Agent.kind == AgentKind.AI,
           Match.game == match.game,
           Match.id != match.id)
    .order_by(Player.id.desc()).limit(1)
)
```
Then `{agent_id: chosen_provider}` for that match, filtered in the template/JS to
agents that are rendered and not seated. Emitted as a `data-last-lineup` JSON
attribute so the button is pure client-side (AC20: never submits).

## Verification

- Full Preflight Gate (migration ⇒ not the small-change lane):
  `ruff` + `mypy app/ mcp_server/` + `pytest`.
- Manual, in the preview browser (LR1–LR4, AC1's pixel half): desktop 1280,
  small-laptop 790, phone 375; ticked and unticked; screenshot each into the PR.
