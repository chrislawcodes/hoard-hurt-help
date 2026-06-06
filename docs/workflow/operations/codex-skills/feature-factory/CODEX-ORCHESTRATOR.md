# Codex Orchestrator Guide

**TL;DR — This guide applies when Codex is the orchestrator: i.e. when the feature run is started from a Codex session (e.g. `codex exec`), or when Claude handed off mid-run. When the run is driven from a Claude session, Claude orchestrates instead — see `SKILL.md`.**

Start a Codex-orchestrated run with:
```bash
codex exec -m gpt-5.4 -s workspace-write "$(cat docs/workflow/orchestrator-prompts/<task>.md)"
```

Codex tokens are free for the operator, so Codex is also the implementation worker even when Claude orchestrates. This guide covers the case where Codex *drives* the whole workflow — authoring artifacts and judging findings — not just implementing.

---

This guide tells you — Codex — exactly how to run the feature workflow when you are the primary orchestrator. Read this before starting any workflow as the Codex Orchestrator.

For the authoritative phase table (what happens at each stage), see `SKILL.md` in this directory. This guide covers the operational details: commands, models, escalation, and handoff.

---

## 1. When This Guide Applies

You are in **Codex Orchestrator** mode when:
- A human dispatches you via `codex exec -m gpt-5.4 -s workspace-write "$(cat ...)"` — this is now the **default** start pattern
- A human says "use feature workflow to implement X" from a Codex session
- Claude has handed off mid-workflow via a `block` note in `state.json`
- The workflow `status` shows a `blocked-state: active` with a reason that starts with "Claude session ended"

In Codex Orchestrator mode, **you drive the workflow end-to-end**. You write artifacts, call the runner, call Gemini for reviews and research, judge findings where you can, and escalate to the human where you cannot.

---

## 2. Models

| Task | Model | Flag |
|------|-------|------|
| All Codex implementation and review tasks | `gpt-5.4-mini` | `-m gpt-5.4-mini` |
| Gemini reviews — routine | `gemini-3.1-flash-lite` | `-m gemini-3.1-flash-lite` |
| Gemini reviews — sensitive checkpoints (`--sensitive`) | `gemini-3.1-pro-preview` | `-m gemini-3.1-pro-preview` |

The runner sets the Gemini model automatically from `factory_review_specs.py`
(`DEFAULT_GEMINI_MODEL` / `SENSITIVE_GEMINI_MODEL`); the flags above are for
direct calls. Note Pro requires the `-preview` suffix — bare `gemini-3.1-pro`
returns `ModelNotFoundError`.

**Gemini launches must be staggered by 30 seconds.** The runner may overlap Gemini reviews,
but it preserves that 30-second stagger. If you call Gemini directly outside the runner, do
not start multiple Gemini calls at the same moment. Start them 30 seconds apart.

---

## 3. Phase-by-Phase Command Reference

All runner commands run from the repo root:

```bash
python3 docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py <command> --slug <slug>
```

| Phase | What you do | Runner command |
|-------|-------------|----------------|
| **Check status** | Always start here — read current state | `status --slug <slug>` |
| **Discovery** | **Mandatory before spec.** Ask clarifying questions one at a time, or explicitly state assumptions you are carrying in. Never silently skip to spec authoring. Record the outcome. | `discover --slug <slug> --question "..." --recommendation "..." --rationale "..."` (repeat per question), then `discover --slug <slug> --summary "<summary>" --complete` |
| **Write spec** | Research real file paths via Gemini, author `spec.md` | Write to `docs/workflow/feature-runs/<slug>/spec.md`, then checkpoint |
| **Spec checkpoint** | Generate adversarial reviews, judge findings | `checkpoint --slug <slug> --stage spec` |
| **Reuse audit** | Dispatch a read-only `codex exec` sub-session: scan the codebase (guided by `ARCHITECTURE.md`) for modules overlapping the feature; write `reuse-report.md` (capability → existing module → reuse/extend/justified-new). The plan must address every row. | `codex exec -m gpt-5.4-mini "<reuse-audit prompt>"` → write `docs/workflow/feature-runs/<slug>/reuse-report.md` |
| **Write plan** | Author `plan.md` with architecture decisions, incorporating the reuse-audit verdicts | Write to `docs/workflow/feature-runs/<slug>/plan.md` |
| **Update docs (up front)** | Dispatch a workspace-write `codex exec` sub-session: update `ARCHITECTURE.md` (and `DESIGN.md` if a design decision changes) to the planned target state, consistent with the plan and reuse report | `codex exec -m gpt-5.4-mini -s workspace-write "<doc-update prompt>"` |
| **Plan checkpoint** | Generate adversarial reviews, judge findings — **include the doc edits + reuse report in the review** | `checkpoint --slug <slug> --stage plan --context ARCHITECTURE.md --context DESIGN.md --context docs/workflow/feature-runs/<slug>/reuse-report.md` |
| **Write tasks** | Author `tasks.md` with `[CHECKPOINT]` markers at slice boundaries | Write to `docs/workflow/feature-runs/<slug>/tasks.md`, then checkpoint |
| **Record parallel analysis** | Look for safe parallel opportunities in tasks.md. Add `[P: file1, file2]` annotations if found. Record result. | `parallel --slug <slug> --note "..." [--found]` |
| **Tasks checkpoint** | Generate adversarial reviews, judge findings | `checkpoint --slug <slug> --stage tasks` |
| **Implementation** | Implement one slice, run build + tests, commit | `codex exec -s workspace-write "..."` |
| **Diff checkpoint** | Generate adversarial reviews of the diff, judge findings | `checkpoint --slug <slug> --stage diff` |
| **Deliver** | Create PR, notify human it is ready to squash merge | See Section 8 below |
| **Closeout** | Write closeout summary | Write to `docs/workflow/feature-runs/<slug>/closeout.md`, then checkpoint |
| **Reconcile docs** | Dispatch a `codex exec` sub-session: compare the merged diff against `plan.md` and the up-front doc edits; update `ARCHITECTURE.md`/`DESIGN.md` if implementation drifted, else record "docs already accurate." Required before done. | `codex exec -m gpt-5.4-mini -s workspace-write "<doc-reconcile prompt>"` |
| **Closeout checkpoint** | Final adversarial review | `checkpoint --slug <slug> --stage closeout` |
| **Write postmortem** | Write `postmortem.md` — what went well, what didn't, proposed workflow changes. Required before done. | Write to `docs/workflow/feature-runs/<slug>/postmortem.md` |
| **Update STATUS.md** | Update `STATUS.md` to reflect what shipped. Required before done. | Edit `STATUS.md` in repo root |
| **Reconcile a review** | Record your judgment on a review finding | `reconcile --slug <slug> --review <path> --status <accepted\|rejected\|deferred> --note "<judgment>"` |
| **Block on a decision** | Escalate to human | `block --slug <slug> --reason "<specific decision needed>"` |
| **Repair stale reviews** | Re-run stale reviews after artifact edits | `repair --slug <slug>` |

---

## 3b. Keep Moving and Report Status

After every runner command completes, read the `→ next:` line printed to stdout and proceed to that action immediately. Do not stop between steps unless the next action is `mark_blocked` or `done`.

After every runner command, emit one sentence to the user before starting the next step: what just completed, and what is starting next. Example: "Spec checkpoint passed — starting plan authoring now."

For long-running commands (checkpoint, implement), emit a "starting X" message before the command runs so the user knows work is in progress.

## 4. Escalation Protocol

### Codex can judge and reconcile:
- Findings that are clearly out of scope for the current slice
- Findings that duplicate something already addressed in the artifact
- Findings that conflict with an explicit decision recorded in the spec or plan
- Low/medium severity findings with an obvious deferral justification

### Codex must escalate to human via `block`:
- Architectural decisions not covered by the existing spec or plan (schema changes, new job types, new external dependencies)
- Conflicting findings from Codex attack and Gemini review that point in opposite directions
- Implementation failures that persist after 3 fix attempts
- Anything that would affect production data, credentials, or deployment configuration
- Any finding where you are genuinely uncertain whether to accept or reject

When escalating:
```bash
python3 docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py \
  block --slug <slug> --reason "<specific decision needed — not just 'blocked'"
```

Be specific. "Gemini flagged X as a security risk but spec explicitly scopes it out — confirm deferral is correct" is useful. "Something went wrong" is not.

---

## 5. What You Must Not Do Without Human Approval

- `git push --force`
- `git merge` into main or any protected branch
- `gh pr merge` — create the PR, but let the human squash merge it
- Any database migration on production
- Any change to credentials, secrets, or deployment configuration

If any runner command would trigger one of these, stop and block first.

---

## 8. Deliver — Creating a PR Ready to Squash Merge

When all checkpoints are reconciled and implementation is complete:

**Step 1:** Push the branch:
```bash
git push --set-upstream origin <branch-name>
```

**Step 2:** Create the PR against `main` on `chrislawcodes/hoard-hurt-help`:
```bash
gh pr create \
  --repo chrislawcodes/hoard-hurt-help \
  --base main \
  --title "<concise title matching commit style>" \
  --body "$(cat <<'EOF'
## Summary
<2-3 bullet points: what changed and why>

## Test plan
- [ ] <key thing to verify manually>
- [ ] CI passes

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

**Step 3:** Notify the human:
> "PR #NNN is open and ready to squash merge: <url>
> CI is running. Once it goes green you can squash merge directly."

Do not run `gh pr merge`. The human squash merges.

---

## 6. Command Failure Protocol

1. If a runner command fails, retry it once
2. If it fails a second time, run:
   ```bash
   block --slug <slug> --reason "<command> failed after 2 attempts: <error summary>"
   ```
3. Stop. Do not silently continue past a failed gate.

Do not suppress errors or attempt workarounds that bypass the workflow steps. The failure is information — record it and let the human decide.

---

## 7. Handoff Back to Claude

When your session is ending or you have reached a natural stopping point:

**Step 1:** Check current state:
```bash
python3 docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py \
  status --slug <slug>
```

**Step 2:** Record state for Claude:
```bash
python3 docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py \
  block --slug <slug> \
  --reason "Codex session ending at <current phase>. Open decisions: <list any unresolved findings or escalated decisions>. Last completed: <last successfully checkpointed stage>."
```

The `block` command writes to `state.json` — this is the handoff artifact. When Claude returns, it reads `status --slug <slug>` and sees the block reason, then clears the block after reviewing the open decisions.

**What to include in the block reason:**
- Current phase (e.g., "tasks checkpoint complete, ready for implementation")
- Any active escalations or unresolved review findings
- Any decisions made during the session that weren't in the original spec/plan
- Anything Claude needs to know before continuing
