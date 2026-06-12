# Tasks — Single source of truth for move-length limits

Source of truth: `spec.md`, `plan.md`. One slice — the whole change is ~60-80 lines and
the test asserts across all three edited files, so splitting would break the invariant
check. One `[CHECKPOINT]` at the end.

## Slice S1 — Single source of truth + regression test  [CHECKPOINT]

Estimated diff: ~60-80 lines across 3 edits + 1 new test file. Dependencies: T1 must
land before T2/T3 (they import the constants). T4 (test) depends on T1-T3.

### T1 — Add the authoritative constants and derive the protocol text
File: `app/agent_prompt.py`
- Add two module-level constants (with the source-of-truth comment from plan §1):
  - `MESSAGE_MAX_LENGTH = 200`
  - `THINKING_MAX_LENGTH = 200`
- Convert `RESPONSE_PROTOCOL` to an f-string that interpolates
  `MESSAGE_MAX_LENGTH` / `THINKING_MAX_LENGTH` in the two "max N chars" message/thinking
  spots and the ACT-phase "max N chars" thinking spot. Escape the literal JSON braces
  as `{{` / `}}`. Rendered output MUST stay byte-identical to today ("max 200 chars"),
  so existing prompt-text tests keep passing.
- Verify: `python3 -c "from app.agent_prompt import RESPONSE_PROTOCOL; assert 'max 200 chars' in RESPONSE_PROTOCOL"`.

### T2 — Derive the four schema sites from the constants
File: `app/schemas/agent.py`
- Add `from app.agent_prompt import MESSAGE_MAX_LENGTH, THINKING_MAX_LENGTH`.
- Replace `max_length=200` with `max_length=MESSAGE_MAX_LENGTH` (message) and
  `max_length=THINKING_MAX_LENGTH` (thinking) in BOTH `SubmitRequest` (L281-282) and
  `MessageRequest` (L291-292). All four enforcing sites; no literal `200` left at these
  sites. (Leave `model_self_report`'s unrelated `max_length=200` at L53 alone — it is a
  different field, not a move-text cap.)

### T3 — Connector: standalone fallback constants + use them at the clip sites
File: `scripts/agentludum_connector.py`
- Before the existing `try: from app.agent_prompt import RESPONSE_PROTOCOL ...` block,
  add explicit fallback constants (with the standalone comment from plan §3):
  - `_FALLBACK_MESSAGE_MAX_LENGTH = 200`
  - `_FALLBACK_THINKING_MAX_LENGTH = 200`
- Extend the `try` block to also import the two caps from `app.agent_prompt` as
  `_MESSAGE_MAX_LENGTH` / `_THINKING_MAX_LENGTH`; in the `except ImportError` set them to
  the `_FALLBACK_*` values (and keep `_CANONICAL_PROTOCOL = None`). Keep the embedded
  `_PROTOCOL` fallback prose as-is (by-design residual R3).
- In `_normalize_move` (L217/218/223) replace the bare `200` args:
  message → `_MESSAGE_MAX_LENGTH`, thinking → `_THINKING_MAX_LENGTH`.
- In `_move_request` (L837/838/849) replace the bare `200` args the same way.
- Verify (standalone safety): the ONLY `app` import in the file remains inside the
  `try/except ImportError`; no unconditional `import app`.

### T4 — Regression test (the core deliverable)
File: `tests/test_move_length_limits.py` (new)
- Load the connector with the proven fixture pattern from
  `tests/test_connector_fallback.py` (`importlib.util.spec_from_file_location`,
  `_CONNECTOR = Path(__file__).resolve().parents[1] / "scripts" / "agentludum_connector.py"`).
- Implement a `_field_max_length(model, name)` helper that scans the Pydantic v2 field's
  `metadata` for the constraint carrying a `max_length` attribute (do NOT hard-index
  `metadata[0]` — plan R4).
- Tests (from plan §4):
  - `test_values_unchanged` — `MESSAGE_MAX_LENGTH == 200`, `THINKING_MAX_LENGTH == 200`.
  - `test_all_four_schema_sites_derive_from_source` — all four
    `{SubmitRequest,MessageRequest}×{message,thinking}` sites == the source constant.
  - `test_normalize_move_clips_to_source` — over-cap input through `_normalize_move`
    (talk + act phase) clips output to the source lengths.
  - `test_move_request_body_clips_to_source` — over-cap input through `_move_request`
    (talk + act) clips the POST-body fields to the source lengths. Build the minimal
    `turn` dicts the function reads (`turn["current"]` with `turn_token`, a future
    `deadline`, phase; `turn["agent_turn_token"]`).
  - `test_connector_fallback_matches_server` — `_FALLBACK_*` == server source.
  - `test_connector_loads_with_app_unimportable` — re-load the connector with `app`
    blocked from import; assert `_CANONICAL_PROTOCOL is None` (proves the except branch
    ran) and resolved `_MESSAGE_MAX_LENGTH`/`_THINKING_MAX_LENGTH` == server source.
  - `test_protocol_text_renders_the_source_value` — `f"max {MESSAGE_MAX_LENGTH} chars"`
    and `f"max {THINKING_MAX_LENGTH} chars"` appear in `RESPONSE_PROTOCOL`.

### T5 — Preflight + drift live-check
- Run the Preflight Gate from repo root:
  `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q`. All green.
- Live drift check (do, observe, revert): temporarily set
  `_FALLBACK_MESSAGE_MAX_LENGTH = 199`, run `pytest -q tests/test_move_length_limits.py`,
  confirm it FAILS, then revert. (Manual verification of the regression guard; not
  committed.)
- Grep guard: `grep -rn "max_length=200\|, 200)" app/ scripts/` shows no ENFORCING site
  outside the constants (the connector's embedded `_PROTOCOL` prose and the unrelated
  `model_self_report` field are allowed).

[CHECKPOINT]  ← diff review here (size-gated Gemini regression review if ≥50 lines)

## Out of scope (do NOT do)
- Change cap values.
- Add an unconditional `import app` to the connector.
- Touch the connector poll loop / move parsing / fallback beyond the clip constants.
- Touch `CLAUDE.md`, `MEMORY.md`, `.gitignore`, migrations, or any file outside
  the four listed above.
