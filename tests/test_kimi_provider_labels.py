#!/usr/bin/env python3
"""Guard user-facing Kimi review/provider labels during the migration."""

from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REVIEW_SH = ROOT / "scripts" / "review.sh"


def _json_from_stdout(stdout: str) -> dict:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end < 0:
        raise AssertionError(f"no JSON object found in stdout: {stdout!r}")
    return json.loads(stdout[start:end + 1])


class KimiProviderLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdirs: list[tempfile.TemporaryDirectory[str]] = []

    def tearDown(self) -> None:
        for td in self._tmpdirs:
            td.cleanup()

    def _tmpdir(self) -> Path:
        td = tempfile.TemporaryDirectory()
        self._tmpdirs.append(td)
        return Path(td.name)

    def _seed_review_repo(self) -> tuple[Path, Path]:
        repo = self._tmpdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "K2B Test"],
            cwd=repo,
            check=True,
        )
        (repo / ".gitignore").write_text("/.code-reviews/\n/fake-bin/\n")
        scripts_dir = repo / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "kimi-review.sh").write_text(
            "#!/usr/bin/env bash\n"
            "echo \"# kimi-k2.7-code review -- APPROVE\"\n"
            "echo '{\"verdict\":\"approve\"}'\n"
            "exit 0\n",
            encoding="utf-8",
        )
        (scripts_dir / "kimi-review.sh").chmod(0o755)
        (scripts_dir / "minimax-review.sh").write_text(
            "#!/usr/bin/env bash\n"
            "echo \"legacy minimax alias should not be selected when kimi exists\" >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        (scripts_dir / "minimax-review.sh").chmod(0o755)
        (repo / "target.py").write_text("print('clean')\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
        (repo / "target.py").write_text("print('dirty')\n", encoding="utf-8")

        plugin = repo / "fake-bin" / "codex"
        plugin.parent.mkdir(parents=True)
        plugin.write_text(
            "#!/usr/bin/env bash\n"
            "if [ \"${1:-}\" = exec ]; then cat >/dev/null; fi\n"
            "printf '# Codex Review\\nAPPROVE\\n[codex] Review output captured.\\n'\n",
            encoding="utf-8",
        )
        plugin.chmod(0o755)
        return repo, plugin

    def _review_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["KIMI_API_KEY"] = "fake-kimi-key-for-review-runner-tests"
        env.pop("K2B_LLM_PROVIDER", None)
        return env

    def _run_review(self, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(REVIEW_SH), *args],
            cwd=repo,
            env=self._review_env(),
            capture_output=True,
            text=True,
        )

    def _state_from_result(self, repo: Path, result: subprocess.CompletedProcess[str]) -> dict:
        data = _json_from_stdout(result.stdout)
        log_path = repo / data["log_path"]
        state_path = log_path.with_suffix(".json")
        return json.loads(state_path.read_text(encoding="utf-8"))

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
        self.assertIn("--max-tokens", result.stdout)

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

    def test_minimax_common_allows_kimi_only_environment(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["KIMI_API_KEY"] = "fake-kimi-key-for-common-sh"
            env["K2B_ENV_FILE"] = str(Path(home) / ".missing-k2b-env")
            env.pop("MINIMAX_API_KEY", None)
            env.pop("K2B_LLM_PROVIDER", None)
            result = subprocess.run(
                [
                    "bash",
                    "-lc",
                    (
                        f"source {str(ROOT / 'scripts' / 'minimax-common.sh')!r} "
                        '&& printf "%s\\n" "$K2B_LLM_MODEL"'
                    ),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("kimi-k2.7-code", result.stdout)

    def test_minimax_common_uses_kimi_code_membership_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["KIMI_API_KEY"] = "fake-kimi-key-for-common-sh"
            env.pop("KIMI_API_HOST", None)
            env.pop("MINIMAX_API_KEY", None)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    (
                        "source scripts/minimax-common.sh >/dev/null 2>&1 && "
                        'printf "%s\\n" "$KIMI_API_HOST"'
                    ),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "https://api.kimi.com/coding")

    def test_minimax_common_rejects_payg_china_platform_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            env["KIMI_API_KEY"] = "fake-kimi-key-for-common-sh"
            env["KIMI_API_HOST"] = "https://api.moonshot.cn"
            env.pop("K2B_ALLOW_METERED_KIMI_PLATFORM", None)
            result = subprocess.run(
                ["bash", "-c", "source scripts/minimax-common.sh"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pay-as-you-go Kimi Open Platform", result.stderr)

    def test_minimax_common_normalizes_metered_host_and_rejects_http(self) -> None:
        cases = [
            ("https://API.MOONSHOT.CN:443/v1", "pay-as-you-go"),
            ("https://api.moonshot.cn./v1", "pay-as-you-go"),
            ("http://api.kimi.com/coding", "must use HTTPS"),
        ]
        for host, expected in cases:
            with self.subTest(host=host), tempfile.TemporaryDirectory() as home:
                env = os.environ.copy()
                env["HOME"] = home
                env["KIMI_API_KEY"] = "fake-kimi-key-for-common-sh"
                env["KIMI_API_HOST"] = host
                env.pop("K2B_ALLOW_METERED_KIMI_PLATFORM", None)
                result = subprocess.run(
                    ["bash", "-c", "source scripts/minimax-common.sh"],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)

    def test_minimax_common_does_not_source_zshrc_after_k2b_env_resolves_key(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            (home_path / ".k2b-env").write_text(
                "export KIMI_API_KEY=fake-kimi-key-for-common-sh\n",
                encoding="utf-8",
            )
            (home_path / ".k2b-env").chmod(0o600)
            (home_path / ".zshrc").write_text(
                "export ZSHRC_WAS_SOURCED=yes\n", encoding="utf-8"
            )
            env = os.environ.copy()
            env["HOME"] = home
            env.pop("KIMI_API_KEY", None)
            env.pop("MINIMAX_API_KEY", None)
            env.pop("K2B_ENV_FILE", None)
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    (
                        "source scripts/minimax-common.sh >/dev/null 2>&1 && "
                        'printf "%s\\n%s\\n" "$K2B_LLM_MODEL" "${ZSHRC_WAS_SOURCED:-no}"'
                    ),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip().splitlines(), ["kimi-k2.7-code", "no"])

    def test_minimax_common_rejects_insecure_dedicated_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env_file = Path(home) / ".k2b-env"
            env_file.write_text(
                "export KIMI_API_KEY=fake-kimi-key-for-common-sh\n",
                encoding="utf-8",
            )
            env_file.chmod(0o644)
            env = os.environ.copy()
            env["HOME"] = home
            env.pop("KIMI_API_KEY", None)
            env.pop("K2B_ENV_FILE", None)
            result = subprocess.run(
                ["bash", "-c", "source scripts/minimax-common.sh"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("owned by the current user and mode 0600", result.stderr)

    def test_python_kimi_loader_reads_private_dedicated_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            home_path = Path(home)
            env_file = home_path / ".k2b-env"
            env_file.write_text(
                "export KIMI_API_KEY=fake-kimi-key-for-python-loader\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            env = os.environ.copy()
            env["HOME"] = home
            env["K2B_ENV_FILE"] = str(env_file)
            env.pop("KIMI_API_KEY", None)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from scripts.lib.minimax_common import load_kimi_api_key; "
                        "print(len(load_kimi_api_key()))"
                    ),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(len("fake-kimi-key-for-python-loader")))

    def test_stage1_has_no_local_model_preflight(self) -> None:
        self.assertFalse(
            (ROOT / "scripts" / "washing-machine" / "preflight.sh").exists()
        )

    def test_direct_kimi_reviewer_rejects_minimax_provider_env(self) -> None:
        env = os.environ.copy()
        env["K2B_LLM_PROVIDER"] = "minimax"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "lib" / "minimax_review.py"),
                "--scope",
                "files",
                "--files",
                "AGENTS.md",
                "--json",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("K2B_LLM_PROVIDER=minimax is deprecated and disabled", result.stderr)

    def test_review_sh_accepts_primary_kimi_and_records_kimi_reviewer(self) -> None:
        repo, plugin = self._seed_review_repo()
        result = self._run_review(
            repo,
            "diff",
            "--files",
            "target.py",
            "--wait",
            "--codex-executable",
            str(plugin),
            "--primary",
            "kimi",
            "--no-fallback",
            "--deadline",
            "10",
            "--heartbeat-interval",
            "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        state = self._state_from_result(repo, result)
        self.assertEqual(state["primary_requested"], "kimi")
        self.assertEqual(state["primary_used"], "kimi")
        self.assertEqual(
            [attempt["reviewer"] for attempt in state["reviewer_attempts"]],
            ["kimi"],
        )

    def test_review_sh_accepts_minimax_only_as_deprecated_alias_to_kimi(self) -> None:
        repo, plugin = self._seed_review_repo()
        result = self._run_review(
            repo,
            "diff",
            "--files",
            "target.py",
            "--wait",
            "--codex-executable",
            str(plugin),
            "--primary",
            "minimax",
            "--no-fallback",
            "--deadline",
            "10",
            "--heartbeat-interval",
            "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("deprecated alias", result.stderr.lower())
        self.assertIn("--primary kimi", result.stderr)
        state = self._state_from_result(repo, result)
        self.assertEqual(state["primary_requested"], "kimi")
        self.assertEqual(state["primary_alias_requested"], "minimax")
        self.assertEqual(
            [attempt["reviewer"] for attempt in state["reviewer_attempts"]],
            ["kimi"],
        )
        log_path = repo / _json_from_stdout(result.stdout)["log_path"]
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        self.assertIn("scripts/kimi-review.sh", log_text)
        self.assertNotIn("scripts/minimax-review.sh", log_text)

    def test_openai_builder_requires_canonical_kimi_no_fallback(self) -> None:
        repo, plugin = self._seed_review_repo()
        result = self._run_review(
            repo,
            "diff",
            "--files",
            "target.py",
            "--wait",
            "--codex-executable",
            str(plugin),
            "--primary",
            "codex",
            "--builder-family",
            "openai",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "builder-family openai requires --primary kimi --no-fallback",
            result.stderr,
        )

    def test_openai_builder_accepts_canonical_kimi_no_fallback(self) -> None:
        repo, plugin = self._seed_review_repo()
        result = self._run_review(
            repo,
            "diff",
            "--files",
            "target.py",
            "--wait",
            "--codex-executable",
            str(plugin),
            "--primary",
            "kimi",
            "--no-fallback",
            "--builder-family",
            "openai",
            "--deadline",
            "10",
            "--heartbeat-interval",
            "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        state = self._state_from_result(repo, result)
        self.assertEqual(state["builder_family"], "openai")
        self.assertEqual(
            [attempt["reviewer"] for attempt in state["reviewer_attempts"]],
            ["kimi"],
        )

    def test_kimi_builder_requires_codex_without_kimi_fallback(self) -> None:
        repo, plugin = self._seed_review_repo()
        result = self._run_review(
            repo,
            "diff",
            "--files",
            "target.py",
            "--wait",
            "--codex-executable",
            str(plugin),
            "--primary",
            "kimi",
            "--no-fallback",
            "--builder-family",
            "kimi",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(
            "builder-family kimi requires --primary codex --no-fallback",
            result.stderr,
        )

    def test_review_sh_help_presents_kimi_not_minimax_as_active_reviewer(self) -> None:
        result = subprocess.run(
            ["bash", str(REVIEW_SH), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("codex|kimi", result.stdout)
        self.assertNotIn("codex|minimax", result.stdout)
        self.assertNotIn("Codex + MiniMax fallback", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
