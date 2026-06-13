# Experiment ledger — Factory arm (byo-terminal-mode-a)

Path: Feature Factory (`run_factory.py`), branch `feat/byo-terminal-mode-a`.

| Stage | Artifact | stage_started_at | stage_finished_at | artifact_before_sha256 | artifact_after_sha256 | review_rounds | issues_raised | issues_accepted | artifact_revised |
|-------|----------|------------------|-------------------|------------------------|-----------------------|---------------|---------------|-----------------|------------------|
| Spec | spec.md | 2026-06-13T07:18:16Z | 2026-06-13T07:31Z | 5b84338323516b89fd65684131378c7d63e7f6d7a9c33bffd2f8a4aa11ffd70b | 9d82e83ad25958775f6d85ddc703587cf5536429dbff17c9f337f11b471feb64 | 2 | 6 | 6 | yes |

Spec notes: Round 1 raised 6 findings (Codex 1H/2M, Gemini 1H/1M/1L), all accepted, spec revised (hash changed). Round 2 = converged ("no actionable findings"): its findings were restatements that code isn't built yet, or implementation concerns deferred into plan.md (counter attribution via served_by_connection_id, atomic SQL increment, call-count first-class). Net: 1 substantive revision round.
| Plan | plan.md | 2026-06-13T07:31Z | (round 2 running) | 06556a9aac0c04513a78d5236bb43b4a506417842590961151b3f9ccd9f860fd | 927f71cc6a00cde2bf5ebb7d62e09750d8c284da38696936b36550e4e096175f | 1+ | 6 | 6 | yes |

Plan notes: Round 1 raised 6 findings (Codex 2H/1M, Gemini 2H/1M). TWO were critical hot-path bugs the Direct arm is at risk of shipping: (1) long-poll would still pin the request-scoped DbSession for the full ~25s hold -> pool exhaustion (fix: drop the DbSession dependency, per-tick async-with sessions); (2) turns_played misattributed via served_by_connection_id since require_agent_player doesn't require the pin (fix: credit the submitting connection). All accepted, plan revised. Round 2 confirming convergence.
| Tasks | tasks.md | | | | | | | | |
| Implement | code | | | | | | | | |

Session JSONL: (Claude orchestrator session — fill in Stage C from ~/.claude/projects/)

Notes:
- Reviews at spec/plan run on Codex + Gemini CLIs (tokens NOT in the Claude JSONL — Factory cost is undercounted by the Claude-only figure).
- artifact_after_sha256 + issues columns filled after each checkpoint's reviews are reconciled.
