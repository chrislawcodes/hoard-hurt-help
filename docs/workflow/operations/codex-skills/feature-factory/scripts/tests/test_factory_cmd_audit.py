"""Tests for the factory_cmd_audit 'audit' subcommand."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_cmd_audit as AUDIT_MOD  # noqa: E402


def _make_state(*, delivery=None, stages=None, token_usage=None) -> dict:
    """Build a minimal state.json dict."""
    state: dict = {}
    if delivery is not None:
        state["delivery"] = delivery
    if stages is not None:
        state["stages"] = stages
    if token_usage is not None:
        state["token_usage"] = token_usage
    return state


class AuditClassificationTests(unittest.TestCase):
    """Each test uses a fresh temp directory as FACTORY_RUNS_ROOT."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.runs_root = Path(self._tmpdir.name)
        self._patch = patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", self.runs_root)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        # Also patch the audit module's reference to factory_state.FACTORY_RUNS_ROOT.
        self._audit_patch = patch.object(AUDIT_MOD.factory_state, "FACTORY_RUNS_ROOT", self.runs_root)
        self._audit_patch.start()
        self.addCleanup(self._audit_patch.stop)

    def _slug_dir(self, slug: str) -> Path:
        d = self.runs_root / slug
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_state(self, slug: str, state: dict) -> None:
        d = self._slug_dir(slug)
        (d / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def _write_file(self, slug: str, name: str, content: str = "placeholder") -> None:
        d = self._slug_dir(slug)
        (d / name).write_text(content, encoding="utf-8")

    def _classify_all(self) -> dict[str, str]:
        """Walk and return {slug: category}."""
        records = AUDIT_MOD._walk_runs_root()
        return {r["slug"]: r["category"] for r in records}

    # ------------------------------------------------------------------
    # Test 1: abandoned slug — spec.md present, state.json is essentially empty
    # ------------------------------------------------------------------
    def test_abandoned_slug_no_runner_activity(self) -> None:
        """A slug with spec.md but empty state is classified 'abandoned'."""
        slug = "test-abandoned"
        self._write_file(slug, "spec.md", "# My Feature\n")
        self._write_state(slug, _make_state())
        cats = self._classify_all()
        self.assertEqual(cats.get(slug), "abandoned")

    # ------------------------------------------------------------------
    # Test 2: active slug — stages populated, delivery empty
    # ------------------------------------------------------------------
    def test_active_slug_stages_but_no_delivery(self) -> None:
        """A slug with stages populated but no delivery is classified 'active'."""
        slug = "test-active"
        self._write_file(slug, "spec.md")
        self._write_file(slug, "tasks.md")
        self._write_state(slug, _make_state(
            stages={"spec": {"adversarial_rounds": 3}},
        ))
        cats = self._classify_all()
        self.assertEqual(cats.get(slug), "active")

    # ------------------------------------------------------------------
    # Test 3: closed slug — delivery non-empty with a branch key
    # ------------------------------------------------------------------
    def test_closed_slug_with_delivery(self) -> None:
        """A slug with delivery.branch set is classified 'closed'."""
        slug = "test-closed"
        self._write_file(slug, "spec.md")
        self._write_state(slug, _make_state(
            delivery={"branch": "claude/my-feature", "head_sha": "abc123"},
            stages={"spec": {}, "plan": {}, "tasks": {}, "diff": {}},
        ))
        cats = self._classify_all()
        self.assertEqual(cats.get(slug), "closed")

    # ------------------------------------------------------------------
    # Test 4: markdown output renders all four sections
    # ------------------------------------------------------------------
    def test_markdown_output_has_four_sections(self) -> None:
        """_render_markdown always emits Abandoned/Active/Closed/Empty sections."""
        # Set up one slug per category.
        self._write_file("slug-abandoned", "spec.md")
        self._write_state("slug-abandoned", _make_state())

        self._write_file("slug-active", "tasks.md")
        self._write_state("slug-active", _make_state(
            token_usage=[{"model": "gpt-5.4-mini", "input_tokens": 100}],
        ))

        self._write_file("slug-closed", "spec.md")
        self._write_state("slug-closed", _make_state(
            delivery={"pr_url": "https://github.com/org/repo/pull/1"},
        ))

        self._slug_dir("slug-empty")  # no files, no state

        records = AUDIT_MOD._walk_runs_root()
        md = AUDIT_MOD._render_markdown(records, "2026-04-30")

        self.assertIn("## Abandoned", md)
        self.assertIn("## Active", md)
        self.assertIn("## Closed", md)
        self.assertIn("## Empty", md)
        self.assertIn("slug-abandoned", md)
        self.assertIn("slug-active", md)

    # ------------------------------------------------------------------
    # Test 5: closed detection via pr_url (no branch key)
    # ------------------------------------------------------------------
    def test_closed_slug_with_pr_url(self) -> None:
        """delivery.pr_url is also a valid closed signal (no branch needed)."""
        slug = "test-closed-prurl"
        self._write_file(slug, "spec.md")
        self._write_state(slug, _make_state(
            delivery={"pr_url": "https://github.com/org/repo/pull/99", "pr_state": "merged"},
        ))
        cats = self._classify_all()
        self.assertEqual(cats.get(slug), "closed")

    # ------------------------------------------------------------------
    # Test 6: empty slug — no work files, no state
    # ------------------------------------------------------------------
    def test_empty_slug_no_files(self) -> None:
        """A slug directory with no files at all is classified 'empty'."""
        slug = "test-empty"
        self._slug_dir(slug)  # create dir only
        cats = self._classify_all()
        self.assertEqual(cats.get(slug), "empty")

    # ------------------------------------------------------------------
    # Test 7: active detection via token_usage only (no stages key)
    # ------------------------------------------------------------------
    def test_active_slug_token_usage_only(self) -> None:
        """A slug with token_usage but no stages is still classified 'active'."""
        slug = "test-active-tokens"
        self._write_file(slug, "spec.md")
        self._write_state(slug, _make_state(
            token_usage=[{"model": "gpt-5.4-mini", "input_tokens": 500}],
        ))
        cats = self._classify_all()
        self.assertEqual(cats.get(slug), "active")


if __name__ == "__main__":
    unittest.main()
