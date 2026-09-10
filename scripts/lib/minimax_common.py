"""Shared Kimi HTTP client behind K2B's historical wrapper module name.

MiniMax is retired and cannot be selected through this module.  The old file
name remains only to avoid a broad import migration during Stage 1.
"""

import http.client
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_TIMEOUT_S = 300

# Kimi K2.7 via the Anthropic-compatible /coding endpoint. Primary text
# provider as of 2026-04-25 -- see scripts/minimax-common.sh header.
# Kimi is text-only. GPTsAPI owns image, VLM/OCR, TTS, and STT in K2B now.
K2B_LLM_PROVIDER = os.environ.get("K2B_LLM_PROVIDER", "kimi").strip() or "kimi"
KIMI_API_HOST = os.environ.get("KIMI_API_HOST", "https://api.kimi.com/coding")
KIMI_MESSAGES_PATH = "/v1/messages"
KIMI_DEFAULT_MODEL = os.environ.get("KIMI_DEFAULT_MODEL", "kimi-k2.7-code")

# Default output ceiling for text completions. 4096 was too small for the
# large background jobs (observer / weave / compile / lint): kimi-k2.7-code
# spends the whole budget reasoning and returns EMPTY content with
# finish_reason=max_tokens. Verified 2026-07-04 on a real observer-size
# prompt: at 4096 -> content_len=0 (finish=max_tokens); at 16384 -> full
# answer (finish=end_turn, ~13k output tokens). Raising a ceiling is free for
# small calls (they still stop at end_turn early) and rescues the big ones.
# Override with K2B_LLM_MAX_TOKENS.
def _env_positive_int(name: str, default: int) -> int:
    """Parse a positive-int env override, falling back to `default` on unset,
    empty, or non-numeric values. Must NEVER raise at import time: a bad
    K2B_LLM_MAX_TOKENS (e.g. "16k", "16,384", "") would otherwise crash every
    module that imports this one, not just the caller that set it."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    return val if val > 0 else default


DEFAULT_MAX_TOKENS = _env_positive_int("K2B_LLM_MAX_TOKENS", 16384)

# Transient server-side HTTP statuses worth retrying. 502/503/504 are upstream
# gateway hiccups. Anything
# else at the HTTP level is treated as a real error and surfaces immediately.
RETRY_HTTP_STATUSES = {429, 500, 502, 503, 504, 529}
# Transient application-level base_resp.status_code values worth retrying
# with the same backoff as HTTP 529. 1002 = rate limit -- Keith's text usage
# is flagged "Heavy" (1500 req / 5h window), so bursty /ship + observer runs
# can hit 1002 without ever seeing a 529.
MAX_RETRIES = 3
RETRY_BACKOFF_S = (10, 20, 40)
# Full-jitter added on top of each backoff step. Keeps concurrent /ship +
# observer callers from retrying in lockstep when the whole fleet hits the
# same rate-limit window. Range is intentionally wide enough that 3 parallel
# callers will land in different seconds.
RETRY_JITTER_MAX_S = 5.0


class MinimaxError(RuntimeError):
    pass


class KimiTransientError(MinimaxError):
    """A provider-declared transient SSE failure that is safe to retry."""


def load_kimi_api_key() -> str:
    key = os.environ.get("KIMI_API_KEY", "").strip()
    if key:
        return key
    env_file = Path(os.environ.get("K2B_ENV_FILE", Path.home() / ".k2b-env"))
    if env_file.is_file():
        env_stat = env_file.stat()
        if env_stat.st_uid != os.getuid() or env_stat.st_mode & 0o777 != 0o600:
            raise MinimaxError(
                f"Kimi credential file must be owned by the current user "
                f"and mode 0600: {env_file}"
            )
        match = re.search(
            r"^\s*(?:export\s+)?KIMI_API_KEY\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s#]+))",
            env_file.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if match:
            return next(value for value in match.groups() if value is not None)
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        match = re.search(
            r'^\s*export\s+KIMI_API_KEY\s*=\s*"([^"]+)"',
            zshrc.read_text(),
            re.MULTILINE,
        )
        if match:
            return match.group(1)
    raise MinimaxError(
        "KIMI_API_KEY not set and not found in the dedicated credential file "
        "or ~/.zshrc. Configure ~/.k2b-env with mode 0600 and keep "
        "K2B_LLM_PROVIDER=kimi."
    )


def _validated_kimi_api_host() -> str:
    """Return the configured Kimi host after guarding metered endpoints."""
    host = KIMI_API_HOST.rstrip("/")
    parsed = urllib.parse.urlparse(host)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not hostname:
        raise MinimaxError("KIMI_API_HOST must be a valid HTTPS URL")
    if hostname in {"api.moonshot.cn", "api.moonshot.ai"} and os.environ.get(
        "K2B_ALLOW_METERED_KIMI_PLATFORM", "false"
    ).lower() != "true":
        raise MinimaxError(
            "KIMI_API_HOST selects the pay-as-you-go Kimi Open Platform. "
            "K2B defaults to Kimi Code membership at "
            "https://api.kimi.com/coding. A metered platform switch requires "
            "explicit K2B_ALLOW_METERED_KIMI_PLATFORM=true."
        )
    return host


def chat_completion(
    model: str,
    messages: list,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = 0.2,
    tools: list | None = None,
    tool_choice: str | None = None,
    response_format: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> dict:
    """POST a chat-completion request and return the parsed JSON response.

    Routes only to Kimi K2.7. Any other provider value fails closed before
    credentials are loaded or a network request is constructed.

    Kimi responses are translated into the MiniMax chatcompletion_v2 envelope
    shape (choices[0].message.content / usage.{prompt,completion,total}_tokens
    / base_resp.status_code=0) so downstream extract_assistant_text /
    extract_token_usage callers keep working unchanged.

    Raises MinimaxError on transport, HTTP, or API-level errors.
    """
    if K2B_LLM_PROVIDER != "kimi":
        raise MinimaxError(
            "MiniMax routing is retired; set K2B_LLM_PROVIDER=kimi"
        )
    kimi_model = model if str(model).lower().startswith("kimi-") else None
    return _kimi_chat_completion(
        messages=messages,
        model=kimi_model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )


def _kimi_chat_completion(
    messages: list,
    *,
    model: str | None = None,
    max_tokens: int,
    temperature: float,
    timeout: int,
) -> dict:
    """Call Kimi K2.7 at /coding/v1/messages and return the response in
    MiniMax chatcompletion_v2 envelope shape.

    Translation:
      - System-role messages -> top-level `system` (Anthropic concatenates
        duplicates; we join with \\n\\n).
      - `response_format` dropped (no Anthropic equivalent; prompts already
        instruct JSON output).
      - Kimi model ids are honored; historical MiniMax model ids fall back to
        KIMI_DEFAULT_MODEL for compatibility.
    """
    api_key = load_kimi_api_key()

    system_parts = [m.get("content", "") for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    payload: dict = {
        "model": model or KIMI_DEFAULT_MODEL,
        "max_tokens": max_tokens,
        "messages": non_system,
        "temperature": temperature,
        # Streaming keeps the authenticated connection active during Kimi's
        # extended-thinking phase. The non-streaming endpoint can otherwise
        # be closed upstream before a long review emits its final text.
        "stream": True,
    }
    if system_parts:
        payload["system"] = "\n\n".join(s for s in system_parts if s)

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        f"{_validated_kimi_api_host()}{KIMI_MESSAGES_PATH}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                parsed = _read_kimi_stream(resp)
            break
        except KimiTransientError as e:
            if attempt < MAX_RETRIES:
                wait_s = RETRY_BACKOFF_S[attempt] + random.uniform(
                    0, RETRY_JITTER_MAX_S
                )
                print(
                    f"[kimi] transient stream error on attempt {attempt + 1}: "
                    f"{e}; retrying in {wait_s:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait_s)
                last_err = e
                continue
            raise MinimaxError(
                f"Transient Kimi stream error after {MAX_RETRIES + 1} "
                f"attempts: {e}"
            ) from e
        except MinimaxError:
            # A response that arrived but violates the stream contract is not
            # a transient transport failure. Retrying can rebill the full
            # prompt and cannot repair the already-received response.
            raise
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if e.code in RETRY_HTTP_STATUSES and attempt < MAX_RETRIES:
                wait_s = RETRY_BACKOFF_S[attempt] + random.uniform(0, RETRY_JITTER_MAX_S)
                print(
                    f"[kimi] HTTP {e.code} (transient) on attempt {attempt + 1}; "
                    f"retrying in {wait_s:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait_s)
                last_err = e
                continue
            raise MinimaxError(f"HTTP {e.code} from Kimi: {detail[:500]}") from e
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, TimeoutError) as e:
            # RemoteDisconnected (HTTPException subclass), connection resets,
            # and socket timeouts all fall here. Kimi has occasional mid-stream
            # drops under long prompts -- retry generously.
            if attempt < MAX_RETRIES:
                wait_s = RETRY_BACKOFF_S[attempt] + random.uniform(0, RETRY_JITTER_MAX_S)
                print(
                    f"[kimi] network error on attempt {attempt + 1}: {type(e).__name__}: {e}; "
                    f"retrying in {wait_s:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(wait_s)
                last_err = e
                continue
            raise MinimaxError(
                f"Network error contacting Kimi after {MAX_RETRIES + 1} attempts: {e}"
            ) from e
    else:
        raise MinimaxError(
            f"Kimi unreachable after {MAX_RETRIES + 1} attempts; last error: {last_err}"
        )

    if isinstance(parsed.get("error"), dict):
        err = parsed["error"]
        raise MinimaxError(
            f"Kimi API error {err.get('type', '?')}: {err.get('message', 'unknown')}"
        )

    content_blocks = parsed.get("content") or []
    assistant_text = "".join(
        b.get("text", "") for b in content_blocks if b.get("type") == "text"
    )
    usage_raw = parsed.get("usage") or {}
    # Kimi already emits OpenAI-style prompt_tokens/completion_tokens/total_tokens
    # alongside its Anthropic-style input_tokens/output_tokens. Prefer the
    # OpenAI-compat fields; fall back to computing from Anthropic fields.
    usage = {
        "prompt_tokens": usage_raw.get("prompt_tokens", usage_raw.get("input_tokens")),
        "completion_tokens": usage_raw.get(
            "completion_tokens", usage_raw.get("output_tokens")
        ),
        "total_tokens": usage_raw.get(
            "total_tokens",
            (usage_raw.get("input_tokens") or 0) + (usage_raw.get("output_tokens") or 0),
        ),
    }

    return {
        "id": parsed.get("id"),
        "model": parsed.get("model", KIMI_DEFAULT_MODEL),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": assistant_text},
                "finish_reason": parsed.get("stop_reason") or "stop",
            }
        ],
        "usage": usage,
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }


def _read_kimi_stream(resp) -> dict:
    """Collapse an Anthropic-compatible SSE response into one message.

    Thinking and signature blocks are deliberately ignored. Only final text,
    public response metadata, stop reason, and aggregate usage leave this
    parser, matching the existing non-streaming return contract.
    """
    response_id = None
    model = KIMI_DEFAULT_MODEL
    stop_reason = None
    text_parts: list[str] = []
    usage_start: dict = {}
    usage_end: dict = {}
    saw_event = False
    saw_message_stop = False
    raw_lines: list[bytes] = []

    for raw_line in resp:
        if isinstance(raw_line, str):
            raw = raw_line.encode("utf-8")
        else:
            raw = raw_line
        if not saw_event:
            raw_lines.append(raw)
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        payload_text = line[5:].strip()
        if not payload_text or payload_text == "[DONE]":
            continue
        try:
            event = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise MinimaxError(
                f"Non-JSON SSE event from Kimi: {payload_text[:500]}"
            ) from exc
        saw_event = True
        event_type = event.get("type")
        if event_type == "error":
            error = event.get("error") or event
            error_type = str(error.get("type", "")).lower()
            if error_type in {
                "overloaded_error",
                "rate_limit_error",
                "internal_server_error",
            }:
                raise KimiTransientError(
                    f"{error_type}: {error.get('message', 'provider error')}"
                )
            return {"error": error}
        if event_type == "message_start":
            message = event.get("message") or {}
            response_id = message.get("id")
            model = message.get("model") or model
            usage_start = message.get("usage") or {}
        elif event_type == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "text" and block.get("text"):
                text_parts.append(str(block["text"]))
        elif event_type == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                text_parts.append(str(delta["text"]))
        elif event_type == "message_delta":
            delta = event.get("delta") or {}
            stop_reason = delta.get("stop_reason") or stop_reason
            usage_end = event.get("usage") or usage_end
        elif event_type == "message_stop":
            saw_message_stop = True

    if not saw_event:
        body = b"".join(raw_lines).decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise MinimaxError(f"Non-JSON response from Kimi: {body[:500]}") from exc

    if not saw_message_stop:
        raise KimiTransientError(
            "Incomplete SSE response from Kimi: missing message_stop"
        )
    if not stop_reason:
        raise KimiTransientError(
            "Incomplete SSE response from Kimi: missing stop_reason"
        )

    prompt_tokens = usage_start.get("prompt_tokens")
    if prompt_tokens is None:
        prompt_tokens = sum(
            int(usage_start.get(field) or 0)
            for field in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
    completion_tokens = usage_end.get(
        "completion_tokens", usage_end.get("output_tokens", 0)
    )
    return {
        "id": response_id,
        "model": model,
        "content": [{"type": "text", "text": "".join(text_parts)}],
        "stop_reason": stop_reason,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": (prompt_tokens or 0) + (completion_tokens or 0),
        },
    }


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


def kimi_completion_from_openai_payload(payload: dict) -> dict:
    """Translate an older shell worker payload through the streaming client."""
    if not isinstance(payload, dict):
        raise MinimaxError("Kimi request payload must be a JSON object")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise MinimaxError("Kimi request payload requires a messages array")

    raw_max_tokens = payload.get(
        "max_tokens", payload.get("max_completion_tokens", DEFAULT_MAX_TOKENS)
    )
    try:
        max_tokens = int(raw_max_tokens)
    except (TypeError, ValueError):
        max_tokens = DEFAULT_MAX_TOKENS
    if max_tokens <= 0:
        max_tokens = DEFAULT_MAX_TOKENS

    temperature = payload.get("temperature", 0.2)
    if not isinstance(temperature, (int, float)):
        temperature = 0.2
    return _kimi_chat_completion(
        messages=messages,
        model=KIMI_DEFAULT_MODEL,
        max_tokens=max_tokens,
        temperature=float(temperature),
        timeout=DEFAULT_TIMEOUT_S,
    )


def _run_shell_kimi_bridge() -> int:
    """stdin/stdout bridge used by historical shell worker wrappers."""
    try:
        payload = json.load(sys.stdin)
        response = kimi_completion_from_openai_payload(payload)
    except (json.JSONDecodeError, MinimaxError) as exc:
        print(f"ERROR: Kimi API call failed: {exc}", file=sys.stderr)
        return 1
    json.dump(response, sys.stdout, separators=(",", ":"), ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--kimi-openai-payload"]:
        raise SystemExit(_run_shell_kimi_bridge())
    print("usage: minimax_common.py --kimi-openai-payload", file=sys.stderr)
    raise SystemExit(2)
