# Reuse audit — move-limit single source of truth

Goal: house ONE authoritative definition of the two caps and make every consumer
derive from it or be test-pinned to it. Adversarial bias toward reuse/extend.

| Capability needed | Existing module (path) | Verdict | Note |
|---|---|---|---|
| Importable home for model-facing constants the connector already shares | `app/agent_prompt.py` (`RESPONSE_PROTOCOL`, `CHAT_INSTRUCTIONS`) | **extend** | Imports only stdlib `json`; the connector ALREADY does `from app.agent_prompt import RESPONSE_PROTOCOL` inside a `try/except ImportError`. Adding the cap constants here means the connector reuses its existing conditional-import line — no new import surface, no new module. This is the dependency-light home the spec asks for. |
| Cross-process constant sharing pattern (server imports `app/`; connector can't at runtime) | `scripts/agentludum_connector.py` L54-59 (`try: from app.agent_prompt import RESPONSE_PROTOCOL ... except ImportError: ... = None`) | **reuse** | Reuse this exact pattern for the caps: import the authoritative value when `app/` is present, fall back to a self-contained local constant otherwise. No new mechanism invented. |
| Server-side `max_length` enforcement on request bodies | `app/schemas/agent.py` `SubmitRequest`/`MessageRequest` `Field(..., max_length=200)` | **extend** | Keep the Pydantic `Field(max_length=...)` mechanism; just feed it the new constant instead of the literal `200`. No new validation layer. |
| Clip-to-length helper in the connector | `scripts/agentludum_connector.py` `_clip(text, limit)` (L184) | **reuse** | Already takes `limit` as a parameter. Reuse unchanged — only the caller's literal `200` argument is replaced by the local cap constant. |
| A test home for an equality/regression invariant | `tests/` (pytest, SQLite in-memory; e.g. `tests/test_agent_api.py`) | **reuse** | New test file `tests/test_move_length_limits.py` — no test framework or harness change. |

## Duplication this feature removes

- The bare integer `200` is currently duplicated across `app/schemas/agent.py`
  (4 sites) and `scripts/agentludum_connector.py` (4 sites). After this feature the
  server sites derive from one constant; the connector keeps ONE local fallback
  constant (required because it can't import `app/` standalone) that the regression
  test pins to the server value.

## Justified-new (kept minimal)

- **New constants in `app/agent_prompt.py`**: `MESSAGE_MAX_LENGTH = 200`,
  `THINKING_MAX_LENGTH = 200`. Justified: there is no existing named constant for
  these caps anywhere (`grep` for `MESSAGE_MAX`/`THINKING_MAX`/`MAX_MOVE` returns
  nothing). They are the single source of truth. Placed in the already-shared,
  stdlib-only module rather than a brand-new file to avoid adding import surface the
  connector would have to special-case.
- **New test file `tests/test_move_length_limits.py`**: justified — no existing test
  asserts connector/server cap parity (that's the whole point of the feature).

No capability the feature needs is being rebuilt where an existing module already
provides it.
