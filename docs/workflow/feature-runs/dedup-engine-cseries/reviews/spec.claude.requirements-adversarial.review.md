---
reviewer: "claude"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/dedup-engine-cseries/spec.md"
artifact_sha256: "c8553c878fb2f124d3b5e72f7851086f95b2593ffd13bf5f6f3f93375d809e82"
repo_root: "."
git_head_sha: "9f8279f0cbec1f4e2b081e2e363c998bab9dad70"
git_base_ref: "origin/main"
git_base_sha: "9d36fdc28273b44ec7b04fbdaf747b1b9f18c221"
generation_method: "claude-subagent"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/dedup-engine-cseries/reviews/spec.claude.requirements-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

**[minor]** Acceptance criterion 4 names "the five above" but the prose body still says "the four characterization tests" in the **Verification of "no behavior change"** section ("only valid for the divergent paths once the four characterization tests above exist" and "C2/C4/C5 slices additionally run their characterization tests"). Since round 2 added C8-cancel as test #5, this section is stale: it undercounts the tests and omits C8 from the "additionally run their characterization tests" list. A reader following the Verification section alone could treat C8-cancel as optional, weakening the very oracle guarantee the section exists to assert. Editorial, not a logic gap — hence minor — but it is a real residual inconsistency about the test-sufficiency claim.

**[minor]** The C8-cancel test (test #5) pins "no inline site acquires a `registry.stop` call" and "each site keeps its own commit," but the acceptance criteria never require asserting the *count* of cancel sites unified. Criterion 2's presence-check pattern (one `def`, all call sites delegate) is well-defined for single-symbol clusters, but C8 has ~6 heterogeneous call sites where the test only proves the helper is field-only — it does not prove that *every* one of the six inline sites was actually converted to call `_mark_cancelled`. A refactor could convert four sites, leave two inline, and still pass both the C8-cancel test and a naive "one def" grep. The spec should state the per-site delegation check for C8 explicitly (tasks.md is deferred to, per criterion 2, but C8's multi-site nature makes the gap more acute than the single-symbol clusters). This is [UNVERIFIED] as to whether tasks.md closes it, so capped at minor.

**[minor]** The deferral floor is well-specified (C1/C3/C6/C7 non-deferrable; C2/C4/C5/C8 deferrable only with risk+test+gate approval). One residual ambiguity: a deferred cluster still requires "the concrete characterization test that demonstrates it," but acceptance criterion 4 lists exactly five tests as the required set tied to the *unification* path. If C2 (or C4/C5/C8) is deferred, it is unclear whether its already-required characterization test (e.g. C2-seq/C2-sim) is the same artifact that satisfies the deferral's "test that demonstrates the risk," or an additional one. The two requirements likely coincide, but the spec does not say so, leaving a small hole in what "deferred-with-test" must deliver versus "unified-with-test."

## Residual Risks

- **C8 multi-site completeness is the weakest verifiable point.** Unlike the single-symbol clusters where "exactly one def + all call sites import it" is a tight invariant, C8's six divergent sites are not provably all-converted by the stated test plus a one-def grep. The real guarantee leans on tasks.md spelling out a per-site check; that file was not provided, so completeness here is [UNVERIFIED].

- **Behavior-preservation oracle still rests on the characterization tests being genuinely failing-on-wrong-merge.** Criterion 4 requires each be "shown to fail under a deliberately wrong merge," which is the right gate. Whether the five tests actually cover the divergent axes (C2's three axes, C4's two filter variants, C8's fresh-vs-captured `now`) cannot be confirmed from the spec text alone — the test *descriptions* map to the axes, but their adequacy is an implementation-time fact.

- **The measured-baseline approach (criterion 4) resolves the brittle "1291" literal correctly**, but it assumes `pytest -q` collection on the branch base is deterministic and that no test is added/removed by the rebase between "run start" and final count. Low risk given the behavior-preserving scope.

All prior-round blockers/majors (C2 third axis, characterization-test oracle, C8 re-target + cycle-free home + non-uniform sites, C6 PAUSED guard, deferral loophole, presence-based removal check, measured baseline) read as genuinely resolved in this revision. No remaining blocker or major requirements/testability gap found.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: open
- note: 