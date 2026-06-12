# Experiment bookkeeping — move-limit single source of truth (Feature Factory arm)

Feature: ONE authoritative definition of the two move-text caps (public `message`=200,
private `thinking`=200) that every consumer derives from or is test-pinned to, so they
can never silently drift again. Non-drift refactor + regression test. Values unchanged.

| Stage | Artifact | stage_started_at | stage_finished_at | artifact_before_sha256 | artifact_after_sha256 | review_rounds | issues_raised | issues_accepted | artifact_revised |
|-------|----------|------------------|-------------------|------------------------|-----------------------|---------------|---------------|-----------------|------------------|
| Spec | spec.md | 2026-06-12T00:21:46Z | 2026-06-12T00:25:59Z | 113370b87483cf6608995b4d9940e231a71fc3cc5fd99d0d230837825a3d9e33 | 58659e7a23c295d2a5711b026b25a42911a9c0c0f921cd610f6cdf15fc6d819e | 1 | 2 | 2 | yes |
| Plan | plan.md | 2026-06-12T00:25:59Z | 2026-06-12T00:44:57Z | 23331b17f7ad1dce85a446abc7258812a58f002460602f7b5a8ba4f2961cbdaa | 4397e11d04f2b4644d0c1518ee5cb31ea61ca525dca4b8c6eb37da14fa23ce33 | 3 | 8 | 8 | yes |
| Tasks | tasks.md | 2026-06-12T00:44:57Z | 2026-06-12T00:50:33Z | adbee215385d55306c07463c27a3d38a179bc2f534e513d2330663345d89007a | adbee215385d55306c07463c27a3d38a179bc2f534e513d2330663345d89007a | 0 | 0 | 0 | no |
| Implement | code | 2026-06-12T00:50:33Z | 2026-06-12T00:55:25Z | 0a1e742ff61b301954b0c6289173002c7ae8401ad64ca900a63862d79b1cd793 | 0a1e742ff61b301954b0c6289173002c7ae8401ad64ca900a63862d79b1cd793 | 1 | 2 | 2 | no |

## What each adversarial review changed

- **Spec (1 round, 2 issues, both accepted → artifact revised):** Codex caught (C-1)
  that a naive parity test would only exercise the connector's importable branch in a
  source checkout and could mask a stale STANDALONE fallback — drove the explicit
  `_FALLBACK_*` constant + "test the fallback" design. Codex (C-2) flagged a spec wording
  inconsistency ("no consumer hard-codes 200" vs the prompt-text exemption) — reworded to
  scope the rule to ENFORCED caps only. Gemini's findings confirmed the spec's own drift
  diagnosis (no new actionable items). Spec re-reviewed round 2 → clean.
- **Plan (3 rounds, 8 issues, all accepted → artifact revised):** Round 1 made the plan
  also DERIVE the prompt guidance text from the constants (f-string) and cover all 4
  schema sites; verified DB columns are `Text`/unbounded. Round 2 caught a real **HIGH**:
  the test pinned constants but NOT the live clip — added behavioral tests that push
  over-cap input through `_normalize_move`/`_move_request`. Round 3 added a test that
  re-loads the connector with `app` blocked to exercise the real `except ImportError`
  branch, and dropped the fragile regex fallback. Round 4 = convergence (only
  restatements). These reviews materially changed the test design.
- **Tasks (0 default reviews):** none.
- **Implement / diff (1 Gemini regression review, 2 issues accepted, code NOT revised):**
  Gemini flagged that the broad `except ImportError` could mask a malformed
  `app.agent_prompt` — accepted but DEFERRED: the pattern pre-dates the feature, narrowing
  it is out of scope for a non-drift refactor, and the drift guarantee is unaffected. The
  staleness/LOW items were the already-accepted operator-deploy point. No code change.

## Cross-process-constant approach chosen

Approach A: authoritative constants `MESSAGE_MAX_LENGTH` / `THINKING_MAX_LENGTH` live in
`app/agent_prompt.py` (stdlib-only, already shared). The connector keeps explicit
standalone `_FALLBACK_*` constants and extends its EXISTING `try/except ImportError`
block (the one that already imported `RESPONSE_PROTOCOL`) to import the caps when `app/`
is present and fall back otherwise — zero new files, zero new unconditional `app` import.
A regression test pins the standalone fallback (and live clip behavior) to the server
source so they cannot silently diverge.

## Preflight result

`python3 -m ruff check .` → All checks passed.
`mypy app/ mcp_server/` → Success: no issues found in 103 source files.
`pytest -q` → 650 passed (includes 7 new tests in tests/test_move_length_limits.py).

Session JSONL: unknown
