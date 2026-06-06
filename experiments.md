# Feature Factory Experiments

Tracking whether adversarial reviews (Feature Factory pipeline) actually change code vs. Direct Path.

**Measurement:** git SHA before and after each review. If the SHA changed, the review had teeth.

**How to run one:** use the `experiment` skill (`.claude/skills/experiment/`). It builds the same feature both ways in parallel worktrees, hashes each artifact before/after review, counts tokens, and appends a verdict here.

**Pattern hypothesis:** Feature Factory has an edge on backend/algorithmic work. Direct Path has an edge on UI/nav work where codebase context eliminates false assumptions.

---

## Prior evidence (ported from ValueRank — NOT yet validated in this repo)

This workflow and its routing recommendation were developed in the ValueRank
project (`chrislawcodes/valuerank`, `experiments.md`) across 6 paired experiments.
Those experiments were on ValueRank's features and PRs, so they are **not** local
data — treat the rule below as a starting hypothesis to test here, not a proven
result. Each category in ValueRank rested on only 1–2 data points.

ValueRank's synthesized routing rule (6 experiments: FF 2/2 on backend, Direct 2/2 on UI, FF 2/2 catching real bugs on full-stack):

- Backend algorithmic / worker internals → Feature Factory
- UI / nav / component refactors → Direct Path
- Full-stack features → Feature Factory; it consistently caught display-logic bugs that are hard to unit-test

We re-test this here before trusting it. Until we have local experiments, this is a hypothesis, not a rule.

---

## Running Tally

_No experiments have been run in this repo yet._ The `experiment` skill appends
rows here (newest experiment entries go above this section).

| Experiment | Type | Feature Factory worth it? | Key reason |
|-----------|------|-------------------|------------|
| _(none yet)_ | | | |

**Pattern (0 local data points):** none yet — see "Prior evidence" above for the ValueRank hypothesis we are testing.

**Recommendation:** until we have local data, start from the ValueRank hypothesis above, but record every run honestly — including results that contradict it.
