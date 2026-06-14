import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import factory_state as FACTORY_STATE  # noqa: E402
import factory_telemetry as FACTORY_TELEMETRY  # noqa: E402


def _completed_process(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["codex"], returncode, stdout=stdout, stderr=stderr)


class FactoryTelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._root_patch = patch.object(FACTORY_STATE, "FACTORY_RUNS_ROOT", Path(self._tmpdir.name))
        self._root_patch.start()
        self.addCleanup(self._root_patch.stop)
        FACTORY_TELEMETRY._PRICING_CACHE = None
        self.addCleanup(lambda: setattr(FACTORY_TELEMETRY, "_PRICING_CACHE", None))

    def _state_path(self, slug: str) -> Path:
        return FACTORY_STATE.factory_state_path(slug)

    def _usage(self, slug: str) -> list[dict]:
        state = json.loads(self._state_path(slug).read_text(encoding="utf-8"))
        return state["token_usage"]

    def test_record_ai_call_records_success(self) -> None:
        slug = "telemetry-success"
        completed = _completed_process(
            stderr=(
                '{"totalTokens": {"prompt": 1500, "candidates": 1000, "total": 2500}}\n'
                "tail"
            )
        )
        returned = FACTORY_TELEMETRY.record_ai_call(
            slug,
            "plan",
            2,
            "adversarial_review",
            "gpt-5.4-mini",
            lambda: completed,
        )

        self.assertIs(returned, completed)
        usage = self._usage(slug)
        self.assertEqual(len(usage), 1)
        record = usage[0]
        self.assertEqual(record["stage"], "plan")
        self.assertEqual(record["round"], 2)
        self.assertEqual(record["activity_type"], "adversarial_review")
        self.assertEqual(record["model"], "gpt-5.4-mini")
        self.assertEqual(record["input_tokens"], 1500)
        self.assertEqual(record["output_tokens"], 1000)
        self.assertAlmostEqual(record["cost_usd_estimate"], 0.0035)
        self.assertIn("timestamp", record)
        self.assertIn("started_at", record)
        self.assertIn("ended_at", record)
        self.assertGreaterEqual(record["duration_seconds"], 0)
        self.assertIsNone(record["agent_id"])
        self.assertIsNone(record["artifact_sha_at_time"])
        # PR #790: lens defaults to None for back-compat callers
        self.assertIn("lens", record)
        self.assertIsNone(record["lens"])

    def test_record_ai_call_records_lens(self) -> None:
        # PR #790 — callers pass lens explicitly; the analyzer needs it
        # to disambiguate per-lens performance.
        slug = "telemetry-lens"
        completed = _completed_process(
            stderr='{"totalTokens": {"prompt": 100, "candidates": 50}}\n'
        )
        FACTORY_TELEMETRY.record_ai_call(
            slug,
            "spec",
            1,
            "adversarial_review",
            "gpt-5.4-mini",
            lambda: completed,
            lens="feasibility-adversarial",
        )
        usage = self._usage(slug)
        self.assertEqual(usage[-1]["lens"], "feasibility-adversarial")

    def test_record_ai_call_records_prompt_size_fields_and_defaults(self) -> None:
        slug = "telemetry-prompt-size"
        completed = _completed_process(stderr='{"totalTokens": {"prompt": 100, "candidates": 50}}\n')

        FACTORY_TELEMETRY.record_ai_call(
            slug,
            "spec",
            1,
            "adversarial_review",
            "gpt-5.4-mini",
            lambda: completed,
            prompt_chars=1234,
            prompt_cap=2000,
        )
        FACTORY_TELEMETRY.record_ai_call(
            slug,
            "plan",
            2,
            "adversarial_review",
            "gpt-5.4-mini",
            lambda: completed,
        )

        usage = self._usage(slug)
        self.assertEqual(usage[0]["prompt_chars"], 1234)
        self.assertEqual(usage[0]["prompt_cap"], 2000)
        self.assertIsNone(usage[1]["prompt_chars"])
        self.assertIsNone(usage[1]["prompt_cap"])

    def test_record_ai_call_invalid_activity_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            FACTORY_TELEMETRY.record_ai_call(
                "telemetry-invalid",
                "plan",
                1,
                "not-an-activity",
                "gpt-5.4-mini",
                lambda: _completed_process(),
            )

    def test_record_ai_call_parse_failure_records_with_null_tokens(self) -> None:
        slug = "telemetry-parse-failure"
        completed = _completed_process(stderr="no token metadata here")

        returned = FACTORY_TELEMETRY.record_ai_call(
            slug,
            "plan",
            1,
            "adversarial_review",
            "gpt-5.4-mini",
            lambda: completed,
        )

        self.assertIs(returned, completed)
        record = self._usage(slug)[0]
        self.assertIsNone(record["input_tokens"])
        self.assertIsNone(record["output_tokens"])
        self.assertIsNone(record["cost_usd_estimate"])
        self.assertEqual(record["parse_error"], "no Codex token block found in stderr")
        self.assertNotIn("activity_subtype", record)

    def test_parse_tokens_codex_happy_path(self) -> None:
        result = _completed_process(
            stderr=(
                'prefix {"totalTokens": {"prompt": 10, "candidates": 20, "total": 30}}\n'
                'middle {"totalTokens": {"prompt": 40, "candidates": 50, "total": 90}}\n'
            )
        )
        self.assertEqual(
            FACTORY_TELEMETRY.parse_tokens_codex(result),
            {"input_tokens": 40, "output_tokens": 50},
        )

    def test_parse_tokens_codex_missing_returns_none(self) -> None:
        self.assertIsNone(FACTORY_TELEMETRY.parse_tokens_codex(_completed_process(stderr="nothing here")))

    def test_parse_tokens_codex_v0_124_total_format(self) -> None:
        # PR #790 — Codex v0.124+ emits a single total instead of the legacy
        # JSON block. Format: literal "tokens used\n<N>" at end of stderr.
        # No input/output split available; record total in input_tokens.
        result = _completed_process(
            stderr="some warning lines\nmore output\ntokens used\n6,062\n"
        )
        self.assertEqual(
            FACTORY_TELEMETRY.parse_tokens_codex(result),
            {"input_tokens": 6062, "output_tokens": 0},
        )

    def test_parse_tokens_codex_v0_124_total_format_no_commas(self) -> None:
        result = _completed_process(stderr="tokens used\n123\n")
        self.assertEqual(
            FACTORY_TELEMETRY.parse_tokens_codex(result),
            {"input_tokens": 123, "output_tokens": 0},
        )

    def test_parse_tokens_codex_legacy_json_takes_precedence(self) -> None:
        # If both formats are present, the legacy JSON wins (it has both
        # input AND output split, which is more useful).
        result = _completed_process(
            stderr=(
                '{"totalTokens": {"prompt": 100, "candidates": 50}}\n'
                "tokens used\n999\n"
            )
        )
        self.assertEqual(
            FACTORY_TELEMETRY.parse_tokens_codex(result),
            {"input_tokens": 100, "output_tokens": 50},
        )

    def test_parse_tokens_gemini_real_world_shape(self) -> None:
        # PR #790: real Gemini stdout has "tokens" key (not "tokenStats")
        # nested inside a stats object. The parser walks the JSON tree to
        # find any of (tokens, tokenStats, totalTokens).
        result = _completed_process(
            stdout=json.dumps(
                {
                    "response": "review body",
                    "stats": {
                        "models": {
                            "gemini-2.5-pro": {
                                "tokens": {
                                    "input": 2440,
                                    "prompt": 13996,
                                    "candidates": 807,
                                    "total": 16603,
                                }
                            }
                        }
                    },
                }
            )
        )
        self.assertEqual(
            FACTORY_TELEMETRY.parse_tokens_gemini(result),
            {"input_tokens": 2440, "output_tokens": 807},
        )

    def test_parse_tokens_gemini_prose_then_json(self) -> None:
        # Real Gemini stdout often has prose before the JSON stats block.
        # Parser locates the embedded JSON object and parses from there.
        result = _completed_process(
            stdout=(
                "Some review prose here.\n"
                "More text.\n"
                + json.dumps({"tokens": {"input": 50, "candidates": 25}})
            )
        )
        self.assertEqual(
            FACTORY_TELEMETRY.parse_tokens_gemini(result),
            {"input_tokens": 50, "output_tokens": 25},
        )

    def test_parse_tokens_gemini_happy_path(self) -> None:
        result = _completed_process(
            stdout=json.dumps(
                {
                    "response": "review body",
                    "stats": {
                        "tokenStats": {
                            "prompt": 111,
                            "candidates": 222,
                            "total": 333,
                        }
                    },
                }
            )
        )
        self.assertEqual(
            FACTORY_TELEMETRY.parse_tokens_gemini(result),
            {"input_tokens": 111, "output_tokens": 222},
        )

    def test_parse_tokens_claude_happy_path(self) -> None:
        result = _completed_process(
            stdout='{"input_tokens": 321, "output_tokens": 654, "other": true}'
        )
        self.assertEqual(
            FACTORY_TELEMETRY.parse_tokens_claude(result),
            {"input_tokens": 321, "output_tokens": 654},
        )

    def test_micro_subtype_tag_appears_below_2000_tokens(self) -> None:
        slug = "telemetry-micro-low"
        FACTORY_TELEMETRY.record_ai_call(
            slug,
            "plan",
            1,
            "implementation_review",
            "gpt-5.4-mini",
            lambda: _completed_process(
                stderr='{"totalTokens": {"prompt": 1000, "candidates": 500, "total": 1500}}'
            ),
        )

        self.assertEqual(self._usage(slug)[0]["activity_subtype"], "micro")

    def test_micro_subtype_absent_above_2000_tokens(self) -> None:
        slug = "telemetry-micro-high"
        FACTORY_TELEMETRY.record_ai_call(
            slug,
            "plan",
            1,
            "implementation_review",
            "gpt-5.4-mini",
            lambda: _completed_process(
                stderr='{"totalTokens": {"prompt": 1500, "candidates": 700, "total": 2200}}'
            ),
        )

        self.assertNotIn("activity_subtype", self._usage(slug)[0])

    def test_pricing_lookup_uses_placeholder_when_flagged(self) -> None:
        pricing = FACTORY_TELEMETRY.lookup_pricing("gpt-5.4-mini")
        self.assertIsNotNone(pricing)
        self.assertTrue(pricing["placeholder"])
        self.assertEqual(pricing["input_usd_per_1k_tokens"], 0.001)
        self.assertEqual(pricing["output_usd_per_1k_tokens"], 0.002)


if __name__ == "__main__":
    unittest.main()
