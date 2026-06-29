# Spec: Per-agent model selection with fail-fast verification

## Status — slice 1 shipped; this run builds slices 2–4

Slice 1 (the backend foundation) is **already merged to main** (#572): `Agent.preferred_model` (migration 0044) + the server-side three-layer model resolution (`resolve_seat_model`, removing legacy `version.model`). FR-001, FR-002, FR-003, FR-004, FR-008 (resolution half), FR-011 and the empty-allowlist rule are satisfied by it.

**This Feature Factory run implements the remaining slices on top of current main:**
- **Slice 2 — Connector model verification** (FR-005, FR-006, FR-007, FR-013, FR-015, FR-016): the dedicated down/up verification channels + the connector's cheap test call + the server cache + the per-model UI status.
- **Slice 3 — Fail-loud at play time** (FR-009, FR-009a, FR-018): route the play-time failure reason on the up-channel and surface it; classify transient vs real.
- **Slice 4 — Agent-settings UI** (FR-001 UI control, FR-010, FR-012, FR-014, FR-017): the preferred-model picker (advanced), the effective-model display, and the join-time warning.

The plan and tasks below cover only slices 2–4. Slice 1's requirements remain in this spec for context but are done.

## Background

Agents are decoupled from any AI model/provider (PR #470): an agent is just a name + a strategy, and `AgentVersion.model` is legacy/NULL. PR #569 added a payload guard (`app/engine/model_provider_match.py:model_for_provider`) so a seat never runs a model belonging to a different provider, backfilled away leftover legacy models (migration 0043), and fixed a `codex exec resume` connector bug.

Today the model a seat runs is decided implicitly:

- **Machine connection** (the always-on connector daemon, `scripts/agentludum_connector.py`): the connector picks a hardcoded per-provider default (`claude-haiku-4-5`, `gpt-5.4-mini`, `gemini-3-flash-preview` — `_resolve`/adapter `default_model`) unless launched with a connector-wide `--model` flag.
- **MCP connection** (the AI client plays itself): the model is whatever that client runs; the server cannot set it.

The user has no supported way to choose a specific model per agent, and when a model *can't* run on their machine the failure is invisible — it surfaces only as a `[FALLBACK]` HOARD with the reason in stderr (`is_connector_fallback` is a bare boolean on the move; the reason never reaches the server). This is the same silent-failure class that made the PR #569 bug hard to find.

## Design decisions already made (discovery)

Settled with the operator before this spec; do not relitigate:

1. **The join/seat page stays provider-only.** No model picker is added there.
2. **The model decision moves server-side** as a three-layer fallback (below). The connector's hardcoded defaults become a true last resort.
3. **The per-agent model is optional and advanced**, set on the agent settings page, guarded by `model_for_provider` (applied only when it matches the seat's chosen provider).
4. **MCP is display-only for model** — the client decides; we show "runs your client's model."
5. **Fail-fast, not fail-soft.** Only the local connector can know whether a model runs on the user's CLI login, so the connector tests it and reports; the website surfaces it. A model that can't run is shown as an actionable error — never silently swapped for a different model.
6. **Fail-loud at play time too** — a model failure during a real turn surfaces its reason on connection/agent status and tags the fallback move.

## Model resolution (server-driven, three layers)

**This feature REPLACES the current model source.** Today the payload model is built at `app/engine/agent_play_next_turn.py:503` as `model_for_provider(player.chosen_provider, version.model)` — sourced from the legacy `AgentVersion.model`. That `version.model` input is **removed** from resolution; `Agent.preferred_model` takes its place. No hidden fourth layer survives.

For a machine-connection turn the server resolves the payload `model`, in order:

1. **Per-agent preferred model** (`Agent.preferred_model`), if set AND `model_for_provider(chosen_provider, preferred)` keeps it (belongs to the seat's chosen provider). **Resolution does NOT consult verification status** — a verified-failing model is handled by the join guard (FR-014) and the UI status (FR-007), never by silently swapping it for the default here. (This is what keeps FR-008's "never silently substitute" true: a chosen, provider-matching model is always what's sent, so resolution is stable turn-to-turn for a fixed preferred model; if it can't actually run, the turn fails *loud* per FR-009, it is not quietly replaced.)
2. else the **server per-provider default** for the seat's provider (only for providers with a non-empty `PROVIDER_MODELS` allowlist).
3. else **None** — the connector falls back to its own built-in default. To keep layer 3 a true last resort (and stop the connector default from silently drifting from `PROVIDER_MODELS`), the server MUST always send a concrete model for any provider with a non-empty allowlist; the connector default applies only when the server sent nothing (old server / unknown provider).

This is exactly `resolve_seat_model` (already shipped in slice 1): preferred-if-it-matches → provider default → None. Slice 2+ adds verification *status* on top, surfaced in the UI and the join guard — it does not change resolution.

**The connector `--model` flag is demoted.** Today `--model` (a connector-wide CLI flag) wins over the payload for *every* agent (`_resolve`, `agentludum_connector.py:787`), which would silently defeat per-agent selection and the verified-failing guard. After this feature, the server-resolved payload model is authoritative; `--model` only supplies the connector's local default when the server sent nothing (it moves below the payload in `_resolve`). See *Open decisions* — this is the recommended default.

**Provider-mismatch and unset are *expected* states** that fall through quietly (layers 2–3). **A verified-failing chosen model is NOT one of these** — it is surfaced as an error to fix, and the seat is guarded against it (below). **"Never silently substitute"** means: the connector never quietly runs a *different* model in place of the operator's chosen one and presents it as if it were the choice. MCP turns ignore all of this; the client's model is used.

## Reporting channels (new transport — not a reuse of the existing one)

The current self-report (`/api/agent/report-pid`: pid + hostname + detected_providers, fire-and-forget 204) and the move submit/message bodies (`is_connector_fallback` boolean only) **cannot** carry this feature. Two concrete additions are required, and both must be a **dedicated verification endpoint**, NOT a field on the turn poll — because when a connection has no live turn the connector takes an early `sleep; continue` and discards the poll body (`agentludum_connector.py` idle branch), exactly the pre-match state US3 must cover:

- **Down-channel (server → connector): the verification worklist.** A new endpoint the connector calls on a **dedicated short verification cadence (~60s when idle)** — NOT the 300s `_DETECT_REPORT_INTERVAL` PID-report hook — returning the set of `(provider, model)` pairs to verify for its connection, independent of any live turn. The short cadence is what makes SC-001's ~60s target reachable.
- **Up-channel (connector → server): verification results + play-time failure reasons.** A new endpoint carrying, per `(provider, model)`: outcome (verified / failed / timeout) and the (bounded, sanitized) error text. The **play-time failure reason MUST travel on this up-channel, not on the submit body**, because a turn that misses its deadline returns without submitting at all (`_decide` returns `None` → no POST) — a reason attached to the submit would never leave the machine in exactly the hang-caused-the-miss case. This is distinct from the existing `is_connector_fallback` flag, which says *that* a fallback happened, not *why*.

The plan picks exact paths/shapes; the spec's requirement is two dedicated channels carrying the named data, independent of the turn poll.

## User stories

### User Story 1 — Choose a model for an agent (Priority: P1)

As a bot operator, I want to optionally set a specific AI model for one of my agents, so I can run a chosen strategy on a model I pick (e.g. compare the same strategy on Haiku vs Opus) instead of always getting the provider default.

**Independent test**: On an agent's settings page, set a preferred model and save; confirm it persists and is shown; confirm the join page is unchanged (provider-only).

**Acceptance scenarios**:
1. **Given** an agent with no preferred model, **When** the operator opens its settings page, **Then** they see an optional "Preferred model (advanced)" control listing only models from `PROVIDER_MODELS`, defaulting to "Provider default."
2. **Given** the operator selects `claude-opus-4-8` and saves, **When** they reload, **Then** the selection persists, labeled "used by machine connections only; ignored by MCP."
3. **Given** any agent, **When** the operator visits the join page, **Then** no model picker appears.

### User Story 2 — The chosen model actually runs on a machine connection (Priority: P1)

As a bot operator, when I run that agent through my machine connection on the matching provider, I want the connector to run my chosen model — and fall through to a sensible default when my choice doesn't apply — so my selection takes effect without breaking play.

**Independent test**: With a verified preferred model, seat the agent on the matching provider; confirm the connector invokes that model. Seat on a non-matching provider; confirm provider default, no error.

**Acceptance scenarios**:
1. **Given** preferred model `claude-opus-4-8` seated as Claude (verified), **When** the connector plays, **Then** it runs the Claude CLI with `--model claude-opus-4-8`.
2. **Given** that agent seated as OpenAI, **When** the connector plays, **Then** the preferred model is ignored and the server's OpenAI default is used (no 404, no dead turn).
3. **Given** no preferred model, **When** the connector plays, **Then** the server's per-provider default is used; the connector built-in default applies only if the server sent no model.

### User Story 3 — Fail fast: know a model can't run before it matters (Priority: P1)

As a bot operator, I want to be told — at setup, in the UI, with an actionable reason — when a model I picked can't run on my machine, so I can fix it before a real match is affected.

**Independent test**: Set a model the local login can't run; confirm the connector reports failure and the UI shows ❌ with the real error. Set a runnable model; confirm ✅ verified.

**Acceptance scenarios**:
1. **Given** the operator sets a preferred model and a live connector hasn't verified it yet, **When** they view its status, **Then** it shows "⏳ checking on your connector."
2. **Given** the connector's test call succeeds, **When** it reports back, **Then** the status shows "✅ verified."
3. **Given** the connector's test call fails, **When** it reports back, **Then** the status shows "❌ can't run: <reason>" with guidance, and the system does not substitute a different model.
4. **Given** a verified result and no change, **When** the connector polls again, **Then** it does not re-test; it re-tests only on a model change or on the periodic refresh interval (FR-016).
5. **Given** no connector has ever polled this connection, **When** the operator views status, **Then** it shows "waiting for your connector" — distinct from "checking" — and never implies success.

### User Story 4 — Fail loud at play time (Priority: P2)

As a bot operator, if a model breaks mid-game, I want the dead turn to carry a visible reason rather than a silent HOARD, so I can tell a real decision from a failure.

**Acceptance scenarios**:
1. **Given** a model that fails during a live turn, **When** the connector submits the forced fallback, **Then** the failure reason reaches connection/agent status (not only stderr), and that model's verification flips to ❌ failed.
2. **Given** that fallback, **When** the operator views status, **Then** the move is distinguishable from a deliberate HOARD.

### User Story 5 — See what model is actually running (Priority: P2)

As a bot operator setting up a seat, I want to see which model will run for each play path, so the model in use is never a mystery.

**Acceptance scenarios**:
1. **Given** a machine-connection seat, **When** the operator views the setup surface, **Then** it shows the effective model (e.g. "runs claude-opus-4-8"); when the server sent no model (layer 3), it shows the provider's default name, not a blank.
2. **Given** an MCP seat, **When** the operator views the setup surface, **Then** it shows "runs your client's model."

## Functional requirements

- **FR-001**: Allow an operator to set/clear an optional preferred model per agent, chosen only from `PROVIDER_MODELS` (`app/config.py`). Default = none ("provider default"). (US1)
- **FR-002**: Store the preferred model on a mutable per-agent field (`Agent.preferred_model`), NOT a new immutable `AgentVersion`. (US1)
- **FR-003**: Resolve the payload model via the three-layer fallback in *Model resolution*, sourced from `Agent.preferred_model`; the legacy `version.model` input is removed, and the connector `--model` flag is demoted below the payload. (US2)
- **FR-004**: Define a per-provider default model derived from `PROVIDER_MODELS`, and send it in the payload for every provider **with a non-empty allowlist**. Providers with an empty allowlist (hermes, openclaw — MCP-only) send no server default. (US2)
- **FR-005**: The connector MUST verify a `(provider, model)` it would run with a cheap, low-token test call against the user's CLI login, driven by the down-channel worklist, independent of whether an agent currently has a live turn. The verification call MUST use its own short timeout (default ~30s, not the 180s turn ceiling) and MUST run in a path isolated from live turns — it must not consume a live-turn concurrency slot or burn a turn's deadline. **Success predicate**: `verified` = the test call exits 0 with non-empty output (the *runnability* check — deliberately looser than the move-parse path, so a model that runs but returns non-JSON still counts as runnable); a clean model-unavailable/unauthorized error = `failed`; a timeout/transport/PATH error = `timeout`. (US3)
- **FR-006**: The connector MUST report each verification outcome plus bounded error text via the dedicated up-channel endpoint; the server caches it keyed by `(connection, provider, model)`. (US3)
- **FR-007**: Show per-model verification status — checking / verified / failed-with-reason / timeout — wherever a preferred model is chosen, plus a distinct "waiting for your connector" when no connector has reported. (US3)
- **FR-008**: Never silently run a *different* model in place of a verified-failing chosen model. A verified-failing preferred model is surfaced as an error; the seat is guarded (FR-014). The layer-2/3 fallback applies only to provider-mismatch and unset (expected states), never to mask a verified-failing choice. (US3)
- **FR-009**: On a model failure during a live turn, the connector MUST send the failure reason on the **up-channel** (not the submit body, which a missed-deadline turn never sends) so it appears on connection/agent status, MUST tag any forced fallback move, AND MUST update that `(provider, model)`'s cached status. (US4)
- **FR-009a**: The connector MUST classify a play-time failure from the only signals it has (exit code + stderr text), with a conservative default. Map to sticky **failed** only when stderr clearly indicates model-unavailable/unauthorized (matches patterns like "model", "not found", "404", "unauthorized", "not available", "no access"). Map to **timeout/retryable** for: `TimeoutExpired`, CLI-missing-from-PATH (`FileNotFoundError`/exit 127), network errors, and — **the default for any unclassifiable error** (non-JSON output, generic non-zero exit, parse failures) — so a blip or an odd-but-runnable response is never reported as a permanent "can't run." A later successful verification supersedes a prior failed/timeout. (US4)
- **FR-010**: Setup surfaces MUST display the effective model read-only via a **new** value (do NOT reuse `Player.model_self_report`, which today stores the *provider* and feeds the public "played by" badge) — the resolved model for machine seats (the provider default name when layer 3 applies; for empty-allowlist machine seats, the provider's own default label), and "your client's model" for MCP seats. (US5)
- **FR-011**: The join/seat page MUST remain provider-only. (US1)
- **FR-012**: The preferred-model control MUST be labeled advanced and "used by machine connections only; ignored by MCP." (US1, US5)
- **FR-013**: The verification status enum MUST include a distinct **timeout/retryable** value, separate from a clean **failed**; timeout is retried with a bound — after N consecutive timeouts (default 3) it is shown as failed so it never sits in a silent retry loop. (US3, edge)
- **FR-014**: When an agent has a preferred model, the join flow MUST **warn** (not hard-block) only when that model is **verified-failing on every** live machine connection covering the chosen provider — i.e. at least one such connection reports it `failed` and none reports it `verified`. A not-yet-checked model (`unknown`/`checking`) MUST NOT warn, so a freshly-set model never cries wolf. MCP and paused connections are excluded from this union. Because the serving connection is not known at join and a user may have several machine connections, the guard reads the union of the user's live machine connections' `(connection, provider, model)` statuses (a new read path the join context must gain), not a single connection. No model picker is added. (US3) (See *Open decisions*.)
- **FR-015**: Error text shown in the UI MUST be length-bounded and sanitized — concretely, capped at 300 characters and stripped of absolute filesystem paths and token-shaped substrings (e.g. `sk_…`, bearer tokens) — while preserving enough of the message to be diagnostic. (US3, security)
- **FR-016**: Verification results MUST carry a last-checked timestamp; the connector MUST re-verify on a defined periodic interval (default: every 6 hours) and whenever the model set changes, so a stale "verified" cannot persist indefinitely after a login silently expires. (US3, anti-stale)
- **FR-017**: If a preferred model is later removed from `PROVIDER_MODELS` (deprecated), the system MUST treat the agent as unset (fall to provider default), clear any stale verified status for it, and show a notice on the agent settings page. (edge)
- **FR-018**: If a model's cached status flips to failed/timeout *during* a match the agent is already seated in, that match keeps playing with clearly-tagged fallback moves (per FR-009) — the running seat is not pulled — and the failure is surfaced so the operator can fix it for future matches. (edge)

## Key entities

- **Per-agent preferred model** — a nullable, mutable `preferred_model` on `Agent` (independent of the versioned strategy). NULL = "provider default."
- **Model verification result** — keyed by **(connection, provider, model)** (not per-agent: a login either can or cannot run a model regardless of which agent uses it; agents sharing a model share the result). Fields: status (`unknown` / `checking` / `verified` / `failed` / `timeout`), bounded error text, last-checked timestamp. Stored in a **new store** (e.g. a `model_verifications` table), NOT the `connection_providers` row, which is unique per `(connection, provider)` and cannot hold multiple models.
- **Server per-provider default model** — provider → default model, sourced from `PROVIDER_MODELS` (first entry unless the plan defines an explicit map); absent for empty-allowlist providers.

## Out of scope / non-goals

- Any model picker on the join/seat page (provider-only stays).
- Setting/controlling the model for MCP connections (the client decides; we only display it).
- Making the per-agent model mandatory or re-coupling agents to a required model.
- Per-turn or per-match model overrides (the model is per-agent).
- Letting operators choose model names outside `PROVIDER_MODELS`.

## Edge cases

- **Preferred model deprecated from `PROVIDER_MODELS`** → treat as unset, clear stale verified, show a notice (FR-017).
- **MCP-only operator (no connector)** → preferred-model field is moot; status shows "waiting for your connector," never success (FR-007).
- **Model changed mid-match** → connector picks up the new model on its next turn; do not crash the in-flight chained session (verification test runs in a path isolated from the live session).
- **Connector live but not yet verified** vs **no connector polling** → distinct UI states (FR-007); "checking" is bounded by the poll cycle, not indefinite (SC-001).
- **Verification call times out** (vs clean "not available") → `timeout` status, retried (FR-013), not a permanent ❌.
- **Empty-allowlist provider** (hermes/openclaw) → no model control, no server default (FR-004).
- **Verified-failing model seated anyway** (stale verification) → fail loud: tagged fallback + reason surfaced + status flipped to failed (FR-009); never a silent different-model substitution.
- **Two agents, same preferred model, same provider** → share one verification result; "one AI = one game" still governs seating.

## Acceptance criteria (feature-level)

1. Operator can set/clear a preferred model per agent from `PROVIDER_MODELS`; the join page is unchanged.
2. The resolved machine-connection model follows the three-layer fallback; a provider-mismatched model never reaches the CLI; the server always sends a model for non-empty-allowlist providers.
3. The connector verifies models from a worklist (no live turn required) and reports verified/failed/timeout + bounded reason; the UI shows all four states plus "waiting for your connector."
4. Verification is cached, re-tested on change and every 6h, and a stale verified is cleared on any play-time failure.
5. A verified-failing model is never silently swapped; the operator sees an actionable error and the seat is guarded at join (FR-014).
6. A live-turn model failure surfaces its reason on status, tags the fallback, and flips verification to failed.
7. MCP seats show "runs your client's model" and ignore the preferred model.
8. UI reason text is bounded and sanitized.
9. Preflight Gate (ruff + mypy + pytest) green; new `app/engine` logic has tests.

## Success criteria

- **SC-001**: When a connector is live and polling, a newly set model reaches a definitive verified/failed/timeout state within **~60 seconds** of the connector's next verification-cadence tick (a wall-clock target, since poll cycles vary); the UI never shows "checking" indefinitely while a connector is live.
- **SC-002**: A model the login can't run produces zero *silent* dead-turns — caught at setup (US3) or, if it slips to play, every affected turn carries a visible reason and flips the status (US4).
- **SC-003**: Operators who never set a preferred model see no behavior change and no new required steps.
- **SC-004**: No model that mismatches a seat's provider is ever passed to that provider's CLI.
- **SC-005**: A login that silently expires after a prior ✅ is re-flagged within the refresh interval (FR-016) or on the next failed turn — a stale ✅ cannot mask a broken login forever.

## Assumptions carried into the plan

- **Storage**: nullable `Agent.preferred_model` (mutable); a new `model_verifications` store keyed by `(connection, provider, model)`. Plan confirms exact tables/migrations.
- **Down/up channels**: plan picks exact endpoint/field shapes for the verification worklist (down) and results + play-time reason (up); both are new schema. The effective-model display (FR-010) uses a **new** value, NOT `Player.model_self_report` (which is unused/`None` at join; the public "played by" badge reads `Player.played_provider`). The legacy payload fields `preferred_model`/`preferred_provider` are not consulted by the new resolution and are treated as retired.
- **UI home**: agent settings page hosts the control + status; connections page may mirror per-provider model status. Join page untouched.
- **Server default**: first entry of each non-empty `PROVIDER_MODELS` list unless the plan defines an explicit map.
- **Verification test call**: a minimal one-token prompt per provider CLI (e.g. `claude --model <m> --print "ok"`), run in a path isolated from any live chained session, cached by `(connection, provider, model)`, refreshed on change and every 6h.

## Open decisions for the operator

Resolved here with recommended defaults; flagged because they are genuine product/architecture choices the reviews surfaced:

1. **Connector `--model` flag precedence.** *Recommended (encoded above):* the server-resolved per-agent/default model wins; `--model` is demoted to a local fallback only when the server sends nothing. Alternative: keep `--model` as an explicit operator override that beats the server (simpler, but it silently defeats per-agent selection and the guard). 
2. **Join guard: warn vs hard-block, across multiple connections.** *Recommended (encoded above):* **warn** if the model isn't verified-runnable on any of the user's live machine connections for that provider; do not hard-block (a hard block is brittle when a user has several connections and the serving one isn't known until claim time). Alternative: hard-block when no live connection can run it.
3. **Two machines, one connection, different logins.** Verification is keyed `(connection, provider, model)` with no machine dimension, so two laptops sharing a connection key but with different model access would overwrite each other's result (last-writer-wins). *Recommended:* accept for now (document it); revisit if multi-machine connections become common.

## Review reconciliation (spec checkpoint)

Spec adversarial reviews ran in two rounds. The Gemini (`requirements`) CLI is **dead on this machine** (deprecated for individual accounts — `IneligibleTierError`), so the run uses the repo's Claude-reviewer path (spec 020) for the Gemini-equivalent lens throughout. **Round 1:** Codex (`feasibility`) + a Claude `requirements` subagent. **Round 2:** Claude `feasibility` + `requirements` subagents on the revised spec.

Round-2 findings addressed: removed `version.model` from resolution (*Model resolution*, FR-003); committed the verification channels to a dedicated endpoint independent of the turn poll and routed the play-time reason on the up-channel, not the submit (*Reporting channels*, FR-009); demoted the connector `--model` flag (*Model resolution*, FR-003); resolved the multi-connection join guard to a warning over live connections (FR-014); replaced the `model_self_report` reuse with a new display value (FR-010); added a verification timeout/isolation budget (FR-005), failure classification (FR-009a), timeout-retry bound (FR-013), in-flight-match behavior (FR-018), and a wall-clock SC-001.

Round-1 findings addressed: 

- **Reporting transport can't be reused as-is (Codex HIGH, Claude HIGH×2)** → new *Reporting channels* section; FR-005/FR-006/FR-009 now require explicit down/up channels and separate the failure *reason* from the existing `is_connector_fallback` flag.
- **Verification record dimensionality (Claude HIGH)** → resolved: keyed `(connection, provider, model)` in a new store, not the per-provider row (*Key entities*).
- **Connector-default drift (Codex MEDIUM)** → FR-003/FR-004: server always sends a model for non-empty-allowlist providers; connector default is last-resort only.
- **Verified-failing runtime behavior (Claude MEDIUM)** → FR-008/FR-009/FR-014 + *Model resolution*: guard at join, fail loud at play, never substitute.
- **Stale verified = silent-failure recurrence (Claude MEDIUM, top residual risk)** → FR-016 (6h refresh + clear-on-failure) + SC-005.
- **Unbounded "checking" / timing (Claude MEDIUM)** → SC-001 (2 poll cycles) + FR-007 "waiting for your connector" state.
- **Timeout vs failed (Claude MEDIUM)** → FR-013 adds a `timeout` status.
- **`model_self_report` reuse (Claude MEDIUM)** → FR-010 reuses it for display (reuse audit will confirm at plan stage).
- **Reason text safety (Claude LOW)** → FR-015 bound + sanitize.
- **FR-004 vs empty allowlist (Claude LOW)** → FR-004 excludes hermes/openclaw.
- **Deprecated preferred model (Claude LOW)** → FR-017.
