#!/usr/bin/env python3
"""Regression tests for minimax_common.chat_completion retry/fail-fast paths.

Added 2026-04-21 per Codex Tier 3 review feedback on the 1002/1008 additions
(exhaustive review exposed the absence of regression coverage for these
branches; they only fire under quota/rate-limit pressure where failures are
hardest to diagnose).

Coverage:
- success (HTTP 200, base_resp.status_code=0)
- 1002 retry-then-success
- 1002 exhaustion (all attempts return 1002)
- 1008 fail-fast (no retry)
- malformed JSON after HTTP 200
- HTTP 529 retry-then-success (baseline, unchanged behavior)
- MM-API-Source: K2B header emission

Run: python3 tests/test_minimax_common.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import minimax_common  # noqa: E402


def _mock_resp(body_dict: dict) -> MagicMock:
    """Build a context-manager mock that urlopen() returns on success."""
    body = json.dumps(body_dict).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _mock_http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.minimaxi.com/v1/text/chatcompletion_v2",
        code=code,
        msg="transient",
        hdrs=None,
        fp=BytesIO(b'{"error": "transient"}'),
    )


SUCCESS_BODY = {
    "base_resp": {"status_code": 0, "status_msg": "success"},
    "choices": [{"message": {"content": "ok"}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}
RATE_LIMIT_BODY = {"base_resp": {"status_code": 1002, "status_msg": "rate limited"}}
QUOTA_BODY = {"base_resp": {"status_code": 1008, "status_msg": "quota exhausted"}}


class TestChatCompletion(unittest.TestCase):
    def setUp(self) -> None:
        # Neutralize the real API key lookup + the backoff sleeps so the test
        # suite runs in <1s regardless of the backoff ladder (10+20+40s).
        self.patchers = [
            patch.object(minimax_common, "load_api_key", return_value="fake-key"),
            patch("time.sleep", return_value=None),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self) -> None:
        for p in self.patchers:
            p.stop()

    def _run(self):
        return minimax_common.chat_completion(
            "MiniMax-M2.7", [{"role": "user", "content": "hi"}]
        )

    def test_success_returns_parsed(self):
        with patch.object(
            minimax_common.urllib.request, "urlopen",
            return_value=_mock_resp(SUCCESS_BODY),
        ) as mock_u:
            result = self._run()
        self.assertEqual(result["base_resp"]["status_code"], 0)
        self.assertEqual(mock_u.call_count, 1)

    def test_1002_retry_then_success(self):
        with patch.object(
            minimax_common.urllib.request, "urlopen",
            side_effect=[_mock_resp(RATE_LIMIT_BODY), _mock_resp(SUCCESS_BODY)],
        ) as mock_u:
            result = self._run()
        self.assertEqual(result["base_resp"]["status_code"], 0)
        self.assertEqual(mock_u.call_count, 2)

    def test_1002_exhaustion_raises(self):
        # MAX_RETRIES + 1 = 4 attempts total, all return 1002.
        with patch.object(
            minimax_common.urllib.request, "urlopen",
            side_effect=[_mock_resp(RATE_LIMIT_BODY)] * 4,
        ) as mock_u:
            with self.assertRaises(minimax_common.MinimaxError) as ctx:
                self._run()
        self.assertIn("1002", str(ctx.exception))
        self.assertEqual(mock_u.call_count, 4)

    def test_1008_fail_fast_no_retry(self):
        with patch.object(
            minimax_common.urllib.request, "urlopen",
            return_value=_mock_resp(QUOTA_BODY),
        ) as mock_u:
            with self.assertRaises(minimax_common.MinimaxError) as ctx:
                self._run()
        msg = str(ctx.exception)
        self.assertIn("1008", msg)
        self.assertIn("quota", msg.lower())
        self.assertEqual(mock_u.call_count, 1)

    def test_malformed_json_raises(self):
        bad = MagicMock()
        bad.read.return_value = b"this is not json {{"
        bad.__enter__.return_value = bad
        bad.__exit__.return_value = False
        with patch.object(minimax_common.urllib.request, "urlopen", return_value=bad):
            with self.assertRaises(minimax_common.MinimaxError) as ctx:
                self._run()
        self.assertIn("Non-JSON", str(ctx.exception))

    def test_http_529_retry_then_success(self):
        # Baseline: pre-existing HTTP 529 retry path must still work.
        with patch.object(
            minimax_common.urllib.request, "urlopen",
            side_effect=[_mock_http_error(529), _mock_resp(SUCCESS_BODY)],
        ) as mock_u:
            result = self._run()
        self.assertEqual(result["base_resp"]["status_code"], 0)
        self.assertEqual(mock_u.call_count, 2)

    def test_retry_diagnostics_go_to_stderr_not_stdout(self):
        """Guard the `minimax-review.sh --json` contract.

        Callers capture stdout as the final JSON payload. Retry diagnostics
        on stdout would corrupt that payload when 1002/529/URLError retries
        fire under real rate-limit conditions -- the exact scenario the
        retry path is meant to recover from. Keep stdout clean; use stderr
        for all retry logs.
        """
        import contextlib
        import io

        # Scenario: 1002 retry then success. Capture both streams.
        with patch.object(
            minimax_common.urllib.request, "urlopen",
            side_effect=[_mock_resp(RATE_LIMIT_BODY), _mock_resp(SUCCESS_BODY)],
        ):
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                 contextlib.redirect_stderr(stderr_buf):
                self._run()
        self.assertEqual(stdout_buf.getvalue(), "",
                         "1002 retry must not write to stdout")
        self.assertIn("1002", stderr_buf.getvalue(),
                      "1002 retry diagnostic must appear on stderr")

        # Scenario: HTTP 529 retry then success. Same invariant.
        with patch.object(
            minimax_common.urllib.request, "urlopen",
            side_effect=[_mock_http_error(529), _mock_resp(SUCCESS_BODY)],
        ):
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                 contextlib.redirect_stderr(stderr_buf):
                self._run()
        self.assertEqual(stdout_buf.getvalue(), "",
                         "HTTP 529 retry must not write to stdout")
        self.assertIn("529", stderr_buf.getvalue(),
                      "HTTP 529 retry diagnostic must appear on stderr")

        # Scenario: URLError (DNS/timeout/connectivity) retry then success.
        # Same invariant -- stdout must remain empty so minimax-review.sh
        # --json captures only the final JSON payload.
        url_err = urllib.error.URLError("connection refused")
        with patch.object(
            minimax_common.urllib.request, "urlopen",
            side_effect=[url_err, _mock_resp(SUCCESS_BODY)],
        ):
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), \
                 contextlib.redirect_stderr(stderr_buf):
                self._run()
        self.assertEqual(stdout_buf.getvalue(), "",
                         "URLError retry must not write to stdout")
        self.assertIn("network error", stderr_buf.getvalue(),
                      "URLError retry diagnostic must appear on stderr")

    def test_mm_api_source_header_emitted(self):
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = dict(req.header_items())
            return _mock_resp(SUCCESS_BODY)

        with patch.object(
            minimax_common.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            self._run()
        # urllib.request.Request normalizes header names to title-case on
        # retrieval, so 'MM-API-Source' becomes 'Mm-api-source'. Compare
        # case-insensitively so the test is resilient to casing changes.
        hdrs_lower = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(hdrs_lower.get("mm-api-source"), "K2B")

    def test_mm_api_source_disable_flag_drops_header(self):
        """MM_API_SOURCE_DISABLE=1 must drop the header entirely.

        Insurance for the case where a proxy or future API version rejects
        unknown headers: operators can disable telemetry without patching
        code. The main path still emits the header (see prior test); this
        one proves the escape hatch works.
        """
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = dict(req.header_items())
            return _mock_resp(SUCCESS_BODY)

        with patch.dict(os.environ, {"MM_API_SOURCE_DISABLE": "1"}, clear=False):
            with patch.object(
                minimax_common.urllib.request, "urlopen", side_effect=fake_urlopen
            ):
                self._run()
        hdrs_lower = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertNotIn("mm-api-source", hdrs_lower)


if __name__ == "__main__":
    unittest.main(verbosity=2)
