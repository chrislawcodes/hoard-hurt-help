# Completeness-Adversarial Review — plan.md (Revision 2, Round 2)

Round-1 findings are not re-argued. This round asks three things only: did
revision 2's fixes land on **every** consumer, did they introduce new gaps, and
does the plan still agree with the spec and `state.json`.

Short answer: the fixes are real but **one-sided**. Revision 2 changed the plan
and changed nothing else. The spec, `state.json` and `scope.json` all still carry
revision 1's design, and two of the new items (the milestone split, the
`first_source_channel` column) got a write side with no read side.

## Findings

### HIGH 1 — The spec was never updated: `set_up_a_way_to_play` and `agent_kind` are still its design [CODE-CONFIRMED]

The plan drops both. The spec keeps both, in four places:

| Location | What it still says |
|---|---|
| `spec.md:75` (D1 milestone table) | `set_up_a_way_to_play` — "first `Agent` of kind `ai` **or** `human`" |
| `spec.md:96` (D1 column table) | `agent_kind` (nullable), with the rationale "the `agents` row is hard-deleted, so the denominator **must** be captured here or it is unrecoverable" |
| `spec.md:102` | the human-path rationale, phrased in terms of the single milestone |
| `spec.md:433` (AC4) | "counted at `set_up_a_way_to_play`, `joined_match`, `played_turn` and `returned`" |

Plus `state.json`'s AC4, which repeats the old name verbatim.

Three consequences, in order of cost:

1. **AC4 is now unsatisfiable as written.** It names a milestone value the plan
   will never write. An implementer who tests AC4 literally writes a test for a
   value that does not exist; one who tests it loosely writes no test at all.
2. **`spec.md:96` states a requirement the plan refuses.** It says the AI
   denominator is *unrecoverable* without an `agent_kind` column. The plan's
   answer — capture it as a separate milestone at insert time — is sound, but it
   lives only in the plan. The spec still instructs the opposite.
3. **The spec is the artifact the tasks and implement stages read.** The plan is
   currently the only place the new design exists.

Revision 2's changelog entry (`plan.md:291`) records the split as accepted, so
this is a propagation failure, not a disagreement.

### HIGH 2 — The plan's own risk table still prescribes the `after_commit` shape the plan spends a section refuting [CODE-CONFIRMED]

`plan.md:266`:

> | `after_insert` cannot do async work | Collect in the listener, write in **`after_commit`**; slice 2 exists to prove this shape before anything depends on it |

`plan.md:88` and `plan.md:95-97` say the opposite, in bold, with a measured
result: `after_commit` "Raises `InvalidRequestError: session is in 'committed'
state`, persists **0 rows** — and because that is a `SQLAlchemyError`, the
fail-open handler swallows it."

The risk row survived the rewrite untouched. Both halves of it are now wrong:
the mechanism is refuted, and "slice 2 exists to prove this shape" is false —
slice 2 now proves a different shape (`after_flush_postexec` + a connection-level
savepoint).

This is the most dangerous leftover in the document. The risk table is the
compact "what could go wrong / what we do about it" summary — the part that gets
lifted into a slice prompt or skimmed by someone who did not read the mechanism
section. It currently instructs the reader to build the exact silent-zero-rows
failure the plan spent four paragraphs and an executed experiment establishing.

### HIGH 3 — "Dry run against a production copy" has no procedure in this repo, and the only documented prod-DB access is the live database [CODE-CONFIRMED]

Revision 2's correction is right, and its gate is the sole mitigation for two
irreversible backfills that run as `preDeployCommand` on merge. But the gate is
an instruction with no method:

- Searched `docs/` and `MEMORY.md`: **no** `pg_dump`, no restore step, no
  snapshot-to-scratch-DB procedure, nothing that produces a production copy.
- `docs/deploy-railway.md:86` is the only backup mention in the whole doc set:
  "Postgres backups: Railway → *Database* → *Backups*." That is a backup
  existing, not a documented way to get one onto a machine and run a migration
  against it.
- The one documented way into prod data is `docs/operations/debugging-history.md:20`
  — "Prod Postgres is reachable via `DATABASE_PUBLIC_URL`". That is the **live**
  database. An implementer told to "dry run against a production copy", who then
  greps the docs for how to reach prod data, finds a direct connection to
  production.

Two uncited precedents that would make it actionable:

- `scripts/preview_match_id_migration.py` — a read-only preview script whose own
  usage line is `--db copy.db --dry-run`. Exactly this shape, already in the repo.
- `docs/workflow/feature-runs/unified-connections/` shipped its migration with a
  reviewed `--dry-run` pass (`plan.md:302`, `spec.md:321`, `closeout.md:65`).

The plan requires neither a `--dry-run` mode on the migration nor a named way to
obtain the copy, so as written the gate is satisfiable by asserting it happened.

### HIGH 4 — `state.json`'s acceptance-criteria list is still the 22-item revision-3 list, and the spec calls it authoritative [CODE-CONFIRMED]

`spec.md:411`: "Authoritative list lives in `state.json`, kept in lockstep with
this revision."

It is not in lockstep. `state.json` holds 22 items; the spec holds 25
(0, 1, 1a, 1b, 2–22). Missing entirely from the declared authority:

- **AC0** — "With `FIRST_TOUCH_CAPTURE_ENABLED=false` (the default), a request
  carrying `?utm_source=` sets no cookie and stores nothing." This is D8's
  privacy gate: the decisive round-3 spec fix, and the only thing standing
  between merging and tracking real visitors on a site with no disclosure.
- **AC1a** — the three summary numbers stating window, population and internal
  filtering. The plan itself notes these "lost their tests in three consecutive
  rounds" (`plan.md:228-230`); the authoritative list is one of the places they
  keep getting lost.
- **AC1b** — shares suppressed below 20 users.

Also stale within the 22: AC1 still reads "403 for a non-admin" without the
401-anonymous correction the spec made (`spec.md:420-421`), and AC4 carries the
removed milestone name (see HIGH 1).

Round 1 found this list stale at 22 revision-3 items. It is still stale at 22
revision-3 items — revision 2 did not touch it.

### HIGH 5 — The milestone split has a write side and no read side [CODE-CONFIRMED]

Tracing every consumer of the two new values:

| Consumer | `set_up_ai_agent` | `set_up_human_play` |
|---|---|---|
| Listener mapping (`plan.md:144-145`) | written | written |
| Slice 7 must-prove (`plan.md:186`) | — | — |
| AC-to-slice table (`plan.md:232-241`) | AC7 denominator | — |
| Slice 8 page items (`plan.md:187`) | — | — |
| Backfill, slice 6 (`plan.md:185`) | — | — |

`set_up_human_play` is written by the listener and **read by nothing**. Four
concrete gaps follow:

1. **Nobody owns the union.** The plan resolves the split with one sentence —
   "'Set up a way to play' is then either of them" (`plan.md:152-153`) — and
   stops. That union is a `COUNT(DISTINCT user_id) WHERE milestone IN (...)`,
   it is what AC4 and D2's funnel row actually display, and it appears in no
   slice's must-prove. AC4 is not in the AC-to-slice table at all; the only
   trace of it is highest-value test #1, which covers the *play* half
   ("Human player reaches `played_turn`") and not the setup half.
2. **D2's page shape is now undefined.** The spec says the page presents
   milestones "in the usual order with counts and the difference between
   neighbours" (`spec.md:112-116`). Two parallel setup milestones have no
   position in a single ordered chain. Does the page show seven rows, or six
   with a merged row, or six plus an AI/human split? Neither artifact says.
3. **The stuck list's "furthest milestone" needs a total ordering.** AC18 labels
   each stuck user by how far they got. With two parallel values there is no
   total order, and slice 7's "stuck list labels, caps at 50 (AC18)" does not
   supply one. A user with `set_up_human_play` and a user with `set_up_ai_agent`
   are equally far along, and the label logic has no rule for that.
4. **The backfill names no milestone values.** Slice 6 must reconstruct these
   from `agents.kind`, and its must-prove ("reconstructs from surviving rows;
   excludes autopilot") never mentions either new value.

This is the same class of defect this lens exists to catch, and it was created by
revision 2's own fix: the value changed at the write site and stayed unchanged
everywhere it is consumed.

### MEDIUM 1 — `scope.json`, the manifest the diff checkpoints run against, does not contain the files revision 2 added [CODE-CONFIRMED]

`factory_cmd_checkpoint.py:373` flows `scope.json`'s `allowed_dirty_paths` into
the diff-stage dirty tolerance. Files outside it need a per-slice
`--allow-dirty-path`. The manifest is missing, among others:

| File | Why the plan needs it |
|---|---|
| `app/models/__init__.py` | **Slice 1's headline new item** (`plan.md:180`) — the registry line every test schema is built from |
| `app/config.py` | Both new config keys (`plan.md:69-71`) |
| `app/db.py` | Calls `install_sqlite_parity_guards()` at line 22 — the import-time registration pattern the plan says it mirrors (`plan.md:123-125`) |
| `app/engine/connection_activity.py` | `mark_seen`, the `ai_connected` choke point (`plan.md:169`) |
| `app/engine/agent_play.py` | `played_turn`, the genuine AI path (`plan.md:170`) |
| `app/routes/web_play.py` | `played_turn`, the human path (`plan.md:170`) |
| `app/routes/dev_login.py` | `picked_handle` + always-internal (`plan.md:72-73`, `:168`) |
| `app/engine/bots/seating.py` | D10's `is_internal=True` creation site |
| `.env.example` | The production gate's only discoverable surface |

Stale in the other direction too: `app/routes/web_join.py` and
`app/routes/agents_create.py` are still in scope from the pre-ORM-listener
design and now need no edit at all. `scope.json` is a revision-1 artifact.

### MEDIUM 2 — Slice 6 depends on 1 and 4 but not 3, and the plan's own test #6 spans 3 and 6 [CODE-CONFIRMED]

Applying the round-1 slice-7→4 test to every other edge, this is the one that
fails the same way.

`plan.md:185` gives slice 6 (milestone backfill) dependencies "1,4". `plan.md:223-224`
lists as a highest-value test: "Autopilot rows excluded in **both** live
recording and backfill, so no step change at the deploy date." Live recording is
slice 3. The backfill's genuineness filter must match slice 3's recorder
definition or the page shows a step change at the deploy date — precisely the
defect D4 exists to prevent (`spec.md:185-188`). As sequenced, slice 6 can be
written, checkpointed and declared green against a definition slice 3 never
agreed to.

The reverse is also suspect: nothing in the milestone backfill reads
`is_internal`, so slice 6's stated dependency on slice 4 looks like the spurious
one. The two other edges check out — slice 3→2 and slice 8→7 are both real.

### MEDIUM 3 — Slice 3 carries the proof obligation for a slice-2 behaviour [CODE-CONFIRMED]

The house-bots-user exclusion is **listener** code: `plan.md:146` puts it in the
`Player` row of the listener mapping and `plan.md:155-157` explains why. Listeners
are slice 2. But the proof — "**bot seats record no `joined_match`**" — sits in
**slice 3's** must-prove (`plan.md:182`).

So slice 2's checkpoint can go green with a listener that records `joined_match`
for every bot seat, and slice 3 then has to edit slice-2 code to pass its own
gate, after slice 2 was declared done and checkpointed. This is the same failure
the testability round already caught once ("slice 2's own must-prove list would
have PASSED against the broken shape", `plan.md:290`).

Related and unstated: the plan never says how the listener identifies the bots
user. `get_or_create_bots_user` (`app/engine/bots/seating.py:45-58`) looks it up
by `BOTS_USER_SUB`. If an implementer instead reaches for `is_internal` — the
natural read of "the house account" — slice 2 silently acquires a dependency on
slice 4, and the exclusion is a no-op until slice 4 lands.

### MEDIUM 4 — The `is_internal` backfill got neither a dry run nor the readback the spec requires [CODE-CONFIRMED]

`plan.md:198`: "The two backfills are irreversible in practice. Slice 6 requires
a dry run against a copy of production data, with a row-count readback, before
merge."

The sentence names two backfills and gates one. Slice 4's must-prove
(`plan.md:183`) is unchanged: "survives email rewrite and promote/demote;
backfill and creation rule agree on one fixture set" — no dry run, no readback,
no post-deploy step.

Spec D10 requires the opposite, and explains why (`spec.md:326-329`): the `.local`
accounts "cannot have come through Google sign-in … so a fourth creation path
exists outside the app … The backfill is therefore the only thing that will ever
flag them, **which is why it must be verified after deploy by reading back the
flagged row count against the known internal accounts**."

This is the higher-blast-radius backfill of the two. `is_internal` is the filter
under *every* number on the page (AC9), the spec measured over 500 of 646 dev
player rows as internal, and a wrong predicate mislabels the population with no
visible symptom.

### MEDIUM 5 — Slice 7 still never reads `first_source_channel`, and D9's label precedence is still absent [CODE-CONFIRMED]

Revision 2 fixed the write half: `first_source_channel` is now named in slice 5
(`plan.md:184`) and `signup_sources` in slice 7's title (`plan.md:186`). The read
half did not move.

- Slice 7's must-prove lists eight items and the channel is not among them.
- The plan still contains **zero** statements of D9's label precedence
  (`utm_source` → `first_referrer_host` → `"direct"`, `spec.md:286`), which is
  the rule that decides what every row of the source table says.
- AC13 has two halves: "record `mcp` in its own column" (slice 5, done) and "a
  real `?utm_source=mcp` does not collide with it" — a read/render rule with no
  owner.

Round 1 flagged that the column "is defined in the data model and then never read
by anything in the plan". It is now also written by a named slice and still read
by none.

### MEDIUM 6 — The `except ValueError` rationale does not hold for the mechanism revision 2 chose [CODE-CONFIRMED]

`plan.md:115-116`: "`ValueError` is caught deliberately: `StringLengthExceeded`
is a `ValueError` and would otherwise escape the advisory catch."

Checked against the real guard:

- `StringLengthExceeded` is raised only by `_check_string_lengths`
  (`app/sqlite_parity.py:23`, `:45`).
- That function is a **`before_flush`** listener (`app/sqlite_parity.py:53`) and
  iterates `session.new` / `session.dirty` — **ORM objects only**.
- It returns immediately on non-SQLite binds, and it explicitly skips
  `FlexibleEnumType` columns (`app/sqlite_parity.py:38-40`: "FlexibleEnumType is a
  TypeDecorator, not a String subclass, so enum members are skipped here"), which
  is the type the plan chose for `milestone`.

The recorder shown at `plan.md:101-113` is a **Core** insert on the connection
(`conn.execute(insert(UserMilestone), row)`) running in `after_flush_postexec`.
That row never enters `session.new`, and `before_flush` has already fired by
then. The guard cannot raise on this path.

Harmless on its own, but it is the plan's stated justification for a broad
`except ValueError` in the one place the repo's fail-loud rule is deliberately
suspended — a leftover from the pre-revision-2 `db.add` shape that now reads as
evidence for a catch that catches nothing.

### LOW 1 — Six accepted round-1 items were not applied in revision 2 [CODE-CONFIRMED]

Recorded once as a fix-completeness observation, not re-argued:

| Round-1 item | Status in revision 2 |
|---|---|
| M5 — AC16's browser-timezone carrier | Still no mechanism, no slice-8 window control, no stated fallback |
| M8 — AC22's capture half ("capture raising does not fail the page") | Still absent from slice 5's must-prove and from the seven highest-value tests |
| M9 — `source_match_id` first-write-wins vs AC17 | Unaddressed; `plan.md:41` still asserts it "enables read-time smoke-test exclusion" |
| L1 — "decisions (D1–D14)" | `plan.md:4` unchanged; the spec has D1–D12 plus D3a |
| L3 — config keys have no doc consumer | `.env.example` still lists 8 keys, neither of them new; the plan still does not name the file |
| L4 — no file manifest | `scope.json` exists and would answer it, but the plan never points at it — and see MEDIUM 1 |

### LOW 2 — The plan targets "spec.md revision 4"; the spec's header says "Revision 3" [CODE-CONFIRMED]

`plan.md:3` versus `spec.md:3`. The spec *body* says "Revised in revision 4" in
D1 and D3, so the content is revision 4 and only the header is stale — but every
reconciliation note keys off that number, and the two artifacts now disagree on
the spec's own version.

### LOW 3 — `user_detail.html` is still unnamed in slice 8, and the milestone enum class is unnamed [CODE-CONFIRMED]

- Slice 8 (`plan.md:187`) names `admin_user_actions.py`, `users_list.html`,
  `admin/dashboard.html` and `base.html` — but not
  `app/templates/admin/user_detail.html`, where the toggle actually renders, and
  not the `{% if not floor_admin %}` gate it must render outside of. "Renders for
  a floor admin" covers it behaviourally only.
- `FlexibleEnumType(enum_cls, *, length)` requires a Python enum class
  (`app/models/enum_types.py:25`). The plan lists seven milestone *values* and
  names no enum class or its home. Every existing use in the repo passes
  `length=16`; the plan specifies 32, and `set_up_human_play` is 18 characters —
  so copying the established pattern silently truncates the value the split
  introduced.

## Residual Risks

- **The bots user still records `signed_up`.** Revision 2 added a house-user
  exclusion to the `Player` listener only (`plan.md:146`). The `User` listener has
  none, so `get_or_create_bots_user` (`app/engine/bots/seating.py:45`) writes a
  `signed_up` milestone for the house account. `is_internal` catches it at read
  time once slice 4 lands, so the page should be right — but between slices 2 and
  4 the raw table is polluted, and any future direct consumer inherits it.
- **No kill switch for the listeners.** Revision 2 correctly withdrew the "inert
  by default" claim, but did not add what the claim was standing in for. The
  listeners begin writing on every `User`/`Agent`/`Player` insert the moment the
  merge deploys, and the only flag in the feature gates the capture cookie. If
  they misbehave in production the remedy is a revert-and-redeploy.
- **Two definitions of "genuine play" still have no test pinning them together.**
  `played_turn` is written at request time by two named hooks; "turns in the
  window", "users active in the window" and `returned` are derived at read time
  from `turn_submissions` with their own filter. Unchanged from round 1.
- **Cross-game scope is still unstated.** `record_submission` has implementations
  in both `app/games/hoard_hurt_help/` and `app/games/liars_dice/`. Whether the
  page spans both games is never said, and it changes both hook placement and
  every number.
- **The listener registration home is named by pattern, not by file.** "Mirroring
  the existing `install_sqlite_parity_guards` pattern" (`plan.md:123`) points at a
  real precedent whose actual call site is `app/db.py:22` — a file that is in
  neither the plan's prose nor `scope.json`.
- **Milestone ordering is now an implementation-time design decision.** The split
  removed a bug and handed the page a question it did not have before: what order
  seven milestones with two parallel setup values render in, and how "furthest
  reached" is defined over them. Deferring that to slice 7/8 means it gets decided
  by whoever writes the query.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "Spec never updated: set_up_a_way_to_play and agent_kind are still its design", "detail": "spec.md:75/96/102/433 and state.json's AC4 still name the milestone and column plan revision 2 dropped, so AC4 is unsatisfiable as written and spec.md:96 still states the agent_kind column is required or the AI denominator is unrecoverable."}, {"severity": "HIGH", "title": "Plan's risk table still prescribes the refuted after_commit shape", "detail": "plan.md:266 says 'Collect in the listener, write in after_commit; slice 2 exists to prove this shape' while plan.md:88 and :95-97 establish that after_commit raises InvalidRequestError and persists 0 rows, so the compact risk summary instructs the exact silent failure the rewrite removed."}, {"severity": "HIGH", "title": "'Dry run against a production copy' has no documented procedure and the only documented prod access is the live DB", "detail": "No pg_dump, restore or snapshot step exists in docs/ or MEMORY.md; docs/deploy-railway.md:86 only notes that Railway backups exist and docs/operations/debugging-history.md:20 documents DATABASE_PUBLIC_URL as live production, while the repo's own read-only precedent scripts/preview_match_id_migration.py (--db copy.db --dry-run) goes uncited and no --dry-run mode is required of the migration."}, {"severity": "HIGH", "title": "state.json's acceptance-criteria list is still the stale 22-item revision-3 list", "detail": "spec.md:411 calls state.json authoritative but it omits AC0 (the FIRST_TOUCH_CAPTURE_ENABLED privacy gate), AC1a and AC1b, still says 403-for-non-admin without the 401-anonymous correction, and still names set_up_a_way_to_play in AC4."}, {"severity": "HIGH", "title": "The milestone split has a write side and no read side", "detail": "set_up_human_play is written by the listener and read by no slice, AC-table row, page item or backfill; the union that AC4 and D2's funnel actually display is owned by nobody, D2's ordered presentation and AC18's 'furthest milestone' label are undefined over two parallel setup values, and slice 6 names neither new value."}, {"severity": "MEDIUM", "title": "scope.json does not contain the files revision 2 added", "detail": "The diff-checkpoint manifest omits app/models/__init__.py (slice 1's headline item), app/config.py, app/db.py, app/engine/connection_activity.py, app/engine/agent_play.py, app/routes/web_play.py, app/routes/dev_login.py, app/engine/bots/seating.py and .env.example, while still carrying web_join.py and agents_create.py from the pre-listener design."}, {"severity": "MEDIUM", "title": "Slice 6 depends on 1 and 4 but not 3, and the plan's own test #6 spans 3 and 6", "detail": "The backfill's genuineness filter must match slice 3's live recorder or the page shows a step change at the deploy date, yet slice 6 can be checkpointed green against a definition slice 3 never agreed to; conversely nothing in the milestone backfill reads is_internal, so the stated dependency on slice 4 is the questionable one."}, {"severity": "MEDIUM", "title": "Slice 3 carries the proof obligation for a slice-2 behaviour", "detail": "The house-bots-user exclusion is listener code (slice 2) but 'bot seats record no joined_match' is in slice 3's must-prove, so slice 2 can be declared done with the bug in it; the plan also never says whether the listener identifies the bots user by BOTS_USER_SUB or by is_internal, which would add a hidden slice-4 dependency."}, {"severity": "MEDIUM", "title": "The is_internal backfill got neither a dry run nor the readback spec D10 requires", "detail": "plan.md:198 names two irreversible backfills and gates only slice 6, while spec.md:326-329 explicitly requires reading back the flagged row count after deploy for the internal-flag backfill, which is the filter under every number on the page."}, {"severity": "MEDIUM", "title": "Slice 7 still never reads first_source_channel and D9's label precedence is still absent", "detail": "Revision 2 fixed the write half (slice 5) but slice 7's must-prove never mentions the channel, the plan still states D9's utm_source to referrer_host to 'direct' precedence zero times, and AC13's no-collision half has no owner."}, {"severity": "MEDIUM", "title": "The except ValueError rationale does not hold for the chosen mechanism", "detail": "StringLengthExceeded is raised only by a before_flush listener over session.new/session.dirty that skips FlexibleEnumType columns (app/sqlite_parity.py:23/38/45/53), so it cannot fire for the Core connection insert in after_flush_postexec that plan.md:101-113 specifies."}, {"severity": "LOW", "title": "Six accepted round-1 items were not applied in revision 2", "detail": "AC16's timezone carrier, AC22's capture half, source_match_id first-write-wins, the D1-D14 miscitation, .env.example and the file manifest are all unchanged from revision 1."}, {"severity": "LOW", "title": "Plan targets spec.md revision 4 while the spec header says Revision 3", "detail": "plan.md:3 versus spec.md:3; the spec body says 'Revised in revision 4' so only the header is stale, but every reconciliation note keys off that number."}, {"severity": "LOW", "title": "user_detail.html unnamed in slice 8 and the milestone enum class is unnamed", "detail": "Slice 8 lists four template and service files but not the one the toggle renders in or its floor_admin gate, and the plan names seven milestone values with no enum class while every existing FlexibleEnumType column in the repo uses length=16 against the plan's 32 and an 18-character set_up_human_play."}]}
```
