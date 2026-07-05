#!/usr/bin/env python3
"""Regression coverage for Kimi reviewer verdict headers in review_runner."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib import minimax_common as mmc  # noqa: E402
from scripts.lib import review_runner as rr  # noqa: E402


def _read_log(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


class ReviewRunnerKimiHeaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.log_path = self.tmp / "review.log"
        self.state_path = self.tmp / "state.json"
        self.log_path.write_text("[test] JOB_START\n", encoding="utf-8")
        self.state = {
            "job_id": "test_job",
            "log_path": str(self.log_path),
            "state_path": str(self.state_path),
        }
        rr.write_state(self.state_path, self.state)

    def test_current_default_kimi_header_counts_as_verdict_marker(self) -> None:
        header = f"# {mmc.KIMI_DEFAULT_MODEL} review -- APPROVE"
        cmd = [
            "bash",
            "-lc",
            f"echo {json.dumps(header)}; "
            'echo "No findings."; '
            "exit 0",
        ]
        rc = rr.run_one_reviewer(
            "kimi",
            cmd,
            "test_job",
            self.log_path,
            self.state_path,
            self.state,
            deadline_s=10,
            heartbeat_s=1,
            reconnect_stall_s=0,
        )
        self.assertEqual(rc, 0)
        self.assertNotIn("QUALITY_GATE_FAIL", _read_log(self.log_path))

    def test_legacy_kimi_header_still_counts_as_verdict_marker(self) -> None:
        cmd = [
            "bash",
            "-lc",
            'echo "# kimi-for-coding review -- APPROVE"; '
            'echo "No findings."; '
            "exit 0",
        ]
        rc = rr.run_one_reviewer(
            "kimi",
            cmd,
            "test_job",
            self.log_path,
            self.state_path,
            self.state,
            deadline_s=10,
            heartbeat_s=1,
            reconnect_stall_s=0,
        )
        self.assertEqual(rc, 0)
        self.assertNotIn("QUALITY_GATE_FAIL", _read_log(self.log_path))

    def test_build_kimi_cmd_prefers_canonical_script(self) -> None:
        scripts_dir = self.tmp / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "kimi-review.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        (scripts_dir / "minimax-review.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        original_root = rr.REPO_ROOT
        self.addCleanup(lambda: setattr(rr, "REPO_ROOT", original_root))
        rr.REPO_ROOT = self.tmp

        cmd = rr.build_kimi_cmd("diff", ["a.py"], None, "")
        self.assertEqual(Path(cmd[0]), scripts_dir / "kimi-review.sh")

    def test_build_kimi_cmd_passes_positive_max_tokens_override(self) -> None:
        scripts_dir = self.tmp / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "kimi-review.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        original_root = rr.REPO_ROOT
        original_max_tokens = os.environ.get("K2B_LLM_MAX_TOKENS")
        self.addCleanup(lambda: setattr(rr, "REPO_ROOT", original_root))
        if original_max_tokens is None:
            self.addCleanup(lambda: os.environ.pop("K2B_LLM_MAX_TOKENS", None))
        else:
            self.addCleanup(lambda: os.environ.__setitem__("K2B_LLM_MAX_TOKENS", original_max_tokens))
        rr.REPO_ROOT = self.tmp

        os.environ["K2B_LLM_MAX_TOKENS"] = "32768"
        cmd = rr.build_kimi_cmd("diff", ["a.py"], None, "")
        self.assertEqual(cmd[-2:], ["--max-tokens", "32768"])

        os.environ["K2B_LLM_MAX_TOKENS"] = "32k"
        cmd = rr.build_kimi_cmd("diff", ["a.py"], None, "")
        self.assertNotIn("--max-tokens", cmd)

    def test_build_kimi_cmd_does_not_fall_back_to_historical_alias(self) -> None:
        scripts_dir = self.tmp / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "minimax-review.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        original_root = rr.REPO_ROOT
        self.addCleanup(lambda: setattr(rr, "REPO_ROOT", original_root))
        rr.REPO_ROOT = self.tmp

        cmd = rr.build_kimi_cmd("diff", ["a.py"], None, "")
        self.assertIsNone(cmd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
