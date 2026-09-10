#!/usr/bin/env python3
"""Regression tests for the Kimi client in the historical wrapper module."""
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


def _mock_http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.minimaxi.com/v1/text/chatcompletion_v2",
        code=code,
        msg="transient",
        hdrs=None,
        fp=BytesIO(b'{"error": "transient"}'),
    )


class _StreamingResponse:
    def __init__(self, events: list[dict]):
        self._lines = []
        for event in events:
            self._lines.extend(
                [
                    f"event:{event['type']}\n".encode(),
                    f"data:{json.dumps(event)}\n".encode(),
                    b"\n",
                ]
            )

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TestProviderRouting(unittest.TestCase):
    def test_non_kimi_provider_fails_before_network_or_credentials(self):
        previous = minimax_common.K2B_LLM_PROVIDER
        minimax_common.K2B_LLM_PROVIDER = "minimax"
        try:
            with patch.object(minimax_common, "load_kimi_api_key") as load_key:
                with patch.object(
                    minimax_common.urllib.request, "urlopen"
                ) as network:
                    with self.assertRaisesRegex(
                        minimax_common.MinimaxError, "retired"
                    ):
                        minimax_common.chat_completion(
                            "legacy-model", [{"role": "user", "content": "hi"}]
                        )
            load_key.assert_not_called()
            network.assert_not_called()
        finally:
            minimax_common.K2B_LLM_PROVIDER = previous


class TestKimiStreaming(unittest.TestCase):
    def test_streaming_response_keeps_only_final_text_and_usage(self):
        events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg-test",
                    "model": "kimi-k2.7-code",
                    "usage": {
                        "input_tokens": 0,
                        "cache_creation_input_tokens": 3,
                        "cache_read_input_tokens": 7,
                    },
                },
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "private reasoning"},
            },
            {
                "type": "content_block_start",
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "OK"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 11},
            },
            {"type": "message_stop"},
        ]
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["payload"] = json.loads(req.data)
            return _StreamingResponse(events)

        with patch.object(minimax_common, "load_kimi_api_key", return_value="fake-key"):
            with patch.object(
                minimax_common.urllib.request, "urlopen", side_effect=fake_urlopen
            ):
                result = minimax_common._kimi_chat_completion(
                    [{"role": "user", "content": "hi"}],
                    model="kimi-custom-review-model",
                    max_tokens=128,
                    temperature=0.2,
                    timeout=30,
                )

        self.assertTrue(captured["payload"]["stream"])
        self.assertEqual(captured["payload"]["model"], "kimi-custom-review-model")
        self.assertEqual(result["choices"][0]["message"]["content"], "OK")
        self.assertNotIn("private reasoning", json.dumps(result))
        self.assertEqual(result["choices"][0]["finish_reason"], "end_turn")
        self.assertEqual(result["usage"], {
            "prompt_tokens": 10,
            "completion_tokens": 11,
            "total_tokens": 21,
        })

    def test_incomplete_stream_retries_then_succeeds(self):
        incomplete_events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg-incomplete",
                    "model": "kimi-k2.7-code",
                    "usage": {"input_tokens": 1},
                },
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "partial"},
            },
        ]
        success_events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg-after-retry",
                    "model": "kimi-k2.7-code",
                    "usage": {"input_tokens": 1},
                },
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "complete"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
        with patch.object(minimax_common, "load_kimi_api_key", return_value="fake-key"):
            with patch.object(minimax_common.time, "sleep", return_value=None):
                with patch.object(
                    minimax_common.urllib.request,
                    "urlopen",
                    side_effect=[
                        _StreamingResponse(incomplete_events),
                        _StreamingResponse(success_events),
                    ],
                ) as mock_open:
                    result = minimax_common._kimi_chat_completion(
                        [{"role": "user", "content": "hi"}],
                        max_tokens=128,
                        temperature=0.2,
                        timeout=30,
                    )
        self.assertEqual(mock_open.call_count, 2)
        self.assertEqual(result["choices"][0]["message"]["content"], "complete")

    def test_stream_requires_stop_reason(self):
        events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg-no-reason",
                    "model": "kimi-k2.7-code",
                    "usage": {"input_tokens": 1},
                },
            },
            {"type": "message_stop"},
        ]
        with self.assertRaisesRegex(minimax_common.MinimaxError, "stop_reason"):
            minimax_common._read_kimi_stream(_StreamingResponse(events))

    def test_transient_stream_error_retries_then_succeeds(self):
        overloaded = _StreamingResponse(
            [
                {
                    "type": "error",
                    "error": {
                        "type": "overloaded_error",
                        "message": "busy",
                    },
                }
            ]
        )
        success = _StreamingResponse(
            [
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg-retry",
                        "model": "kimi-k2.7-code",
                        "usage": {"input_tokens": 1},
                    },
                },
                {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "OK"},
                },
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 1},
                },
                {"type": "message_stop"},
            ]
        )
        with patch.object(minimax_common, "load_kimi_api_key", return_value="fake-key"):
            with patch.object(minimax_common.time, "sleep", return_value=None):
                with patch.object(
                    minimax_common.urllib.request,
                    "urlopen",
                    side_effect=[overloaded, success],
                ) as mock_open:
                    result = minimax_common._kimi_chat_completion(
                        [{"role": "user", "content": "hi"}],
                        max_tokens=128,
                        temperature=0.2,
                        timeout=30,
                    )
        self.assertEqual(mock_open.call_count, 2)
        self.assertEqual(result["choices"][0]["message"]["content"], "OK")

    def test_http_429_and_500_retry_then_succeed(self):
        success_events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg-http-retry",
                    "model": "kimi-k2.7-code",
                    "usage": {"input_tokens": 1},
                },
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "OK"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
        for status in (429, 500):
            with self.subTest(status=status):
                with patch.object(
                    minimax_common, "load_kimi_api_key", return_value="fake-key"
                ):
                    with patch.object(minimax_common.time, "sleep", return_value=None):
                        with patch.object(
                            minimax_common.urllib.request,
                            "urlopen",
                            side_effect=[
                                _mock_http_error(status),
                                _StreamingResponse(success_events),
                            ],
                        ) as mock_open:
                            result = minimax_common._kimi_chat_completion(
                                [{"role": "user", "content": "hi"}],
                                max_tokens=128,
                                temperature=0.2,
                                timeout=30,
                            )
                self.assertEqual(mock_open.call_count, 2)
                self.assertEqual(
                    result["choices"][0]["message"]["content"], "OK"
                )

    def test_openai_payload_bridge_preserves_shell_worker_controls(self):
        captured = {}

        def fake_completion(**kwargs):
            captured.update(kwargs)
            return {"base_resp": {"status_code": 0}}

        payload = {
            "model": "ignored-old-model",
            "messages": [
                {"role": "system", "content": "System prompt"},
                {"role": "user", "content": "hi"},
            ],
            "max_completion_tokens": 1234,
            "temperature": 0.3,
        }
        with patch.object(
            minimax_common, "_kimi_chat_completion", side_effect=fake_completion
        ):
            result = minimax_common.kimi_completion_from_openai_payload(payload)
        self.assertEqual(result, {"base_resp": {"status_code": 0}})
        self.assertEqual(captured["messages"], payload["messages"])
        self.assertEqual(captured["model"], minimax_common.KIMI_DEFAULT_MODEL)
        self.assertEqual(captured["max_tokens"], 1234)
        self.assertEqual(captured["temperature"], 0.3)
        self.assertEqual(captured["timeout"], minimax_common.DEFAULT_TIMEOUT_S)

    def test_python_rejects_metered_kimi_host_without_explicit_opt_in(self):
        with patch.object(minimax_common, "KIMI_API_HOST", "https://api.moonshot.cn"):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("K2B_ALLOW_METERED_KIMI_PLATFORM", None)
                with self.assertRaisesRegex(minimax_common.MinimaxError, "pay-as-you-go"):
                    minimax_common._validated_kimi_api_host()

    def test_python_rejects_metered_kimi_host_trailing_dot_alias(self):
        with patch.object(
            minimax_common, "KIMI_API_HOST", "https://api.moonshot.cn./v1"
        ):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("K2B_ALLOW_METERED_KIMI_PLATFORM", None)
                with self.assertRaisesRegex(minimax_common.MinimaxError, "pay-as-you-go"):
                    minimax_common._validated_kimi_api_host()

    def test_python_allows_metered_kimi_host_with_explicit_opt_in(self):
        with patch.object(minimax_common, "KIMI_API_HOST", "https://api.moonshot.cn/"):
            with patch.dict(
                os.environ,
                {"K2B_ALLOW_METERED_KIMI_PLATFORM": "true"},
                clear=False,
            ):
                self.assertEqual(
                    minimax_common._validated_kimi_api_host(),
                    "https://api.moonshot.cn",
                )

    def test_python_rejects_plaintext_kimi_host(self):
        with patch.object(
            minimax_common, "KIMI_API_HOST", "http://api.kimi.com/coding"
        ):
            with self.assertRaisesRegex(minimax_common.MinimaxError, "HTTPS"):
                minimax_common._validated_kimi_api_host()


if __name__ == "__main__":
    unittest.main(verbosity=2)
