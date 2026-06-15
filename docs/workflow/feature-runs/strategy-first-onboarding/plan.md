# Plan

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: FR-001/FR-002 now require changing BOTH the POST gate and the GET form/template (new_agent_form has_enabled_provider + agents/new.html connect-first card + disabled picker). FR-004 requires a ?provider= hint to preselect the connect tab (one client=one provider per #392); generic fallback kept. FR-006 names agents/list.html + detail.html and a provider-scoped CTA.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: HIGH create-blocks/join-hijack covered by FR-001/FR-005; picker MEDIUM by FR-002. Added edge cases: preserve ?next through create validation failure; disconnected agents excluded from capacity math; FR-006 batches the per-agent coverage query.
