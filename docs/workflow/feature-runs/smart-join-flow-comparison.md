# Experiment — Smart gated Join flow

A/B of Direct Path vs Feature Factory on the same feature: turn the join page into a
setup hub where one **Join** sends the operator to the existing page for the first
missing step (`?next=` threading), no new page.

## Outputs

- Direct Path: PR #372 (merged, squash `98ccb7a`) — branch `direct/smart-join-flow`
- Feature Factory: branch `factory/smart-join-flow` (`720d5de`, not shipped)

## Did Reviews Change The Work?

| Stage | Path | Artifact | artifact_revised | issues_raised | issues_accepted | review_rounds |
|-------|------|----------|-----------------|---------------|-----------------|---------------|
| Implement | Direct Path | code | yes | 1 | 1 | 1 |
| Spec | Feature Factory | spec.md | unrecorded (review ran: Codex feasibility + Gemini requirements) | — | — | 1 |
| Plan | Feature Factory | plan.md | unrecorded | — | — | — |
| Tasks | Feature Factory | tasks.md | unrecorded | — | — | — |
| Implement | Feature Factory | code | n/a (no tests written; left uncommitted by the run) | — | — | 0 |

Direct's single self-review pass added a chained-flow test and also surfaced the
open-redirect risk during the build (added a `safe_internal_next` guard). Feature
Factory ran adversarial reviews on the **spec only**; it independently flagged the
same open-redirect concern (added its own `safe_redirect.py`). Its run stalled after
the planning ceremony and had to be re-launched to implement; the implementation it
produced was preflight-clean but **had no tests** and was left uncommitted.

## Token Efficiency

| Path | Real-work (Claude billed+output) | Notes |
|------|----------------------------------|-------|
| Direct Path | ~149k | one agent, end to end |
| Feature Factory | ~282k (143k planning + 139k implement) | **plus uncounted Codex/Gemini review calls** — true cost higher |

Real-work ratio ≈ **1.9× Claude-only** (a floor; the reviewer-CLI tokens push it higher).

## Outcome

- **Did Feature Factory catch problems Direct missed?** No. Both paths independently
  added the open-redirect guard — the one real risk — so FF's reviews surfaced
  nothing unique.
- **Did the extra review steps change the code/scope/tests?** FF's reviews ran on the
  spec; the implementation it produced had **no tests**. Direct's lightweight
  self-review added a test and the security guard.
- **Was the overhead worth it?** No. ~1.9× the tokens (more with the reviewer CLIs),
  it stalled before implementing (1 human intervention to re-launch), and shipped
  untested, uncommitted code.
- **Which path next time?** Direct Path — for a reuse-and-glue UI/flow feature where
  the design is already settled, the planning ceremony re-derives a plan you already
  have.
