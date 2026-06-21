#!/usr/bin/env python3
"""Guard user-facing Kimi review/provider labels during the migration."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class KimiProviderLabelTests(unittest.TestCase):
    def test_hot_error_paths_do_not_claim_minimax_when_kimi_is_default(self) -> None:
        checks = {
            "scripts/minimax-compile.sh": [
                "MiniMax API call failed",
                "Empty response from MiniMax",
                "Invalid JSON from MiniMax",
            ],
            "scripts/minimax-lint-deep.sh": [
                "MiniMax API call failed",
                "Empty response from MiniMax",
                "Invalid JSON from MiniMax",
            ],
            "scripts/minimax-research-extract.sh": [
                "MiniMax API call failed",
                "Empty response from MiniMax",
                "Invalid JSON from MiniMax",
            ],
            "scripts/minimax-json-job.sh": [
                "Invalid JSON from MiniMax",
            ],
            "scripts/minimax-bootstrap.sh": [
                "Calling MiniMax",
            ],
            "scripts/minimax-weave.sh": [
                "invalid JSON from MiniMax",
            ],
            "scripts/k2b-weave.sh": [
                "weave: MiniMax call failed",
                "minimax_api_error",
                "weave: MiniMax returned invalid JSON schema",
            ],
        }
        for rel, forbidden_snippets in checks.items():
            text = (ROOT / rel).read_text(encoding="utf-8")
            for snippet in forbidden_snippets:
                self.assertNotIn(snippet, text, msg=f"{rel} still contains {snippet!r}")

    def test_kimi_review_shell_fails_fast_on_missing_kimi_key(self) -> None:
        text = (ROOT / "scripts" / "kimi-review.sh").read_text(encoding="utf-8")
        self.assertIn("kimi-review: KIMI_API_KEY not set", text)
        self.assertNotIn("MINIMAX_API_KEY:-", text)

    def test_kimi_review_shell_direct_help_in_repo(self) -> None:
        env = os.environ.copy()
        env["KIMI_API_KEY"] = "fake-kimi-key"
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "kimi-review.sh"), "--help"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Usage: scripts/kimi-review.sh [--help] [flags]", result.stdout)

    def test_kimi_review_shell_reports_non_git_repo(self) -> None:
        env = os.environ.copy()
        env["KIMI_API_KEY"] = "fake-kimi-key"
        with tempfile.TemporaryDirectory() as td:
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "kimi-review.sh"), "--help"],
                cwd=td,
                env=env,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage: scripts/kimi-review.sh [--help] [flags]", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
