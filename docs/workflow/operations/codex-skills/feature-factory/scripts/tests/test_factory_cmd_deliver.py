import argparse
import contextlib
import gc
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_cmd_deliver as DELIVER  # noqa: E402


SLUG = "ff-deliver-test"
_UNSET = object()


def _make_stage_state() -> dict:
    return {
        "adversarial_rounds": 3,
        "annotations": [],
        "adversarial_sha_history": ["sha-round-1", "sha-round-2", "sha-round-3"],
        "initial_sha": "sha-round-1",
    }


def _make_state(stages: dict[str, dict]) -> dict:
    state = FACTORY_STATE._default_workflow_state()
    state["schema_version"] = 2
    state["stages"] = stages
    return state


def _make_pr_payload(
    *,
    state: str = "OPEN",
    body: str = "",
    number: int = 17,
    merge_commit: str = "",
    merged_at: str = "",
) -> dict:
    payload = {
        "number": number,
        "url": f"https://example.test/pr/{number}",
        "state": state,
        "isDraft": False,
        "headRefName": "feature/branch",
        "headRefOid": "head-sha",
        "baseRefName": "main",
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "body": body,
        "mergeCommit": {"oid": merge_commit} if merge_commit else None,
        "mergedAt": merged_at,
    }
    return payload


def _deliver_args(**overrides: object) -> argparse.Namespace:
    args = {
        "slug": SLUG,
        "create_pr": False,
        "draft": False,
        "base": None,
        "title": None,
        "reason": None,
        "refresh": False,
        "resume_merge_wait": False,
        "watch_ci": False,
        "interval": 10,
        "merge_when_green": False,
        "auto_merge": False,
        "dry_run": False,
    }
    args.update(overrides)
    return argparse.Namespace(**args)


class FactoryDeliverTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        # Patch every loaded factory_state module instance so that lazy imports
        # inside record_command_telemetry (and similar) also see the tmpdir.
        self._patches: list = []
        for mod in list(gc.get_objects()):
            if not isinstance(mod, types.ModuleType):
                continue
            if getattr(mod, "__name__", "") != "factory_state":
                continue
            if not hasattr(mod, "FACTORY_RUNS_ROOT"):
                continue
            p = patch.object(mod, "FACTORY_RUNS_ROOT", Path(self._tmpdir.name))
            p.start()
            self._patches.append(p)
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def _write_state(self, state: dict) -> None:
        path = FACTORY_STATE.factory_state_path(SLUG)
        FACTORY_STATE.atomic_json_write(path, state)
        reviews_dir = FACTORY_STATE.reviews_dir(SLUG)
        reviews_dir.mkdir(parents=True, exist_ok=True)
        for stage in ("spec", "plan", "tasks", "diff"):
            manifest_path = FACTORY_STATE.checkpoint_manifest_path(SLUG, stage)
            if not manifest_path.exists():
                FACTORY_STATE.atomic_json_write(manifest_path, {"healthy": True})

    def _base_patches(self):
        import importlib
        factory_deliver = importlib.import_module("factory_deliver")
        return [
            patch.object(DELIVER, "ensure_sync", return_value=None),
            patch.object(DELIVER, "command_path", return_value=True),
            patch.object(DELIVER, "verify_checkpoint_manifest", return_value=(True, "")),
            patch.object(DELIVER, "reconciliation_state", return_value=(True, "")),
            patch.object(DELIVER, "current_branch_name", return_value="feature/branch"),
            patch.object(DELIVER, "upstream_branch_name", return_value="origin/main"),
            patch.object(DELIVER, "git_output", return_value="head-sha"),
            patch.object(DELIVER, "commits_behind_upstream", return_value=0),
            # Push-first publishing runs real git; covered by
            # test_deliver_push_first.py. Neutralize it here.
            patch.object(
                DELIVER,
                "ensure_branch_pushed",
                side_effect=lambda branch, upstream, dry_run=False: upstream,
            ),
            patch.object(factory_deliver, "check_implementation_rule", return_value=("ok", "")),
            # The pre-deliver conflict-marker gate runs real git plumbing; it is
            # covered by test_conflict_marker_gate.py. Neutralize it here so these
            # tests exercise the rest of the deliver path.
            patch.object(factory_deliver, "find_conflict_markers", return_value=[]),
        ]

    def _run_deliver(
        self,
        args: argparse.Namespace,
        *,
        current_pr_payload: object = _UNSET,
        required_check_summary: object = _UNSET,
        gh_side_effect=None,
        extra_patches=None,
    ) -> tuple[int, str, str]:
        with contextlib.ExitStack() as stack:
            for patcher in self._base_patches():
                stack.enter_context(patcher)
            # Entered after the base patches so a test-specific patch on the
            # same attribute wins.
            for patcher in extra_patches or []:
                stack.enter_context(patcher)
            if current_pr_payload is not _UNSET:
                if isinstance(current_pr_payload, list):
                    pr_values = iter(current_pr_payload)

                    def _next_pr(*_args, **_kwargs):
                        return next(pr_values)

                    stack.enter_context(patch.object(DELIVER, "current_pr_payload", side_effect=_next_pr))
                else:
                    stack.enter_context(patch.object(DELIVER, "current_pr_payload", return_value=current_pr_payload))
            if required_check_summary is not None:
                if callable(required_check_summary):
                    stack.enter_context(patch.object(DELIVER, "required_check_summary", side_effect=required_check_summary))
                else:
                    stack.enter_context(patch.object(DELIVER, "required_check_summary", return_value=required_check_summary))
            if gh_side_effect is not None:
                stack.enter_context(patch.object(DELIVER.subprocess, "run", side_effect=gh_side_effect))
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = DELIVER.command_deliver(args)
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_pr_body_generated_when_no_existing_pr(self) -> None:
        self._write_state(_make_state({"plan": _make_stage_state()}))

        captured_bodies: list[str] = []

        def gh_side_effect(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "auth", "status"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[:3] == ["gh", "pr", "create"]:
                body_index = cmd.index("--body") + 1
                captured_bodies.append(cmd[body_index])
                return subprocess.CompletedProcess(cmd, 0, stdout="https://example.test/pr/18\n", stderr="")
            raise AssertionError(f"unexpected command: {cmd}")

        rc, _, _ = self._run_deliver(
            _deliver_args(create_pr=True, draft=True, dry_run=True),
            current_pr_payload=None,
            gh_side_effect=gh_side_effect,
        )

        self.assertEqual(rc, 0)
        self.assertTrue(captured_bodies)
        body = captured_bodies[0]
        self.assertIn("## Workflow", body)

    def test_merge_wait_transitions_to_merged(self) -> None:
        state = _make_state({"plan": _make_stage_state()})
        self._write_state(state)

        pr_sequence = [
            _make_pr_payload(body="# existing body"),
            _make_pr_payload(state="MERGED", body="# existing body", merge_commit="merge-sha", merged_at="2026-04-19T17:00:00Z"),
        ]

        def gh_side_effect(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "auth", "status"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[:3] == ["gh", "pr", "edit"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            raise AssertionError(f"unexpected command: {cmd}")

        rc, _, _ = self._run_deliver(
            _deliver_args(),
            current_pr_payload=pr_sequence,
            required_check_summary=("pass", [], ""),
            gh_side_effect=gh_side_effect,
        )

        self.assertEqual(rc, 0)
        persisted = json.loads(FACTORY_STATE.factory_state_path(SLUG).read_text(encoding="utf-8"))
        delivery = persisted["delivery"]
        self.assertEqual(delivery["merge_wait_state"], "merged")
        self.assertEqual(delivery["merged_sha"], "merge-sha")
        self.assertEqual(delivery["merged_at_iso8601"], "2026-04-19T17:00:00Z")

    def test_resume_merge_wait_from_waiting(self) -> None:
        state = _make_state({"plan": _make_stage_state()})
        state["delivery"] = {
            "pr_number": 17,
            "merge_wait_state": "waiting",
            "branch": "feature/branch",
            "head_sha": "head-sha",
            "upstream": "origin/main",
            "pr_url": "https://example.test/pr/17",
            "checks_summary": "pass",
        }
        self._write_state(state)

        def gh_side_effect(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "auth", "status"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            raise AssertionError(f"unexpected command: {cmd}")

        rc, _, _ = self._run_deliver(
            _deliver_args(resume_merge_wait=True),
            current_pr_payload=_make_pr_payload(
                state="MERGED",
                body="# existing body",
                merge_commit="merge-sha",
                merged_at="2026-04-19T17:00:00Z",
            ),
            gh_side_effect=gh_side_effect,
        )

        self.assertEqual(rc, 0)
        persisted = json.loads(FACTORY_STATE.factory_state_path(SLUG).read_text(encoding="utf-8"))
        self.assertEqual(persisted["delivery"]["merge_wait_state"], "merged")
        self.assertEqual(persisted["delivery"]["merged_sha"], "merge-sha")
        self.assertEqual(persisted["delivery"]["merged_at_iso8601"], "2026-04-19T17:00:00Z")

    def test_create_pr_pushes_branch_first_when_no_upstream(self) -> None:
        """create-pr no longer requires a pre-published upstream; it pushes first."""
        self._write_state(_make_state({"plan": _make_stage_state()}))
        push_mock = mock.MagicMock(return_value=None)

        def gh_side_effect(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "auth", "status"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[:3] == ["gh", "pr", "create"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="https://example.test/pr/19\n", stderr="")
            raise AssertionError(f"unexpected command: {cmd}")

        rc, _, _ = self._run_deliver(
            _deliver_args(create_pr=True, dry_run=True),
            current_pr_payload=None,
            gh_side_effect=gh_side_effect,
            extra_patches=[
                patch.object(DELIVER, "upstream_branch_name", return_value=None),
                patch.object(DELIVER, "ensure_branch_pushed", push_mock),
            ],
        )

        self.assertEqual(rc, 0)
        push_mock.assert_called_once_with("feature/branch", None, dry_run=True)

    def test_create_pr_blocked_branch_behind_origin_main_propagates(self) -> None:
        """A behind-origin/main block from the push helper aborts deliver."""
        self._write_state(_make_state({"plan": _make_stage_state()}))
        blocked = SystemExit(
            "deliver blocked: branch 'feature/branch' is 2 commits behind origin/main."
        )

        def gh_side_effect(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "auth", "status"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            raise AssertionError(f"unexpected command: {cmd}")

        with self.assertRaises(SystemExit) as ctx:
            self._run_deliver(
                _deliver_args(create_pr=True),
                current_pr_payload=None,
                gh_side_effect=gh_side_effect,
                extra_patches=[
                    patch.object(DELIVER, "ensure_branch_pushed", side_effect=blocked),
                ],
            )
        self.assertIn("behind origin/main", str(ctx.exception))

    def test_explicit_pr_path_skips_push_first(self) -> None:
        """The no-gh path (--pr-number/--pr-url) must not touch the push helper."""
        self._write_state(_make_state({"plan": _make_stage_state()}))
        push_mock = mock.MagicMock()

        rc, stdout, _ = self._run_deliver(
            _deliver_args(pr_number=21, pr_url="https://example.test/pr/21"),
            extra_patches=[
                patch.object(DELIVER, "ensure_branch_pushed", push_mock),
            ],
        )

        self.assertEqual(rc, 0)
        push_mock.assert_not_called()
        self.assertIn("pr: #21", stdout)
        persisted = json.loads(FACTORY_STATE.factory_state_path(SLUG).read_text(encoding="utf-8"))
        self.assertEqual(persisted["delivery"]["pr_number"], 21)

    def test_resume_merge_wait_nothing_to_resume(self) -> None:
        state = _make_state({"plan": _make_stage_state()})
        state["delivery"] = {
            "pr_number": 17,
            "merge_wait_state": "none",
        }
        self._write_state(state)

        def gh_side_effect(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "auth", "status"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            raise AssertionError(f"unexpected command: {cmd}")

        rc, stdout, _ = self._run_deliver(
            _deliver_args(resume_merge_wait=True),
            gh_side_effect=gh_side_effect,
        )

        self.assertEqual(rc, 0)
        self.assertIn("nothing to resume", stdout)


if __name__ == "__main__":
    unittest.main()
