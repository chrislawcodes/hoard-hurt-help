---
name: project-constitution
description: Bootstrap a NEW repository with Chris's working contract — generate a tailored CLAUDE.md plus the companion machinery (preflight skill, ship skill, worktree script, ledgers, STATUS.md) adapted to that repo's real stack. Use when starting a new project, when asked to "set this repo up the way I work", or to port the constitution to a repo that lacks one. Do NOT use on hoard-hurt-help itself (it already has the constitution this skill is distilled from). Portable — this folder can be copied to ~/.claude/skills/ or into any repo.
argument-hint: [path-to-target-repo]
---

# Project Constitution Skill

Turn a fresh repo into one that works the way Chris works. The output is a
tailored `CLAUDE.md` plus the machinery that enforces it — not a copy of
hoard-hurt-help's file. The core judgment this skill encodes: which rules are
**portable principles** (go everywhere, verbatim), which are **project-shaped
instances** (re-derive per repo), and which are **local scar tissue** (never
copy). That split lives in `references/portable-principles.md` — read it first.

Read `references/templates.md` for the CLAUDE.md skeleton and companion stubs.

## Step 1 — Inspect, don't ask

Detect everything detectable in the target repo. Do not ask about anything in
this table:

| Detect | How |
|--------|-----|
| Language & package manager | Manifest files (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, ...) and lockfiles (prefer `uv`/`pnpm`/etc. per lockfile present). No lockfile → infer from CI's commands, else the stack default; if CI runs a lockfile-requiring command (`npm ci`) with no lockfile committed, CI is broken on arrival — surface that as a finding |
| Remote | `git remote -v`. No `origin` → generate the local-only variant (see the Remote row in `portable-principles.md` Pile 2): `origin/main` → `main`, no ship skill, Step 5 delivery ends at commit |
| Linter / formatter | Config files (`ruff` section, `.eslintrc*`, `clippy` via Cargo, `biome.json`, ...) |
| Type checker | `mypy`/`pyright` config, `tsconfig.json`, or built-in (Rust, Go) |
| Test runner & test count | Config + `ls tests/` or equivalent; note if the suite is empty |
| CI | `.github/workflows/*` — what it runs, on what triggers |
| Async vs sync, web vs CLI vs library | Entry points, framework deps |
| Existing conventions | An existing CLAUDE.md/CONTRIBUTING.md means MERGE, don't overwrite — show a diff of what you'd add and ask |
| Empty repo | No manifest at all → ask which stack before anything else |

## Step 2 — Ask the genuine variables

Decide the full set of questions first, say how many, ask one at a time
(the constitution's own rule — practice it here). Usually three:

1. **Rigor level** — production-bound or prototype? The gate commands are the
   SAME either way: the repo's real checks, always including the language's
   own type/compile check where one exists (`tsc`, `mypy` — dropping the type
   checker guts the no-suppressions rule). What rigor changes is the *ritual*:
   production adds the Small-Change Lane distinction, stricter PR ceremony,
   and ledgers; prototype keeps ceremony light. No-suppressions is never
   relaxed at either level.
2. **Deploy target** — is there a prod? If yes, scaffold the ops ledger
   (`docs/operations/debugging-history.md`) and the ledger pointer inside
   "When Something Breaks"; if no, skip the ledger and its pointer only —
   the When Something Breaks *principle* is Pile 1 and always included.
   No deploy signal anywhere in the repo (no Dockerfile, no deploy workflow,
   no infra config) → you may assume "no prod" without asking; say so in the
   report.
3. **Multi-agent?** — will Codex/Gemini/multiple Claude sessions work here
   concurrently? If yes, copy `agent-worktree.sh` and include the full
   worktree section; if solo, include only the pristine-main and
   prune-on-merge rules.

Skip any question the repo already answers (e.g. a `Dockerfile` + deploy
workflow answers #2).

## Step 3 — Generate CLAUDE.md

Fill the skeleton in `references/templates.md`:

- Copy the **portable principles** verbatim from
  `references/portable-principles.md` — do not paraphrase them; drift starts
  as paraphrase.
- Re-derive every **project-shaped instance** from Step 1's findings. The
  Preflight Gate must be the repo's *real* commands, verified runnable in
  Step 5 — an aspirational gate ("add mypy later") is worse than a small one,
  because the first failing run teaches agents to ignore it.
- Scale the Small-Change Lane to the repo: the ≤40-line/≤5-file thresholds
  are sane defaults; drop the lane entirely if the full gate runs in
  under ~30s (a lane that saves nothing is pure rule-surface).
- Include NOTHING from the leave-behind list in
  `references/portable-principles.md`, and no rule the repo can't yet
  enforce.

## Step 4 — Scaffold the companions

A constitution without machinery is a wish. From `references/templates.md`:

| Companion | When |
|-----------|------|
| `.claude/skills/preflight/SKILL.md` — wired to the real commands (see the solo/no-lane variant in `templates.md`) | Always |
| `.claude/skills/ship/SKILL.md` — rebase → preflight → CI → squash-merge → prune | When there's a remote + PR flow (and then Pile 1's `/ship` slot gets filled; no ship skill → no `/ship` reference anywhere) |
| `scripts/agent-worktree.sh` — copy verbatim from hoard-hurt-help (it is fully generic) | Multi-agent = yes |
| `docs/operations/debugging-history.md` — empty ledger with entry format | Prod = yes |
| `STATUS.md` — stub, AND the Project Status section in CLAUDE.md that tells agents to update it (never one without the other) | Always |
| `.gitignore` | Create a stack-appropriate one if missing (`node_modules/`, build dirs, venvs, caches) — Step 5's validation will otherwise flood the tree. Ensure `.claude/skills/` and CLAUDE.md are TRACKED (if `.claude` is ignored wholesale, add `!.claude/skills/` or use `git add -f` and say so in the commit). Commit the lockfile if CI's install step requires one |

Start with ONE ledger (debugging-history). Split out a failure-archaeology
skill only when settled-decision entries accumulate — pre-creating empty
knowledge files teaches agents they're decorative.

## Step 5 — Validate, then hand over

1. Run the generated preflight commands in the target repo. Every command must
   actually pass. If a command fails because the choice was wrong, fix the
   command. If it fails because the repo's tooling is broken (misconfigured
   lint, dead CI), **repairing the tooling is in scope** — a working gate is
   the bootstrap's deliverable, and shipping a red gate teaches agents to
   ignore it. Repairs needing a judgment call (a new dependency, a config
   rewrite) get asked about first; if you can't ask, ship the gate minus the
   broken command and record the breakage in STATUS.md as the top open item.
2. Commit on a feature branch (`constitution-bootstrap`), following the new
   constitution's own PR rules — the bootstrap is its first test. No remote →
   done at the local commit; note the pending push/PR in STATUS.md.
3. Report: what was detected, what was asked, what was left out and why, and
   the one-paragraph diff from hoard-hurt-help's constitution.

## Maintenance note

When a portable principle improves in ANY repo, back-port it to this skill's
`references/portable-principles.md` — this file is the trunk; per-repo
CLAUDE.md files are branches. That's the whole point of the split.
