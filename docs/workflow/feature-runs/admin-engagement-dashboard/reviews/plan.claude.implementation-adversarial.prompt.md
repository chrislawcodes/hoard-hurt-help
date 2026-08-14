Review this plan artifact using a implementation-adversarial lens.
Stay scoped to that lens.
Approach the artifact adversarially: look for hidden flaws, omitted cases, and weak assumptions before giving credit.
No code context files were provided. Flag any finding that depends on an assumption about the existing codebase as [UNVERIFIED] and limit it to MEDIUM severity or lower.
The full review artifact text is included below in this prompt.
Output length is limited and a response can be cut off before it finishes. Emit the required structured output first — the "## Findings" section, the "## Residual Risks" section, and the fenced findings JSON block — before any exploratory narration or extended analysis. Do not spend your early output investigating the artifact in prose and save the required format for last: a response cut off mid-investigation must still contain parseable findings. Any deeper supporting analysis you want to add can follow after the required sections and JSON block are complete.
Return markdown using exactly these sections:
## Findings
## Residual Risks
Keep the response concrete and ordered by severity.
End your review with exactly one fenced JSON block — the machine-readable findings summary:
```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "<short title>", "detail": "<one-sentence detail>"}]}
```
Severity must be one of: CRITICAL, HIGH, MEDIUM, LOW. Include one entry per finding in your "## Findings" section.
If you found no issues, the block must be the affirmative clean bill exactly: {"reviewed": true, "findings": []}
This JSON block is required, is machine-parsed, and must be the last thing in your response.

Artifact: plan.md
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


Return only markdown with exactly these sections:
## Findings
## Residual Risks
Do not include any other sections. After the Residual Risks section, end with the required fenced findings JSON block described above.