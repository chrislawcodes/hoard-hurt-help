---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/admin-engagement-dashboard/spec.md"
artifact_sha256: "3f6a14c57a9fd923dc6e68385a91916deaa3c59f8d48b04da2df1becd92badc7"
repo_root: "."
git_head_sha: "ea2d5a003657eb453cba580ddd0acc80691b72e5"
git_base_ref: "origin/main"
git_base_sha: "0a38ccf04bbb00ad4e47446f20ebd95638a0d4a1"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Both MEDIUM findings accepted and fixed in the spec revision. (1) Mutable email: D8 rewritten to store users.is_internal at account creation instead of matching email live, plus an admin toggle to correct a wrong flag. (2) Distinct-user rule: added D9 requiring COUNT(DISTINCT users.id) everywhere, with a multi-agent regression test."
raw_output_path: "docs/workflow/feature-runs/admin-engagement-dashboard/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

- **MEDIUM** Mutable email makes the shared exclusion filter unstable [CODE-CONFIRMED] — The spec’s internal-user filter keys off email, but [`app/routes/auth.py`]( /Users/chrislaw/hoard-hurt-help--admin-engagement-dashboard/app/routes/auth.py#L52-L79 ) rewrites `users.email` on later logins when there is no collision, so a signup can silently move into or out of the excluded cohort over time.
- **MEDIUM** Source counts need an explicit distinct-user rule [CODE-CONFIRMED] — The repository already allows one user to own multiple agents and player rows, as shown in [`tests/test_migrations.py`]( /Users/chrislaw/hoard-hurt-help--admin-engagement-dashboard/tests/test_migrations.py#L229-L258 ), but the spec only asks how many signups “went on to play a turn,” so a row-based join will overcount unless you explicitly dedupe by `users.id`.

## Residual Risks

- I did not see the eventual read-model code, so the exact dedupe rules for the funnel and source table still need verification against archived agents, deleted connections, and multi-agent users.
- The first-touch path still depends on correct middleware ordering and session persistence through OAuth; that can fail silently if the session key is never written before the Google round trip.

```json
{"reviewed": true, "findings": [{"severity": "MEDIUM", "title": "Mutable email makes exclusion unstable", "detail": "[CODE-CONFIRMED] The spec’s internal-user filter keys off email, but app/routes/auth.py rewrites users.email on later logins when there is no collision, so a signup can silently move into or out of the excluded cohort over time."},{"severity": "MEDIUM", "title": "Source counts need a distinct-user rule", "detail": "[CODE-CONFIRMED] The repository already allows one user to own multiple agents and player rows, but the spec only asks how many signups went on to play a turn, so a row-based join will overcount unless you explicitly dedupe by users.id."}]}
```

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Both MEDIUM findings accepted and fixed in the spec revision. (1) Mutable email: D8 rewritten to store users.is_internal at account creation instead of matching email live, plus an admin toggle to correct a wrong flag. (2) Distinct-user rule: added D9 requiring COUNT(DISTINCT users.id) everywhere, with a multi-agent regression test.
