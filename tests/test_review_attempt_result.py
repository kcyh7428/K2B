#!/usr/bin/env python3
"""Attempt-bound normalized Kimi review result coverage."""

from __future__ import annotations

import json
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from scripts.lib import minimax_common as mmc  # noqa: E402
from scripts.lib import minimax_review as mr  # noqa: E402
from scripts.lib import review_result as rres  # noqa: E402
from scripts.lib import review_runner as rr  # noqa: E402


APPROVE_REVIEW = {
    "verdict": "approve",
    "summary": "No findings.",
    "findings": [],
    "next_steps": [],
}

ATTENTION_REVIEW = {
    "verdict": "needs-attention",
    "summary": "One issue.",
    "findings": [],
    "next_steps": ["fix it"],
}


def _scope_for(cmd: list[str]) -> str:
    return rr._kimi_scope_sha256(cmd)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self._env_backup = dict(os.environ)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def _write_raw(self, path: Path, payload: object) -> None:
        snapshot = path.parent / "review_input.txt"
        snapshot.write_text("synthetic reviewed input")
        snapshot.chmod(0o600)
        path.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(path, 0o600)

    def _good_payload(self, attempt_id: str, scope: str):
        return {
            "protocol_version": 1,
            "attempt_id": attempt_id,
            "scope_sha256": scope,
            "finish_reason": "end_turn",
            "model": "kimi-k2.7-code",
            "content_sha256": hashlib.sha256(b"synthetic reviewed input").hexdigest(),
            "review": APPROVE_REVIEW,
        }


class ReadAttemptResultTests(_Base):
    def test_changed_input_invalidates_result(self):
        target = self.tmp / "review_result.json"
        self._write_raw(target, self._good_payload("a1", "s1"))
        (self.tmp / "review_input.txt").write_text("changed reviewed bytes")
        with self.assertRaises(ValueError):
            rres.read_attempt_result(target, attempt_id="a1", scope_sha256="s1")

    def test_different_expected_content_digest_invalidates_result(self):
        target = self.tmp / "review_result.json"
        self._write_raw(target, self._good_payload("a1", "s1"))
        with self.assertRaises(ValueError):
            rres.read_attempt_result(target, attempt_id="a1", scope_sha256="s1",
                                     expected_content_sha256="0" * 64)

    def test_approve_and_needs_attention(self) -> None:
        for review, expected in (
            (APPROVE_REVIEW, "approve"),
            (ATTENTION_REVIEW, "needs-attention"),
        ):
            with self.subTest(expected=expected):
                d = self.tmp / expected
                d.mkdir()
                os.chmod(d, 0o700)
                p = d / "review_result.json"
                payload = self._good_payload("a1", "s1")
                payload["review"] = review
                self._write_raw(p, payload)
                got = rres.read_attempt_result(
                    p, attempt_id="a1", scope_sha256="s1"
                )
                self.assertEqual(got, expected)

    def test_missing_result(self) -> None:
        p = self.tmp / "missing.json"
        with self.assertRaises(ValueError):
            rres.read_attempt_result(p, attempt_id="a1", scope_sha256="s1")

    def test_stale_attempt_mismatch(self) -> None:
        p = self.tmp / "review_result.json"
        self._write_raw(p, self._good_payload("old", "s1"))
        with self.assertRaises(ValueError):
            rres.read_attempt_result(p, attempt_id="new", scope_sha256="s1")

    def test_scope_mismatch(self) -> None:
        p = self.tmp / "review_result.json"
        self._write_raw(p, self._good_payload("a1", "other-scope"))
        with self.assertRaises(ValueError):
            rres.read_attempt_result(p, attempt_id="a1", scope_sha256="s1")

    def test_malformed_json(self) -> None:
        p = self.tmp / "review_result.json"
        p.write_text("{not json", encoding="utf-8")
        os.chmod(p, 0o600)
        with self.assertRaises(ValueError):
            rres.read_attempt_result(p, attempt_id="a1", scope_sha256="s1")

    def test_truncated_json(self) -> None:
        p = self.tmp / "review_result.json"
        body = json.dumps(self._good_payload("a1", "s1"))
        p.write_text(body[: len(body) // 2], encoding="utf-8")
        os.chmod(p, 0o600)
        with self.assertRaises(ValueError):
            rres.read_attempt_result(p, attempt_id="a1", scope_sha256="s1")

    def test_bad_schema_review(self) -> None:
        p = self.tmp / "review_result.json"
        payload = self._good_payload("a1", "s1")
        payload["review"] = {"verdict": "approve"}
        self._write_raw(p, payload)
        with self.assertRaises(ValueError):
            rres.read_attempt_result(p, attempt_id="a1", scope_sha256="s1")

    def test_bad_finish_reason(self) -> None:
        p = self.tmp / "review_result.json"
        payload = self._good_payload("a1", "s1")
        payload["finish_reason"] = "max_tokens"
        self._write_raw(p, payload)
        with self.assertRaises(ValueError):
            rres.read_attempt_result(p, attempt_id="a1", scope_sha256="s1")

    def test_bad_protocol_version(self) -> None:
        p = self.tmp / "review_result.json"
        payload = self._good_payload("a1", "s1")
        payload["protocol_version"] = 2
        self._write_raw(p, payload)
        with self.assertRaises(ValueError):
            rres.read_attempt_result(p, attempt_id="a1", scope_sha256="s1")

    def test_symlink_rejected(self) -> None:
        real = self.tmp / "real.json"
        self._write_raw(real, self._good_payload("a1", "s1"))
        link = self.tmp / "link.json"
        link.symlink_to(real)
        with self.assertRaises(ValueError):
            rres.read_attempt_result(link, attempt_id="a1", scope_sha256="s1")

    def test_oversized_rejected(self) -> None:
        p = self.tmp / "review_result.json"
        p.write_text("x" * (rres.MAX_RESULT_BYTES + 10), encoding="utf-8")
        os.chmod(p, 0o600)
        with self.assertRaises(ValueError):
            rres.read_attempt_result(p, attempt_id="a1", scope_sha256="s1")


class WriteAttemptResultTests(_Base):
    def test_missing_stop_is_strict_only_when_requested(self):
        for strict, expected in ((False, "stop"), (True, None)):
            response = mock.MagicMock()
            with mock.patch.object(mmc, "load_kimi_api_key", return_value="synthetic-key"), \
                 mock.patch.object(mmc.urllib.request, "urlopen", return_value=response) as network, \
                 mock.patch.object(mmc, "_read_kimi_stream", return_value={"content": [], "usage": {}}):
                result = mmc._kimi_chat_completion(
                    [{"role": "user", "content": "synthetic"}], max_tokens=32,
                    temperature=0.2, timeout=1, strict_finish_reason=strict,
                )
            self.assertEqual(result["choices"][0]["finish_reason"], expected)
            payload = json.loads(network.call_args.args[0].data)
            self.assertNotIn("strict_finish_reason", payload)

    def test_no_binding_is_direct_wrapper(self) -> None:
        for k in (rres.RESULT_PATH_ENV, rres.ATTEMPT_ID_ENV,
                  rres.SCOPE_SHA256_ENV):
            os.environ.pop(k, None)
        self.assertIsNone(
            rres.write_attempt_result(
                APPROVE_REVIEW, finish_reason="end_turn", model="m"
            )
        )

    def test_partial_binding_is_error(self) -> None:
        os.environ[rres.RESULT_PATH_ENV] = str(self.tmp / "r.json")
        os.environ.pop(rres.ATTEMPT_ID_ENV, None)
        os.environ.pop(rres.SCOPE_SHA256_ENV, None)
        with self.assertRaises(ValueError):
            rres.write_attempt_result(
                APPROVE_REVIEW, finish_reason="end_turn", model="m"
            )

    def test_writes_mode0600_and_refuses_overwrite(self) -> None:
        target = self.tmp / "review_result.json"
        os.environ[rres.RESULT_PATH_ENV] = str(target)
        os.environ[rres.ATTEMPT_ID_ENV] = "a1"
        os.environ[rres.SCOPE_SHA256_ENV] = "s1"
        out = rres.write_attempt_result(
            APPROVE_REVIEW, finish_reason="stop", model="m",
            content_sha256=rres.write_review_input("synthetic reviewed input"),
        )
        self.assertEqual(out, target)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        with self.assertRaises(ValueError):
            rres.write_attempt_result(
                APPROVE_REVIEW, finish_reason="stop", model="m"
            )

    def test_refuses_symlink_target(self) -> None:
        real = self.tmp / "elsewhere.json"
        real.write_text("{}")
        target = self.tmp / "review_result.json"
        target.symlink_to(real)
        os.environ[rres.RESULT_PATH_ENV] = str(target)
        os.environ[rres.ATTEMPT_ID_ENV] = "a1"
        os.environ[rres.SCOPE_SHA256_ENV] = "s1"
        with self.assertRaises(ValueError):
            rres.write_attempt_result(
                APPROVE_REVIEW, finish_reason="end_turn", model="m"
            )

    def test_bad_finish_reason_not_written(self) -> None:
        target = self.tmp / "review_result.json"
        os.environ[rres.RESULT_PATH_ENV] = str(target)
        os.environ[rres.ATTEMPT_ID_ENV] = "a1"
        os.environ[rres.SCOPE_SHA256_ENV] = "s1"
        with self.assertRaises(ValueError):
            rres.write_attempt_result(
                APPROVE_REVIEW, finish_reason="max_tokens", model="m"
            )
        self.assertFalse(target.exists())


class _RunnerCase(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.log_path = self.tmp / "review.log"
        self.state_path = self.tmp / "state.json"
        self.log_path.write_text("[test] JOB_START\n", encoding="utf-8")
        self.state = {
            "job_id": "test_job",
            "log_path": str(self.log_path),
            "state_path": str(self.state_path),
        }
        rr.write_state(self.state_path, self.state)

    def _run(self, cmd: list[str], reviewer: str = "kimi") -> int:
        return rr.run_one_reviewer(
            reviewer,
            cmd,
            "test_job",
            self.log_path,
            self.state_path,
            self.state,
            deadline_s=20,
            heartbeat_s=1,
            reconnect_stall_s=0,
        )

    def _dir_created(self) -> Path:
        dirs = [p for p in self.tmp.iterdir()
                if p.is_dir() and p.name.startswith("k2b-review-attempt-")]
        self.assertEqual(len(dirs), 1)
        return dirs[0]

    def _fabricate_valid_result(self) -> str:
        d = self._dir_created()
        p = d / "review_result.json"
        attempt_id = self.state.get("reviewer_attempt_id")
        self.assertIsInstance(attempt_id, str)
        # Scope binding cannot be recomputed here; write via env triple from
        # the attempt directory to emulate the real child.
        os.environ[rres.RESULT_PATH_ENV] = str(p)
        os.environ[rres.ATTEMPT_ID_ENV] = attempt_id
        # Scope is logged at spawn for the real child; read result via the
        # attempt state after spawn through fresh run.
        return str(p)


class RunnerVerdictTests(_RunnerCase):
    def test_kimi_fast_nonzero_exit_is_not_a_timeout_with_short_deadline(self):
        rc = rr.run_one_reviewer(
            "kimi", [sys.executable, "-c", "raise SystemExit(1)"],
            "test", self.log_path, self.state_path, self.state,
            deadline_s=1, heartbeat_s=1,
        )
        self.assertEqual(rc, 1)
        self.assertNotIn("killed_by_deadline_attempt", self.state)

    def test_kimi_deadline_signal_records_exact_attempt(self):
        with mock.patch.object(rr, "KILL_GRACE_S", 0):
            rc = rr.run_one_reviewer(
                "kimi", [sys.executable, "-c", "import time; time.sleep(10)"],
                "test", self.log_path, self.state_path, self.state,
                deadline_s=1, heartbeat_s=0.05,
            )
        self.assertEqual(rc, 124)
        self.assertEqual(self.state["killed_by_deadline_attempt"], self.state["reviewer_attempt_id"])

    def test_deadline_flag_exists_before_long_grace_sleep_finishes(self):
        with mock.patch.object(rr, "KILL_GRACE_S", 6):
            rc = rr.run_one_reviewer(
                "kimi", [sys.executable, "-c", "import time; time.sleep(20)"],
                "test", self.log_path, self.state_path, self.state,
                deadline_s=1, heartbeat_s=0.05,
            )
        self.assertEqual(rc, 124)
        self.assertEqual(self.state["killed_by_deadline_attempt"], self.state["reviewer_attempt_id"])

    def test_published_result_before_failed_child_exit_cannot_approve(self):
        code = (
            "import json, os; from pathlib import Path; "
            "r = {'protocol_version': 1, 'attempt_id': os.environ['K2B_REVIEW_ATTEMPT_ID'], "
            "'scope_sha256': os.environ['K2B_REVIEW_SCOPE_SHA256'], 'model': 'synthetic', "
            "'finish_reason': 'end_turn', 'review': " + repr(APPROVE_REVIEW) + "}; "
            "p = Path(os.environ['K2B_REVIEW_RESULT_PATH']); "
            "s = p.with_name('review_input.txt'); s.write_text('synthetic reviewed input'); s.chmod(0o600); "
            "r['content_sha256'] = __import__('hashlib').sha256(s.read_bytes()).hexdigest(); "
            "p.write_text(json.dumps(r)); p.chmod(0o600); raise SystemExit(9)"
        )
        self.assertEqual(self._run([sys.executable, "-c", code]), 9)
        self.assertNotIn("review_verdict", self.state)
        self.assertNotIn("review_result_path", self.state)
        self.assertTrue((Path(self.state["review_attempt_dir"]) / "review_result.json").is_file())

    def test_raw_exit_four_without_validated_result_is_failure(self):
        args = SimpleNamespace(
            primary="kimi", no_fallback=True, files=None, scope="files", plan=None,
            focus="", deadline=20, heartbeat_interval=1,
            reconnect_stall_threshold_s=0, codex_executable="missing",
        )
        with mock.patch.object(rr, "build_kimi_cmd", return_value=["fixture"]), \
             mock.patch.object(rr, "run_one_reviewer", return_value=4):
            result = rr.run_fallback_chain(args, "test", self.log_path, self.state_path, self.state)
        self.assertEqual(result, 2)
        self.assertEqual(self.state["status"], "primary_failed")

    def test_echo_markers_and_header_cannot_approve(self) -> None:
        cmd = ["bash", "-lc",
               "echo '# kimi-k2.7-code review -- APPROVE'; "
               "echo 'K2B_REVIEW_VERDICT: APPROVE'; "
               "echo '---'; exit 0"]
        rc = self._run(cmd)
        self.assertEqual(rc, 125)
        self.assertNotIn("review_verdict", self.state)
        self.assertNotIn("review_result_path", self.state)

    def test_missing_result_rc125(self) -> None:
        rc = self._run(["bash", "-lc", "exit 0"])
        self.assertEqual(rc, 125)
        self.assertNotIn("review_verdict", self.state)

    def test_result_from_previous_attempt_cannot_approve(self) -> None:
        # First fake attempt writes a valid file bound to attempt X.
        d0 = self.tmp / "k2b-review-attempt-prev"
        d0.mkdir()
        os.chmod(d0, 0o700)
        prev = d0 / "review_result.json"
        self._write_raw(prev, self._good_payload("old-attempt", "old-scope"))
        # Second attempt reuses but fixture leaves stale file behind.
        rc = self._run(["bash", "-lc", "exit 0"])
        self.assertEqual(rc, 125)
        self.assertNotIn("review_verdict", self.state)

    def test_interleaved_watchdog_log_cannot_affect_valid_result(self) -> None:
        code = (
            "import json, os; from pathlib import Path; "
            "r = {'protocol_version': 1, 'attempt_id': os.environ['K2B_REVIEW_ATTEMPT_ID'], "
            "'scope_sha256': os.environ['K2B_REVIEW_SCOPE_SHA256'], 'model': 'synthetic', "
            "'finish_reason': 'end_turn', 'review': " + repr(ATTENTION_REVIEW) + "}; "
            "p = Path(os.environ['K2B_REVIEW_RESULT_PATH']); "
            "s = p.with_name('review_input.txt'); s.write_text('synthetic reviewed input'); s.chmod(0o600); "
            "r['content_sha256'] = __import__('hashlib').sha256(s.read_bytes()).hexdigest(); "
            "p.write_text(json.dumps(r)); p.chmod(0o600); "
            "print('---'); print('[2026-01-01T00:00:00Z] HEARTBEAT pid=1'); "
            "print('K2B_REVIEW_VERDICT: approve'); print('trailing output')"
        )
        self.assertEqual(self._run([sys.executable, "-c", code]), 4)
        self.assertEqual(self.state["review_verdict"], "needs-attention")


class RealWrapperIntegrationTests(_RunnerCase):
    """Drive the real scripts/kimi-review.sh through run_one_reviewer.

    Only the provider network boundary is replaced: a python3 shim on PATH
    delegates normal invocations to sys.executable, but for the exact
    minimax_review.py invocation it loads the real module and replaces
    chat_completion with a synthetic response, then calls main().
    """

    def setUp(self) -> None:
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.shim_env = self.tmp / "shim.env"
        self.stale_result = self.tmp / "stale-result.json"
        self.stale_result.write_text("sentinel")
        shim = self.bin / "python3"
        repo_lib = str(REPO_ROOT / "scripts" / "lib")
        shim.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f"TARGET_ARG={str(REPO_ROOT / 'scripts' / 'lib' / 'minimax_review.py')!r}\n"
            "for a in \"$@\"; do\n"
            "  if [[ \"$a\" == */lib/minimax_review.py ]]; then\n"
            f"    export K2B_SYNTH_DIR={str(self.tmp)!r}\n"
            f"    exec {sys.executable!r} - \"$@\" <<'PY'\n"
            "import os, sys\n"
            "sys.path.insert(0, %r)\n" % repo_lib
            + "import minimax_review as mr\n"
            "resp = {\n"
            "    'stop_reason': 'end_turn',\n"
            "    'model': 'kimi-k2.7-code',\n"
            "    'content': [{'type': 'text', "
            "'text': __import__('json').dumps({\n"
            "        'verdict': os.environ.get('K2B_SYNTH_VERDICT', 'approve'),\n"
            "        'summary': 'No findings.',\n"
            "        'findings': [],\n"
            "        'next_steps': [],\n"
            "    })}],\n"
            "    'usage': {'input_tokens': 1, 'output_tokens': 1},\n"
            "}\n"
            "resp = {'model': resp['model'], 'choices': [{'finish_reason': os.environ.get('K2B_SYNTH_FINISH', 'end_turn'), 'message': {'content': resp['content'][0]['text']}}], 'usage': {}}\n"
            "mr.chat_completion = lambda **_: resp\n"
            "if os.environ.get('K2B_SYNTH_ARCHIVE_FAIL'): mr.archive = lambda *a, **k: (_ for _ in ()).throw(PermissionError('synthetic storage failure'))\n"
            "sys.argv = sys.argv[1:]\n"
            "rc = mr.main()\n"
            "if os.environ.get('K2B_SYNTH_NOISE'): print('---\\n[2026-01-01T00:00:00Z] HEARTBEAT\\nK2B_REVIEW_VERDICT: approve\\ntrailing stdout')\n"
            "sys.exit(rc)\n"
            "PY\n"
            "  fi\n"
            "done\n"
            f"exec {sys.executable!r} \"$@\"\n",
            encoding="utf-8",
        )
        os.chmod(shim, 0o755)

    def _wrapper_env(self) -> dict:
        return {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "KIMI_API_KEY": "synthetic-key-0123456789",
            "K2B_ENV_FILE": str(self.shim_env),
            "K2B_LLM_PROVIDER": "kimi",
            "K2B_REVIEW_RESULT_PATH": str(self.stale_result),
            "K2B_REVIEW_ATTEMPT_ID": "stale",
            "K2B_REVIEW_SCOPE_SHA256": "stale",
        }

    def test_real_wrapper_approve(self) -> None:
        self._invoke_wrapper()

    def test_real_wrapper_json_needs_attention_with_noise(self) -> None:
        self._invoke_wrapper(verdict="needs-attention", json_mode=True, noise=True, expected_rc=4)

    def test_real_review_sh_refuses_needs_attention_without_fallback(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "scripts").symlink_to(REPO_ROOT / "scripts", target_is_directory=True)
        (repo / "fixture.py").write_text("x = 1\n")
        env = os.environ.copy()
        env.update(self._wrapper_env(), K2B_SYNTH_VERDICT="needs-attention")
        result = subprocess.run(
            [str(REPO_ROOT / "scripts" / "review.sh"), "files", "--files", "fixture.py",
             "--primary", "kimi", "--deadline", "20", "--wait"],
            cwd=repo, env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 4, result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["review_verdict"], "needs-attention")
        state = json.loads((repo / data["log_path"]).with_suffix(".json").read_text())
        self.assertFalse(state["fallback_used"])
        self.assertEqual(len(state["reviewer_attempts"]), 1)

    def test_real_wrapper_truncated_response_cannot_write_result(self) -> None:
        self._invoke_wrapper(finish="max_tokens", expected_rc=3)

    def test_real_wrapper_archive_failure_cannot_publish_result(self):
        self._invoke_wrapper(archive_failure=True, expected_rc=3)

    def _invoke_wrapper(self, verdict="approve", json_mode=False, noise=False,
                        finish="end_turn", expected_rc=0, archive_failure=False) -> None:
        wrapper = REPO_ROOT / "scripts" / "kimi-review.sh"
        cmd = [str(wrapper), "--scope", "files", "--files", "README.md", "--archive-dir", str(self.tmp / "archives")]
        if json_mode:
            cmd.append("--json")
        env = self._wrapper_env()
        env.update(K2B_SYNTH_VERDICT=verdict, K2B_SYNTH_FINISH=finish,
                   K2B_SYNTH_NOISE="1" if noise else "",
                   K2B_SYNTH_ARCHIVE_FAIL="1" if archive_failure else "")
        rc = rr.run_one_reviewer(
            "kimi",
            cmd,
            "test_job",
            self.log_path,
            self.state_path,
            self.state,
            deadline_s=60,
            heartbeat_s=1,
            reconnect_stall_s=0,
            extra_env=env,
        )
        self.assertEqual(rc, expected_rc,
                         msg=self.log_path.read_text(encoding="utf-8"))
        self.assertEqual(self.stale_result.read_text(), "sentinel")
        if expected_rc not in (0, 4):
            self.assertNotIn("review_verdict", self.state)
            self.assertNotIn("review_result_path", self.state)
            return
        self.assertEqual(self.state.get("review_verdict"), verdict)
        saved = self.state.get("review_result_path")
        self.assertIsInstance(saved, str)
        self.assertTrue(Path(saved).is_file())
        attempt = Path(self.state["review_attempt_dir"])
        metadata = json.loads((attempt / "attempt.json").read_text())
        self.assertEqual(metadata["job_id"], "test_job")
        self.assertEqual(metadata["attempt_id"], self.state["reviewer_attempt_id"])
        digest = hashlib.sha256((attempt / "review_input.txt").read_bytes()).hexdigest()
        self.assertEqual(self.state["review_content_sha256"], digest)

if __name__ == "__main__":
    unittest.main(verbosity=2)
