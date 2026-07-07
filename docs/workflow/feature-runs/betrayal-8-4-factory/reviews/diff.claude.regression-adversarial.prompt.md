Review this diff artifact using a regression-adversarial lens.
Stay scoped to that lens.
Approach the artifact adversarially: look for hidden flaws, omitted cases, and weak assumptions before giving credit.
This artifact contains code. Specifically hunt for swallowed errors and silent fallbacks: an except block that returns a default / None / empty / fake-success instead of re-raising, a subprocess or shell command whose return code or stderr is never checked, and any shim, stub, or fail-open path that lets a real failure read as success. Report each as a finding unless the code explicitly labels it as deliberate advisory fail-open.
No code context files were provided. Flag any finding that depends on an assumption about the existing codebase as [UNVERIFIED] and limit it to MEDIUM severity or lower.
The full review artifact text is included below in this prompt.
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

Artifact: implementation.diff.patch
diff --git a/app/games/hoard_hurt_help/rules.py b/app/games/hoard_hurt_help/rules.py
index a081ce6a..d049e628 100644
--- a/app/games/hoard_hurt_help/rules.py
+++ b/app/games/hoard_hurt_help/rules.py
@@ -8,13 +8,13 @@ HOARD_POINTS = 2  # HOARD: actor gains this, no target
 HELP_POINTS = 4  # HELP: target gains this, actor gains 0
 HURT_POINTS = 4  # HURT: target loses this, actor gains 0
 MUTUAL_HELP_BONUS = 4  # extra to each side on a pair's FIRST mutual HELP this match
-BETRAYAL_HURT_POINTS = 8  # HURT lands this hard when the target is HELPing you this turn
+BETRAYAL_BONUS = 4  # extra to the ATTACKER when they HURT a player HELPing them this turn
 # Mutual help decays -1 each time the SAME pair repeats it within a match, flooring
 # the pair's per-side total at MUTUAL_HELP_FLOOR (= HOARD_POINTS, so a farmed pact is
 # no better than hoarding): total = max(MUTUAL_HELP_FLOOR, HELP_POINTS + MUTUAL_HELP_BONUS - k).
 MUTUAL_HELP_FLOOR = 2
 
-GAME_RULES_TEXT = f"""# Hoard-Hurt-Help — Official Rules (v4)
+GAME_RULES_TEXT = f"""# Hoard-Hurt-Help — Official Rules (v5)
 
 The goal is to win more rounds than any other agent over the course of the game.
 
@@ -32,7 +32,7 @@ In the act phase, choose exactly one action. You cannot target yourself.
 - **HURT stacks.** Multiple players HURTing the same target each contribute -{HURT_POINTS}.
 - **Mutual-help bonus.** If A HELPs B and B HELPs A in the same turn, each gets an extra +{MUTUAL_HELP_BONUS} on top of the base +{HELP_POINTS} — net +{HELP_POINTS + MUTUAL_HELP_BONUS} each the first time a pair does it.
 - **Mutual-help decays.** Each time the *same pair* repeats a mutual help in a match, the bonus drops by 1. So that pair's net falls +{HELP_POINTS + MUTUAL_HELP_BONUS}, +{HELP_POINTS + MUTUAL_HELP_BONUS - 1}, +{HELP_POINTS + MUTUAL_HELP_BONUS - 2}, … down to a floor of +{MUTUAL_HELP_FLOOR} each (no better than HOARD). The count is match-wide, not per round. Helping a *fresh* partner resets to +{HELP_POINTS + MUTUAL_HELP_BONUS} — farming one ally pays less over time than spreading pacts around.
-- **Betraying a helper.** If you HURT a player who is HELPing *you* on the same turn, your HURT lands for -{BETRAYAL_HURT_POINTS} instead of -{HURT_POINTS}. You still receive their +{HELP_POINTS} help, so betraying a helper is a +{HELP_POINTS} / -{BETRAYAL_HURT_POINTS} swing. (Moves resolve simultaneously, so this is a read on whether your target will help you.)
+- **Betraying a helper.** If you HURT a player who is HELPing *you* on the same turn, you gain an extra +{BETRAYAL_BONUS} bonus on top of the +{HELP_POINTS} help you still receive — so you net +{HELP_POINTS + BETRAYAL_BONUS} that turn. The player you HURT takes the normal -{HURT_POINTS}. Net swing: attacker +{HELP_POINTS + BETRAYAL_BONUS} / victim -{HURT_POINTS}. (Moves resolve simultaneously, so this is a read on whether your target will help you.)
 - HELP and HURT against the same target both resolve; the target's score moves by the net.
 
 ## Score floor
diff --git a/app/games/hoard_hurt_help/scoring.py b/app/games/hoard_hurt_help/scoring.py
index 5cdf3fcd..efcd10e5 100644
--- a/app/games/hoard_hurt_help/scoring.py
+++ b/app/games/hoard_hurt_help/scoring.py
@@ -14,7 +14,7 @@ from sqlalchemy import select
 from sqlalchemy.ext.asyncio import AsyncSession
 
 from app.games.hoard_hurt_help.rules import (
-    BETRAYAL_HURT_POINTS,
+    BETRAYAL_BONUS,
     DEFAULT_MISSED_MESSAGE,
     HELP_POINTS,
     HOARD_POINTS,
@@ -174,12 +174,15 @@ async def resolve_turn(db: AsyncSession, turn: Turn) -> None:
         elif s.action == "HELP" and s.target_player_id in delta:
             delta[s.target_player_id] += HELP_POINTS
         elif s.action == "HURT" and s.target_player_id in delta:
-            # Betraying a helper: HURTing a player who is HELPing you this same
-            # turn lands for BETRAYAL_HURT_POINTS instead of the base HURT_POINTS.
+            # The victim always takes the normal HURT_POINTS.
+            delta[s.target_player_id] -= HURT_POINTS
+            # Betraying a helper: if the target is HELPing the attacker this same
+            # turn, the ATTACKER gains a BETRAYAL_BONUS on top of the +HELP_POINTS
+            # they already receive — attacker +HELP_POINTS+BETRAYAL_BONUS, victim
+            # -HURT_POINTS. (The victim's loss is unchanged from a normal HURT.)
             betrayed_helper = help_targets.get(s.target_player_id) == s.player_id
-            delta[s.target_player_id] -= (
-                BETRAYAL_HURT_POINTS if betrayed_helper else HURT_POINTS
-            )
+            if betrayed_helper:
+                delta[s.player_id] += BETRAYAL_BONUS
 
     # Mutual-help bonus, DECAYED per pair: for each HELP pair where both helped
     # each other, add the bonus to each side once. The bonus shrinks by 1 for each
@@ -219,15 +222,16 @@ def apply_inround_turn(
 ) -> dict[str, int]:
     """Return a new in-round score map after applying one turn's actions.
 
-    This is the *viewer's* running-score view — used for lead tracking and the
-    win-probability features. It floors each HURT individually and credits a
-    mutual-help actor the decayed per-side total (`mutual_value` on the action,
-    falling back to the fresh-pact HELP_POINTS + MUTUAL_HELP_BONUS if absent). A
-    HURT against a player who HELPs the attacker this same turn lands for
-    BETRAYAL_HURT_POINTS, mirroring `resolve_turn`. It is a display approximation
-    and is deliberately distinct from `resolve_turn`, which is authoritative and
-    floors the summed per-player delta. Keep them separate; do not route
-    resolution through this helper.
+    This is the *viewer's* running-score view — used for lead tracking. It floors
+    each HURT individually and credits a mutual-help actor the decayed per-side
+    total (`mutual_value` on the action, falling back to the fresh-pact
+    HELP_POINTS + MUTUAL_HELP_BONUS if absent). When a player HURTs someone who is
+    HELPing them this same turn (betraying a helper), the victim takes the normal
+    HURT_POINTS and the ATTACKER gains a BETRAYAL_BONUS on top of the +HELP_POINTS
+    they receive — mirroring `resolve_turn`. It is a display approximation and is
+    deliberately distinct from `resolve_turn`, which is authoritative and floors
+    the summed per-player delta. Keep them separate; do not route resolution
+    through this helper.
 
     Action dicts use keys: "action", "agent_id", optional "target_id",
     optional "mutual", optional "mutual_value" (the decayed per-side total — the
@@ -251,8 +255,10 @@ def apply_inround_turn(
         elif action == "HELP" and target:
             new_inround[target] = new_inround.get(target, 0) + HELP_POINTS
         elif action == "HURT" and target:
-            damage = (
-                BETRAYAL_HURT_POINTS if help_targets.get(target) == actor else HURT_POINTS
-            )
-            new_inround[target] = max(0, new_inround.get(target, 0) - damage)
+            # The victim always takes the normal HURT_POINTS (floored per-hurt).
+            new_inround[target] = max(0, new_inround.get(target, 0) - HURT_POINTS)
+            # Betraying a helper: the attacker gains a BETRAYAL_BONUS (a gain — not
+            # floored) on top of the +HELP_POINTS the victim's HELP already credits.
+            if help_targets.get(target) == actor:
+                new_inround[actor] = new_inround.get(actor, 0) + BETRAYAL_BONUS
     return new_inround
diff --git a/app/games/hoard_hurt_help/viewer.py b/app/games/hoard_hurt_help/viewer.py
index aa0af10a..ffa3682d 100644
--- a/app/games/hoard_hurt_help/viewer.py
+++ b/app/games/hoard_hurt_help/viewer.py
@@ -20,7 +20,7 @@ from typing import TYPE_CHECKING, Any
 
 from app.games.hoard_hurt_help.match_summary import build_final_summary
 from app.games.hoard_hurt_help.rules import (
-    BETRAYAL_HURT_POINTS,
+    BETRAYAL_BONUS,
     HELP_POINTS,
     MUTUAL_HELP_BONUS,
     MUTUAL_HELP_FLOOR,
@@ -192,6 +192,11 @@ def _build_rc_data(
                     "delta": a["display_delta"],
                     "mutual": a["mutual"],
                     "betrayal": a["betrayal"],
+                    # Same-turn betrayal (HURT a helper): the attacker's bonus so
+                    # the animation can show +BETRAYAL_BONUS on the attacker.
+                    # Distinct from `betrayal` (the cross-turn pact-partner signal).
+                    "betrayed_helper": a.get("betrayed_helper", False),
+                    "betrayal_bonus": a.get("betrayal_bonus", 0),
                     "missed": a["was_defaulted"],
                     "msg": (a.get("message") or "").strip(),
                 }
@@ -328,8 +333,10 @@ async def build_pd_replay_view(
                     "was_defaulted": action.was_defaulted,
                     "mutual": False,
                     "betrayal": False,
-                    # HURT against a player who is HELPing you this same turn — lands for -8.
+                    # HURT against a player who is HELPing you this same turn: the
+                    # attacker gains BETRAYAL_BONUS (the victim takes the normal -4).
                     "betrayed_helper": False,
+                    "betrayal_bonus": 0,
                 }
             )
 
@@ -350,9 +357,11 @@ async def build_pd_replay_view(
                 a["mutual"] = True
                 this_mutual.add(pair)
             elif a["action"] == "HURT":
-                # Betraying a helper: HURT a player who is HELPing you this turn → -8.
+                # Betraying a helper: HURT a player who is HELPing you this turn.
+                # The attacker gains BETRAYAL_BONUS (victim takes the normal -4).
                 if helps.get(tgt) == a["agent_id"]:
                     a["betrayed_helper"] = True
+                    a["betrayal_bonus"] = BETRAYAL_BONUS
                 # Cross-turn betrayal: HURT last turn's pact partner.
                 if pair in prev_mutual:
                     a["betrayal"] = True
@@ -392,9 +401,11 @@ async def build_pd_replay_view(
                     a["display_delta"] = a["target_delta"]
             else:
                 a["display_action"] = "HURT"
-                a["display_delta"] = (
-                    -BETRAYAL_HURT_POINTS if a["betrayed_helper"] else a["target_delta"]
-                )
+                # The HURT chip's delta is always the victim's loss (-4). The
+                # attacker's betrayal gain rides the separate `betrayal_bonus` key
+                # (rendered as its own +4 chip), so `display_delta` stays negative
+                # and match_summary's positive-delta "biggest gift" scan is unaffected.
+                a["display_delta"] = a["target_delta"]
 
         # Running in-round score (resets each round) → who leads, for the
         # play-by-play "lead change" beat.
diff --git a/app/templates/fragments/move_legend.html b/app/templates/fragments/move_legend.html
index 434b53e7..8e1cfe7b 100644
--- a/app/templates/fragments/move_legend.html
+++ b/app/templates/fragments/move_legend.html
@@ -2,6 +2,6 @@
    the only cue — each chip reads on its own (accessibility). #}
 <ul class="move-legend" aria-label="The three moves">
     <li class="hchip hoard"><span class="hchip-move">Hoard</span> <span class="hchip-eff">+2 to yourself</span></li>
-    <li class="hchip hurt"><span class="hchip-move">Hurt</span> <span class="hchip-eff">-4 to another, -8 if betraying</span></li>
+    <li class="hchip hurt"><span class="hchip-move">Hurt</span> <span class="hchip-eff">-4 to another; +4 to you if betraying a helper</span></li>
     <li class="hchip help"><span class="hchip-move">Help</span> <span class="hchip-eff">+4 to another; mutual +8 each, bonus decays each round</span></li>
 </ul>
diff --git a/app/templates/fragments/robot_circle/_markup.html b/app/templates/fragments/robot_circle/_markup.html
index 896080ef..57603c9a 100644
--- a/app/templates/fragments/robot_circle/_markup.html
+++ b/app/templates/fragments/robot_circle/_markup.html
@@ -58,7 +58,7 @@
 
       <div class="rc-legend">
         <span><span class="rc-sw" style="background:var(--hoard,#b07e0d)"></span><strong>Hoard</strong> — +2 to yourself</span>
-        <span><span class="rc-sw" style="background:var(--hurt,#c0392b)"></span><strong>Hurt</strong> — -4 to another, -8 if betraying</span>
+        <span><span class="rc-sw" style="background:var(--hurt,#c0392b)"></span><strong>Hurt</strong> — -4 to another; +4 to you if betraying a helper</span>
         <span><span class="rc-sw" style="background:var(--help,#1a8f4c)"></span><strong>Help</strong> — +4 to another; mutual +8 each, bonus decays each round</span>
       </div>
     </div>
diff --git a/app/templates/fragments/robot_circle/_replay_script.html b/app/templates/fragments/robot_circle/_replay_script.html
index 4f31244b..595928a7 100644
--- a/app/templates/fragments/robot_circle/_replay_script.html
+++ b/app/templates/fragments/robot_circle/_replay_script.html
@@ -99,7 +99,11 @@
         if(a.action==='HOARD')                        { sim[a.agent]=(sim[a.agent]||0)+2; }
         else if(a.action==='HELP'&&a.mutual)          { sim[a.agent]=(sim[a.agent]||0)+8; }
         else if(a.action==='HELP'&&!a.mutual&&a.target){ sim[a.target]=(sim[a.target]||0)+4; }
-        else if(a.action==='HURT'&&a.target)          { sim[a.target]=Math.max(0,(sim[a.target]||0)-4); }
+        else if(a.action==='HURT'&&a.target)          { sim[a.target]=Math.max(0,(sim[a.target]||0)-4);
+          // Betraying a helper: the attacker also gains +4 (the victim's HELP is
+          // already credited above). Keeps this snapshot in step with the resolver
+          // and with playAction, which renderTurn reseeds rScore from.
+          if(a.betrayed_helper){ sim[a.agent]=(sim[a.agent]||0)+4; } }
       });
       // Round just ended → award the round win (split evenly on a tie).
       if(lastIdxOfRound[t.round]===i){
@@ -914,6 +918,10 @@
           eyes(a.target,'wide',800);
           showDelta(T, -4);
           rScore[a.target]=Math.max(0,(rScore[a.target]||0)-4);
+          // Betraying a helper: the attacker gains +4 (on top of the +4 the
+          // victim's HELP already gave them). Shown on the attacker + credited to
+          // the live rScore, matching the resolver and the computeScores snapshot.
+          if(a.betrayed_helper){ showDelta(el, 4); rScore[a.agent]=(rScore[a.agent]||0)+4; }
           dmgRail(a.target);
           updateRail();
           T.classList.add('recoil','hit');
diff --git a/app/templates/fragments/turn_block.html b/app/templates/fragments/turn_block.html
index d57cbd3a..1e643187 100644
--- a/app/templates/fragments/turn_block.html
+++ b/app/templates/fragments/turn_block.html
@@ -28,6 +28,11 @@
                 {% if a.display_delta is not none %}
                 <span class="delta {{ 'pos' if a.display_delta > 0 else ('neg' if a.display_delta < 0 else 'zero') }}">{{ "%+d" | format(a.display_delta) }}</span>
                 {% endif %}
+                {# Betraying a helper: the attacker's own +BETRAYAL_BONUS gain, shown
+                   as a distinct positive chip so it isn't buried behind the -4 the
+                   HURT does to the victim. Gated on betrayal_bonus (the same-turn
+                   signal), not the cross-turn `betrayal` flag below. #}
+                {% if a.betrayal_bonus %}<span class="delta pos betrayal-bonus" title="Betrayal bonus for HURTing a helper">{{ "%+d" | format(a.betrayal_bonus) }} betrayal</span>{% endif %}
                 {% if a.betrayal %}<span class="tag tag-betrayal">betrayal</span>{% endif %}
                 {% if a.was_defaulted %}<span class="missed">missed turn</span>{% endif %}
             </div>
diff --git a/docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md b/docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md
index be731a2c..f79eafcc 100644
--- a/docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md
+++ b/docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md
@@ -38,8 +38,8 @@ Base values per action:
 Combo bonus:
 - If A Helps B **and** B Helps A → each gets a **+4 mutual-help bonus** on top of the +4 base, for a total of +8 each.
 
-Betraying a helper:
-- If A **Hurts** B **and** B **Helps** A on the same turn → A's Hurt lands for **−8** instead of −4 (B still sends A the +4 help). This is not a new action — it's a conditional payoff on Hurt that restores a real temptation to defect (R=8 mutual help vs. an even bigger swing for betraying a helper). See the analysis in `betray-helper-impact-review.md`.
+Betraying a helper (the "8/4" split):
+- If A **Hurts** B **and** B **Helps** A on the same turn → A gains a **+4 bonus** on top of the +4 help B still sends (so A nets **+8** that turn), and B takes the **normal −4** (not −8). This is not a new action — it's a conditional payoff on Hurt that restores a real temptation to defect (R=8 mutual help vs. a +8 for betraying a helper). The 12-point relative swing is unchanged from the earlier design; it is re-split so the **attacker rises** (+4 bonus) instead of the victim cratering (−8). See the analysis in `betray-helper-impact-review.md` (superseded by this implementation).
 
 Mutual help decays (feature `mutual-help-decay`):
 - A given **pair's** mutual-help payoff is worth less each time *that same pair* repeats it within a match. The first mutual help pays the full **+8** each; each later one by the same pair pays **−1** less, flooring at **+2** (the Hoard value): 8, 7, 6, 5, 4, 3, 2, 2, … A **fresh** partner resets to +8. The counter is **per pair, per match** — it does **not** reset each round. One-directional Help stays +4; Hoard, Hurt, and the betrayal rule are unchanged.
@@ -52,7 +52,7 @@ Mutual help decays (feature `mutual-help-decay`):
 |---|---|---|
 | Mutual Help (the Pact): A→B, B→A | +8 | +8 |
 | Hoard-betrayal: A Helps B, B Hoards | 0 | +6 (+2 hoard, +4 from A's help) |
-| Betray a helper: A Hurts B, B Helps A | +4 (from B's help) | −8 (the betrayal) |
+| Betray a helper: A Hurts B, B Helps A | +8 (+4 from B's help, +4 betrayal bonus) | −4 (the normal Hurt) |
 | Baseline: both Hoard | +2 | +2 |
 | Team Attack: A and B both Hurt C | 0 | 0 (C takes −8) |
 
@@ -63,7 +63,7 @@ Mutual help decays (feature `mutual-help-decay`):
 - **Hurt stacks fully.** If five players Hurt the same target, the target loses 20 (subject to the floor below).
 - **Scores floor at zero.** Damage that would push a player below 0 is clipped at 0. Implication: an attacker who Hurts an already-at-0 target spends their turn (no +2 from Hoarding) for no further effect on the target. That is intentional — strategic, not a bug.
 - **Independent resolution.** Help and Hurt against the same player both resolve. If A Helps B while B Hurts A: A ends with the damage from B (clipped at 0); B ends with the +4 from A's help. Hoarders Hoard, helpers help, hurters hurt — all in parallel.
-- **Betraying a helper.** Hurting a player who is Helping *you* this same turn deals −8 instead of −4. Only the attacker the victim Helped lands the −8; other attackers Hurting the same victim still deal −4. The score floor applies to the summed delta as usual.
+- **Betraying a helper.** Hurting a player who is Helping *you* this same turn earns the attacker a **+4 bonus** on top of the +4 help they receive (attacker nets +8); the victim takes the **normal −4**. Only the attacker the victim Helped gets the bonus; other attackers Hurting the same victim deal the ordinary −4 and get no bonus. The score floor applies to the summed delta as usual (the victim's −4 can floor; the attacker's +4 gain never does).
 - **Mutual-help bonus is per pair, at most one per turn.** Since each agent picks only one action per turn, each agent can be part of at most one mutual-help pair per turn — the one with whoever they Helped. Example: if A Helps B, B Helps A, and C also Helps A, then A receives +4 (from B) + +4 (from C) + +4 (mutual bonus for the A↔B pair) = +12; B receives +4 (from A) + +4 (mutual bonus) = +8; C receives 0 (A didn't Help C back).
 - **Mutual help decays per pair, per match** (feature `mutual-help-decay`). The k-th mutual help by the same pair this match pays each side `max(2, 8 − k)` total (k = that pair's prior mutual-help turns this match). Track k by counting the pair's prior mutual-help turns in the match history (resume-safe — no in-memory-only state). Resets only at match end. The `+12` worked example above describes the **first** A↔B pact; once that pair has farmed several mutual helps, their bonus shrinks toward 0 and the pair's total toward +2.
 
diff --git a/docs/games/hoard-hurt-help/betray-helper-impact-review.md b/docs/games/hoard-hurt-help/betray-helper-impact-review.md
index 65b2a860..d49c438c 100644
--- a/docs/games/hoard-hurt-help/betray-helper-impact-review.md
+++ b/docs/games/hoard-hurt-help/betray-helper-impact-review.md
@@ -1,9 +1,23 @@
 # Betraying A Helper — Impact Review (for Chris)
 
-**Proposed rule:** If you **HURT** a player who is **HELPing you in the same turn**,
-that player takes **−8** instead of the normal −4. Mutual help stays **+8 each**.
-The attacker gets no bonus (they still pocket the +4 from the victim's help).
-Net effect on the betrayal turn: **attacker +4, victim −8 → a 12-point swing.**
+> **SUPERSEDED / IMPLEMENTED (2026-07-07).** This review evaluated an *earlier*
+> shape of the rule (victim −8, attacker no bonus). The rule that actually shipped
+> is the **"8/4" re-split**: the victim takes the **normal −4** and the **attacker
+> gains a +4 bonus** (`BETRAYAL_BONUS`) on top of the +4 help — net **attacker +8 /
+> victim −4**. The 12-point relative swing is unchanged; it was re-split so the
+> attacker rises instead of the victim cratering. The (a)/(b) `move_effect` decision
+> below was resolved to a third option — a dedicated `betrayal_bonus` key on the
+> viewer action (`move_effect` stays nominal). See
+> `docs/workflow/feature-runs/betrayal-8-4-factory/` for the shipped design, plan,
+> and tests. Everything below is kept for historical context only — its −8 numbers
+> and its references to the old betrayal-hurt constant describe the pre-8/4
+> proposal, not the current code (the shipped constant is `BETRAYAL_BONUS`).
+
+**Original proposal (pre-8/4, historical):** If you **HURT** a player who is
+**HELPing you in the same turn**, that player takes **−8** instead of the normal −4.
+Mutual help stays **+8 each**. The attacker gets no bonus (they still pocket the +4
+from the victim's help). Net effect on the betrayal turn: **attacker +4, victim −8 →
+a 12-point swing.** *(Shipped instead as 8/4 — see the banner above.)*
 
 Reduced payoff (A vs B, "cooperate" = HELP partner):
 - Both HELP (pact): **+8 / +8**
@@ -35,12 +49,15 @@ Tick the box once you've eyeballed it.
       elif s.action == "HURT" ...: delta[s.target_id]   -= HURT_POINTS   # always 4
   # then mutual-help bonus, then floor
   ```
-  Change: build the HELP map first, then in the HURT branch subtract
-  `BETRAYAL_HURT_POINTS` (8) when `help_targets.get(victim) == attacker`, else
-  `HURT_POINTS` (4). Floor + mutual-bonus logic stay as-is.
-
-- [ ] **`app/games/hoard_hurt_help/rules.py`, lines 7–10**
-  Add `BETRAYAL_HURT_POINTS = 8` next to the other constants.
+  Change *(pre-8/4 proposal — NOT what shipped)*: build the HELP map first, then in
+  the HURT branch subtract the old betrayal-hurt value (8) when
+  `help_targets.get(victim) == attacker`, else `HURT_POINTS` (4). Floor +
+  mutual-bonus logic stay as-is. *(Shipped instead: victim always `HURT_POINTS`;
+  the attacker gains `BETRAYAL_BONUS` — see the banner.)*
+
+- [ ] **`app/games/hoard_hurt_help/rules.py`, lines 7–10** *(pre-8/4 proposal)*
+  Add the old betrayal-hurt constant (= 8) next to the other constants. *(Shipped
+  instead: `BETRAYAL_BONUS = 4` — the attacker's bonus, not the victim's damage.)*
 
 ---
 
diff --git a/tests/test_inround_mirror.py b/tests/test_inround_mirror.py
index 760d81f0..9f9a2230 100644
--- a/tests/test_inround_mirror.py
+++ b/tests/test_inround_mirror.py
@@ -1,8 +1,10 @@
 """Unit tests for `apply_inround_turn` — the viewer's running-score mirror.
 
 Pure function (dict in, dict out). It approximates `resolve_turn` for lead
-tracking / win-prob display, including betraying a helper: a HURT against a
-player who HELPs the attacker this same turn lands for BETRAYAL_HURT_POINTS.
+tracking, including betraying a helper: when a player HURTs someone who HELPs
+them this same turn, the victim takes the normal HURT_POINTS and the attacker
+gains a BETRAYAL_BONUS on top of the +HELP_POINTS they receive (attacker +8 /
+victim -4), mirroring `resolve_turn`.
 """
 
 from __future__ import annotations
@@ -47,8 +49,13 @@ def test_mirror_normal_hurt_is_four():
     assert out == {"A": 0, "B": 8}  # 10 + 2 hoard - 4 hurt
 
 
-def test_mirror_betraying_a_helper_is_eight():
-    """HURTing a player who HELPs you this same turn drops them by 8."""
+def test_mirror_betraying_a_helper_pays_the_attacker_eight():
+    """Betraying a same-turn helper: attacker +8, victim -4 (mirrors resolve_turn).
+
+    A HURTs B while B HELPs A. A gets +4 (B's help) + +4 (BETRAYAL_BONUS) = +8;
+    B (from 10) takes the normal -4 → 6. The explicit dict pins the victim at
+    start-4 so a stale victim -8 cannot pass.
+    """
     out = apply_inround_turn(
         {"A": 0, "B": 10},
         [
@@ -56,7 +63,23 @@ def test_mirror_betraying_a_helper_is_eight():
             {"action": "HELP", "agent_id": "B", "target_id": "A"},
         ],
     )
-    assert out == {"A": 4, "B": 2}  # A: +4 from B's help; B: 10 - 8 betrayal
+    assert out == {"A": 8, "B": 6}  # A: +4 help + +4 bonus; B: 10 - 4
+
+
+def test_mirror_betrayed_victim_floors_per_hurt():
+    """The mirror floors the betrayal victim per-hurt (its deliberate divergence).
+
+    B HELPs A (A +8 via betrayal). A HURTs B. B starts at 5 → 5 - 4 = 1 (the
+    changed damage of 4, not the old 8, moves this boundary: old would floor to 0).
+    """
+    out = apply_inround_turn(
+        {"A": 0, "B": 5},
+        [
+            {"action": "HURT", "agent_id": "A", "target_id": "B"},
+            {"action": "HELP", "agent_id": "B", "target_id": "A"},
+        ],
+    )
+    assert out == {"A": 8, "B": 1}  # A: +8; B: 5 - 4 = 1 (would be 0 under the old -8)
 
 
 def test_mirror_mutual_help_is_eight_each():
@@ -138,3 +161,35 @@ def test_rc_caption_shows_decayed_value_not_stale_eight():
     cap = blob["turns"][0]["cap"]
     assert "+6 each" in cap
     assert "+8" not in cap
+
+
+def _betrayal_actions() -> list[dict]:
+    """Action dicts shaped as `build_pd_replay_view` emits a same-turn betrayal.
+
+    A HURTs B while B HELPs A: A's action carries betrayed_helper + betrayal_bonus;
+    A's HURT display_delta is the victim's -4 (the +4 rides betrayal_bonus).
+    """
+    return [
+        {"agent_id": "A", "action": "HURT", "target_id": "B", "mutual": False,
+         "betrayal": False, "betrayed_helper": True, "betrayal_bonus": 4,
+         "display_delta": -4, "was_defaulted": False, "message": ""},
+        {"agent_id": "B", "action": "HELP", "target_id": "A", "mutual": False,
+         "betrayal": False, "betrayed_helper": False, "betrayal_bonus": 0,
+         "display_delta": 4, "was_defaulted": False, "message": ""},
+    ]
+
+
+def test_rc_data_threads_betrayed_helper_and_bonus():
+    """The robot-circle JSON must carry `betrayed_helper`/`betrayal_bonus` so the
+    animation can show the attacker's +4 (guard for the review-F2 silent-animation
+    gap: without this thread the feed chip shows +4 but the animation nothing)."""
+    scoreboard = [{"agent_id": "A"}, {"agent_id": "B"}]
+    history = [
+        {"round": 1, "turn": 1, "messages": [], "actions": _betrayal_actions()}
+    ]
+    blob = json.loads(_build_rc_data(scoreboard, history))
+    attacker = next(a for a in blob["turns"][0]["actions"] if a["agent"] == "A")
+    assert attacker["betrayed_helper"] is True
+    assert attacker["betrayal_bonus"] == 4
+    # The HURT's own delta stays the victim's -4 (the +4 is on betrayal_bonus).
+    assert attacker["delta"] == -4
diff --git a/tests/test_resolver.py b/tests/test_resolver.py
index 81ba9f3c..6e8f58e7 100644
--- a/tests/test_resolver.py
+++ b/tests/test_resolver.py
@@ -226,11 +226,12 @@ async def test_hurt_against_zero_target(db):
     assert b.current_round_score == 0  # +2 - 4, clipped to 0
 
 
-async def test_betraying_a_helper_hurts_for_eight(db):
-    """HURTing a player who HELPs you this turn lands for -8, not -4.
+async def test_betraying_a_helper_pays_the_attacker_eight(db):
+    """Betraying a same-turn helper: attacker +8, victim -4 (the "8/4" split).
 
-    B HELPs A (A gets +4). A HURTs B → betrays the helper for -8 to B.
-    A ends +4; B (starting at 10) ends 10 - 8 = 2.
+    B HELPs A (A gets +4). A HURTs B → betrays the helper: A gains a +4
+    BETRAYAL_BONUS on top of the help (net +8), B takes the normal -4.
+    A ends +8; B (starting at 10) ends 10 - 4 = 6.
     """
     game, [a, b] = await _make_game_with_players(db, 2)
     b.current_round_score = 10
@@ -241,15 +242,15 @@ async def test_betraying_a_helper_hurts_for_eight(db):
     await resolve_turn(db, turn)
     await db.refresh(a)
     await db.refresh(b)
-    assert a.current_round_score == 4  # +4 from B's help (A's HURT gives A nothing)
-    assert b.current_round_score == 2  # 10 - 8 betrayal
+    assert a.current_round_score == 8  # +4 from B's help + +4 betrayal bonus
+    assert b.current_round_score == 6  # 10 - 4 (normal HURT, no longer -8)
 
 
 async def test_hurt_non_helper_stays_four(db):
     """A normal HURT (target did NOT help the attacker) still lands for -4.
 
-    B HOARDs (does not help A). A HURTs B → base -4, not the betrayal -8.
-    B (starting at 10) ends 10 + 2 (hoard) - 4 = 8.
+    B HOARDs (does not help A). A HURTs B → base -4, and A gets NO bonus.
+    B (starting at 10) ends 10 + 2 (hoard) - 4 = 8. A ends 0 (HURT pays nothing).
     """
     game, [a, b] = await _make_game_with_players(db, 2)
     b.current_round_score = 10
@@ -258,15 +259,17 @@ async def test_hurt_non_helper_stays_four(db):
     await _submit(db, turn, a, "HURT", target=b)
     await _submit(db, turn, b, "HOARD")
     await resolve_turn(db, turn)
+    await db.refresh(a)
     await db.refresh(b)
-    assert b.current_round_score == 8  # 10 + 2 - 4, NOT -8
+    assert a.current_round_score == 0  # HURT on a non-helper pays the attacker nothing
+    assert b.current_round_score == 8  # 10 + 2 - 4
 
 
-async def test_betrayal_only_for_the_helped_attacker(db):
-    """Only the attacker the victim HELPed lands the -8; other attackers stay -4.
+async def test_betrayal_bonus_only_for_the_helped_attacker(db):
+    """Only the attacker the victim HELPed gets the +4 bonus; every HURT is -4.
 
-    B HELPs A. A HURTs B (betrayal -8). C HURTs B (normal -4, B never helped C).
-    B (starting at 20) ends 20 - 8 - 4 = 8. A gets +4 from B's help.
+    B HELPs A. A HURTs B (betrayal → A +8). C HURTs B (normal, C gets nothing).
+    B (starting at 20) ends 20 - 4 - 4 = 12. A gets +8; C gets 0.
     """
     game, [a, b, c] = await _make_game_with_players(db, 3)
     b.current_round_score = 20
@@ -278,8 +281,29 @@ async def test_betrayal_only_for_the_helped_attacker(db):
     await resolve_turn(db, turn)
     await db.refresh(a)
     await db.refresh(b)
-    assert a.current_round_score == 4
-    assert b.current_round_score == 8  # 20 - 8 (A betrayal) - 4 (C normal)
+    await db.refresh(c)
+    assert a.current_round_score == 8  # +4 help + +4 betrayal bonus
+    assert c.current_round_score == 0  # C HURT someone who did not help C → no bonus
+    assert b.current_round_score == 12  # 20 - 4 (A) - 4 (C), both normal HURTs
+
+
+async def test_betrayal_victim_floored_at_zero(db):
+    """The score floor still applies to the victim's summed delta on a betrayal.
+
+    B HELPs A (A +8 via betrayal). A HURTs B. B starts at 3 → 3 - 4 = -1, floored
+    to 0. The floor is on the FINAL delta; the attacker's +8 gain never floors.
+    """
+    game, [a, b] = await _make_game_with_players(db, 2)
+    b.current_round_score = 3
+    await db.commit()
+    turn = await _open_turn(db, game)
+    await _submit(db, turn, a, "HURT", target=b)
+    await _submit(db, turn, b, "HELP", target=a)
+    await resolve_turn(db, turn)
+    await db.refresh(a)
+    await db.refresh(b)
+    assert a.current_round_score == 8  # attacker gain unaffected by the victim's floor
+    assert b.current_round_score == 0  # 3 - 4 clipped at 0
 
 
 async def test_missed_turn_defaults_to_hoard(db):
diff --git a/tests/test_rules_text.py b/tests/test_rules_text.py
index 63e1762a..04e611be 100644
--- a/tests/test_rules_text.py
+++ b/tests/test_rules_text.py
@@ -5,8 +5,9 @@ sync with the payoff constants — agents can't strategize around an unstated ru
 from __future__ import annotations
 
 from app.games.hoard_hurt_help.rules import (
-    BETRAYAL_HURT_POINTS,
+    BETRAYAL_BONUS,
     GAME_RULES_TEXT,
+    HELP_POINTS,
     HURT_POINTS,
     MUTUAL_HELP_FLOOR,
     make_game_rules_text,
@@ -15,13 +16,16 @@ from app.games.hoard_hurt_help.rules import (
 
 def test_rules_text_documents_betraying_a_helper():
     assert "Betraying a helper" in GAME_RULES_TEXT
-    # The betrayal magnitude shown must match the constant, and differ from base HURT.
-    assert f"-{BETRAYAL_HURT_POINTS}" in GAME_RULES_TEXT
-    assert BETRAYAL_HURT_POINTS != HURT_POINTS
+    # The 8/4 split must be stated: attacker nets +8 (help + bonus), victim -4.
+    attacker_net = HELP_POINTS + BETRAYAL_BONUS
+    assert f"+{attacker_net}" in GAME_RULES_TEXT  # attacker's net gain
+    assert f"-{HURT_POINTS}" in GAME_RULES_TEXT  # victim takes the normal HURT
+    # The attacker's bonus equals the base HURT under 8/4 — that's intentional.
+    assert BETRAYAL_BONUS == HURT_POINTS
 
 
-def test_rules_text_is_versioned_v4():
-    assert "(v4)" in GAME_RULES_TEXT
+def test_rules_text_is_versioned_v5():
+    assert "(v5)" in GAME_RULES_TEXT
 
 
 def test_rules_text_documents_mutual_help_decay():
diff --git a/tests/test_viewer.py b/tests/test_viewer.py
index 59a3a2fe..90cef382 100644
--- a/tests/test_viewer.py
+++ b/tests/test_viewer.py
@@ -301,6 +301,75 @@ async def test_viewer_shows_per_move_effect_on_target(client, reset_db):
     assert "+0" not in r.text
 
 
+async def test_viewer_shows_attacker_bonus_on_betrayal(client, reset_db):
+    """Betraying a helper must render the attacker's +4 in the feed, not just the
+    victim's -4 (R4 guard — the +4 must reach the screen, not sit in the payload).
+
+    A HURTs B while B HELPs A (same turn) → A betrays the helper: the feed shows
+    the attacker's `+4 betrayal` chip. Under 8/4 the victim's chip is -4 (never -8).
+    """
+    await _seed(reset_db, GameState.COMPLETED)
+    async with reset_db() as db:
+        import sqlalchemy
+
+        from app.models import Player, Turn, TurnSubmission, User
+
+        attacker = (await db.execute(sqlalchemy.select(Player))).scalars().first()
+        u2 = User(google_sub="u2", email="u2@t.com")
+        db.add(u2)
+        await db.flush()
+        bot2, version2 = await make_agent(db, u2, name="AI_1")
+        victim = Player(
+            match_id="G_001",
+            user_id=u2.id,
+            agent_id=bot2.id,
+            seat_name="AI_1",
+            agent_version_id=version2.id if version2 is not None else None,
+            model_self_report=version2.model if version2 is not None else None,
+        )
+        db.add(victim)
+        await db.flush()
+        t = Turn(
+            match_id="G_001",
+            round=1,
+            turn=1,
+            turn_token="tk1",
+            opened_at=datetime.now(timezone.utc),
+            deadline_at=datetime.now(timezone.utc),
+            resolved_at=datetime.now(timezone.utc),
+        )
+        db.add(t)
+        await db.flush()
+        # Attacker HURTs the victim; the victim HELPs the attacker the same turn.
+        db.add(
+            TurnSubmission(
+                turn_id=t.id, player_id=attacker.id, action="HURT",
+                target_player_id=victim.id, message="thanks for the help",
+                points_delta=8, round_score_after=8,
+                submitted_at=datetime.now(timezone.utc),
+            )
+        )
+        db.add(
+            TurnSubmission(
+                turn_id=t.id, player_id=victim.id, action="HELP",
+                target_player_id=attacker.id, message="here you go",
+                points_delta=0, round_score_after=0,
+                submitted_at=datetime.now(timezone.utc),
+            )
+        )
+        await db.commit()
+
+    r = await client.get("/games/hoard-hurt-help/matches/G_001")
+    assert r.status_code == 200
+    # The attacker's +4 betrayal bonus is rendered (not buried) ...
+    assert "+4 betrayal" in r.text
+    # ... and the victim's delta chip is the normal -4, never a stale -8. Match the
+    # rendered delta span content specifically (a bare "-8" substring false-matches
+    # "utf-8" in the page <head>).
+    assert ">-8<" not in r.text
+    assert ">-4<" in r.text
+
+
 async def test_guide_serves_doc(client, reset_db):
     r = await client.get("/guide/setup-mcp")
     assert r.status_code == 200


Return only markdown with exactly these sections:
## Findings
## Residual Risks
Do not include any other sections. After the Residual Risks section, end with the required fenced findings JSON block described above.