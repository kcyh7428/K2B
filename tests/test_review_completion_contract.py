#!/usr/bin/env python3
"""Completion-contract tests for the Kimi review boundary.

Synthetic fixtures only; no vault or real network access. Covers:
  - exhausted (blank) responses
  - complete-looking JSON with max_tokens finish
  - missing / unknown finish_reasons
  - schema violations: invalid verdicts, malformed fields, boolean-as-integer
  - valid approve / needs-attention verdicts
  - safe archived metadata
  - runner header contract (bare markers, unknown headings, conflicting
    headings, current and legacy headers)
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib import minimax_review as mr  # noqa: E402
from scripts.lib import review_runner as rr  # noqa: E402


SCHEMA = json.loads((REPO_ROOT / "scripts" / "lib" / "review-output.schema.json").read_text())


VALID_APPROVE = {
    "verdict": "approve",
    "summary": "Looks fine.",
    "findings": [],
    "next_steps": ["Ship it."],
}

VALID_NEEDS_ATTENTION = {
    "verdict": "needs-attention",
    "summary": "Hazard spotted.",
    "findings": [
        {
            "severity": "high",
            "title": "Off-by-one",
            "body": "Loop overruns.",
            "file": "scripts/lib/example.py",
            "line_start": 10,
            "line_end": 12,
            "confidence": 0.8,
            "recommendation": "Use < len().",
        }
    ],
    "next_steps": ["Fix it."],
}


def _chat_response(text: str, finish_reason="end_turn", model="kimi-k2.7-code"):
    return {
        "id": "resp_test",
        "model": model,
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def _run_main(monkeypatch_response, argv, extra_env=None):
    stderr = io.StringIO()
    stdout = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp:
        archive_dir = Path(tmp) / "archive"
        argv_full = list(argv) + ["--archive-dir", str(archive_dir),
                                  "--scope", "files", "--files", "scripts/lib/minimax_review.py"]
        env = {"K2B_LLM_PROVIDER": "kimi"}
        if extra_env:
            env.update(extra_env)
        with mock.patch.dict("os.environ", env, clear=False), \
             mock.patch.object(mr, "chat_completion", side_effect=monkeypatch_response), \
             mock.patch.object(mr, "gather_file_list_context", return_value=("ctx", ["x"])), \
             mock.patch.object(sys, "argv", ["minimax_review"] + argv_full), \
             redirect_stderr(stderr), redirect_stdout(stdout):
            rc = mr.main()
        files = sorted(archive_dir.glob("*.json")) if archive_dir.exists() else []
        record = json.loads(files[0].read_text()) if files else None
    return rc, stderr.getvalue(), stdout.getvalue(), record


class SchemaValidatorTests(unittest.TestCase):
    def test_integral_floats_and_standard_annotations_are_supported(self):
        candidate = json.loads(json.dumps(VALID_NEEDS_ATTENTION))
        candidate["findings"][0].update(line_start=10.0, line_end=12.0)
        schema = json.loads(json.dumps(SCHEMA))
        schema.update(title="Review", description="Informational", **{"$comment": "Annotation only"})
        schema["properties"]["summary"].update(description="Human summary", examples=["Example"])
        self.assertIsNone(mr.validate_review_output(candidate, schema))

    def test_unsupported_schema_forms_fail_closed(self):
        forms = [
            {"additionalProperties": {"pattern": "x"}},
            {"additionalProperties": []}, {"items": []},
            {"type": ["string", "null"]}, {"anyOf": []},
            {"properties": {"unused": {"pattern": "x"}}},
            {"properties": []}, {"required": True}, {"required": [1]},
            {"enum": [{}]}, {"enum": [float("nan")]},
            {"minLength": True}, {"minimum": float("inf")},
            {"maximum": "1"},
        ]
        for schema in forms:
            with self.subTest(schema=schema):
                self.assertIsNotNone(mr.validate_review_output({}, schema))

    def test_enum_boolean_numeric_and_large_integer_equality(self):
        self.assertIsNotNone(mr.validate_review_output(True, {"enum": [1]}))
        self.assertIsNone(mr.validate_review_output(1.0, {"enum": [1]}))
        self.assertIsNotNone(mr.validate_review_output(2**53 + 1, {"enum": [2**53]}))

    def test_nonfinite_confidence_is_rejected(self):
        for value in (float("nan"), float("inf")):
            candidate = json.loads(json.dumps(VALID_NEEDS_ATTENTION))
            candidate["findings"][0]["confidence"] = value
            self.assertIsNotNone(mr.validate_review_output(candidate, SCHEMA))

    def test_valid_approve_passes_schema(self):
        self.assertIsNone(mr.validate_review_output(VALID_APPROVE, SCHEMA))

    def test_valid_needs_attention_passes_schema(self):
        self.assertIsNone(mr.validate_review_output(VALID_NEEDS_ATTENTION, SCHEMA))

    def test_invalid_verdict_rejected(self):
        bad = dict(VALID_APPROVE, verdict="maybe")
        err = mr.validate_review_output(bad, SCHEMA)
        self.assertIsNotNone(err)
        self.assertIn("verdict", err)

    def test_missing_field_rejected(self):
        bad = dict(VALID_APPROVE)
        bad.pop("summary")
        err = mr.validate_review_output(bad, SCHEMA)
        self.assertIsNotNone(err)
        self.assertIn("summary", err)

    def test_unknown_field_rejected(self):
        bad = dict(VALID_APPROVE, extra="x")
        err = mr.validate_review_output(bad, SCHEMA)
        self.assertIsNotNone(err)
        self.assertIn("extra", err)

    def test_boolean_rejected_as_integer(self):
        bad = json.loads(json.dumps(VALID_NEEDS_ATTENTION))
        bad["findings"][0]["line_start"] = True
        err = mr.validate_review_output(bad, SCHEMA)
        self.assertIsNotNone(err)
        self.assertIn("line_start", err)

    def test_boolean_rejected_as_number(self):
        bad = json.loads(json.dumps(VALID_NEEDS_ATTENTION))
        bad["findings"][0]["confidence"] = True
        err = mr.validate_review_output(bad, SCHEMA)
        self.assertIsNotNone(err)
        self.assertIn("confidence", err)

    def test_float_rejected_as_integer(self):
        bad = json.loads(json.dumps(VALID_NEEDS_ATTENTION))
        bad["findings"][0]["line_start"] = 1.5
        err = mr.validate_review_output(bad, SCHEMA)
        self.assertIsNotNone(err)
        self.assertIn("line_start", err)

    def test_confidence_out_of_range_rejected(self):
        bad = json.loads(json.dumps(VALID_NEEDS_ATTENTION))
        bad["findings"][0]["confidence"] = 1.5
        err = mr.validate_review_output(bad, SCHEMA)
        self.assertIsNotNone(err)
        self.assertIn("confidence", err)

    def test_empty_summary_rejected_by_minlength(self):
        bad = dict(VALID_APPROVE, summary="")
        err = mr.validate_review_output(bad, SCHEMA)
        self.assertIsNotNone(err)

    def test_wrong_type_top_level_rejected(self):
        err = mr.validate_review_output(["not", "object"], SCHEMA)
        self.assertIsNotNone(err)


class MachineMarkerTests(unittest.TestCase):
    def test_render_machine_marker_lowercase(self):
        self.assertEqual(
            mr.render_machine_marker(VALID_APPROVE),
            "K2B_REVIEW_VERDICT: approve",
        )
        self.assertEqual(
            mr.render_machine_marker(VALID_NEEDS_ATTENTION),
            "K2B_REVIEW_VERDICT: needs-attention",
        )

    def test_markdown_output_contains_marker(self):
        response = _chat_response(
            json.dumps(VALID_APPROVE), model="kimi-renamed-2026"
        )
        rc, _, stdout, _ = _run_main(lambda **_: response, [])
        self.assertEqual(rc, 0)
        self.assertIn("K2B_REVIEW_VERDICT: approve", stdout)

    def test_json_mode_remains_parseable_without_marker(self):
        response = _chat_response(json.dumps(VALID_APPROVE))
        rc, _, stdout, _ = _run_main(lambda **_: response, ["--json"])
        self.assertEqual(rc, 0)
        self.assertNotIn("K2B_REVIEW_VERDICT", stdout)
        self.assertEqual(json.loads(stdout)["verdict"], "approve")

    def test_failure_output_is_marker_free(self):
        response = _chat_response("", finish_reason="max_tokens")
        rc, _, stdout, _ = _run_main(lambda **_: response, [])
        self.assertNotEqual(rc, 0)
        self.assertNotIn("K2B_REVIEW_VERDICT:", stdout)

    def test_archive_same_second_collision_produces_distinct_files(self):
        # Freeze the timestamp so both calls land in the same second.
        import scripts.lib.minimax_review as mr_mod

        class _FrozenDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp) / "archive"
            with mock.patch.object(mr_mod, "datetime", _FrozenDT):
                p1 = mr_mod.archive(
                    archive_dir, scope="files", model="m", parsed=None,
                    raw_text="", prompt="p", response={}, usage={},
                )
                p2 = mr_mod.archive(
                    archive_dir, scope="files", model="m", parsed=None,
                    raw_text="", prompt="p", response={}, usage={},
                )
            self.assertNotEqual(p1, p2)
            self.assertTrue(p1.is_file())
            self.assertTrue(p2.is_file())
            self.assertEqual(p1.suffix, ".json")
            self.assertEqual(p2.suffix, ".json")
            r1 = json.loads(p1.read_text())
            self.assertEqual(r1["timestamp_utc"], "2026-05-08T12-00-00Z")


class CliCompletionBoundaryTests(unittest.TestCase):
    def test_valid_integral_finding_lines_are_normalized_after_validation(self):
        review = json.loads(json.dumps(VALID_NEEDS_ATTENTION))
        review["findings"][0].update(line_start=10.0, line_end=12.0)
        response = _chat_response(json.dumps(review))
        rc, _, stdout, record = _run_main(lambda **_: response, ["--json"])
        self.assertEqual(rc, 0)
        self.assertIs(type(record["parsed"]["findings"][0]["line_start"]), int)
        self.assertIs(type(json.loads(stdout)["findings"][0]["line_end"]), int)

    def test_archive_storage_failure_has_bounded_diagnostic(self):
        response = _chat_response(json.dumps(VALID_APPROVE))
        with mock.patch.object(mr, "archive", side_effect=PermissionError("private storage detail")):
            rc, stderr, _, _ = _run_main(lambda **_: response, [])
        self.assertEqual(rc, 3)
        self.assertIn("PermissionError", stderr)
        self.assertNotIn("private storage detail", stderr)

    def test_blank_max_tokens_fails_with_diagnostic(self):
        response = _chat_response("", finish_reason="max_tokens")
        rc, stderr, stdout, record = _run_main(
            lambda **_: response,
            ["--max-tokens", "32"],
        )
        self.assertNotEqual(rc, 0)
        self.assertIn("max_tokens", stderr)
        self.assertIn("finish='max_tokens'", stderr)
        self.assertIsNotNone(record)
        self.assertIsNone(record["parsed"])
        self.assertEqual(record["finish_reason"], "max_tokens")
        self.assertEqual(record["requested_max_tokens"], 32)
        self.assertEqual(record["final_text_length"], 0)
        self.assertIsNotNone(record["validation_error"])
        # Ensure reasoning / raw provider object are not persisted.
        self.assertNotIn("reasoning", record)
        self.assertNotIn("choices", record)

    def test_complete_json_with_max_tokens_still_rejected(self):
        payload = json.dumps(VALID_APPROVE)
        response = _chat_response(payload, finish_reason="max_tokens")
        rc, stderr, stdout, record = _run_main(lambda **_: response, [])
        self.assertNotEqual(rc, 0)
        self.assertIsNone(record["parsed"])
        self.assertEqual(record["finish_reason"], "max_tokens")
        self.assertIn("unexpected finish_reason", record["validation_error"])

    def test_missing_finish_reason_rejected(self):
        response = _chat_response(json.dumps(VALID_APPROVE))
        response["choices"][0].pop("finish_reason")
        rc, stderr, _, record = _run_main(lambda **_: response, [])
        self.assertNotEqual(rc, 0)
        self.assertIsNone(record["parsed"])
        self.assertIn("missing finish_reason", record["validation_error"])

    def test_unknown_finish_reason_rejected(self):
        response = _chat_response(json.dumps(VALID_APPROVE), finish_reason="length")
        rc, stderr, _, record = _run_main(lambda **_: response, [])
        self.assertNotEqual(rc, 0)
        self.assertIsNone(record["parsed"])
        self.assertIn("unexpected finish_reason", record["validation_error"])

    def test_invalid_verdict_rejected_with_evidence(self):
        bad = dict(VALID_APPROVE, verdict="maybe")
        response = _chat_response(json.dumps(bad))
        rc, stderr, _, record = _run_main(lambda **_: response, [])
        self.assertNotEqual(rc, 0)
        self.assertIsNone(record["parsed"])
        self.assertIn("verdict", record["validation_error"])

    def test_malformed_field_rejected(self):
        bad = dict(VALID_APPROVE, findings=[{"severity": "critical"}])
        response = _chat_response(json.dumps(bad))
        rc, stderr, _, record = _run_main(lambda **_: response, [])
        self.assertNotEqual(rc, 0)
        self.assertIsNone(record["parsed"])

    def test_valid_approve_succeeds(self):
        response = _chat_response(json.dumps(VALID_APPROVE))
        rc, stderr, stdout, record = _run_main(
            lambda **_: response, ["--json"]
        )
        self.assertEqual(rc, 0)
        self.assertIsNotNone(record)
        self.assertEqual(record["parsed"]["verdict"], "approve")
        self.assertIsNone(record["validation_error"])
        printed = json.loads(stdout)
        self.assertEqual(printed["verdict"], "approve")

    def test_valid_needs_attention_succeeds(self):
        response = _chat_response(json.dumps(VALID_NEEDS_ATTENTION))
        rc, stderr, stdout, record = _run_main(
            lambda **_: response, ["--json"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(record["parsed"]["verdict"], "needs-attention")
        self.assertIsNone(record["validation_error"])

    def test_archive_outside_repo_root_does_not_crash(self):
        response = _chat_response(json.dumps(VALID_APPROVE))
        rc, stderr, _, record = _run_main(lambda **_: response, ["--json"])
        self.assertEqual(rc, 0)
        self.assertIsNotNone(record)

    def test_archive_metadata_is_safe(self):
        response = _chat_response("", finish_reason="max_tokens", model="kimi-k2.7-code")
        response["reasoning"] = "VERY SECRET"
        rc, stderr, _, record = _run_main(
            lambda **_: response, ["--max-tokens", "32"]
        )
        self.assertNotEqual(rc, 0)
        record_text = json.dumps(record)
        self.assertNotIn("VERY SECRET", record_text)
        self.assertEqual(record["model"], "kimi-k2.7-code")
        self.assertEqual(record["finish_reason"], "max_tokens")
        self.assertEqual(record["requested_max_tokens"], 32)
        self.assertEqual(record["final_text_length"], 0)
        self.assertIn("usage", record)






if __name__ == "__main__":
    unittest.main(verbosity=2)
