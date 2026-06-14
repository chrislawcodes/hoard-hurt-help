# Experiment — Liar's Dice engine: Direct Path vs Feature Factory

Goal: did the Feature Factory's extra review steps change the outcome enough to be worth the
overhead? Substrate: the Liar's Dice game (a well-specified, test-verifiable rules engine).

## Outputs

- **Direct Path:** PR #371 — *engine only* (329-line engine + 692 lines of tests). **Merged** to
  `main` (commit `7552ea4`). Built in a prior session, outside this experiment harness.
- **Feature Factory:** PR #377 — *full Phase C* (engine + module + bots + viewer + admin, ~2,127
  lines across 31 files). **Open, intentionally not merged** (kept as the comparison arm; merging
  would overwrite #371's better-tested engine and tangle the two arms).

The arms are not the same scope — Direct was engine-only, Factory was full Phase C. The clean
head-to-head is the **engine**; the rest of Phase C is Factory-only.

## Did the reviews change the work?

| Stage | Path | review_rounds | issues_raised | artifact_revised | What the review caught |
|------|------|---------------|---------------|------------------|------------------------|
| Spec | Feature Factory | 5 | ~15 (code-confirmed) | **yes** (revised 4×) | validate_move gets no game state; THREE+a-fourth admin create paths; game-aware player bounds (blanket relax would break arena); public action schema needs widening; `match_placement_key` must be overridden; per-match config persistence. |
| Plan | Feature Factory | 2 | 4 | **yes** | **Real bug:** bot seed used Python `hash()` (salted per process → non-reproducible across restarts) → fixed to `hashlib`. Plus atomic `MatchState` seeding, SC-HD must cover the MCP path, PD byte-identical assertion. |
| Tasks | Feature Factory | 0 | 0 | no | (no default reviews) |
| Implement | Feature Factory | partial | 3 (low-confidence) | n/a | Diff review hit PARTIAL coverage (Gemini times out on the one giant diff). Findings were design notes; none confirmed bugs. |
| (any) | Direct Path | — | — | — | Built outside the harness; no per-stage review record. |

**Orchestrator manual review (compensating for the failed diff review) found two real test-rigor
gaps the Factory shipped:**
- The Factory's headline engine test was **circular** — it compared `is_legal_raise` against a
  verbatim copy of the engine's own ranking formula, so it would pass even if the formula were
  wrong. Replaced with an independent test derived from the spec's rule formulas. (The engine
  itself turned out correct.)
- The hidden-info (SC-HD) test was **module-level only**, not the 3-channel sweep the plan
  required. Low risk (no public channel reads player dice), logged as follow-up.

## Token / cost (Factory; Claude-only totals UNDERCOUNT — see caveat)

| Signal | Feature Factory | Direct Path |
|---|---|---|
| Runner commands | 30 | — |
| Wall time (runner) | ~29 min | — |
| Codex calls / input tokens | 8 / 1,528,135 | — |
| Gemini calls / in+out | 11 / 310,181 + 6,718 | — |
| Claude orchestrator tokens | not captured by runner | — |

Caveats: Codex output tokens are reported as 0 by the codex-runner (not measured); the Claude
orchestrator session (spec/plan/tasks authoring + all reconciliation + manual review) is not
captured at all. So Factory's true cost is materially higher than the table shows. Direct Path
ran outside the harness — no token record exists.

## Outcome

- **Did FF catch problems the Direct Path missed?** Yes, at the design stages — emphatically. The
  spec review surfaced ~15 code-confirmed platform-integration gaps and the plan review caught a
  genuine determinism bug (`hash()` seed). These changed the work materially. *But* almost all of
  that value was about wiring the game **into the platform** — scope the Direct arm never attempted
  (it was engine-only), so it's not a like-for-like "FF caught what Direct missed."
- **Did the extra review steps change the code/scope/tests?** Yes — the spec was revised 4× and its
  scope roughly doubled; the plan fixed a real bug.
- **Was the overhead worth it?** Mixed. The **design-stage reviews earned their keep.** The
  **implementation stage discipline silently broke down:** the 10-slice plan never engaged (a
  `tasks.md` marker-format mismatch made the runner build everything as one slice, warning instead
  of failing), which then made the diff review impossible (too big → partial), which let a circular
  engine test through. The safety net that should have caught these *warned and continued* instead
  of stopping.
- **Which path next time?** For a pure, test-verifiable engine: **Direct Path + one review pass**
  beats full FF — the FF's design reviews mostly addressed integration scope an engine doesn't have,
  and its per-slice implementation safety didn't actually hold. For a genuinely fuzzy,
  platform-spanning feature, FF's spec/plan reviews are worth it — **if** the slicing/diff guards
  are made hard-fail (see below).

## Workflow fixes this surfaced (for the postmortem)

1. **Hard-fail (don't warn) on 0 `[CHECKPOINT]` markers for a non-trivial feature.** Both inputs are
   already computed programmatically (regex marker count + threshold-based size estimate); change
   `warn` → `exit non-zero`. This is the single highest-value guard.
2. **Loosen or document the marker format**, or echo the detected slice count loudly at tasks time
   (`Detected N slices`). The strict regex silently rejected markers in headings/backticks.
3. **Cap per-Codex-dispatch diff size** as a backstop even when markers are missing.
4. **The `/tmp` worktree tripped Gemini's trusted-folder guard** — needs `GEMINI_CLI_TRUST_WORKSPACE=true`.
5. **scope.json `allowed_dirty_paths: ["."]`** crashes the diff writer ("cannot target repo root").
