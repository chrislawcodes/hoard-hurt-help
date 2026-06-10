# Spec: Hermes connector adapter (Path A)

**Slug:** hermes-openclaw-adapters · **Scope:** Hermes only this run; OpenClaw is a
fast-follow using the same pattern. **Size:** SMALL (FF engine recommends `quick`).

## Goal
Let a Hermes (`NousResearch/hermes-agent`) agent play through the standard
low-token connector, exactly like claude/codex/gemini — invoked one-shot per turn,
sending the full (bounded) game state each turn (Path A, no session resume yet).

## Why this is small / low-risk
The unified-connections machine model already routes turns by the agent's stored
`provider` to any live connection whose `connection_providers` has that provider
enabled. Hermes is already a `ConnectionProvider` enum value. So routing,
coverage, toggles, and the agent record need **no change**. The only missing
piece is a connector *adapter* that knows how to drive the `hermes` CLI, plus
detection.

## Changes (all in `scripts/agentludum_connector.py` + tests)

### 1. `_HermesAdapter`
A new adapter next to `_ClaudeAdapter` / `_CodexAdapter` / `_GeminiAdapter`:
- `cli = "hermes"`.
- No `default_model`/`--model`: Hermes uses its own configured model (the
  unified-connections decision). The connector must NOT pass `--model` for hermes.
- **One-shot, full-state every turn (Path A):** both `first()` and `resume()` run
  `hermes -z` with the full game-state body (the connector's `_setup_body`, not the
  delta `_delta_body`). `-z` = "single prompt in, final reply text out, exit." No
  session id is captured or stored.
- Parse the move from stdout (the reply text) with the connector's existing move
  parser (`_parse_move`); on malformed output, the connector's existing fallback
  move path applies (`was_defaulted`).

### 2. Always-full-state for sessionless providers
The connector currently sends `_setup_body` on first contact and `_delta_body`
on resume (the others keep server memory). Hermes has no captured session, so it
must get the FULL body every turn. Add a small, explicit notion of "this adapter
has no resumable session" (e.g. `supports_resume = False` on the adapter, defaults
`True`) and have the turn loop send `_setup_body` whenever `supports_resume` is
False. Do not special-case by name.

### 3. Detection
Extend `_detect_providers()` so `shutil.which("hermes")` adds `"hermes"` to the
reported `detected_providers` (the report-pid path already stores it into
`connection_providers.detected`).

### 4. Provider resolution
`_resolve` already prefers the server's explicit `provider` payload field. Ensure
`hermes` maps to `_HermesAdapter` in `_ADAPTERS`. Because Hermes uses its own
model, the model in the payload is informational; the adapter ignores `--model`.

## Out of scope (non-goals)
- Session-id capture / `hermes --resume` delta turns (Path B optimization — needs
  a live install to confirm how `-z` exposes the session id; see
  `discovery-research.md`).
- OpenClaw adapter (fast-follow, same pattern; `openclaw agent --message`, also
  uses its own model).
- Any change to the routing/coverage model, the agent record, or the
  claude/codex/gemini adapters.

## Tests (mock the subprocess)
- `_detect_providers` includes `hermes` when `shutil.which("hermes")` is truthy.
- `_HermesAdapter.first/resume` invoke `hermes -z` with the FULL body and no
  `--model`; the parsed move is returned.
- A second turn for the same agent+match still sends the full body (Path A), not
  a delta.
- Malformed `hermes` output → the connector emits its fallback move
  (`was_defaulted=True`), never crashes.

## Acceptance / verification
1. Unit tests above pass (mocked `hermes` subprocess).
2. **Live-install smoke (REQUIRED before merge):** on a machine with a real
   `hermes` CLI, run the connector against a dev server with a hermes-provider
   agent in an active match; confirm `hermes -z` is invoked, returns a parseable
   move, and the turn is submitted. This is the integration-critical step that the
   docs alone cannot confirm (per the data-critical-waves discipline for anything
   that depends on an external tool's exact I/O).

## Risks
- **`hermes -z` output not cleanly parseable** (banner/extra text despite docs).
  verification: the live smoke test; the connector's existing fallback-move path
  prevents a crash, and `was_defaulted` makes a parse failure visible.
- **Hermes doesn't hand control back cleanly** (open bug #38252). verification:
  the connector runs `hermes` as a one-shot subprocess with a timeout (existing
  `_run` helper); confirm the process exits in the live smoke test.
