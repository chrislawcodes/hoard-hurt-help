---
reviewer: "claude"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/betrayal-8-4-factory/spec.md"
artifact_sha256: "1d2f4fb351c7cddea26fe0a4fd9b7f4ce1323637ad772165d9bd12cdd278aa85"
repo_root: "."
git_head_sha: "6a69b7b62c2e109585191216494a0daf9fbe81f9"
git_base_ref: "origin/main"
git_base_sha: "6799bb0123823cc75bde3ce9fd06255ea931dcb9"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "All 6 findings accepted and folded into spec.md: F1 mirror needs ADDED attacker-credit line (§3.3); F2 display_delta overload + F3 match_summary gift-mislabel resolved via dedicated betrayal_bonus key (§3.4/§6); F4 stale -8 comments in viewer.py scope; F5 three design-doc -8 sites (R3); F6 .venv preflight form intentional."
raw_output_path: "docs/workflow/feature-runs/betrayal-8-4-factory/reviews/spec.claude.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

### F1 — [CODE-CONFIRMED] MEDIUM — The mirror's HURT branch has no attacker-credit line; §3.3 tells you to change the damage but not to *add* the attacker's +4
Spec §3.3 says "the attacker gains `BETRAYAL_BONUS` (+4)" in `apply_inround_turn`. But the real code (`scoring.py:253-257`) modifies **only the target** on a HURT:
```python
elif action == "HURT" and target:
    damage = (BETRAYAL_HURT_POINTS if help_targets.get(target) == actor else HURT_POINTS)
    new_inround[target] = max(0, new_inround.get(target, 0) - damage)
```
There is no `new_inround[actor] += …` line anywhere in the HURT branch to edit. An implementer who reads §3.3's damage instruction literally (swap `BETRAYAL_HURT_POINTS`→`HURT_POINTS`) will produce a mirror where the victim is −4 but the attacker's +4 bonus is silently dropped — reintroducing exactly the resolver/mirror divergence R1 exists to prevent. The spec should state that a **new** actor-crediting statement must be inserted, not just that a constant changes. (Intent is present in §3.3's prose "the attacker gains +4"; the feasibility gap is that the named edit target doesn't exist and the naive edit misses it.)

### F2 — [CODE-CONFIRMED] MEDIUM — D1(a) reuses the attacker HURT's `display_delta`, but that field already carries the victim's −4; the spec never says which field the +4 goes in
For a HURT, the attacker action's `display_delta` is set to the **target's** loss, not the actor's gain (`viewer.py:393-397`: `display_delta = a["target_delta"]` = −4). The spec §3.4 / D1(a) says to "surface the attacker's +4 bonus … on the attacker's action" and to "drop the −8 override so the victim chip shows −4 via `target_delta`." These two instructions collide on the **same** `display_delta` slot: if the +4 is written into the attacker's HURT `display_delta`, it overwrites the −4-on-target signal that AC5 and `test_viewer_shows_per_move_effect_on_target` (`test_viewer.py`, asserts the HURT shows `-4` on the target and no `+0`) rely on. The spec neither names a new field (e.g. `attacker_bonus`) nor says how `_build_rc_data`, `_turn_groups`, and `_feed_sort_key` should read it. As written, D1(a) is under-specified at the exact point it claims to be "contained."

### F3 — [CODE-CONFIRMED] MEDIUM — Putting a positive `display_delta` on a betraying HURT collides with `match_summary.py`, which the spec does not list in scope or out-of-scope
`match_summary.py:_superlatives` (lines 84-86) scans every action's `display_delta` and treats any **positive** value as a candidate for the match's "biggest single swing / biggest gift" highlight:
```python
delta = action.get("display_delta") or 0
if delta > 0 and (biggest is None or delta > biggest[0]):
    biggest = (delta, seat, turn["round"], turn["turn"])
```
Today a HURT's `display_delta` is always negative, so betrayals never reach this. Under D1(a), if the attacker's **+4** lands on the attacker's HURT `display_delta`, a betraying HURT becomes a positive-delta action and can be reported as a "gift" in the finale summary — a wrong, betrayal-mislabeled highlight. `match_summary.py` appears in neither the In-scope nor Out-of-scope lists (the impact-review flagged it Tier 5 precisely because it "only looks at positive `display_delta`"). This is a missed touchpoint created by the chosen mechanism.

### F4 — [CODE-CONFIRMED] LOW — AC6's grep target (`BETRAYAL_HURT_POINTS`) will not catch two stale inline `-8` comments in the same file
`viewer.py:331` (`# HURT against a player who is HELPing you this same turn — lands for -8.`) and `viewer.py:353` (`# Betraying a helper: HURT a player who is HELPing you this turn → -8.`) both hard-state −8 in prose, not via the constant. AC6 only forbids stale **`BETRAYAL_HURT_POINTS`** references, so it passes while these comments still lie. R2 does grep for a literal `-8` in the game module, so the risk is caught by R2's verification — but the scope's edit list for `viewer.py` mentions only `display_delta`, not these comments, so an implementer working the scope list alone can leave them.

### F5 — [CODE-CONFIRMED] LOW — Design-doc `−8` edits span three separate locations the scope collapses into "one bullet rewrite"
The scope says the DESIGN.md edit is the "'Betraying a helper' bullet rewrite (attacker +4 / victim −4)." But the live `−8` for betrayal appears in **three** distinct places that all must change: the payoff-math bullet (line 42), the worked-scenarios **table row** (line 55, "Betray a helper … −8"), and the edge-case bullet (line 66). Meanwhile the Team-Attack row (line 57) and the mutual-help "8 − k"/"+8" lines (45-46, 53, 68) must stay. R3 flags the stay-put ones, but the scope wording ("bullet rewrite") undercounts the change-these-three set. Feasible, but the enumeration is incomplete.

### F6 — [UNVERIFIED] LOW — AC7 preflight command form is consistent with the worktree, but note it diverges from CLAUDE.md's `python3 -m` form
AC7 pins `.venv/bin/ruff … .venv/bin/mypy … .venv/bin/pytest`. The worktree does have `.venv/bin/{ruff,mypy,pytest}` present (verified), so the command is runnable here. This differs from the constitution's `python3 -m ruff …` Preflight Gate wording, but since the worktree venv exists the `.venv/bin/` form is valid; flagging only so the plan doesn't assume a bare-`ruff` PATH that may resolve to main's venv.

## Residual Risks

- **Mirror-vs-resolver equivalence rests entirely on one test (R1).** The spec's only guard against F1/F2 drift is "a mirror unit test asserts the same numbers." The bundled `test_mirror_betraying_a_helper_is_eight` currently asserts the **old** `{"A": 4, "B": 2}`; it must be flipped to `{"A": 8, "B": 6}`, and `test_mirror_value_matches_resolver_decay`-style parity for the betrayal case does not exist yet. If the test is updated to match a buggy implementation rather than the intended +8/−4, the divergence ships green. [UNVERIFIED — depends on how the plan writes the test]

- **`_build_rc_data` re-derives its own betrayal/help groupings.** The robot-circle builder recomputes `betrayals`, `mutuals`, `hurts`, `helps` from `rc_actions` and emits `delta: display_delta` per action. Whatever field carries the attacker's +4 (F2) must be threaded through `_build_rc_data` and the caption logic too, or the animated stage and the compact feed will disagree about the betrayal. The spec's "no stale −8 anywhere (chip, caption, groups)" (AC5) names these surfaces but the mechanism to feed them the +4 is unspecified. [CODE-CONFIRMED that these surfaces read `display_delta`; UNVERIFIED how the plan wires them]

- **Balance/behavioral consequences are explicitly deferred.** Re-attributing the swing from victim-loss to attacker-gain changes what the score floor clips (a +8 attacker gain is never floored; a −8 victim loss often was), which can shift round-win/tie rates. The spec correctly scopes out win-prob retraining, but there is no acceptance criterion asserting the *aggregate* swing invariant beyond a single-turn unit test — the "12-point swing unchanged" claim is verified only at the per-turn delta level, not across the floor interaction. [UNVERIFIED]

- **`betray-helper-impact-review.md` line references are already stale.** The spec tells the implementer to mark that doc superseded; note its internal line numbers (`resolve_turn, lines ~67–92`, `apply_inround_turn, lines 110–140`, `move_effect, lines 226–234`) no longer match the real files (the functions are at different offsets after the mutual-help-decay feature landed). Marking it superseded avoids relying on those numbers, but anyone using it as an implementation map will be misled. [CODE-CONFIRMED the line numbers drifted]

```json
{"reviewed": true, "findings": [{"severity": "MEDIUM", "title": "Mirror HURT branch has no attacker-credit line to edit", "detail": "apply_inround_turn only modifies the target on a HURT; §3.3's damage-swap instruction, taken literally, drops the attacker's new +4 and reintroduces the resolver/mirror divergence R1 exists to prevent."}, {"severity": "MEDIUM", "title": "D1(a) overloads the attacker HURT's display_delta, which already holds the victim's -4", "detail": "The HURT action's display_delta is the target's -4 (viewer.py:393-397); the spec never names a distinct field for the attacker's +4, so surfacing it there corrupts the -4-on-target signal AC5 and test_viewer_shows_per_move_effect_on_target depend on."}, {"severity": "MEDIUM", "title": "Positive HURT display_delta collides with unlisted match_summary.py consumer", "detail": "match_summary._superlatives treats any positive display_delta as a 'biggest gift' candidate, so a +4 on a betraying HURT can be reported as a gift; match_summary.py is in neither the in-scope nor out-of-scope lists."}, {"severity": "LOW", "title": "AC6 grep misses two stale '-8' comments in viewer.py", "detail": "viewer.py:331 and :353 state -8 in prose, which AC6's BETRAYAL_HURT_POINTS grep won't catch and the viewer.py scope entry (display_delta only) doesn't cover."}, {"severity": "LOW", "title": "Design-doc -8 edits span three locations, not one bullet", "detail": "Betrayal -8 lives in the payoff-math bullet (line 42), the worked-scenarios table row (line 55), and the edge-case bullet (line 66); the scope collapses these into a single 'bullet rewrite'."}, {"severity": "LOW", "title": "AC7 uses .venv/bin/ preflight form diverging from CLAUDE.md python3 -m form", "detail": "The worktree .venv/bin/{ruff,mypy,pytest} exist so the command runs, but it differs from the constitution's Preflight Gate wording and assumes the worktree venv, not a bare-PATH tool."}]}
```

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: All 6 findings accepted and folded into spec.md: F1 mirror needs ADDED attacker-credit line (§3.3); F2 display_delta overload + F3 match_summary gift-mislabel resolved via dedicated betrayal_bonus key (§3.4/§6); F4 stale -8 comments in viewer.py scope; F5 three design-doc -8 sites (R3); F6 .venv preflight form intentional.
