import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
import minimax_review as review


def test_diff_only_keeps_changed_lines_and_untracked_content(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    source = tmp_path / "code.py"
    source.write_text("unchanged = 1\n" * 40 + "value = 1\n")
    git("add", "code.py")
    git("commit", "-qm", "fixture")
    source.write_text("unchanged = 1\n" * 40 + "value = 2\n")
    (tmp_path / "new.py").write_text("new_value = 9\n")
    monkeypatch.setenv("K2B_REVIEW_DIFF_ONLY", "1")
    context, paths = review.gather_diff_scoped_context(["code.py", "new.py"], repo_root=tmp_path)
    assert "+value = 2" in context
    assert "new_value = 9" in context
    # Three context lines and one Git hunk-heading context, not the full body.
    assert context.count("unchanged = 1") == 4
    assert paths == ["code.py", "new.py"]


def test_review_redacts_deleted_mcp_secret_without_hiding_placeholder():
    content = '- "OBSIDIAN_API_KEY": "fixture-exposed-value"\n+ "OBSIDIAN_API_KEY": "${OBSIDIAN_API_KEY}"'
    prompt = review.build_prompt("test", "", content, "{}")
    assert "fixture-exposed-value" not in prompt
    assert "${OBSIDIAN_API_KEY}" in prompt
    assert "[REDACTED]" in prompt


def test_review_redacts_shell_yaml_toml_cli_and_literal_tokens():
    exposed = [
        "shell-secret-value",
        "yaml-secret-value",
        "toml-secret-value",
        "cli-secret-value",
        "space containing secret",
        "header-secret",
        "api-header-secret",
        "aws-secret-value",
        "hyphen-json-secret",
        "hyphen-yaml-secret",
        r'prefix\"suffix-secret',
        r'cli-prefix\"cli-suffix-secret',
        "compact-python-secret",
        "github_pat_abcdefghijklmnopqrstuvwxyz1234567890",
    ]
    content = "\n".join(
        [
            "- export KIMI_API_KEY='shell-secret-value'",
            "- GITHUB_TOKEN: yaml-secret-value",
            '- CLIENT_SECRET = "toml-secret-value"',
            "- command --token=cli-secret-value --mode safe",
            '- tool --password "space containing secret" --mode safe',
            '- curl -H "Authorization: Bearer header-secret" https://example.test',
            '- curl -H "x-api-key: api-header-secret" https://example.test',
            "- AWS_SECRET_ACCESS_KEY=aws-secret-value",
            '- {"api-key": "hyphen-json-secret"}',
            "- api-key: hyphen-yaml-secret",
            r'- {"api_key":"prefix\"suffix-secret"}',
            r'- tool --password "cli-prefix\"cli-suffix-secret" --safe',
            "- {'api_key':'compact-python-secret'}",
            f"- leaked {exposed[-1]}",
        ]
    )
    redacted = review._redact_review_secrets(content)
    for secret in exposed:
        assert secret not in redacted
    assert redacted.count("[REDACTED]") == len(exposed)
    assert "{'api_key':'[REDACTED]'}" in redacted
    assert 'tool --password "[REDACTED]" --safe' in redacted


def test_review_keeps_non_secret_token_settings_and_placeholders():
    content = "\n".join(
        [
            "MAX_TOKENS=16384",
            "TOKENIZERS_PARALLELISM=false",
            '"OBSIDIAN_API_KEY": "${OBSIDIAN_API_KEY}"',
            "KIMI_API_KEY=$KIMI_API_KEY",
        ]
    )
    assert review._redact_review_secrets(content) == content


def test_review_redacts_credentials_in_numbered_full_file_body():
    content = """    1  harmless = true
    2  export KIMI_API_KEY='numbered-shell-secret'
   38  OBSIDIAN_API_KEY: numbered-yaml-secret
"""
    redacted = review._redact_review_secrets(content)
    assert "numbered-shell-secret" not in redacted
    assert "numbered-yaml-secret" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_diff_only_reviews_deleted_body_after_prompt_redaction(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    retired = tmp_path / "retired.txt"
    retired.write_text("OBSIDIAN_API_KEY=deleted-secret-value\n")
    subprocess.run(["git", "add", "retired.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    retired.unlink()
    monkeypatch.setenv("K2B_REVIEW_DIFF_ONLY", "1")
    context, _ = review.gather_diff_scoped_context(["retired.txt"], repo_root=tmp_path)
    assert "+++ /dev/null" in context
    assert "deleted-secret-value" in context
    prompt = review.build_prompt("test", "", context, "{}")
    assert "deleted-secret-value" not in prompt
    assert "[REDACTED]" in prompt


def test_diff_scope_reads_staged_index_and_rejects_unstaged_overlay(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    source = tmp_path / "code.py"
    source.write_text("value = 'base'\n")
    git("add", "code.py")
    git("commit", "-qm", "fixture")

    source.write_text("value = 'staged'\n")
    git("add", "code.py")
    context, _ = review.gather_diff_scoped_context(["code.py"], repo_root=tmp_path)
    assert "value = 'staged'" in context

    source.write_text("value = 'unstaged-overlay'\n")
    with pytest.raises(ValueError, match="unstaged or untracked overlay"):
        review.gather_diff_scoped_context(["code.py"], repo_root=tmp_path)


def test_diff_chunks_are_gap_free_and_exhaustive():
    original = "".join(f"line-{index}\n" for index in range(1, 102))
    bodies = []
    for index in range(1, 5):
        chunk = review._select_diff_chunk(original, f"{index}/4")
        bodies.append(chunk.split("# K2B_REVIEW_DIFF_PAYLOAD_START\n", 1)[1])
    assert "".join(bodies) == original


def test_diff_chunk_rejects_invalid_partition():
    with pytest.raises(ValueError, match="I/N"):
        review._select_diff_chunk("line\n", "first/three")
    with pytest.raises(ValueError, match="exceed"):
        review._select_diff_chunk("line\n", "3/2")
    with pytest.raises(ValueError, match="line count"):
        review._select_diff_chunk("line\n", "1/2")
    with pytest.raises(ValueError, match="empty diff"):
        review._select_diff_chunk("", "1/1")


def test_diff_chunk_repeats_active_file_and_hunk_attribution():
    original = "".join(
        [
            "diff --git a/code.py b/code.py\n",
            "index 111..222 100644\n",
            "--- a/code.py\n",
            "+++ b/code.py\n",
            "@@ -10,3 +10,3 @@ def function():\n",
            " context\n",
            "-old\n",
            "+new\n",
        ]
    )
    chunk = review._select_diff_chunk(original, "3/3")
    context, payload = chunk.split("# K2B_REVIEW_DIFF_PAYLOAD_START\n", 1)
    assert "diff --git a/code.py b/code.py" in context
    assert "--- a/code.py" in context
    assert "+++ b/code.py" in context
    assert "@@ -10,3 +10,3 @@ def function():" in context
    assert payload == " context\n-old\n+new\n"


def test_diff_chunk_does_not_prepend_stale_context_at_structural_boundary():
    original = "".join(
        [
            "diff --git a/one.py b/one.py\n",
            "--- a/one.py\n",
            "+++ b/one.py\n",
            "@@ -1 +1 @@\n",
            "-old\n",
            "+new\n",
            "diff --git a/two.py b/two.py\n",
            "--- a/two.py\n",
            "+++ b/two.py\n",
        ]
    )
    chunk = review._select_diff_chunk(original, "3/3")
    context, payload = chunk.split("# K2B_REVIEW_DIFF_PAYLOAD_START\n", 1)
    assert "Attribution context" not in context
    assert payload.startswith("diff --git a/two.py b/two.py\n")


def test_official_review_rejects_partial_diff_chunk(monkeypatch):
    env = dict(os.environ)
    env["K2B_REVIEW_DIFF_CHUNK"] = "1/2"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(review.__file__)),
            "--scope",
            "diff",
            "--files",
            "AGENTS.md",
            "--builder-family",
            "openai",
            "--no-fallback",
        ],
        cwd=Path(review.__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "not allowed for an official" in result.stderr
