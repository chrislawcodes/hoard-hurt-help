# Feature Factory Feedback

## Context

This feedback comes from using feature factory on a real repo feature, `vignette-analysis-decision-model`, from spec through plan, tasks, implementation slices, and review checkpoints.

I am not commenting on the feature itself here. This note is about the workflow system:

- what made the feature run safer
- where the workflow was hard to follow
- what would make future runs easier for another agent to operate

## Summary

Feature factory is **good at structure and safety**, but it needs **stricter phase boundaries, better state reporting, and clearer delivery/closeout sequencing**.

The biggest recurring problem was not code delivery. It was ambiguity about:

- whether a phase meant “artifact writing” or “implementation”
- whether the workflow was still expected to continue automatically
- what the current next action was after a checkpoint

## Run-Specific Post Mortem

This note comes from the `vignette-analysis-decision-model` workflow run that ended in PR [#399](https://github.com/chrislawcodes/valuerank/pull/399) merging successfully after CI passed.

The feature shipped, but the workflow exposed several places where a future agent could get stuck, repeat work, or misunderstand the current state.

### What Happened In Practice

1. The workflow started with the right general shape, but the phase labels were easy to misread.
   - Early on, “phase 1” could sound like “the whole first phase is complete” even when it only meant the contract artifacts were done.
   - That made it easy to stop too early or to ask whether implementation should begin.

2. The repo-owned workflow runner was not immediately obvious from memory.
   - I had to rediscover `docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py`.
   - The workflow would be easier to resume if the canonical command path were called out more loudly in the status output or a short quick-start note.

3. The workflow status was useful, but it still required interpretation.
   - `status` told me the stage, dirty overrides, and diff review budget.
   - It did not always make the next action feel obvious without reading the surrounding context.

4. Diff review was valuable, but reconciliation was more awkward than it should be.
   - Gemini and Codex reviews were generated.
   - The Codex review stayed open until I used the lower-level review updater directly.
   - That means the high-level reconciliation path was not obviously complete enough on its own.

5. CI caught problems that the workflow should have made cheaper to catch earlier.
   - One failure was a missing Python dependency install in CI.
   - Another failure was an unresolved merge conflict marker that slipped into `cloud/apps/api/tests/graphql/queries/domain-analysis.test.ts`.
   - Both are the kind of issue a pre-push or pre-deliver hygiene check should catch automatically.

6. Delivery and closeout were not as clearly sequenced as they should be.
   - The PR was merged only after CI passed.
   - But the feature-run workflow still had `closeout: not-checkpointed` when delivery was happening.
   - That suggests the workflow should state more clearly whether closeout is required before merge, required after merge, or allowed to be deferred.

### What Helped

| Area | Why it helped |
|---|---|
| Review gates | They forced the workflow to surface hidden assumptions instead of letting them drift into code. |
| `status` output | It exposed stale diff artifacts, dirty-path allowances, and the current next action. |
| Diff review budget | It made it possible to tell when a diff had drifted from the last reviewed head. |
| Additive slices | Small slices made it easier to keep fixes scoped and to recover after CI failures. |

### What Hurt

| Problem | What happened | Why it matters |
|---|---|---|
| Phase ambiguity | “Phase” sometimes meant artifact writing and sometimes meant implementation. | Another agent can stop too early or keep going when they should hand off. |
| State ambiguity | The workflow did not always make current stage, next action, and blockers obvious at a glance. | It takes too much reading to resume safely. |
| Reconciliation friction | The high-level reconcile path did not clearly finish the review lifecycle on its own. | A review can still show as open even when the human thinks it is done. |
| Hygiene gaps | Conflict markers and CI wiring issues were not caught until later. | This wastes CI cycles and makes the workflow feel less reliable. |
| Closeout sequencing | The run reached delivery without an obvious closeout gate having been completed. | The workflow can look “done” while the final summary step is still missing. |

### Concrete Improvements I Would Make

| Priority | Change | Why |
|---|---|---|
| High | Make `status` print current stage, current artifact, next action, blockers, and whether delivery/closeout are still outstanding. | Another agent should be able to resume in seconds, not minutes. |
| High | Make phase labels always distinguish “artifact complete” from “implementation complete.” | This removes the biggest source of human confusion. |
| High | Make the main reconciliation path update review resolution end-to-end and confirm terminal status in its own output. | The workflow should not require a lower-level escape hatch for normal review closure. |
| High | Add a pre-deliver hygiene check for merge conflict markers and stale diff artifacts. | These are mechanical failures and should be caught before CI. |
| Medium | Add a clearly named delivered/merged state so the workflow stops reporting repairable diff work after the branch is already merged. | This avoids stale follow-up loops after delivery. |
| Medium | Make closeout an explicit gate with a clear rule: required before merge, required after merge, or explicitly deferred. | The workflow should not leave that decision implicit. |
| Medium | Include the canonical runner path in the workflow status or quick-start text. | That reduces the chance of an agent guessing the wrong command or folder. |

### Acceptance Criteria For The Next Version

Feature factory is working better if another agent can answer these questions without rereading the full conversation:

1. What stage is the workflow in right now?
2. What is the next action?
3. Is anything blocking progress?
4. Has the diff review been reconciled to a terminal status?
5. Is delivery complete, and if so, is closeout still pending?

If any of those answers are unclear, the workflow is still too conversational and not machine-readable enough.

## What Worked Well

| Area | Why it helped |
|---|---|
| Small phases | Breaking work into phases reduced blast radius and kept diffs reviewable. |
| Review gates | The adversarial checkpoints caught weak assumptions before they became code. |
| Additive changes | Keeping slices additive made it easier to reason about rollback risk. |
| Handoff structure | The workflow already encouraged deliberate checkpoints instead of ad hoc progress. |

## What Hurt The Most

| Problem | What happened | Why it matters |
|---|---|---|
| Phase ambiguity | It was easy to confuse “phase 1 artifacts are done” with “phase 1 implementation is done.” | The workflow stopped too early or sounded finished when it was not. |
| Weak state visibility | The workflow did not always make the current stage and next action obvious. | Another agent could not tell whether to continue, revise, or stop. |
| Inconsistent stopping point | The workflow sometimes paused after plan or task setup even when implementation was expected next. | This creates hidden delays and extra back-and-forth. |
| Handoff drift | A handoff note could say “phase complete” without clearly stating whether coding should continue. | That makes it hard for a second agent to resume cleanly. |

## Concrete Changes I Recommend

| Priority | Change | Rationale |
|---|---|---|
| High | Make workflow state explicit and machine-readable at every stage. | The runner should always know and report: current stage, current artifact, next action, and whether the workflow is blocked. |
| High | Separate “phase number” from “artifact state” in wording. | “Phase 1 artifacts complete” and “Phase 1 implementation complete” should never sound interchangeable. |
| High | After `tasks.md` is ready and reviewed, auto-continue into implementation unless there is an explicit blocker or the user asked to stop. | This avoids accidental stalling between planning and coding. |
| Medium | Put open decisions, blocked state, and the exact next action into every handoff note. | Another agent can resume without guessing. |
| Medium | Add a short status summary that says “done / in progress / next.” | This makes the workflow easier to inspect quickly. |
| Medium | Keep phase boundaries tied to files, tests, and review gates, not just narrative labels. | That keeps the workflow grounded in concrete deliverables. |

## Suggested Status Model

I would make the runner report one of these states explicitly:

- `discovering`
- `writing_spec`
- `spec_review`
- `writing_plan`
- `plan_review`
- `writing_tasks`
- `tasks_review`
- `implementing`
- `diff_review`
- `closing`
- `post_mortem`
- `blocked`

That would reduce the ambiguity I ran into around “we are in phase 1” versus “we are done with phase 1.”

## Suggested Acceptance Criteria For A Better Feature Factory

Feature factory is working better if, after a checkpoint, another agent can answer these three questions without reading the full conversation:

1. What stage is this workflow in right now?
2. What is the next action?
3. Is anything blocking progress?

If the answer to any of those is unclear, the workflow still has a usability problem.

## One-Sentence Recommendation

Make feature factory more like a small state machine and less like a guided conversation.
