"""Shared HTTP client + key loading for K2Bi MiniMax wrappers.

Always uses the global endpoint (api.minimaxi.com), never the China-only
.chat host. See ~/.claude/projects/.../memory/minimax_endpoint.md.
"""

import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MINIMAX_API_HOST = os.environ.get("MINIMAX_API_HOST", "https://api.minimaxi.com")
CHAT_PATH = "/v1/text/chatcompletion_v2"
DEFAULT_TIMEOUT_S = 300

# Transient server-side HTTP statuses worth retrying. 529 = "overloaded"
# (MiniMax congestion peak), 502/503/504 = upstream gateway hiccups. Anything
# else at the HTTP level is treated as a real error and surfaces immediately.
RETRY_HTTP_STATUSES = {502, 503, 504, 529}
# Transient application-level base_resp.status_code values worth retrying
# with the same backoff as HTTP 529. 1002 = rate limit -- Keith's text usage
# is flagged "Heavy" (1500 req / 5h window), so bursty /ship + observer runs
# can hit 1002 without ever seeing a 529.
RETRY_APP_STATUSES = {1002}
# Application-level status_codes where retry is guaranteed not to help.
# 1008 = "insufficient balance / quota" per MiniMax -- either the paid
# balance is depleted OR the per-window rate quota is exhausted. Either way
# no amount of client-side retry recovers; the operator has to top up or
# wait. Fail loud immediately instead of burning the full backoff ladder.
FAIL_FAST_APP_STATUSES = {1008}
MAX_RETRIES = 3
RETRY_BACKOFF_S = (10, 20, 40)
# Full-jitter added on top of each backoff step. Keeps concurrent /ship +
# observer callers from retrying in lockstep when the whole fleet hits the
# same rate-limit window. Range is intentionally wide enough that 3 parallel
# callers will land in different seconds.
RETRY_JITTER_MAX_S = 5.0


class MinimaxError(RuntimeError):
    pass


def load_api_key() -> str:
    key = os.environ.get("MINIMAX_API_KEY", "").strip()
    if key:
        return key
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        match = re.search(
            r'^\s*export\s+MINIMAX_API_KEY\s*=\s*"([^"]+)"',
            zshrc.read_text(),
            re.MULTILINE,
        )
        if match:
            return match.group(1)
    raise MinimaxError(
        "MINIMAX_API_KEY not set and not found in ~/.zshrc. "
        "Export it or add: export MINIMAX_API_KEY=\"...\""
    )


def chat_completion(
    model: str,
    messages: list,
    *,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    tools: list | None = None,
    tool_choice: str | None = None,
    response_format: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict:
    """POST to chatcompletion_v2 and return the parsed JSON response.

    Raises MinimaxError on transport, HTTP, or API-level errors.
    """
    api_key = load_api_key()
    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice
    if response_format:
        payload["response_format"] = response_format

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # Telemetry-only header that lets MiniMax distinguish K2B traffic from
    # their official MCP client. Set MM_API_SOURCE_DISABLE=1 to drop it if
    # a proxy or future API version rejects unknown headers.
    if os.environ.get("MM_API_SOURCE_DISABLE") != "1":
        headers["MM-API-Source"] = "K2B"
    req = urllib.request.Request(
        f"{MINIMAX_API_HOST}{CHAT_PATH}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code in RETRY_HTTP_STATUSES and attempt < MAX_RETRIES:
                wait_s = RETRY_BACKOFF_S[attempt] + random.uniform(0, RETRY_JITTER_MAX_S)
                # stderr only: callers like minimax-review.sh --json capture
                # stdout verbatim for the final JSON payload, so retry
                # diagnostics on stdout would corrupt the JSON.
                print(
                    f"[minimax] HTTP {e.code} (transient) on attempt {attempt + 1}; "
                    f"retrying in {wait_s:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait_s)
                last_err = e
                continue
            raise MinimaxError(f"HTTP {e.code} from MiniMax: {detail[:500]}") from e
        except urllib.error.URLError as e:
            # Network errors (timeout, DNS, connection refused) are also worth
            # one or two retries -- often resolves on the next attempt.
            if attempt < MAX_RETRIES:
                wait_s = RETRY_BACKOFF_S[attempt] + random.uniform(0, RETRY_JITTER_MAX_S)
                print(
                    f"[minimax] network error on attempt {attempt + 1}: {e}; "
                    f"retrying in {wait_s:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait_s)
                last_err = e
                continue
            raise MinimaxError(f"Network error contacting MiniMax after {MAX_RETRIES + 1} attempts: {e}") from e

        # HTTP 200. Parse and dispatch on application-level status_code.
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as e:
            raise MinimaxError(f"Non-JSON response from MiniMax: {body[:500]}") from e

        base_resp = parsed.get("base_resp") or {}
        status_code = base_resp.get("status_code")
        if status_code in (None, 0):
            return parsed

        status_msg = base_resp.get("status_msg", "unknown")

        # 1008: insufficient balance or exhausted rate quota -- no amount of
        # retry recovers. Raise immediately and surface the provider's own
        # status_msg so the operator sees the actual remediation path (top
        # up balance vs wait for window reset) without us guessing.
        if status_code in FAIL_FAST_APP_STATUSES:
            raise MinimaxError(
                f"MiniMax API error {status_code} "
                f"(insufficient balance or rate quota): {status_msg}. "
                f"Check balance/quota at minimaxi.com -- retry will not help."
            )

        # 1002 rate limit: transient, retry with the HTTP-level backoff ladder.
        if status_code in RETRY_APP_STATUSES and attempt < MAX_RETRIES:
            wait_s = RETRY_BACKOFF_S[attempt] + random.uniform(0, RETRY_JITTER_MAX_S)
            print(
                f"[minimax] API status {status_code} (rate-limit) on attempt "
                f"{attempt + 1}; retrying in {wait_s:.1f}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(wait_s)
            last_err = MinimaxError(f"status {status_code}: {status_msg}")
            continue

        # Non-retryable, non-fail-fast app error, or retries exhausted on 1002.
        raise MinimaxError(f"MiniMax API error {status_code}: {status_msg}")

    # All attempts hit a transient condition (HTTP 5xx, URLError, or 1002)
    # and we ran out of retries without ever getting a 200 with status_code=0.
    raise MinimaxError(
        f"MiniMax unreachable after {MAX_RETRIES + 1} attempts; last error: {last_err}"
    )


def extract_assistant_text(response: dict) -> str:
    """Pull the assistant message content out of a chatcompletion_v2 response."""
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def extract_token_usage(response: dict) -> dict:
    usage = response.get("usage") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
