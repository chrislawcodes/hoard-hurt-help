# Plan — Admin Engagement Dashboard + Signup-Source Capture

Implementation plan for `spec.md` revision 4. The spec holds the *why* and the
decisions (D1–D14); this holds the *how*: concrete shapes, build order, and what
each slice must prove before the next starts.

**Delivery path:** full Feature Factory (Chris's call, reaffirmed after the spec
stage). Eight slices, each with its own diff checkpoint.

---

## Architecture at a glance

Three independent pieces that meet only at the read models:

```
  event happens ──► recorder ──► user_milestones (durable, append-only)
                                          │
  visitor lands ──► middleware ──► session cookie ──► users.first_* columns
                                          │                    │
                                          └──────► read models ◄┘
                                                        │
                                              /admin/engagement
```

The recorder and the capture middleware never read each other. The page is the
only consumer of both.

---

## Data model

### New table `user_milestones`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | int FK → `users.id`, `ondelete="CASCADE"` | indexed |
| `milestone` | `String(32)` | `FlexibleEnumType`, matching the repo's existing enum handling |
| `reached_at` | `DateTime(timezone=True)` | UTC. Every windowed count depends on this |
| `agent_kind` | `String(16)` nullable | captured at `set_up_a_way_to_play`; the `agents` row is hard-deleted, so the AC7 denominator is unrecoverable otherwise |
| `source_match_id` | `String(32)` nullable | captured at `joined_match` / `played_turn`; enables read-time smoke-test exclusion (AC17) |

`UniqueConstraint(user_id, milestone)` — the idempotency guarantee.
Index on `(milestone, reached_at)` — every page query filters that way.

**Milestone values:** `signed_up`, `picked_handle`, `set_up_a_way_to_play`,
`ai_connected`, `joined_match`, `played_turn`. Note `returned` is deliberately
**absent** — it is derived at read time (spec D1).

### New columns on `users`

`first_utm_source(120)`, `first_utm_medium(120)`, `first_utm_campaign(120)`,
`first_referrer_host(255)`, `first_landing_path(255)` — all nullable.
`first_source_channel(16)` nullable — holds `"mcp"`, kept out of the UTM columns
so a real `?utm_source=mcp` cannot collide.
`is_internal` — `Boolean`, `nullable=False`, `server_default=false`.

### New config keys

`FIRST_TOUCH_CAPTURE_ENABLED: bool = False` — the deploy gate (spec D8).
`INTERNAL_EMAIL_DOMAINS: str = "agentludum.local,house.local,local.test"` —
parsed to a set, exactly like the existing `platform_admin_emails_set` property.
`dev@localhost` is handled by `dev_login.py` always flagging internal, not by the
domain list (a round-3 finding: it is an address, not a domain).

---

## The two mechanisms that need care

### 1. The recorder — `app/identity/milestones.py`

```python
async def record_milestone(db, user_id, milestone, *, reached_at=None,
                           agent_kind=None, source_match_id=None) -> None:
    """Record a milestone once. Advisory: never raises to the caller."""
    try:
        async with db.begin_nested():          # savepoint — the ONLY safe shape
            db.add(UserMilestone(...))
    except IntegrityError:
        pass          # already recorded; the unique constraint is the guard
    except SQLAlchemyError:
        logger.exception(...)   # fail-open: advisory only
```

Why the savepoint is mandatory, not stylistic (spec D3a, verified empirically in
review): `db.add` alone surfaces the `IntegrityError` at the *caller's* commit,
and `add` + `flush` inside a `try` leaves the session in `PendingRollbackError`.
Either would let a failed analytics write break the user action that triggered it.

Catching `IntegrityError` **inside** the savepoint is what lets one code path work
on both SQLite (dev, tests) and Postgres (prod) — there is no dialect-agnostic
"insert or ignore" in SQLAlchemy, and this repo has never used one.

### 2. The listeners — `app/identity/milestone_listeners.py`

Row-creation milestones come from SQLAlchemy `after_insert` on `User`, `Agent`,
`Player`, registered once at app startup. This replaces the hand-written call-site
list that was wrong in all three review rounds.

`after_insert` fires inside the flush, where async DB work is not allowed, so the
listener **collects** into `session.info` and a matching `after_flush_postexec` /
`after_commit` hook performs the writes. This is the one genuinely fiddly bit of
the feature and slice 2 exists to prove it.

Mapping:

| Model inserted | Milestone | Extra captured |
|---|---|---|
| `User` | `signed_up` | — |
| `Agent` where `kind in (ai, human)` | `set_up_a_way_to_play` | `agent_kind` |
| `Player` | `joined_match` | `source_match_id` |

Bots (`kind='bot'`) record nothing.

**Stated limit:** `after_insert` does not fire for Core bulk inserts. No current
code creates these rows that way; slice 2 asserts the events fire for all three
models so a future bulk insert breaks a test rather than the dashboard.

### Explicit recorder calls (field updates, not inserts)

| Milestone | Sites |
|---|---|
| `picked_handle` | `handle_web.py`, `dev_login.py` |
| `ai_connected` | `connection_activity.mark_seen` (web/runner choke point) + the four `mcp_connection.py` branches at 145/174/206/222 |
| `played_turn` | two request-level hooks — `record_player_action` and `record_submission` are each shared by genuine and non-genuine paths, so the hook sits at the request level where the caller is known |

---

## Build order

Each slice ends green (`ruff`, `mypy`, `pytest`) and gets a diff checkpoint.

| # | Slice | Depends on | Must prove |
|---|---|---|---|
| 1 | Models + schema migration (no backfill) | — | `upgrade`/`downgrade` clean on SQLite; existing users read NULL |
| 2 | Recorder + ORM listeners | 1 | idempotent; savepoint isolates failure; listeners fire for all three models; a forced `IntegrityError` does not break the caller's transaction |
| 3 | Explicit recorders (handle, connect, play) | 2 | **human path records play** (AC4); **MCP-connect-before-agent records both** (AC5); autopilot, defaulted, connector-fallback and null-timestamp rows record nothing |
| 4 | Internal-account predicate + creation wiring + `is_internal` backfill | 1 | survives email rewrite and promote/demote; backfill and creation rule agree on one fixture set |
| 5 | First-touch capture behind the flag | 1 | **flag off ⇒ no cookie, nothing stored** (AC0); flag on ⇒ survives navigation + OAuth; no overwrite; truncated at capture; cleared on sign-out; MCP records channel |
| 6 | Milestone backfill migration | 1,4 | reconstructs from surviving rows; **excludes autopilot** via `players.autopilot_at` |
| 7 | Read models | 1 | distinct users; shares suppressed below 20; return detection in the window timezone; smoke-test matches excluded; empty DB renders |
| 8 | Page, nav, admin toggle | 7 | 401 anonymous / 403 non-admin; toggle works both ways and audit-logs; renders for a floor admin; `colspan` 7→8 |

**Slice 5 is independently shippable and inert by default.** If anything later
goes wrong, the flag stays off and nothing about the live site changes.

---

## Test strategy

Fast lane (`pytest -m "not integration"`) must stay green throughout; CI runs the
full suite.

**Highest-value tests, by risk** — these map to the findings that cost the most to
discover:

1. Human player reaches `played_turn` (round 2's decisive finding; the default
   onboarding path was invisible to revisions 1–2).
2. MCP user connecting before building an agent records both, in either order.
3. Milestone survives each of the five deletion sites: setup GC, seat release,
   agent hard-delete, match delete, handle reset.
4. A failing milestone write leaves the caller's transaction usable (round 3's
   empirical finding).
5. Flag off ⇒ no cookie set (the deploy gate).
6. Autopilot rows excluded in **both** live recording and backfill, so no step
   change at the deploy date.
7. One user with three agents and many player rows counts once.

**Fixtures:** a `make_user(internal=False)` helper, and a match factory that can
produce genuine, defaulted, autopilot, and connector-fallback submissions — the
four cases D5 distinguishes.

---

## Risks carried into implementation

| Risk | Mitigation |
|---|---|
| `after_insert` cannot do async work | Collect in the listener, write in `after_commit`; slice 2 exists to prove this shape before anything depends on it |
| Backfill volume in one migration | Dev DB has 1,648 matches / 20,276 submissions; batch the UPDATE and measure before prod. Railway runs it as `preDeployCommand` while old code still serves, so it must be additive-only |
| Two admin pages disagreeing | `/admin/reports` also filters `state == COMPLETED` and windows on `completed_at`; the engagement page states its own window explicitly rather than claiming parity |
| Extra write on the turn-submission hot path | Only the *first* genuine play writes; the unique constraint short-circuits the rest. Measure in slice 3 |
| Scope | 8 slices, ~26 files. If it needs splitting, slices 1–6 (data) and 7–8 (page) are the natural seam |

## Open item, not blocking implementation

The privacy note for the persistent cookie (spec D8). Implementation proceeds
with the flag off; **turning it on is Chris's separate decision** once the site
has a disclosure. Raised again at the PR, and the flag is what makes that raise
meaningful rather than decorative.

---

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: Both MEDIUM findings accepted and fixed in the spec revision. (1) Mutable email: D8 rewritten to store users.is_internal at account creation instead of matching email live, plus an admin toggle to correct a wrong flag. (2) Distinct-user rule: added D9 requiring COUNT(DISTINCT users.id) everywhere, with a multi-agent regression test.
- review: reviews/spec.claude.requirements-adversarial.review.md | status: accepted | note: Round 3. 19 findings (7 HIGH), all code-confirmed. Decisive: D8's privacy obligation was honest but had NO GATE - docs/deploy-railway.md:87 confirms push to main auto-deploys, so 'raised at the PR' and 'shipped to production' are the same instant. Fixed by shipping first-touch capture behind FIRST_TOUCH_CAPTURE_ENABLED, default false, with AC0 and a test asserting no cookie and no storage when off. Also fixed: the AC2-vs-AC16 contradiction (write-once-at-event-time vs read-time timezone) resolved by splitting durable setup milestones from read-time-derived play metrics (D1); AC1 corrected to 401-anonymous / 403-non-admin; summary numbers given their own criterion and tests; small-numbers rule added suppressing shares below 20 users, which the earlier non-goal implied but never applied.
- review: reviews/spec.claude.completeness-adversarial.review.md | status: accepted | note: Round 3. 16 findings (6 HIGH), all verified against real code. Decisive: the spec's call-site list was wrong - human agents are created at human_player.py:101 (not agents_create.py), players at 3 sites, first_connected_at at 6 including connection_activity.mark_seen which sets the field inside a values dict and is invisible to text search. Fixed STRUCTURALLY in revision 4 D3 by recording row-creation milestones from SQLAlchemy after_insert events instead of a hand-maintained call-site list. Also fixed: missing reached_at column (D1), agent_kind and source_match_id columns added so AC7 and AC17 are achievable, reset_handle added to the evidence-destruction table (D4).
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: failed | note: Gemini CLI is non-functional on this machine: IneligibleTierError, Google ended Gemini Code Assist for individuals. Not a transient failure and retry cannot succeed. Substituted with spec.claude.requirements-adversarial (same lens, Claude reviewer, spec 020 path), which ran and is reconciled as accepted.
- review: reviews/spec.gemini.completeness-adversarial.review.md | status: failed | note: Gemini CLI is non-functional on this machine: IneligibleTierError, Google ended Gemini Code Assist for individuals. Not a transient failure and retry cannot succeed. Substituted with spec.claude.completeness-adversarial (same lens, Claude reviewer, spec 020 path), which ran and is reconciled as accepted.
- review: reviews/spec.claude.feasibility-adversarial.review.md | status: accepted | note: Round 3. 16 findings (6 HIGH), all code-confirmed; the reviewer empirically ran the transaction question against this repo's SQLAlchemy rather than reasoning about it. Decisive: 'advisory, never blocking' had no mechanism - db.add alone surfaces IntegrityError at the caller's commit and add+flush leaves PendingRollbackError, so only db.begin_nested() works, and UNIQUE raises rather than no-opping with no dialect-agnostic on_conflict available. Fixed in new section D3a. Also decisive: the D4 backfill OVERCOUNTS rather than producing a floor, because autopilot rows carry was_defaulted=False and a real submitted_at - fixed via players.autopilot_at, with the over-exclusion accepted and stated.
