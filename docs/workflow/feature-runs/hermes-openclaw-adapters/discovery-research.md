# Discovery research: Hermes/OpenClaw connector adapters

**Question driving this:** can Hermes and OpenClaw be driven by the connector
as one-shot-per-turn adapters (like claude/codex/gemini), WITHOUT burning tokens
deciding "when to connect"?

## The connector adapter contract (what an adapter must provide)
Each provider adapter (`scripts/agentludum_connector.py`) implements:
- `cli` — a binary invoked **one-shot per turn** (`claude --print` / `codex exec`
  / `gemini -p`): prompt in → move text out → exit. No interactive loop.
- `first(body, framing, model, session)` — first turn: send framing + full state,
  **capture a session id**, return the move.
- `resume(body, model, session)` — later turns: `--resume <session_id>` + only the
  **delta**, return the move.

Token efficiency is structural: the connector **polls over cheap HTTP** and only
invokes the tool when `status == your_turn`. The tool never reasons about timing —
it just answers "given this game state, what's your move." Resume keeps each turn
delta-only. So the token concern is satisfied by the existing design; an adapter
just needs a one-shot CLI mode (+ ideally session resume).

## Hermes (NousResearch/hermes-agent) — FEASIBLE
- **One-shot headless mode exists:** `hermes -z` is "the purest one-shot entry
  point: single prompt in, final response text out, nothing else on stdout/stderr."
  No banner, no spinner, no interactive dialogs, returns and exits. Perfect fit.
- **Session resume exists:** `hermes --resume <id>` / `--continue` / `-c`; resuming
  restores full history from SQLite. Session id format `YYYYMMDD_HHMMSS_<hex>`.
- **OPEN (the flagged spike):** the docs do NOT confirm how `-z` emits the session
  id of a NEW session for later `--resume`. `-z` prints only the reply. Capture
  options to verify on a live install: a flag to set/emit the id; `hermes sessions
  list` after the call; or stderr/a file. Unlike gemini (where the connector
  *assigns* a `--session-id` uuid), Hermes ids look server-assigned.
- **Watch:** open bug NousResearch/hermes-agent#38252 — "does not cleanly hand
  control back to CLI after session ends." `-z` one-shot may sidestep it; verify.

## OpenClaw — FEASIBLE
- **One-shot exists:** `openclaw agent --agent <id> --message "<prompt>"` (and
  `--local` embedded runs are "treated as one-shot runs," retiring child processes
  after the reply — good, no lingering processes). `--json` emits payload+metadata.
- **Model:** `--model <provider/model>` is an *optional* per-run override;
  **without it the agent uses its own configured default model** — exactly the
  unified-connections decision ("their value is their own model/memory").
- **OPEN:** docs don't show retrieving a thread/session id from a one-shot for
  resumption. The `--json` metadata may carry it; persistent/threaded ACP modes
  exist (`--mode persistent --thread auto`). Verify on a live install.

## Conclusion — NOT blocked
Both tools satisfy the hard constraint (one-shot, token-cheap; connector owns the
"when"). The only unknown is **session-id capture for delta-resume** — and that is
**not a blocker**, because the connector already has a full-history path
(`_setup_body`, used on first turn + failover). So:
- **Ship path A (works today, no spike):** one-shot per turn sending the full
  (bounded, low-token) game state every turn — no session resume. Correct and
  immediate.
- **Optimization path B (after the spike):** add `--resume`/`--thread` delta turns
  once the live install confirms how each tool exposes its session id.

Both tools use their OWN configured model (no `--model` passed) → no model dropdown
for hermes/openclaw agents; the agent's stored provider routes turns to a machine
whose connector has that tool installed (`shutil.which("hermes")` /
`shutil.which("openclaw")` feed the existing detection).

## Sources
- Hermes CLI: https://hermes-agent.nousresearch.com/docs/user-guide/cli
- Hermes sessions: https://hermes-agent.nousresearch.com/docs/user-guide/sessions
- Hermes handback bug: https://github.com/NousResearch/hermes-agent/issues/38252
- OpenClaw CLI agent: https://docs.openclaw.ai/cli/agent
- OpenClaw CLI reference: https://docs.openclaw.ai/cli
