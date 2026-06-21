#!/usr/bin/env python3
"""Regression coverage for the K2B Kimi default model.

The live default is resolved in three places:
- scripts/minimax-common.sh (shell wrappers)
- scripts/lib/minimax_common.py (Python wrappers)
- scripts/lib/minimax_review.py (--model argparse default/help text)

All three must move together when the default Kimi coding model changes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = ROOT / "scripts" / "lib"
SCRIPT_DIR = ROOT / "scripts"
EXPECTED_MODEL = "kimi-k2.7-code"


class TestKimiDefaultModel(unittest.TestCase):
    def test_python_wrapper_default_model(self) -> None:
        env = os.environ.copy()
        env.pop("KIMI_DEFAULT_MODEL", None)
        env["K2B_LLM_PROVIDER"] = "kimi"
        code = (
            "import sys; "
            f"sys.path.insert(0, {str(LIB_DIR)!r}); "
            "import minimax_common; "
            "print(minimax_common.KIMI_DEFAULT_MODEL)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), EXPECTED_MODEL)

    def test_shell_wrapper_default_model(self) -> None:
        env = os.environ.copy()
        env.pop("KIMI_DEFAULT_MODEL", None)
        env.pop("K2B_LLM_MODEL", None)
        env["K2B_LLM_PROVIDER"] = "kimi"
        env["MINIMAX_API_KEY"] = "fake-minimax-key"
        env["KIMI_API_KEY"] = "fake-kimi-key"
        result = subprocess.run(
            [
                "bash",
                "-lc",
                (
                    "source scripts/minimax-common.sh >/dev/null 2>&1 && "
                    "printf '%s\\n%s\\n' \"$KIMI_DEFAULT_MODEL\" \"$K2B_LLM_MODEL\""
                ),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(
            result.stdout.strip().splitlines(),
            [EXPECTED_MODEL, EXPECTED_MODEL],
        )

    def test_reviewer_help_uses_same_default_model(self) -> None:
        env = os.environ.copy()
        env.pop("K2B_LLM_MODEL", None)
        env["K2B_LLM_PROVIDER"] = "kimi"
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "lib" / "minimax_review.py"), "--help"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn(f"Model id (default {EXPECTED_MODEL})", result.stdout)

    def test_shell_kimi_translation_honors_max_completion_tokens(self) -> None:
        env = os.environ.copy()
        env["K2B_LLM_PROVIDER"] = "kimi"
        env["MINIMAX_API_KEY"] = "fake-minimax-key"
        env["KIMI_API_KEY"] = "fake-kimi-key"

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fakebin = tmp / "bin"
            fakebin.mkdir()
            body_path = tmp / "request.json"
            fake_curl = fakebin / "curl"
            fake_curl.write_text(
                f"""#!/usr/bin/env bash
set -euo pipefail
body=""
while (($#)); do
  case "$1" in
    -d) body="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf '%s' "$body" > "$BODY_PATH"
printf '%s\\n' '{{"id":"msg_test","model":"{EXPECTED_MODEL}","content":[{{"type":"text","text":"{{}}"}}],"usage":{{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}},"stop_reason":"end_turn"}}'
""",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)

            env["BODY_PATH"] = str(body_path)
            env["PATH"] = f"{fakebin}{os.pathsep}{env['PATH']}"
            command = (
                "source scripts/minimax-common.sh >/dev/null 2>&1 && "
                "mm_api POST /v1/text/chatcompletion_v2 "
                + json.dumps(
                    json.dumps(
                        {
                            "model": "ignored",
                            "messages": [
                                {"role": "system", "content": "System prompt line"},
                                {"role": "user", "content": "hi"},
                                {"role": "assistant", "content": "past"},
                            ],
                            "max_completion_tokens": 1234,
                            "temperature": 0.2,
                        }
                    )
                )
                + " >/dev/null"
            )
            subprocess.run(
                ["bash", "-c", command],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

            translated = json.loads(body_path.read_text(encoding="utf-8"))
            self.assertEqual(translated["max_tokens"], 1234)
            self.assertEqual(translated["model"], EXPECTED_MODEL)
            self.assertEqual(translated["system"], "System prompt line")
            self.assertEqual(translated["temperature"], 0.2)
            self.assertEqual(
                translated["messages"],
                [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "past"},
                ],
            )
            for msg in translated["messages"]:
                self.assertNotEqual(msg.get("role"), "system")


if __name__ == "__main__":
    unittest.main(verbosity=2)
