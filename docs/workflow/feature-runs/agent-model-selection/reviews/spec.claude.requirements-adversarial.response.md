## Findings

All four previously-flagged contradictions are genuinely resolved in the spec body; no HIGH (code-confirmed) blockers remain. Verified: (a) `resolve_seat_model` never reads verification status, so FR-008 holds; (b) FR-010 and the Assumptions block agree on a NEW field (not `model_self_report`); (c) SC-001 and the verification cadence agree (~60s, not 300s); (d) FR-014 warns only on verified-failing-everywhere and explicitly not on `unknown`/`checking`.

Remaining minor items (none blocking):

**1. [MEDIUM] [CODE-CONFIRMED fact / UNVERIFIED rule] Effective-model display for empty-allowlist machine seats is underspecified (FR-010 vs FR-004).** Hermes/OpenClaw are real machine-connection providers (the connector ships `_HermesAdapter`/`_OpenClawAdapter`) but their `PROVIDER_MODELS` lists are empty and the adapters never pass `--model` (placeholders `default_model = "hermes"`/`"openclaw"`). FR-004 sends no server default for them, yet US5/FR-010 say show "the provider's own default label, never blank." There is no real model name to show — the model lives only in the operator's CLI config. The plan needs a concrete string (e.g. "runs your hermes config's model").

**2. [LOW→MEDIUM] [UNVERIFIED] Ambiguous whether a timeout escalated to "failed" (FR-013) triggers the join warning (FR-014).** FR-013 says after N timeouts the status "is shown as failed"; FR-014 keys on a connection reporting `failed`. The spec doesn't say whether the escalated state is *stored* as `failed` (would warn) or merely *displayed* (would not). The plan should pin that an escalated state counts as `failed` for the union.

**3. [LOW] [CODE-CONFIRMED] FR-010's rationale is factually off (directive still correct).** It says `model_self_report` "stores the provider and feeds the public 'played by' badge"; in code it is `None` at join and the badge reads `Player.played_provider`. The instruction (use a new value) is right; only the stated reason is wrong.

**4. [LOW] Artifact self-contradiction: the Round-1 reconciliation bullet still says "FR-010 reuses model_self_report," contradicting the current FR-010.** Stale leftover from before the round-2 fix; FR-010 wins. Strike the line so an implementer doesn't act on it.

## Residual Risks

- FR-016's 6h re-verify ownership is split between server (worklist) and connector (timer); the plan must pick one.
- SC-001's user-perceived latency (set-model → status) can approach ~90s (≤60s to next tick + ≤30s call), though SC-001 anchored to the tick is met.
- "Live machine connection" is undefined for the FR-014 union (liveness window); defer to the plan but pin it so the warning doesn't flap.
- By design, FR-009a maps several real "can't run" errors to retryable until the next refresh/turn re-classifies them — accepted cost of the conservative default.
