# Adversarial Review Log — 015

Per the experiment-review protocol: capture SHA before/after, record whether the review changed the design.

## Round 1 — Design review (spec + plan + tasks)

- **Before SHA**: `c480f8f` (spec + plan + tasks, solo-Claude, unreviewed)
- **Reviewers**: Codex `gpt-5.4-mini` (read-only, with code access) + Gemini `2.5-pro` (docs inlined), run serially.
- **Did the review change the design?** **YES** — materially. 2 blockers + 7 issues; all applied.

### Findings & resolution

| # | Sev | Source | Finding | Resolution |
|---|---|---|---|---|
| 1 | BLOCKER | Codex | `next-turn` resolved by connection-key + `match_id` only → can't tell two agents of one connection apart in one match (the past-freeze identity class). | Key by `(agent_id, match_id)` + an `agent_turn_token` required by write endpoints. FR-021; contracts; T009/T011/T012/T013. |
| 2 | BLOCKER | Gemini | Contradiction: "different strategy = different agent" vs US5 "edit strategy, keep rank" → ranks meaningless. | **Versioned agents** (`agent_versions`, rating per version). US5 rewritten; Decision 2; FR-010/011. |
| 3 | MAJOR | Codex+Gemini | `Player.agent_id` becomes int FK but protocol/viewer still exposes `agent_id` as the public label; `agent.name` only unique per user. | `seat_name = handle/name`, uniquified per match; explicit contract everywhere. FR-013; contracts; T028. |
| 4 | MAJOR | Codex | `strategy_snapshot` write-only; read paths show latest → completed matches show wrong strategy (FR-012). | Subsumed by versioning: `players.agent_version_id` is the immutable snapshot; reads source the version. |
| 5 | MAJOR | Codex | Connection health still single-Bot logic. | `connection_health` first-class across a connection's agents. Decision 7; FR-024; T019b. |
| 6 | MAJOR | Codex | `test_migrations` runs upgrade **and downgrade** on SQLite; one-way `0023` fails. | `0023` round-trip safe (real `downgrade()`). Decision 4; T007. |
| 7 | MINOR | Gemini | `max_concurrent_games` never enforced. | Enforce at join. FR-022; T027. |
| 8 | MINOR | Gemini | No source of truth for valid models per provider. | `PROVIDER_MODELS` config. FR-023; T006b. |
| 9 | MAJOR | Gemini | Combined-create flow had no abandonment/failure states. | `pending` connection + resume + 24h GC. Decision 5; FR-024; T019/T019c. |

### Post-review refinements (Chris)
- AgentVersions are **append-only and retained forever** (version_no + `created_at` timestamp) for later review/analysis.
- **Implementation assigned to Codex** (slice-by-slice, boundary reviews).

- **After SHA**: `e40356b`

## Round 2 — Implementation review (per slice)
_Pending. When Codex implements, capture before/after SHA at each slice boundary and whether the review changed the code._
