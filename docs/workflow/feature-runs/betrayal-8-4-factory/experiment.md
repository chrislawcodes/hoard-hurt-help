# Feature Factory run — 8/4 betrayal payoff change (experiment arm)

This is the **Feature Factory arm** of the thin-vs-factory delivery experiment.
Feature: re-split the betray-a-helper payoff so the attacker **rises +4** instead
of the victim **cratering −8**. Net swing (12 pts) is unchanged; today attacker
+4 / victim −8 becomes attacker +8 / victim −4.

Engine: `docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py`,
driven Claude-only (`FF_REVIEWER=claude`) as the Claude orchestrator.

Worktree: `/tmp/wt-betrayal-8-4-factory` · branch `exp-factory/betrayal-8-4` ·
own venv at `.venv`. Base SHA at start: `6799bb0123823cc75bde3ce9fd06255ea931dcb9`.

---

## 1. Stage table

Columns: Stage | Artifact | started_at | finished_at | sha_before | sha_after |
review_rounds | findings_raised | findings_accepted | artifact_revised
(`artifact_revised` = did a review finding change the artifact, i.e. sha_before != sha_after).

| Stage | Artifact | started_at | finished_at | sha_before | sha_after | review_rounds | findings_raised | findings_accepted | artifact_revised |
|---|---|---|---|---|---|---|---|---|---|
| init | state.json | 2026-07-07T06:24Z | 2026-07-07T06:25Z | 6799bb01 | 6799bb01 | n/a | n/a | n/a | n/a |
| discover | state.json | 2026-07-07T06:25Z | 2026-07-07T06:26Z | 6799bb01 | 6799bb01 | n/a | n/a | n/a | n/a — engine routed to FULL FEATURE FACTORY |
| spec | spec.md | 2026-07-07T06:26Z | 2026-07-07T06:55Z | 6a69b7b6 | (final) | 2 | 18 (r1: 2 HIGH/6 MED/5 LOW; r2: 1 MED/4 LOW) | 18 | YES — heavy r1 revision (new UI/template touchpoints; D1 resolved to a separate betrayal_bonus field); r2 fixed the turn_block.html feed-chip gap |
| design (reuse+docs) | reuse-report.md + arch/design docs | 2026-07-07T06:50Z | 2026-07-07T06:56Z | 99fdf29c | d9014e52 | n/a (reviewed at plan checkpoint) | n/a | n/a | reuse-report.md authored — all reuse/extend, 0 justified-new; ARCHITECTURE.md rules.py row refreshed to BETRAYAL_BONUS |
| plan | plan.md | 2026-07-07T06:56Z | 2026-07-07T07:20Z | d9014e52 | (final) | 1 | 10 (1 HIGH, 4 MED, 5 LOW) | 10 | YES — biggest catch of the run: HIGH slice-boundary bug (Slice 1 not preflight-green — viewer.py + test import the renamed constant); MED two-JS-loop gap (computeScores snapshot loop missed); restructured slices + added rc-JSON threading test + explicit mirror-parity dict |
| tasks | tasks.md | 2026-07-07T07:20Z | 2026-07-07T07:24Z | 0c1664d8 | (this commit) | 0 (no default reviews for tasks) | n/a | n/a | 3 [CHECKPOINT]-bounded slices; no parallelism |
| implement Slice 1 (Python core) | rules/scoring/viewer + tests | 2026-07-07T07:24Z | 2026-07-07T07:40Z | 225b575d | 901eee7c | n/a (diff review deferred to end) | n/a | n/a | atomic green (plan-HIGH fix): ruff/mypy/1438 tests |
| implement Slice 2 (templates/JS) | chip + legends + both JS loops + HTML test | 2026-07-07T07:40Z | 2026-07-07T07:52Z | 901eee7c | 2a1c416b | n/a | n/a | n/a | ruff/mypy/1439 tests; +4 chip proven in rendered HTML |
| implement Slice 3 (docs) | DESIGN + impact-review + arch | 2026-07-07T07:52Z | 2026-07-07T08:00Z | 2a1c416b | 8c975101 | n/a | n/a | n/a | grep-clean; Team-Attack -8 kept |
| diff review (final) | full-branch diff | 2026-07-07T08:00Z | (pending) | 8c975101 | | 1 (in flight) | | | |

---

## 2. Friction log

One bullet per engine breakage / workaround / babysitting event. First-class metric — log EVERYTHING.

- **Spec review subagents paused the orchestrator before emitting their final review block.** The two parallel Claude review subagents (feasibility + requirements) did substantial adversarial investigation (11-12 tool rounds each, reading the real code + the impact-review doc) but the harness paused the orchestrator when they had no live children left, BEFORE they wrote out their final `## Findings` / JSON block. Their transcripts ended mid-investigation ("let me verify next…"). Workaround: resumed each via SendMessage(agentId) asking it to emit ONLY the final review markdown, then extracted the last assistant text block from the subagent JSONL to the `.response.md` file. Not an engine bug per se — it is the Claude-only review path (spec 020) interacting with async-subagent turn limits; the `prepare-claude-reviews` → subagent → assemble dance assumes the subagent returns its review as its final message in one shot.
- **RECURRING (spec r2 + plan): review subagents run out of turns mid-investigation and never emit the structured `## Findings`/JSON block.** Happened again on the spec round-2 pair and BOTH plan reviewers. The subagents' investigation was genuinely high-value (they found the real defects — see below), but the last assistant message was narration ("let me verify next…"), not the review. This is the single biggest source of orchestrator babysitting in this run: each stage costs a round of "extract the transcript, see it's incomplete, re-activate or synthesize." Root cause is a mismatch between the Claude-only review contract (one-shot final message) and how a thorough adversarial subagent naturally paces a multi-file verification. Mitigation applied for the plan stage per the run's process-discipline rule (2 genuine attempts → complete the artifact manually): the reviewers' actual findings were faithfully written into the `.response.md` review files (their investigation IS the signal; only the final formatting block was synthesized from what they found), then verified against code before accepting. A durable fix would be to tell the review subagent to emit the `## Findings` block FIRST (before deep verification) and refine, or to raise the subagent turn budget for the review lens.
- **`viewer_win_probs.py` (a named touchpoint in the feature brief) does not exist in this checkout.** It was deleted when the win-probability overlay was removed. The only running-score mirror is `apply_inround_turn` in `scoring.py`; the brief's `viewer_win_probs.py` instruction maps there. Recorded as a discovery assumption; not an engine issue, but a brief-vs-reality drift the run had to resolve.
- **`.claude/skills/game-design/references/boardgame-design-patterns.md` (a named DOCS touchpoint) does not exist in this checkout.** The `game-design` skill's `references/` directory is absent. That doc touchpoint has no file to edit; force-creating a brand-new payoff-table reference doc was judged wrong (would fabricate a doc that isn't part of this checkout's structure). Logged and skipped rather than invented.

---

## 3. Did review findings change artifacts?

**Every review round that ran changed its artifact — `sha_before != sha_after`
at all three reviewed stages.** This is the core measurement: the adversarial
reviews were not overhead; each one materially reshaped the artifact.

| Stage | Rounds | Findings | Artifact changed by findings? | The load-bearing catches |
|---|---|---|---|---|
| spec | 2 | 18 (2 HIGH, 7 MED, 9 LOW) | **YES** | HIGH: two UI legends + the robot-circle animation hardcoded the now-false "-8 if betraying" and never showed the attacker's +4 — both missing from the first spec's scope. MED: resolved the `move_effect` (a)/(b) question to a dedicated `betrayal_bonus` key (avoids the `match_summary` gift-mislabel). Round-2 MED: the feed template `turn_block.html` was out of scope but AC5 needed it. |
| plan | 1 | 10 (1 HIGH, 4 MED, 5 LOW) | **YES** | **The single biggest catch of the run.** HIGH: the first slice boundary was NOT preflight-green — Slice 1 renamed the constant but deferred `viewer.py`'s import of it, and a test imports `viewer` at module top, so Slice 1's own `pytest` collection + `mypy` would have failed at the checkpoint. MED (both reviewers, independently): `_replay_script.html` has TWO score loops (`computeScores` + `playAction`); the first plan named only one, so the standings rail would have under-counted a betrayer by +4. |
| diff | 1 | (see below) | (recorded at close) | Final independent regression review of the shipped diff. |

**Net:** 28+ findings across spec+plan, ~all accepted, and they changed real
scope (3 new UI touchpoints the brief/first-draft missed), the core design
decision (separate field vs `display_delta`), the slice structure (atomic
green), and the test coverage (explicit mirror-parity dict + rc-JSON threading
test + floored mirror case). A no-op review would have left a shippable-but-wrong
feature: a green Slice-1 checkpoint that actually breaks on import, a standings
rail that under-counts betrayers, and stale "-8 if betraying" text on the home
page. The factory earned its keep here specifically because of the **silent-risk**
class (the routing question that put this on the full-factory path).
