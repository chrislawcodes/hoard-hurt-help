---
name: one-home
description: Find code that DOES a similar thing, however differently it is written — the same question answered in two places, where the answers can quietly disagree. Enforces the "One Home Per Rule" section of CLAUDE.md. Use before adding a helper, query, or constant ("do we already have this?"), when reviewing a diff for a rule written twice, or for an on-demand whole-repo sweep. Reports and judges; it does not refactor on its own.
argument-hint: "[--changed | <paths>]"
---

# One Home Per Rule — finder

`CLAUDE.md` says a rule belongs in one module, named for the question it
answers. This skill finds where that has stopped being true.

**It looks for code that does a similar job, not for copied text.** That
distinction is the whole design, and it is measured, not assumed — see the
backtest below.

## Why not match on code structure

The obvious build is a structural matcher: hash each function's shape, report
collisions. It was tried here first and it does not work.

Backtested against the tree immediately before the C-series dedup (`34c1cab^`),
where all eight of that survey's clusters were still live, a structural matcher
found **0 of 8**. The real pairs were never copy-paste. The C-series
reuse-report describes `_has_moved` as *"semantically equivalent (same
join/filter/`limit(1)`), NOT byte-identical — differ in param name + structure"*.
A shape hash sails straight past that.

Plain text search is worse. On this repo it surfaces per-game protocol
implementations, deliberate re-export aliases, and constants quoted inside
docstrings — all noise, no signal.

## What it does instead

`find_similar.py` scores every candidate pair on **two independent signals**:

- **name** — do these ask the same question? `_is_bot` and `_is_bot` do, even
  though one takes a kind string and the other a database row, so their
  vocabularies barely touch. Vocabulary alone never finds that pair. This is the
  repo's own *"the name is the index"* rule, applied as a score.
- **does** — do they touch the same models, columns, helpers and constants?
  Tokens are weighted by rarity, so `db`, `select` and `await` count for nothing
  while `PREGAME_STATES` or `Match.state` count for a lot. This catches a rule
  copied under two unrelated names.

Output comes in **three bands**, not one ranked list, because mixing them buries
the good signal. Measured: in a single combined list the C-series true pairs sat
at ranks 224, 147 and 46 of 268.

| Band | What it is | Hit rate |
|---|---|---|
| **A. Same name, two homes** | One question with two answers | Highest — read all of it |
| **B. Same question, different names** | `count_players` / `_active_player_count` | Medium |
| **C. Similar work, unrelated names** | Shared vocabulary only | Lowest — often just two functions about one model |

```bash
cd "$(git rev-parse --show-toplevel)"

# Guard: does what I just changed re-answer an existing question?
python3 .claude/skills/one-home/find_similar.py app mcp_server --changed

# Sweep: on-demand whole-repo survey.
python3 .claude/skills/one-home/find_similar.py app mcp_server
```

Start in guard mode unless a sweep was actually asked for. The script has no
default mode — pass one.

Useful flags:

- `--why` shows the shared vocabulary behind a score. This is how you tell a
  real pair from a coincidence, and it is worth using on anything in band C.
- `--top` bounds each band (default 40); the header gives true totals.
- `--threshold` (default 0.50) trades recall against reading time.
- `--contained` switches to one-way overlap: "is this small helper reimplemented
  inside that bigger function?". Much noisier; use it hunting a known helper.
- `--include-protocol` keeps the per-game interface implementations that are
  filtered out by default.

Pairs at 0.80 or above also print a `differs:` line naming the literals one side
has and the other does not. When two functions are otherwise identical, **that
difference is the finding** — `ai_only=True` against `ai_only=False`.

## How well it actually works

Backtested against `34c1cab^`, the tree immediately before the C-series dedup
(#559), scored against the five clusters that survey **merged**:

| Approach | Result |
|---|---|
| Structural (hash the code shape) | **0 of 8** clusters |
| Vocabulary only, one ranked list | 5 found, but at ranks 9, 46, 147, 224, 239 |
| Name + vocabulary, banded (current) | **3 of 5 in band A**, 1 in band B, 1 not found |

Read that last row honestly. Band A held 81 pairs on that fixture, and the three
true clusters sat at ranks 2, 6 and 33 within it. **This is a shortlist, not an
answer.** It takes a million possible pairs down to about eighty worth reading.
Deciding which of those eighty should actually be merged is the next step, and
it needs a human or an agent to read both functions.

The one it cannot find — `_scoreboard_order` against `_public_standings` — is a
synonym pair: no shared name words, no shared vocabulary, different code. No
lexical method reaches it. Only reading the module does.

## Blind spots

A clean run means "no similar functions", not "no duplicated rules".

- **It compares whole functions.** A rule spelled once as a function and once as
  one line inside a much bigger function stays hidden, because the big
  function's vocabulary drowns it. That is exactly how `provider_loop_running`
  hides its copy of the liveness window. `--contained` helps and does not
  fully solve it.
- **It cannot see prose.** Docs describing current behaviour drift silently
  because nothing reads them.
- **It cannot see across languages** — templates and JavaScript are invisible.

The semantic sweep below covers what the parser cannot.

## Get band A from repowise when an index exists

**This was measured, and repowise wins the same-name half.** If `.repowise/`
exists (built with `repowise init --yes`; no API key needed), its symbol index
gives band A exactly rather than approximately — and cross-language, which this
Python-only script cannot do.

```bash
repowise search "<name>" --mode symbol --limit 10   # supported interface
```

For the whole sweep, group its symbol table by name. Against `wiki_symbols`
(repowise's internal store — the schema can drift, so sanity-check the counts):

```sql
SELECT name, file_path, start_line FROM wiki_symbols
WHERE kind IN ('function','method')
  AND (file_path LIKE 'app/%' OR file_path LIKE 'mcp_server/%');
```

Group by `name`, keep names in 2+ files, then drop three noise classes: dunders,
anything under `app/games/` (the `GameModule` protocol — `base.py` declares,
`base.py` implements, each game overrides, so every slot looks 3x duplicated),
and methods whose `parent_name` differs (different classes).

Measured on this repo: 1156 symbols → 54 names in 2+ files → **12 candidates**
after filtering, against this script's 68-item band A. The 12 were all worth
reading, and included `_safe_next` (an open-redirect check hand-rolled in
`dev_login.py` beside the canonical `safe_internal_next`) which this script's
band A had buried.

**What repowise does not do, and this script is for:** its symbol index groups by
*exact name*, so it structurally cannot find the same question asked under a
different name — `_active_player_count` against `count_players`, which is C4 in
the backtest and lands in band B here. Band B is this script's real contribution;
band A is a fallback for when no index exists (a fresh clone, a worktree, or
Claude Code on the web, where `.repowise/` is gitignored and absent).

Do not reach for repowise's own `dry_violation` findings for this job. They are
line-clone detection at **file** granularity — "30% of this file is duplicated
with that file" — with `function_name` null on all 330 of them, and 67% pointing
at `tests/` or `docs/`. They caught the `_load_user_agents` file pair and missed
`_game_display_name` entirely.

## How this relates to the guard tests already in the suite

This repo already has a well-developed answer to duplication, and it is **not**
this skill. It is a family of guard tests, in three shapes:

| Shape | Example | What it does |
|---|---|---|
| Agreement | `test_rules_single_source.py`, `test_agent_playability.py`, `test_connection_badge_parity.py` | Two forms of one rule must produce the same answer |
| Dedup pin | `test_is_bot_kind.py` (C3), `test_bot_presets_profile.py` (D3), `test_turn_openers.py` (C2) | Pins a merge — or pins a deliberate divergence so nobody "fixes" it |
| Structural tripwire | `test_css_duplicate_selectors.py`, `test_badge_partial_single_source.py` | Scans source and fails if a named duplicate reappears |

Those tests are stronger than this skill in every way that matters day to day:
they run in CI on every PR, they have no false positives, and they never
regress. **Prefer them.**

Their one limit is what this skill is for: every one of them was written *after*
a human found the duplication. They guard; they do not discover. The CSS test
says so in its own docstring — tripwires for *those four* selectors. Nothing in
the suite would find a fifth.

So the two fit together as a loop, and **a finding is not finished until it
leaves a test behind**:

> sweep finds a candidate → Step 3 decides the verdict → if merged, write the
> guard test in the shape above so it cannot come back

Do not generalise a tripwire into a repo-wide assertion without checking what it
reports. Generalising the CSS test from its 4 named selectors to all 837 finds
5 duplicated selectors — and all 5 are additive blocks setting different
properties, so all 5 are false alarms. Checking for the shape that actually
broke (one selector redeclaring the *same* property, where the later block
silently wins) finds 0. A broad check that does not encode the failure mode
produces noise and then gets ignored.

## Step 1 — Drop what is already settled

**Before reporting anything**, read the *"Refactors adjudicated do not
re-attempt"* table in `.claude/skills/failure-archaeology/SKILL.md` and drop
every candidate that already has a verdict.

That table is deliberately **not** copied here. It has one home, and duplicating
it would be the exact bug this skill hunts. Several known duplications in this
repo are intentional — merging them reintroduces a known bug. A **new**
leave-as-is verdict gets recorded there, not here.

## Step 2 — The semantic sweep

The questions the parser cannot ask.

**Does a sort tie?** Ordering by a unique id is stable; ordering by a timestamp
or a score is not, so equal rows come back arbitrarily and two call sites
disagree. This is the `load_open_turn` bug.

```bash
grep -rnE '\.order_by\([A-Za-z_.]+\.(desc|asc)\(\))' app --include=*.py
```

**Do two defaults for one setting disagree?** Grep the setting name, then
compare the function default, the column default, and any config default. Trap:
a **missing argument** and a **NULL column** are different questions and may
legitimately resolve differently — never collapse them.

**Is a predicate re-spelled inline?** Grep the *question*, not the code:
`is_bot`, `has_moved`, `within_window`. The named home already exists; the
inline copy is the drift.

**Does prose still match the code?** Compare numbers in docs against the
constants. Trap: docs recording *history* must keep their old numbers. Only docs
describing *current* behaviour are guarded.

## Step 3 — Judge each candidate

**A score is a shortlist entry, never a verdict.** Open both functions and read
them. Similar-looking code that should stay two is the common case here, not the
exception — the past runs rejected as many candidates as they merged.

Run these five questions in order. The first "no" ends it.

1. **Do they answer the same question?** Not "do they look alike" — what does a
   caller ask each one for? `_load_user_agents` returning AI-only agents and
   `_load_user_agents` returning every agent answer *different* questions in the
   same words. Different question → not a duplicate, go to 5.
2. **Would either caller break if you swapped in the other?** If yes, they are
   behaviorally different on purpose. This is what killed C2: one opener is
   get-or-create and resume-safe, the other a blind INSERT, and merging them
   would have silently changed persisted game state.
3. **What does the merged function look like?** Write the signature. If serving
   both callers needs two or more new flags, or a parameter that flips a filter,
   the merge is worse than the duplication. This is what killed D5: per-site seed
   arguments and access patterns differed, so the shared helper would have added
   determinism risk with no real saving.
4. **Is the payoff worth the risk here?** Weigh lines saved against what breaks
   if it is wrong. The turn-loop twins were left alone deliberately: real
   duplication, ~40 lines of payoff, in the code that freezes live games when it
   breaks. Boring and separate was chosen as the safety feature.
5. **If they stay two, does a shared name lie?** Two functions with one name
   giving two answers is its own bug, even when both are correct. Rename so the
   name states the question — that is the `rename` verdict.

Only a candidate that survives 1–4 is genuinely mergeable. Record the rest with
the reason, so the next sweep does not re-litigate them.

| Verdict | Means | Do |
|---|---|---|
| `reuse` | An existing symbol is the home | Point the other call sites at it |
| `extend` | One home, plus a parameter for the axis that differs | Add the parameter; do not fork |
| `justified-new` | No home exists yet | Create one, named for the question |
| `rename` | They should stay two, but a shared name is lying | Rename so the name is the index |
| `not-a-true-duplicate` | Deliberately different | Leave byte-unchanged, record why, pin with a test |

Then rank by consequence, not by line count:

- **Bug** — the copies already disagree and something depends on it. Fix now.
- **Fragile** — they agree today, nothing stops them drifting. Unify or pin.
- **Cleanup** — real duplication, no divergence risk. Ride along with feature work.
- **Settled** — adjudicated already, or deliberate. Report as closed.

Two rules from `CLAUDE.md` bind here. **Never invent a `utils.py` or
`helpers.py`** as the new home; name the module for the question. And whatever
the verdict, **leave a guard test behind** in one of the three shapes above —
agreement, dedup pin, or structural tripwire. That applies to a
`not-a-true-duplicate` verdict too: `test_turn_openers.py` exists to stop
someone "fixing" C2's deliberate divergence. Nothing else stops these drifting;
review does not catch it.

## Step 4 — Report

Lead with pairs that already disagree. For each: the two locations, the one
thing that differs, whether anything depends on that difference, the verdict,
and the severity. A finding with no stated consequence is not a finding yet.

Then hand off — this skill reports, it does not refactor:

- A **Bug** is a Direct Path fix plus a regression test that fails on the copy.
- A **Fragile** cluster of two or three sites is Direct Path, or `feature-thin`.
- A large cluster spanning subsystems is a Feature Factory run, the way the
  C-series and D-series dedup runs were done.

Run the Preflight Gate before any push, as always.

## False positives to expect

All observed in real output:

- **Inverse pairs.** `provider_for_model` and `model_for_provider` share every
  name word, because word sets ignore order. They are opposite directions of one
  mapping, not duplicates.
- **Singular against plural.** `load_open_turn` and `load_open_turns` answer
  "one row" and "many rows". Band A keeps plurals intact so these land in band
  B, but they still surface — check which one a caller wants.
- **Per-game interface implementations.** `resolve_turn`, `tagline`,
  `build_replay_view` appear once per game. Filtered by default; the header says
  how many were dropped.
- **Deliberate re-export aliases.** `mcp_server/key_auth.py` does
  `CONNECTION_KEY_PREFIX = _CONNECTION_KEY_PREFIX`. Single-sourcing working
  correctly.
- **Route handler families.** Thin endpoints that differ only in a route string
  score 1.00. Whether that is duplication or a readable set of endpoints is a
  judgement call — make it deliberately.
- **Shared domain vocabulary.** Two functions about one model share tokens
  without doing the same job. This is most of band C; `--why` is how you check.

## Worked example

`_load_user_agents` exists in `app/routes/agents_list.py` and
`app/routes/web_player_shared.py`, scoring 1.00 with
`differs: kw:ai_only, const:True, const:False`. Merging them would be wrong —
the difference is intended and commented. But two functions with one name giving
two answers is precisely the "two people each had a reasonable definition"
shape. Verdict `rename`: names that state which question each answers.

## Provenance

Written 2026-08-28. The design is backtested, not assumed. Fixture: `34c1cab^`,
the tree immediately before C-series dedup #559, scored against the five
clusters that survey merged. See **How well it actually works** for the numbers
and for what it still misses.

**Re-run that backtest before trusting any change to the scoring.** Every
tuning decision here came from it and several plausible ideas failed it: a
six-token floor excluded every tiny helper, skipping same-file pairs lost C7
entirely, cosine buried small-against-large pairs, and one-way containment
matched `_now` against 222 unrelated functions.
