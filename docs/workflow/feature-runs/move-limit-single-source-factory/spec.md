# Spec — Single source of truth for move-length limits

## Summary

The game enforces two caps on move text: the public `message` (200 chars) and the
private `thinking` (200 chars). Today those two numbers are **hand-copied** across
several files. They have drifted apart twice in history. When they drift, the server
422-rejects an over-cap move and the agent's move is silently dropped for that turn.

This feature makes ONE authoritative definition of each cap. Every consumer either
**derives** from it (server side, which can import `app/`) or is **test-pinned** to it
(the standalone connector, which cannot import `app/` at runtime). A regression test
**fails** if any consumer's limit diverges from the source of truth.

This is a non-drift refactor plus a regression test. **The values do not change**:
`message` stays 200, `thinking` stays 200.

## Problem

### Where the limits live today (verified in the codebase)

| Consumer | File | Location | Current form |
|---|---|---|---|
| Server request schema | `app/schemas/agent.py` | `SubmitRequest.message`, `.thinking` (~L281-282) | `Field(default="", max_length=200)` |
| Server request schema | `app/schemas/agent.py` | `MessageRequest.message`, `.thinking` (~L291-292) | `Field(default="", max_length=200)` |
| Connector clip (normalize) | `scripts/agentludum_connector.py` | `_normalize_move` (~L217-218, L223) | `_clip(..., 200)` |
| Connector clip (POST body) | `scripts/agentludum_connector.py` | `_move_request` (~L837-838, L849) | `_clip(..., 200)` |
| Model-facing prompt text | `app/agent_prompt.py` | `RESPONSE_PROTOCOL` | "max 200 chars" (x3) |
| Model-facing prompt text | `scripts/agentludum_connector.py` | embedded `_PROTOCOL` fallback (~L108-111) | "max 200 chars" (x3) |

Note: `app/engine/rules.py` was swept and contains **no** "200"/char guidance — it
imports `app/agent_prompt.py` for the protocol, so it is already a derived consumer.
The robot-circle template references "~200 chars" only as a CSS sizing comment, not an
enforced limit, so it is out of scope for the invariant.

### Why it drifts

The numeric `200` is a magic literal repeated in at least six places. A developer who
changes one cap (e.g. the server schema) has no compiler or test forcing the others to
match. Twice the connector clip and the server `max_length` drifted, so over-cap moves
the connector thought were fine got 422'd and silently dropped server-side.

### The hard design constraint (the crux)

`scripts/agentludum_connector.py` is a **standalone script copied to operators'
machines**. The server streams it verbatim from `scripts/` (see
`app/routes/web_player.py::_serve_agent_file`) and operators run it from
`~/.agentludum/` under launchd. At runtime it **cannot** `import app...` — there is no
`app/` package on the operator's machine.

So the single source of truth must serve BOTH:
- the **server** (can `import app`), and
- the **connector** (cannot import `app/` at runtime).

The connector already models the correct pattern for sharing with `app/`: it does
`from app.agent_prompt import RESPONSE_PROTOCOL` inside a `try/except ImportError` and
falls back to a self-contained embedded copy when `app/` is not importable. The limits
must follow the same "import-when-available, self-contained-constant-otherwise" shape,
and a regression test must guarantee the two never disagree.

## User stories (prioritized)

### US1 — One authoritative definition (P1, must-have)

As a maintainer, when I need to change a move-text cap, I want exactly one place to
edit, so I cannot leave a stale copy behind that silently drops moves.

**Acceptance:**
- There is a single named constant for the `message` cap and one for the `thinking`
  cap in `app/` (the server side, importable).
- The server request schemas (`SubmitRequest`, `MessageRequest`) derive their
  `max_length` from those constants instead of hard-coded `200`.
- No server-side consumer that **enforces** a cap (clips or validates against it)
  hard-codes the literal `200` anymore. The model-facing prompt **guidance text**
  ("max 200 chars") is explicitly excluded from this rule — it is human/LLM-facing
  prose, not an enforced limit (see FR5). This exclusion is intentional so the
  invariant stays focused on the caps that, when drifted, silently drop moves.

### US2 — Connector stays standalone (P1, must-have)

As an operator, I want the downloaded connector to keep running with no `app/` package
present, so the existing zero-dependency deployment keeps working.

**Acceptance:**
- The connector has NO new unconditional `import app...`.
- The connector clips to a self-contained local constant (not a bare literal), so a
  standalone run never needs `app/`.
- When `app/` IS importable (source checkout / server host), the connector may import
  the authoritative value, but an `ImportError` path falls back to the local constant —
  mirroring the existing `RESPONSE_PROTOCOL` pattern.

### US3 — Regression test catches divergence (P1, must-have)

As a maintainer, I want a test that fails the moment the connector's caps and the
server's caps disagree, so a future edit to one side can't silently drift.

**Acceptance:**
- A test asserts the connector's `message` cap == the server's authoritative `message`
  cap, and likewise for `thinking`.
- The test reads the connector's value the same way a standalone run would (so it
  pins the actual standalone behavior, not a re-import of the server value).
- **Critical (Codex C-1):** in a source checkout the connector CAN import
  `app/`, so a naive test would only exercise the importable branch and a stale
  *local fallback* constant could still pass. The connector's local fallback constant
  must therefore be a distinct, directly-readable module-level value (the value used
  when `app/` is absent), and the test must assert THAT fallback constant equals the
  server's authoritative value — not just whatever the connector resolved via import.
  This is what catches a broken standalone deployment.
- If someone edits the server cap to 250 but leaves the connector's fallback at 200 (or
  vice versa), the test fails.

### US4 — Values unchanged (P1, guardrail)

As a player, I want move caps to behave exactly as before, so this refactor is
invisible at runtime.

**Acceptance:**
- After the change, both caps still resolve to 200.
- A test pins the authoritative `message` cap == 200 and `thinking` cap == 200, so an
  accidental value change is caught (the invariant test alone would still pass if both
  sides moved together — this guards against that).

## Functional requirements

- **FR1.** Define one authoritative `message` cap constant and one authoritative
  `thinking` cap constant in an importable `app/` module. Both = 200.
- **FR2.** `SubmitRequest` and `MessageRequest` in `app/schemas/agent.py` derive their
  `message`/`thinking` `max_length` from FR1's constants — no literal `200`.
- **FR3.** The connector carries a self-contained local copy of both caps and uses it
  in `_normalize_move` and `_move_request` (replacing the bare `200` literals). It
  prefers the authoritative `app/` value when importable, falling back to the local
  constant on `ImportError` (mirroring the `RESPONSE_PROTOCOL` pattern). No new
  unconditional `app/` import.
- **FR4.** A regression test in `tests/` asserts the connector caps equal the
  authoritative server caps (US3) AND that the authoritative caps equal 200/200 (US4).
- **FR5.** The model-facing prompt strings ("max 200 chars") are out of scope to
  dynamically template in this slice. They are guidance text, not enforced limits, so a
  drift there is cosmetic (it would not drop moves). Default is to leave them as literal
  text to keep the diff minimal and the invariant focused on the enforced caps. The
  plan may revisit only if it can template them without breaking mypy/ruff.

## Out of scope (non-goals)

- Changing the cap values (stays 200/200).
- Adding a runtime `import app...` to the connector (would break standalone runs).
- Re-architecting the connector poll loop, prompts, move parsing, or fallback beyond
  the limit constants.
- The robot-circle CSS "~200 chars" sizing comment (cosmetic, not an enforced limit).

## Constraints & risks

- **C1.** Connector must run standalone — verified by `app/routes/web_player.py`
  streaming `scripts/agentludum_connector.py` verbatim. **verification:** grep the
  final connector for any unconditional `import app` (must be zero); confirm the only
  `app` import is inside a `try/except ImportError`.
- **C2.** Preflight must stay green: `ruff check .`, `mypy app/ mcp_server/`, `pytest -q`.
  **verification:** run the full Preflight Gate from repo root; paste pass/fail.
- **C3.** No suppressions (`# type: ignore`, `# noqa`), no bare except, full type
  annotations. **verification:** ruff + mypy catch these; manual diff read.
- **C4.** The invariant test must read the connector's **standalone fallback** value,
  not the value it resolved via an `app/` import in the source checkout — otherwise it
  only proves parity in the checkout and misses a broken standalone fallback (Codex
  C-1). **verification:** the test imports the connector module and reads the explicit
  module-level fallback constant (the one used on `ImportError`); a deliberate mismatch
  of that fallback (temporarily) makes the test fail.

## Acceptance criteria (feature-level)

1. One authoritative definition of `message`(200) and `thinking`(200); consumers derive
   from it or are test-pinned.
2. Connector still works standalone (no runtime import of `app/`).
3. Regression test fails if connector vs server limits diverge.
4. Values unchanged (200/200).
5. Preflight passes (ruff + mypy + pytest), all green.

## Scope boundary (files this feature may touch)

- `app/schemas/agent.py` — derive `max_length` from new constants.
- `app/agent_prompt.py` OR a new small constants module under `app/` — home of the
  authoritative caps (plan decides exact home; must be importable and dependency-light).
- `scripts/agentludum_connector.py` — local cap constants + use them in the two clip
  sites; keep the `try/except ImportError` shape.
- `tests/` — new regression test (US3 + US4).

Do NOT touch: `CLAUDE.md`, `MEMORY.md`, `.gitignore`, migrations, game engine payoff
logic, or any file outside the list above.
