# Feature Factory Experiments

Tracking whether adversarial reviews (Feature Factory pipeline) actually change code vs. Direct Path.

**Measurement:** git SHA before and after each review. If the SHA changed, the review had teeth.

**How to run one:** use the `experiment` skill (`.claude/skills/experiment/`). It builds the same feature both ways in parallel worktrees, hashes each artifact before/after review, counts tokens, and appends a verdict here.

**Token cost — how to read it:** quote the cost gap as **real-work = billed input + output** (the full-price tokens), NOT cache reads. Cache reads are ~10× cheaper per token and balloon on longer Feature Factory sessions, so a "9× cache read" gap can sit next to a real cost gap of only ~2.5× (Experiment 7). Also note: these Claude token counts **exclude the Codex/Gemini review calls** Feature Factory makes, so the Claude-only multiple is a *floor* on the true cost — the real bill is higher.

**Pattern hypothesis:** Feature Factory has an edge on backend/algorithmic work. Direct Path has an edge on UI/nav work where codebase context eliminates false assumptions.

> **Data provenance.** Experiments **1–6 were run in the ValueRank project**
> (`chrislawcodes/valuerank`) — their PR numbers are ValueRank PRs and the
> features are ValueRank's. They are kept here as a baseline for how the Feature
> Factory framework performs; each category rests on only 1–2 data points, so
> treat the recommendation as a hypothesis we keep testing. From **Experiment 7**
> on, entries are tagged with the repo they ran in, and the dataset grows as we
> build more features here.

---

## Experiment 8 — `byo-terminal-mode-a` (hoard-hurt-help, 2026-06-13)

**Feature:** Interactive "Mode A" MCP play — bounded long-poll on next-turn, per-connection usage counters (dashboard), connect docs; built on the existing MCP/agent stack.

**Direct PR:** #NNN (merged) | **Feature Factory PR:** — (not built; spec+plan+reviews only, record under `feature-runs/byo-terminal-mode-a-factory/`)

| | Direct Path | Feature Factory |
|--|--------------|---------|
| Reviews that changed code | 1/1 self-review changed the implementation | 2/2 design stages changed (spec + plan); code never built |
| Critical catch | Caught pool-pinning (surfaced as a test slowdown) + correct turns attribution + opt-in design — all on its own | disabled-user mid-hold revalidation (minor, ≤25s self-correcting) — only thing it caught that Direct missed |
| False positives | Low | 1 (`session_usage` field — conflicted with the explicit dashboard-only scope decision) |
| Tests | 3 new (long-poll, counter, migration) | 0 (no code) |
| Claude tokens (billed input / cache read / output) | ~212k total (subagent lump) | review-only: Codex 375k + Gemini 130k/2.9k; orchestrator tokens large & excluded |
| Human interruptions | 0 | 0 (but heavy orchestrator involvement across 4 review rounds) |

**Verdict:** Direct Path won decisively. FF's spec/plan reviews raised real, code-confirmed risks, but the Direct build independently handled every critical one (DB pinning, turns attribution, write-amplification, two-phase prompt) AND made a better core design choice (opt-in long-poll) the FF plan got wrong. FF's sole unique catch was a minor, self-correcting edge. Decisive factor: the biggest risk (DB pinning) was test-visible — it slowed the suite — so one self-review caught it, making FF's extra rounds redundant. FF also cost far more and never produced code.

**Lesson:** The predictor for FF value is **silent vs. test-visible risk**, not backend-vs-UI. This backend feature contradicts the "backend → Feature Factory" lean: its risks surfaced in the test suite, so Direct + one self-review sufficed. Reserve FF for failure modes that are *silent* (pass tests, break in prod) — data-model/semantics bugs — not perf/concurrency a test run exposes.

---

## Experiment 7 — `move-limit-single-source` (hoard-hurt-help, 2026-06-11)

**Feature:** Make the public-`message` (200) and private-`thinking` (200) char caps a single source of truth across the server schema, the standalone connector, and the model-facing prompt — so they can't silently drift apart again (the drift had 422-dropped oversized moves). No value change; core deliverable is a regression test that fails on divergence.

**Direct PR:** none (branch `direct/move-limit-single-source`, local) | **Feature Factory PR:** none (branch `factory/move-limit-single-source`, local)

| | Direct Path | Feature Factory |
|--|--------------|---------|
| Reviews that changed code | 1/1 stages (Implement self-review extended the test to pin the prompt prose) | 2/4 stages (Spec drove the fallback-constant design; Plan reshaped the test); Diff review deferred 1 out-of-scope |
| Critical catch | — | Plan review (HIGH) caught that pinning constants isn't enough — added **behavioral clip tests** + an `app`-blocked import test that catch a wrong call-site literal Direct's structural test would miss |
| False positives | None | Low (the 2 Diff-review issues were real but correctly deferred as out-of-scope) |
| Tests | 5 new (structural) | 7 new (structural + behavioral) |
| Claude tokens (billed input / cache read / output) | 171,199 / 3,687,466 / 17,004 | 417,767 / 32,674,286 / 64,109 (plus uncounted Codex+Gemini review calls) |
| Human interruptions | 0 | 0 |

**Verdict:** Both paths shipped a correct, green (preflight-passing) solution; the values stayed 200/200. They chose different designs: Direct added a new dependency-free `app/move_limits.py` and kept a test-pinned local copy in the connector; Feature Factory reused the existing `app/agent_prompt.py` + the connector's existing import-guard, falling back to a local copy only when standalone. Feature Factory won on test quality — its Plan-stage review turned a structural (constant-equality) test into a behavioral one that actually exercises the clip, a gap Direct never noticed. It cost ~2.5× the Claude real-work tokens, ~6.5× wall-clock, and extra Codex/Gemini calls. No post-merge bugs (neither merged yet).

**Lesson:** When the deliverable *is* the safety-net test, Feature Factory's adversarial reviews reliably upgrade a structural test into a behavioral one — worth it if the test is the whole point. For a low-risk, mechanical refactor where you just need it done, Direct Path is sufficient and ~6× faster. Consistent with the ValueRank "backend/correctness → Feature Factory finds a real gap" pattern — first hoard-hurt-help data point agrees.

---

## Experiment 6 — `per-model-coverage` (valuerank, 2026-04-03)

**Feature:** Per-model trial counts in the coverage matrix — min/max trials per cell across default models, mismatch warning (orange border + ⚠) when models have uneven coverage. Includes `defaultModelIds` on Domain, global model fallback, and `modelBreakdown` tooltip.

**Direct PR:** #530 (closed, UI bugs) | **Feature Factory PR:** #532 (merged, originally #531 — rebased to clean branch due to stale commits)

| | Direct Path | Feature Factory |
|--|--------------|---------|
| Reviews that changed code | — | Yes — Gemini spec + Codex adversarial both changed implementation |
| Critical catch | — | 2 real UI bugs caught: (1) color threshold used `primaryCount` instead of `countForColor` (cells colored wrong in per-model mode); (2) label showed "batch" instead of "trial (min)" in per-model mode |
| False positives | — | Low |
| Tests | 0 new | Several new (39 total in domain-coverage.test.ts) |
| Human interruptions | 0 | 1 (conflict resolution on stale branch) |
| Post-merge production bugs | 3 | 3 (same bugs — introduced by feature itself, not path-specific) |

**Post-merge bugs (both paths would have had these):**
1. Empty `defaultModelIds` showed batch count instead of falling back to global defaults → PR #533
2. Double-counting paired companion runs (gpt-5.1 showing 10 instead of 5) → PR #534
3. Structural root cause: dedup belonged at call site, not inside `computePerModelTrialCounts` → PR #535 (`deduplicateRunsByGroupId` exported helper)

**Verdict:** Feature Factory won. It caught two real UI bugs that Direct Path shipped — both were silent (no test coverage for color thresholds or label text). The post-merge production bugs were structural/domain-knowledge issues neither path would have caught without real data.

**Lesson:** Full-stack features with non-obvious display logic (color thresholds, conditional labels) favor Feature Factory. The adversarial review found exactly the cases that are hard to unit-test. Post-merge bugs came from paired-run domain knowledge gaps, not from the delivery path.

---

## Experiment 5 — `provider-budget` (valuerank, 2026-03-31)

**Feature:** Per-provider balance tracking — manual entry, auto-deduct on run completion, manual sync with drift logging, soft pre-run warning gate. UI on Settings → Models.

**Direct PR:** #482 (closed, duplicate) | **Feature Factory PR:** #483 (merged)

| | Direct Path | Feature Factory |
|--|--------------|---------|
| Reviews that changed code | 3/4 (spec, plan, tasks) | 2/3 (spec via Gemini, plan via Codex) |
| Critical catch | Spec: `Run` has no `estimatedCost` (would be runtime bug); Tasks: race condition on deduction → atomic `{ decrement }` | Spec: cost data source clarified (`run.config.estimatedCosts.perModel`); added FR-015/016/017 |
| Post-implementation bug | None caught | `cache-only` → overdraft check silent in cold session (caught in manual review, fixed before merge) |
| Tests | 0 new | 2 new test files (mutations + deduct service) |
| Claude tokens | ~32.8M cache read, ~73k output | ~4.9M cache read, ~7k output (coordinator only) |
| Human interruptions | 0 | 2 (Prisma version conflict, cache-only bug) |

**Verdict:** Feature Factory won on tests — it added 2 test files that Direct Path skipped entirely. Both pipelines caught the same core correctness issues (atomic deduction, cost data source). Feature Factory required 2 human interventions (Prisma version conflict mid-run, cache-only bug missed by Phase 7 cleanup). Direct Path ran cleanly.

**Lesson:** Feature Factory enforces test discipline that Direct Path skips. For features with non-trivial service logic, that's worth the overhead. But Feature Factory still needs human review of the final output — it shipped a silent bug in the pre-run gate.

---

## Experiment 4 — `cross-run-reliability` (valuerank, 2026-03-31)

**Feature:** Fix `build_pooled_aggregate_reliability` so N-runs × 1-sample/condition aggregates surface `baselineReliability` + `directionalAgreement` instead of "unavailable". Also fix silent drift collection bug.

**Direct PR:** #471 | **Feature Factory PR:** #472

| | Direct Path | Feature Factory |
|--|--------------|---------|
| Reviews that changed code | 1/1 (self-review: removed dead loop) | 3/4 (spec, plan, Codex adversarial) |
| Critical catch | n/a | Codex adversarial caught: `drift_samples` still always empty after implementation — wrong key name (`uniqueScenarios` not in `ModelStats`). Tests passed silently. |
| False positives | 0 | 1 (Gemini HIGH on `isMultiSample` — misread, uses `max` not `avg`) |
| Tests | 32/32 | 32/32 |
| Claude tokens | 125,452 | 129,517 |
| Human interruptions | 0 | 0 |

**Verdict:** Feature Factory was worth it. The Codex adversarial review caught a silent correctness bug that unit tests masked — the drift fix compiled and all tests passed, but `drift_samples` was always empty because Codex used the wrong dict key. Direct Path avoided this by writing the fix directly with the correct field names. Token delta negligible (<4%).

**Lesson:** Use Feature Factory for Python worker internals with non-obvious field names. Direct Path is fine for straightforward refactors.

---

## Experiment 3 — `settings-restructure` (valuerank, 2026-03-30)

**Feature:** Restructure Settings nav from single tab to dropdown with separate pages per section. Move Preambles + Level Presets from Domains dropdown to Settings > Research Setup.

**PR:** #468

| | Direct Path | Feature Factory (spec+checkpoint) |
|--|--------------|--------------------------|
| Pre-impl actionable findings | 4 (redirect, ref/state wiring, tests, thin wrappers) | 0 actionable |
| Unique findings | 4 real structural issues | 0 |
| False positives | 0 | 6 (deep links, RBAC, shared state, MEMORY.md clause misread) |
| Human interruptions | 0 | 1 (triage) |
| Tests | 1466/1466 | — |

**Verdict:** Feature Factory overhead not justified. All 6 Feature Factory findings were false positives based on assumptions that don't hold (app has no URL-hash tabs, no RBAC, no shared panel state). Direct Path caught the real structural issues (redirect needed, NavTabs ref/state wiring, test updates) via pre-implementation analysis.

**Lesson:** UI/nav refactors favor Direct Path. Codebase context eliminates the assumptions that Feature Factory reviewers false-positive on.

---

## Experiment 2 — `aggregate-cross-batch-reliability` (valuerank, 2026-03-30)

**Feature:** Fix reliability metrics for mixed aggregates (some within-run repeats, some without).

**PR:** #466

| | Direct Path | Feature Factory (spec+checkpoint) |
|--|--------------|--------------------------|
| Actionable findings pre-implementation | 0 | 3 |
| Unique findings | 0 | 1 HIGH (mixed-mode gap — real correctness bug) |
| False positives | n/a | Low |
| Human interruptions | 0 | 1 (approved acting on all findings) |

**Verdict:** Feature Factory justified. Caught a real correctness bug: the conditional fallback silently under-reported reliability for mixed aggregates. Final implementation materially better because of the review.

---

## Experiment 1 — `domain-coverage-hub` (valuerank, 2026-03-30)

**Feature:** UI feature — domain coverage hub page.

**PR:** #465

| | Direct Path | Feature Factory (spec+checkpoint) |
|--|--------------|--------------------------|
| Actionable findings pre-implementation | 7 | 4 |
| Unique findings | 3 (file size limit, legacy fallback, empty state) | 0 |
| False positives | Low | Several |
| Human interruptions | 1 (4 product decisions) | n/a |

**Verdict:** Feature Factory's one "unique" finding turned out to be a deliberate architectural choice. Direct Path caught more real issues.

---

## Running Tally

| Experiment | Repo | Type | Feature Factory worth it? | Key reason |
|-----------|------|------|-------------------|------------|
| 1 — domain-coverage-hub | valuerank | UI | No | Direct Path found more real issues; Feature Factory had false positives |
| 2 — aggregate-cross-batch-reliability | valuerank | Backend bug fix | Yes | Feature Factory caught real correctness gap Direct Path missed |
| 3 — settings-restructure | valuerank | UI/nav refactor | No | 6 false positives, 0 actionable |
| 4 — cross-run-reliability | valuerank | Backend/worker fix | Yes | Codex adversarial caught silent wrong-key bug that passed tests |
| 5 — provider-budget | valuerank | Full-stack feature | Partial | Feature Factory enforced test discipline; both caught same correctness bugs; Feature Factory needed 2 human interventions |
| 6 — per-model-coverage | valuerank | Full-stack feature | Yes | Caught 2 real UI bugs (color threshold, label) that Direct Path shipped silently |
| 7 — move-limit-single-source | hoard-hurt-help | Backend refactor + test | Partial | Plan review upgraded a structural test to a behavioral one; both paths otherwise correct; ~2.5× tokens / ~6.5× time |
| 8 — byo-terminal-mode-a | hoard-hurt-help | Backend / hot-path concurrency | No | Direct caught the critical risks itself (DB-pin showed as a test slowdown) + chose a better opt-in design; FF's lone unique catch was minor; FF cost far more and never built code |

**Pattern (8 data points — 6 ValueRank, 2 hoard-hurt-help):** Feature Factory 2/2 on backend/algorithmic work. Direct Path 2/2 on UI/nav work. Full-stack features: Feature Factory 2/2 on catching real bugs (though Experiment 5 was partial on process friction). First hoard-hurt-help point (backend refactor) agrees with the backend lean: Feature Factory found a real test-coverage gap Direct missed — but on a low-risk refactor the win was test robustness, not correctness, so it was only partially worth the steep cost. **Experiment 8 (backend, hot-path concurrency) contradicts the backend lean:** Direct won outright — its critical risks were test-visible (a DB-pin that slowed the suite), so one self-review caught them and FF's extra rounds were redundant at far higher cost. The emerging better predictor is **silent vs. test-visible risk**, not backend-vs-UI.

**Recommendation:** Route features by type before choosing pipeline:
- Backend algorithmic / Python worker internals → Feature Factory
- UI / nav / component refactors → Direct Path
- Full-stack features → Feature Factory; it consistently catches display-logic bugs that are hard to unit-test
- Low-risk mechanical refactor where you just need it done → Direct Path (~6× faster); but if the deliverable IS a safety-net test, Feature Factory's reviews reliably upgrade a structural test into a behavioral one (Experiment 7)
- **Better predictor than feature-type (Experiment 8):** route by *failure-mode visibility*. If the main risk is **silent** (passes tests, breaks in prod — data-model/semantics, wrong-key) → Feature Factory. If it's **test-visible** (perf/concurrency that slows or fails the suite; UI you can see) → Direct Path + one self-review, at a fraction of the cost. Mode A's worst risk (DB-pin) was test-visible, so Direct sufficed despite being "backend."

_Next hoard-hurt-help experiments append above as Experiment 7+ (tagged `hoard-hurt-help`). Re-check whether the ValueRank pattern holds on this codebase as local data accumulates._
