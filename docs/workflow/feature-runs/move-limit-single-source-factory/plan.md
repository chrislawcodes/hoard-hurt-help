# Plan

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: Acknowledged but deliberately scoped out (FR5). The prompt strings ('max 200 chars') are LLM/operator GUIDANCE text, not an enforced cap: if they drift, the model just gets slightly stale advice — the server still clips/validates at the real cap and no move is silently dropped. The drift path this feature kills is the ENFORCED one (schema max_length vs connector clip), which both become derived/test-pinned. Templating the prose into the constants is recorded as a known-remaining literal in FR5 and an explicit non-goal for this slice to keep the diff minimal and the invariant focused; it can be a cheap follow-up. No spec change required — FR5 already states this decision and rationale.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: All three findings describe the CURRENT pre-refactor state (the drift problem) and confirm the spec's diagnosis; they raise no flaw in the spec. The plan resolves all three: a single source of truth (MESSAGE_MAX_LENGTH/THINKING_MAX_LENGTH in app/agent_prompt.py), server schemas derive from it, the connector uses a fallback constant test-pinned to it, and a new regression test enforces parity. No spec change required.
- review: reviews/plan.codex.implementation-adversarial.review.md | status: accepted | note: Round 4 = convergence confirmation; both findings are restatements of accepted decisions. MED (standalone _PROTOCOL prose still literal) = accepted by-design residual R3 reconciled in rounds 1-3: it's guidance text, not an enforced cap, can't drop a move. LOW (test reads schema metadata, not the live FastAPI route) noted: the route uses the same SubmitRequest/MessageRequest schema, so pinning the schema max_length to the source pins the route; a full route-level test is a reasonable optional add but out of scope for this non-drift refactor. No new defect; plan stays healthy/unchanged.
- review: reviews/plan.gemini.testability-adversarial.review.md | status: accepted | note: Round 4 = convergence. #1 (fixture might mask fallback): the new test_connector_loads_with_app_unimportable explicitly blocks 'app' import and asserts _CANONICAL_PROTOCOL is None, proving the fallback branch ran; plus the parity test reads _FALLBACK_* by name (defined unconditionally before the try), so it can't be masked. #2 (DB/other layers): verified Text/unbounded, no other enforcement layer; documented. #3 (_clip edge cases): _clip is exercised end-to-end by the new live-clip tests; a dedicated _clip unit test is a nice-to-have but out of scope - the enforcement guarantee is already covered. No new defect; plan unchanged.
- review: reviews/diff.gemini.regression-adversarial.review.md | status: accepted | note: MEDIUM (broad except ImportError could mask a malformed app.agent_prompt): valid observation, but DEFERRED as out of scope for this non-drift refactor. The try/except ImportError pattern PRE-DATES this feature (it already guarded RESPONSE_PROTOCOL); this slice only extended the same block to also import the two cap constants — it did not introduce the swallow. Narrowing it to distinguish 'app absent' (expected standalone case) from 'app present but agent_prompt broken' (unexpected) would change established import-guard behavior beyond this slice and is a separate hardening task. The feature's drift guarantee is unaffected: when app/ IS importable the authoritative values are used; when it is genuinely absent the test-pinned fallback is used; the regression test covers both. LOW + both residuals = the already-accepted operator deploy-staleness point (CI parity test is the in-scope guard; forcing standalone-connector refresh is out of scope for a non-drift refactor).

## Goal

Make ONE authoritative definition of the two enforced move-text caps —
public `message` (200) and private `thinking` (200) — that every enforcing consumer
derives from or is test-pinned to, so they can never silently drift apart again. Values
do not change. Deliver a regression test that FAILS if any consumer diverges.

## Architecture decision: where the source of truth lives

### The crux (cross-process constant)

The server can `import app...`; the connector (`scripts/agentludum_connector.py`) is
streamed verbatim to operators and run standalone from `~/.agentludum/`, so it **cannot**
`import app...` at runtime. The single source of truth must serve both.

### Options considered

| Approach | How the connector gets the value | Drift protection | Verdict |
|---|---|---|---|
| **A. Shared constants in `app/agent_prompt.py`; connector keeps a local fallback constant + regression test pins it** | `try: from app.agent_prompt import MESSAGE_MAX_LENGTH... except ImportError: local constant` (reuses the EXISTING pattern the connector already uses for `RESPONSE_PROTOCOL`) | Regression test asserts the connector's local **fallback** constant == the server constant | **CHOSEN** |
| B. New dependency-free shared module the connector physically carries (vendored copy) | Connector ships a copy of the module file | Same test, but adds a new file + a copy/sync step and new import surface | Rejected — more moving parts; the connector already special-cases exactly one shared module (`app/agent_prompt`), and adding a second shared module to carry is strictly more surface than extending the one it already imports. |
| C. Connector imports `app/` unconditionally | direct import | Trivially in sync | Rejected — breaks standalone runs (no `app/` on operator machines). Explicit non-goal. |

### Why A

1. **Reuses the exact mechanism already in the file.** The connector already does
   `from app.agent_prompt import RESPONSE_PROTOCOL` inside `try/except ImportError`
   (L54-59). Extending that same import to also pull the two cap constants adds **zero**
   new import surface and **zero** new files for the connector to carry.
2. **`app/agent_prompt.py` is the right home.** It imports only stdlib `json`, already
   houses the model-facing protocol constants, and is the module the connector already
   reaches into. So it is dependency-light and importable — exactly what the spec asks.
3. **Standalone safety is structural, not incidental.** The connector keeps an explicit
   module-level fallback constant used when `app/` is absent. That is the value real
   operators run with.
4. **The test pins the value that actually ships to operators** (the fallback), closing
   Codex finding C-1 — a source-checkout import can't mask a stale standalone fallback.

## Target design

### 1. `app/agent_prompt.py` — source of truth (new constants) + derive the protocol text

Add two module-level constants near the top:

```python
# Enforced caps on move text (chars). Single source of truth — every enforcing
# consumer derives from or is test-pinned to these. Changing one here is the only
# edit needed server-side; the connector's standalone fallback is pinned to these
# by tests/test_move_length_limits.py so the two can never silently drift.
MESSAGE_MAX_LENGTH = 200   # public `message`
THINKING_MAX_LENGTH = 200  # private `thinking`
```

**Decision changed by plan review (Codex/Gemini): also derive the prompt guidance text
from the constants.** The reviews showed the `"max 200 chars"` strings in
`RESPONSE_PROTOCOL` are already a real contract pinned by existing tests
(`tests/test_connector_fallback.py`, `test_agent_next_turn_fanout.py`,
`test_per_game_strategy.py` all assert `"max 200 chars"`). Leaving them as bare literals
keeps one un-derived copy of the cap. So `RESPONSE_PROTOCOL` becomes an f-string that
interpolates the constants:

```python
RESPONSE_PROTOCOL = f"""TALK PHASE response:
{{"message": "<public message, max {MESSAGE_MAX_LENGTH} chars>", "thinking": "<private reasoning, max {THINKING_MAX_LENGTH} chars>"}}
...
```

The rendered text is still exactly `max 200 chars` (so the existing prompt-text tests
keep passing unchanged), but the number now flows from the single source. This removes
the last un-derived server-side copy. The `{{` / `}}` escape the literal JSON braces in
the f-string.

### 2. `app/schemas/agent.py` — derive from the constants

Import the constants and feed them to the four `Field(max_length=...)` sites in
`SubmitRequest` and `MessageRequest`:

```python
from app.agent_prompt import MESSAGE_MAX_LENGTH, THINKING_MAX_LENGTH
...
message: str = Field(default="", max_length=MESSAGE_MAX_LENGTH)
thinking: str = Field(default="", max_length=THINKING_MAX_LENGTH)
```

No literal `200` remains at these enforcing sites.

### 3. `scripts/agentludum_connector.py` — local fallback + use it

Extend the existing conditional import and add explicit fallback constants:

```python
# Standalone fallback values — used when app/ is not importable (operator machines).
# tests/test_move_length_limits.py pins these to the server's authoritative caps so a
# divergence fails CI before it can silently drop a move.
_FALLBACK_MESSAGE_MAX_LENGTH = 200
_FALLBACK_THINKING_MAX_LENGTH = 200

try:
    from app.agent_prompt import RESPONSE_PROTOCOL as _CANONICAL_PROTOCOL
    from app.agent_prompt import MESSAGE_MAX_LENGTH as _MESSAGE_MAX_LENGTH
    from app.agent_prompt import THINKING_MAX_LENGTH as _THINKING_MAX_LENGTH
except ImportError:
    _CANONICAL_PROTOCOL = None
    _MESSAGE_MAX_LENGTH = _FALLBACK_MESSAGE_MAX_LENGTH
    _THINKING_MAX_LENGTH = _FALLBACK_THINKING_MAX_LENGTH
```

Then replace the bare `200` args in `_normalize_move` (L217, L218, L223) and
`_move_request` (L837, L838, L849):
- `message` clips use `_MESSAGE_MAX_LENGTH`
- `thinking` clips use `_THINKING_MAX_LENGTH`

The connector then uses the authoritative value when `app/` is present and the
fallback otherwise — and the fallback is what the test pins.

The connector's embedded `_PROTOCOL` fallback string (the prose "max 200 chars" used
only when `app/` is absent) stays as a literal — it is operator/LLM guidance text, and
when `app/` IS present the connector already prefers the derived `_CANONICAL_PROTOCOL`
(`_PROTOCOL = _CANONICAL_PROTOCOL or """..."""`). The ENFORCED behavior (the clip) is
constant-backed and test-pinned, which is what prevents move drops. We do not f-string
the connector's embedded fallback off the `_FALLBACK_*` ints to keep the standalone
fallback dead-simple; the parity test guards the only number that can drop a move.

### 4. `tests/test_move_length_limits.py` — regression test (the core deliverable)

```python
from app.agent_prompt import MESSAGE_MAX_LENGTH, THINKING_MAX_LENGTH

def test_values_unchanged():       # US4 guardrail
    assert MESSAGE_MAX_LENGTH == 200
    assert THINKING_MAX_LENGTH == 200

def test_connector_fallback_matches_server(connector):  # US3 — the divergence guard
    # Reads the connector's STANDALONE fallback constants — the values an operator
    # runs with when app/ is absent — NOT the import-resolved values.
    assert connector._FALLBACK_MESSAGE_MAX_LENGTH == MESSAGE_MAX_LENGTH
    assert connector._FALLBACK_THINKING_MAX_LENGTH == THINKING_MAX_LENGTH

def test_connector_loads_with_app_unimportable():  # Codex round-3: exercise except-branch
    # Re-load the connector with `app` blocked from import, simulating the real
    # operator machine where app/ is absent. This actually runs the
    # `except ImportError` branch and proves the fallback binding works AND still
    # matches the server source. Without this, the checkout's clean `app` import
    # means the except-branch is never exercised by tests.
    import builtins
    real_import = builtins.__import__
    def _blocked(name, *a, **k):
        if name == "app" or name.startswith("app."):
            raise ImportError("simulated standalone: app/ absent")
        return real_import(name, *a, **k)
    builtins.__import__ = _blocked
    try:
        spec = importlib.util.spec_from_file_location("connector_standalone", _CONNECTOR)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # runs the except ImportError path
    finally:
        builtins.__import__ = real_import
    # In the standalone load, the resolved limits equal the fallback == server source.
    assert mod._MESSAGE_MAX_LENGTH == MESSAGE_MAX_LENGTH
    assert mod._THINKING_MAX_LENGTH == THINKING_MAX_LENGTH
    assert mod._CANONICAL_PROTOCOL is None  # confirms the except-branch actually ran

def test_all_four_schema_sites_derive_from_source():  # Codex/Gemini: cover ALL sites
    # SubmitRequest.{message,thinking} AND MessageRequest.{message,thinking}
    for model, field, expected in [
        (SubmitRequest, "message", MESSAGE_MAX_LENGTH),
        (SubmitRequest, "thinking", THINKING_MAX_LENGTH),
        (MessageRequest, "message", MESSAGE_MAX_LENGTH),
        (MessageRequest, "thinking", THINKING_MAX_LENGTH),
    ]:
        assert _field_max_length(model, field) == expected

def test_protocol_text_renders_the_source_value():  # guidance text now derived
    assert f"max {MESSAGE_MAX_LENGTH} chars" in RESPONSE_PROTOCOL
    assert f"max {THINKING_MAX_LENGTH} chars" in RESPONSE_PROTOCOL

# --- Codex round-2 HIGH: pin the LIVE clip behavior, not just the constants ---
# A hard-coded 200 left in _normalize_move/_move_request would pass the constant
# checks above but still drift. So exercise the actual enforcement sites with
# over-cap input and assert the OUTPUT length tracks the source of truth.

def test_normalize_move_clips_to_source(connector):
    over = "x" * (MESSAGE_MAX_LENGTH + 50)
    talk = connector._normalize_move({"message": over, "thinking": over}, "talk")
    assert len(talk["message"]) == MESSAGE_MAX_LENGTH
    assert len(talk["thinking"]) == THINKING_MAX_LENGTH
    act = connector._normalize_move({"action": "HOARD", "thinking": over}, "act")
    assert len(act["thinking"]) == THINKING_MAX_LENGTH

def test_move_request_body_clips_to_source(connector):
    # Build a minimal turn/decision and assert the POST body fields are clipped to
    # the source-of-truth lengths (covers _move_request L837/838/849).
    over = "x" * (MESSAGE_MAX_LENGTH + 50)
    # talk-phase body:
    _, _, talk_body = connector._move_request(base, match_id, talk_turn,
                                              {"message": over, "thinking": over})
    assert len(talk_body["message"]) == MESSAGE_MAX_LENGTH
    assert len(talk_body["thinking"]) == THINKING_MAX_LENGTH
    # act-phase body:
    _, _, act_body = connector._move_request(base, match_id, act_turn,
                                             {"action": "HOARD", "thinking": over})
    assert len(act_body["thinking"]) == THINKING_MAX_LENGTH
```

**These two behavioral tests are the core enforcement guard (Codex round-2 HIGH).** They
call the real `_normalize_move` and `_move_request` with input longer than the cap and
assert the result is exactly the source-of-truth length. If a hard-coded `200` is left
behind in any of the four clip sites, these still pass ONLY while 200 == the constant —
and they FAIL the moment the source-of-truth constant changes but a clip site doesn't,
which is exactly the drift we're killing. Combined with the fallback-constant parity
test, every enforcement path (schema + both connector clip functions) is pinned.

The `_move_request` test builds the minimal `turn`/`decision` shapes that function reads
(`turn["current"]` with `turn_token`/`deadline`/phase, `turn["agent_turn_token"]`); the
slice fills in concrete fixtures. If wiring a full `turn` dict proves noisy, the
equivalent is to assert via `_normalize_move` plus a direct `_clip(over, _MESSAGE_MAX_LENGTH)`
check — but the preferred form exercises the real POST-body builder.

`_field_max_length` reads the Pydantic v2 field's `max_length` from
`model.model_fields[field].metadata` (the `MaxLen` constraint). The test asserts ALL
FOUR enforcement sites (Codex residual + Gemini finding #2), so a single leftover
hard-coded `200` can't slip through.

The parity test asserts the connector's **fallback** constants (the standalone-run
values) equal the server's authoritative constants. It must NOT read whatever the
connector resolved via import in the checkout — that would mask a stale fallback
(Codex C-1).

**Connector import in the test (review finding — fragility):** the repo ALREADY loads
the connector as a module in tests via `importlib.util.spec_from_file_location`
(`tests/test_connector_fallback.py` `connector` fixture). `httpx` is present in the test
env (confirmed: 0.28.1), so this is a proven, non-fragile pattern — **the regex fallback
is dropped** (it was the part both reviewers flagged as unstable). The new test reuses
the same `connector` fixture approach. If the connector module ever fails to import in
the test env, that is a real failure to surface, not something to silently route around.

## Wave / slice breakdown

One `[CHECKPOINT]` — the whole change is well under 300 lines and the pieces are
tightly coupled (the test asserts on all three edited files at once).

| Slice | Files | Est. lines |
|---|---|---|
| S1 (only slice) | `app/agent_prompt.py` (constants + f-string protocol), `app/schemas/agent.py` (derive 4 sites), `scripts/agentludum_connector.py` (fallback consts + use them), `tests/test_move_length_limits.py` (new) | ~60-80 |

## Reuse audit decisions (from reuse-report.md)

- **extend** `app/agent_prompt.py` — home of the new constants (stdlib-only, already shared).
- **reuse** the connector's `try/except ImportError` pattern and `_clip(text, limit)` helper.
- **extend** `app/schemas/agent.py` `Field(max_length=...)` — feed the constant.
- **reuse** `tests/` (pytest) — new test file, no harness change.
- **justified-new**: the two constants (none exist today) and the new test file.

Every reuse-report row is addressed here.

## DB / model-layer check (Gemini finding #2 — resolved)

Gemini asked whether DB column lengths implicitly encode 200. They do NOT:
`app/models/turn.py` stores `message` and `thinking` as `mapped_column(Text, ...)` —
unbounded. So there is no `String(200)` constraint to reconcile; the only enforcement
points are the Pydantic schemas (request validation) and the connector clip. No model
change needed.

## Verification plan

1. **Values unchanged** — `test_values_unchanged` pins 200/200.
2. **Server derives (all 4 sites)** — `test_all_four_schema_sites_derive_from_source`
   reads `max_length` from each of `SubmitRequest`/`MessageRequest` × `message`/`thinking`.
3. **Connector LIVE clip behavior (Codex round-2 HIGH)** — `test_normalize_move_clips_to_source`
   and `test_move_request_body_clips_to_source` feed over-cap input through the real
   `_normalize_move` / `_move_request` and assert the output length == the source cap.
   This catches a hard-coded `200` left in any of the four clip sites.
4. **Connector standalone parity** — `test_connector_fallback_matches_server` pins the
   `_FALLBACK_*` constants to the server source, and
   `test_connector_loads_with_app_unimportable` re-loads the connector with `app`
   blocked so the real `except ImportError` fallback branch is exercised and its resolved
   limits == the server source (Codex round-3).
5. **Guidance text derived** — `test_protocol_text_renders_the_source_value` confirms
   `RESPONSE_PROTOCOL` interpolates the constant (rendered text still "max 200 chars").
6. **Connector still standalone** — grep the final connector: the ONLY `app` import is
   inside `try/except ImportError`; no unconditional `import app`.
7. **Preflight** — `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q` green.
   Existing prompt-text tests (`tests/test_connector_fallback.py`,
   `test_agent_next_turn_fanout.py`, `test_per_game_strategy.py` asserting "max 200 chars")
   must still pass — the rendered protocol string is unchanged.

## Residual Risks

- **R1. The test could accidentally read the import-resolved value instead of the
  fallback, masking a stale standalone fallback (Codex C-1).**
  verification: the test references `_FALLBACK_MESSAGE_MAX_LENGTH` /
  `_FALLBACK_THINKING_MAX_LENGTH` by name (not the resolved `_MESSAGE_MAX_LENGTH`); a
  reviewer greps the test for `_FALLBACK_` to confirm. As a live check during the slice,
  temporarily set `_FALLBACK_MESSAGE_MAX_LENGTH = 199`, confirm `pytest -q` FAILS, then
  revert.
- **R2. Future proliferation of a NEW "magic 200" copy elsewhere (Gemini residual #1).**
  verification: the source-of-truth lives in `app/agent_prompt.py`; the diff-review and a
  one-line grep before merge (`grep -rn "max_length=200\|, 200)" app/ scripts/`) confirm
  no enforcing site outside the constants hard-codes 200. Re-running that grep is the
  cheap recurring guard; a lint rule is a possible follow-up but out of scope here.
- **R3. The connector's embedded `_PROTOCOL` fallback prose still says "max 200 chars"
  literally (used only when app/ is absent).**
  verification: this is operator/LLM guidance, not an enforced cap — when app/ is present
  the connector prefers the DERIVED `_CANONICAL_PROTOCOL`; when absent the enforced clip
  is still constant-backed and the parity test guards it. A drift here cannot drop a move.
  Confirmed by: the parity test + grep showing the only enforcing literal removed.
- **R4. The Pydantic metadata read (`max_length` from `model_fields[f].metadata`) is
  version-coupled (Codex round-2 residual).**
  verification: use a small helper that scans `metadata` for the constraint carrying a
  `max_length` attribute rather than hard-indexing `metadata[0]`; the helper is exercised
  by `test_all_four_schema_sites_derive_from_source`, which would fail loudly (not
  silently pass) if the shape changed. Pydantic version is pinned in the lockfile, so a
  shape change can only arrive via a deliberate dependency bump that re-runs this test.

## Out of scope

Per spec non-goals: value changes, unconditional connector `app/` import, connector
poll/parse rearchitecture, robot-circle CSS comment, and dynamicizing prompt guidance text.
