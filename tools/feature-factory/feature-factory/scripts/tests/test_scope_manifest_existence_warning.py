"""save_scope_manifest warns about scope paths that do not exist yet.

A typo like "templates" instead of "app/templates" otherwise stays silent until
write_canonical_diff's validate_scope_paths aborts the diff checkpoint much
later. The warning surfaces it at generation time. It must stay a warning, not a
hard failure: a new feature can legitimately scope a path that does not exist yet.
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import factory_state as FS


class ScopeManifestExistenceWarningTests(unittest.TestCase):
    @contextlib.contextmanager
    def _sandbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "scope.json"
            with mock.patch.object(FS, "REPO_ROOT", root), \
                 mock.patch.object(FS, "scope_manifest_path", lambda slug: manifest):
                yield root, manifest

    def _save(self, root: Path, paths: list[str]) -> str:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            FS.save_scope_manifest("test-slug", paths)
        return stderr.getvalue()

    def test_warns_on_missing_path(self) -> None:
        with self._sandbox() as (root, manifest):
            warning = self._save(root, ["templates"])
            self.assertIn("templates", warning)
            self.assertIn("do not exist", warning)
            # Still writes the manifest — a warning, not a failure.
            self.assertTrue(manifest.exists())

    def test_no_warning_when_paths_exist(self) -> None:
        with self._sandbox() as (root, _manifest):
            (root / "app" / "templates").mkdir(parents=True)
            warning = self._save(root, ["app/templates"])
            self.assertEqual(warning, "")


if __name__ == "__main__":
    unittest.main()
