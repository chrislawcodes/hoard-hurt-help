# Experiment bookkeeping — move-limit single source of truth (Feature Factory arm)

Feature: ONE authoritative definition of the two move-text caps (public `message`=200,
private `thinking`=200) that every consumer derives from or is test-pinned to, so they
can never silently drift again. Non-drift refactor + regression test. Values unchanged.

| Stage | Artifact | stage_started_at | stage_finished_at | artifact_before_sha256 | artifact_after_sha256 | review_rounds | issues_raised | issues_accepted | artifact_revised |
|-------|----------|------------------|-------------------|------------------------|-----------------------|---------------|---------------|-----------------|------------------|
| Spec | spec.md | 2026-06-12T00:21:46Z | 2026-06-12T00:25:59Z | 113370b87483cf6608995b4d9940e231a71fc3cc5fd99d0d230837825a3d9e33 | 58659e7a23c295d2a5711b026b25a42911a9c0c0f921cd610f6cdf15fc6d819e | 1 | 2 | 2 | yes |
| Plan | plan.md | 2026-06-12T00:25:59Z | 2026-06-12T00:44:57Z | 23331b17f7ad1dce85a446abc7258812a58f002460602f7b5a8ba4f2961cbdaa | 4397e11d04f2b4644d0c1518ee5cb31ea61ca525dca4b8c6eb37da14fa23ce33 | 3 | 8 | 8 | yes |
| Tasks | tasks.md | 2026-06-12T00:44:57Z | 2026-06-12T00:50:33Z | adbee215385d55306c07463c27a3d38a179bc2f534e513d2330663345d89007a | adbee215385d55306c07463c27a3d38a179bc2f534e513d2330663345d89007a | 0 | 0 | 0 | no |
| Implement | code | 2026-06-12T00:50:33Z | | | | | | | |

Session JSONL: unknown
