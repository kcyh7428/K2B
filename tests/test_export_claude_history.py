from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export-claude-history.py"


def _run(
    *args: str | Path,
    check: bool = True,
    add_redact: bool = True,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)
    env.pop("K2B_BOT_TOKEN", None)
    command_args = list(map(str, args))
    if add_redact and "--freeze" in command_args and "--redact" not in command_args:
        command_args.insert(command_args.index("--freeze") + 1, "--redact")
    return subprocess.run(
        [sys.executable, str(SCRIPT), *command_args],
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def _write_session(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )


def test_script_exists():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"


def test_freeze_requires_source_dir(tmp_path):
    result = _run("--freeze", "--source", tmp_path / "missing", "--destination", tmp_path / "out", check=False)
    assert result.returncode != 0
    assert "source" in result.stderr.lower() or "directory" in result.stderr.lower()


def test_freeze_rejects_unredacted_request(tmp_path):
    src = tmp_path / "sessions"
    _write_session(
        src / "Projects-K2B" / "session.jsonl",
        [{"type": "session_meta", "payload": {"cwd": "/Users/keith/Projects/K2B"}}],
    )
    out = tmp_path / "out"

    result = _run(
        "--freeze",
        "--source",
        src,
        "--destination",
        out,
        check=False,
        add_redact=False,
    )

    assert result.returncode == 2
    assert "requires --redact" in result.stderr
    assert not out.exists()


def test_freeze_emits_manifest_and_redacted_artifacts(tmp_path):
    src = tmp_path / "sessions"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {
            "type": "response_item",
            "payload": {
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "save this"}],
                }
            },
        },
    ]
    _write_session(src / "2026" / "07" / "29" / "session-s1.jsonl", events)
    out = tmp_path / "out"

    result = _run(
        "--freeze",
        "--redact",
        "--source", src,
        "--destination", out,
        "--projects", "K2B",
        check=True,
    )

    assert result.returncode == 0
    manifest_path = out / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest.get("schemaVersion") == 1
    assert manifest.get("frozen") is True
    assert isinstance(manifest.get("sourceSessionCount"), int)
    assert manifest["sourceSessionCount"] == 1
    assert len(manifest.get("artifacts", [])) == 1
    artifact_rel = manifest["artifacts"][0]["relativePath"]
    assert artifact_rel.startswith("sessions/")
    artifact_path = out / artifact_rel
    assert artifact_path.exists()
    assert manifest["artifacts"][0]["sha256"] == hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()


def test_freeze_redacts_secrets(tmp_path):
    src = tmp_path / "sessions"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2Bi"}},
        {
            "type": "response_item",
            "payload": {
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "legacy key sk-abc123xyz and modern key "
                                "sk-proj-synthetic-value-1234567890 plus "
                                "lowercase bearer synthetic-token-value\n"
                                "OPENAI_API_KEY_sk-proj-env-name-1234567890=synthetic\n"
                                "https://user:password@example.com/private"
                            ),
                        }
                    ],
                }
            },
        },
        {
            "type": "response_item",
            "payload": {
                "item": {
                    "type": "function_call",
                    "name": "call_api",
                    "arguments": {"api_key": "super-secret-key-123", "url": "https://example.com"},
                }
            },
        },
    ]
    _write_session(src / "2026" / "07" / "29" / "session-s2.jsonl", events)
    out = tmp_path / "out"

    _run(
        "--freeze",
        "--redact",
        "--source", src,
        "--destination", out,
        check=True,
    )

    artifact = next((out / "sessions").rglob("*.jsonl"))
    text = artifact.read_text(encoding="utf-8")
    assert "sk-abc123xyz" not in text
    assert "sk-proj-synthetic-value-1234567890" not in text
    assert "bearer synthetic-token-value" not in text
    assert "sk-proj-env-name-1234567890" not in text
    assert "super-secret-key-123" not in text
    assert "user:password" not in text
    assert "https://[REDACTED]@example.com/private" in text
    assert "[REDACTED" in text
    assert "https://example.com" in text


def test_freeze_redacts_semantic_short_credentials(tmp_path):
    src = tmp_path / "sessions"
    secrets = (
        "synthetic-obsidian-short",
        "hunter2-synthetic",
        "sessionid=synthetic-short",
        "synthetic-cli-short",
        "synthetic-x-api-short",
        "synthetic-json-short",
        "synthetic-client-short",
    )
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {
            "type": "user",
            "content": "\n".join(
                (
                    f"OBSIDIAN_API_KEY: {secrets[0]}",
                    f"password: {secrets[1]}",
                    f"Cookie: {secrets[2]}",
                    f"run-tool --api-key {secrets[3]}",
                    f"X-API-Key: {secrets[4]}",
                    f'{{"api_key":"{secrets[5]}"}}',
                    f"run-tool --client-secret {secrets[6]}",
                )
            ),
        },
    ]
    _write_session(src / "2026" / "09" / "10" / "session-short-secrets.jsonl", events)
    out = tmp_path / "out"

    _run("--freeze", "--redact", "--source", src, "--destination", out)

    artifact_text = next((out / "sessions").rglob("*.jsonl")).read_text(
        encoding="utf-8"
    )
    for secret in secrets:
        assert secret not in artifact_text
    assert artifact_text.count("[REDACTED]") >= len(secrets)


def test_credential_boundary_redacts_slack_tokens_in_all_supported_contexts(tmp_path):
    """A removed Slack matcher would expose at least one full token below."""
    src = tmp_path / "sessions"
    free_start = "xoxb-123456789012-1234567890123-abcdEFGHijkl"
    punctuated = "xoxp-abcdef1234-ABCDEF5678"
    embedded = "xoxa-2-abcdef123456"
    assignment = "xoxr-abcdef1234-567890ABCD"
    sensitive_value = "xoxo-abcdef1234-1234567890"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {
            "type": "response_item",
            "payload": {
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"{free_start} ({punctuated}) "
                                f"label_{embedded}\nSLACK_TOKEN={assignment}"
                            ),
                        }
                    ],
                }
            },
        },
        {
            "type": "response_item",
            "payload": {
                "item": {
                    "type": "function_call",
                    "name": "send_message",
                    "arguments": {"token": sensitive_value},
                }
            },
        },
    ]
    _write_session(src / "2026" / "07" / "29" / "session-slack.jsonl", events)
    out = tmp_path / "out"

    _run("--freeze", "--redact", "--source", src, "--destination", out)

    artifact_text = next((out / "sessions").rglob("*.jsonl")).read_text(
        encoding="utf-8"
    )
    for secret in (
        free_start,
        punctuated,
        embedded,
        assignment,
        sensitive_value,
    ):
        assert secret not in artifact_text
    assert artifact_text.count("[REDACTED]") >= 5


def test_credential_boundary_redacts_google_api_keys_in_all_supported_contexts(tmp_path):
    """A removed Google matcher would expose at least one exact AIza key below."""
    src = tmp_path / "sessions"
    free_start = "AIzaSyD-abcdefghijklmnopqrstuvwxyz12345"
    punctuated = "AIzaTEST_abcdefghijklmnopqrstuvwxyz1234"
    embedded = "AIza0123456789abcdefghijklmnopqrstuvwxy"
    assignment = "AIzaSYNTH_abcdefghijklmnopqrstuvw1234"
    sensitive_value = "AIzaVALUE_abcdefghijklmnopqrstuvwx123"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {
            "type": "response_item",
            "payload": {
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"{free_start} ({punctuated}) "
                                f"label_{embedded}\nGOOGLE_API_KEY={assignment}"
                            ),
                        }
                    ],
                }
            },
        },
        {
            "type": "response_item",
            "payload": {
                "item": {
                    "type": "function_call",
                    "name": "call_google",
                    "arguments": {"api_key": sensitive_value},
                }
            },
        },
    ]
    _write_session(src / "2026" / "07" / "29" / "session-google.jsonl", events)
    out = tmp_path / "out"

    _run("--freeze", "--redact", "--source", src, "--destination", out)

    artifact_text = next((out / "sessions").rglob("*.jsonl")).read_text(
        encoding="utf-8"
    )
    for secret in (
        free_start,
        punctuated,
        embedded,
        assignment,
        sensitive_value,
    ):
        assert secret not in artifact_text
    assert artifact_text.count("[REDACTED]") >= 5


def test_credential_boundary_preserves_slack_and_google_near_misses(tmp_path):
    """Prefix collisions, wrong case, and malformed lengths are ordinary text."""
    src = tmp_path / "sessions"
    near_misses = (
        "XOXB-abcdef1234-567890ABCD",
        "xoxz-abcdef1234-567890ABCD",
        "xoxb-short",
        "axoxb-abcdef1234-567890ABCD",
        "aizaTEST_abcdefghijklmnopqrstuvwxyz1234",
        "aAIzaTEST_abcdefghijklmnopqrstuvwxyz1234",
        "AIzaTEST_abcdefghijklmnopqrstuvwxyz123",
        "AIzaTEST_abcdefghijklmnopqrstuvwxyz12345",
    )
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {
            "type": "response_item",
            "payload": {
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "\n".join(near_misses)}
                    ],
                }
            },
        },
    ]
    _write_session(src / "2026" / "07" / "29" / "session-near-miss.jsonl", events)
    out = tmp_path / "out"

    _run("--freeze", "--redact", "--source", src, "--destination", out)

    artifact_text = next((out / "sessions").rglob("*.jsonl")).read_text(
        encoding="utf-8"
    )
    for value in near_misses:
        assert value in artifact_text


def test_freeze_is_idempotent(tmp_path):
    src = tmp_path / "sessions"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {"type": "response_item", "payload": {"item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}}},
    ]
    _write_session(src / "2026" / "07" / "29" / "session-s3.jsonl", events)
    out = tmp_path / "out"

    _run("--freeze", "--source", src, "--destination", out, check=True)
    first_manifest = (out / "manifest.json").read_text(encoding="utf-8")
    first_artifacts = sorted(p.read_bytes() for p in out.rglob("*.jsonl"))

    _run("--freeze", "--source", src, "--destination", out, check=True)
    second_manifest = (out / "manifest.json").read_text(encoding="utf-8")
    second_artifacts = sorted(p.read_bytes() for p in out.rglob("*.jsonl"))

    assert first_manifest == second_manifest
    assert first_artifacts == second_artifacts


def test_freeze_rejects_changed_manifest(tmp_path):
    src = tmp_path / "sessions"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {"type": "response_item", "payload": {"item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}}},
    ]
    _write_session(src / "2026" / "07" / "29" / "session-s4.jsonl", events)
    out = tmp_path / "out"

    _run("--freeze", "--source", src, "--destination", out, check=True)
    artifact = next(out.rglob("*.jsonl"))
    before_bytes = artifact.read_bytes()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["sourceSessionCount"] = 999
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = _run("--freeze", "--source", src, "--destination", out, check=False)
    assert result.returncode != 0
    assert "manifest" in result.stderr.lower()
    assert artifact.read_bytes() == before_bytes, "artifact mutated before refusal"


def test_freeze_identical_rerun_does_not_mutate_artifacts(tmp_path):
    """Regression: an identical rerun must return success without rewriting artifacts."""
    src = tmp_path / "sessions"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {"type": "response_item", "payload": {"item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}}},
    ]
    _write_session(src / "2026" / "07" / "29" / "session-s4a.jsonl", events)
    out = tmp_path / "out"

    _run("--freeze", "--source", src, "--destination", out, check=True)
    artifact = next(out.rglob("*.jsonl"))
    before_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    before_mtime = artifact.stat().st_mtime_ns

    result = _run("--freeze", "--source", src, "--destination", out, check=True)
    assert result.returncode == 0
    after_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    after_mtime = artifact.stat().st_mtime_ns
    assert before_hash == after_hash
    assert before_mtime == after_mtime, "artifact was rewritten on identical rerun"


def test_freeze_changed_source_with_changed_manifest_leaves_archive_intact(tmp_path):
    """Regression: a changed source + changed manifest must refuse without mutating the archive."""
    src = tmp_path / "sessions"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {"type": "response_item", "payload": {"item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}}},
    ]
    _write_session(src / "2026" / "07" / "29" / "session-s4b.jsonl", events)
    out = tmp_path / "out"

    _run("--freeze", "--source", src, "--destination", out, check=True)
    artifact = next(out.rglob("*.jsonl"))
    before_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()

    # Mutate both the source and the manifest.
    changed_events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {"type": "response_item", "payload": {"item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "changed"}]}}},
    ]
    _write_session(src / "2026" / "07" / "29" / "session-s4b.jsonl", changed_events)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["sourceSessionCount"] = 999
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = _run("--freeze", "--source", src, "--destination", out, check=False)
    assert result.returncode != 0
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == before_hash, "archive mutated before refusal"


def test_freeze_rejects_malformed_manifest(tmp_path):
    src = tmp_path / "sessions"
    _write_session(src / "2026" / "07" / "29" / "session-s5.jsonl", [{"type": "user", "content": "hi"}])
    out = tmp_path / "out"
    out.mkdir(parents=True)
    (out / "manifest.json").write_text("{not json", encoding="utf-8")

    result = _run("--freeze", "--source", src, "--destination", out, check=False)
    assert result.returncode != 0


def test_verify_manifest_reports_mismatch(tmp_path):
    src = tmp_path / "sessions"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {"type": "response_item", "payload": {"item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}}},
    ]
    _write_session(src / "2026" / "07" / "29" / "session-s6.jsonl", events)
    out = tmp_path / "out"

    _run("--freeze", "--source", src, "--destination", out, check=True)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"][0]["sha256"] = "0" * 64
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = _run("--verify-manifest", out / "manifest.json", check=False)
    assert result.returncode != 0
    assert "hash" in result.stderr.lower() or "mismatch" in result.stderr.lower()


def test_verify_manifest_succeeds(tmp_path):
    src = tmp_path / "sessions"
    _write_session(src / "2026" / "07" / "29" / "session-s7.jsonl", [{"type": "user", "content": "hi"}])
    out = tmp_path / "out"

    _run("--freeze", "--source", src, "--destination", out, check=True)
    result = _run("--verify-manifest", out / "manifest.json", check=True)

    assert result.returncode == 0
    assert "verified" in result.stdout.lower()


def test_freeze_sorts_artifacts(tmp_path):
    src = tmp_path / "sessions"
    for name in ("session-z.jsonl", "session-a.jsonl", "session-m.jsonl"):
        _write_session(
            src / "2026" / "07" / "29" / name,
            [{"type": "user", "content": name}],
        )
    out = tmp_path / "out"

    _run("--freeze", "--source", src, "--destination", out, check=True)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    paths = [a["relativePath"] for a in manifest["artifacts"]]
    assert paths == sorted(paths)


def test_freeze_filters_to_projects(tmp_path):
    src = tmp_path / "sessions"
    _write_session(src / "k2b" / "session.jsonl", [{"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}}])
    _write_session(src / "other" / "session.jsonl", [{"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/Other"}}])
    out = tmp_path / "out"

    result = _run(
        "--freeze",
        "--source", src,
        "--destination", out,
        "--projects", "K2B",
        check=True,
    )

    assert result.returncode == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sourceSessionCount"] == 1


def test_freeze_skips_non_jsonl(tmp_path):
    src = tmp_path / "sessions"
    _write_session(src / "session.jsonl", [{"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}}])
    (src / "notes.txt").write_text("not jsonl", encoding="utf-8")
    out = tmp_path / "out"

    result = _run("--freeze", "--source", src, "--destination", out, check=True)

    assert result.returncode == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sourceSessionCount"] == 1


def test_preview_does_not_write_and_includes_schema_v1_manifest(tmp_path):
    src = tmp_path / "sessions"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {
            "type": "response_item",
            "payload": {
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "my api key is sk-abc123xyz"}],
                }
            },
        },
    ]
    _write_session(src / "2026" / "07" / "29" / "session-preview.jsonl", events)
    out = tmp_path / "out"

    result = _run(
        "--preview",
        "--redact",
        "--source", src,
        "--destination", out,
        check=True,
    )

    assert result.returncode == 0
    assert "preview" in result.stdout.lower()
    assert not out.exists() or not any(out.iterdir())
    manifest_text = result.stdout.split("\nexport-claude-history:")[0]
    manifest = json.loads(manifest_text)
    assert manifest.get("schemaVersion") == 1
    assert manifest.get("frozen") is True
    assert manifest.get("sourceSessionCount") == 1
    assert len(manifest.get("artifacts", [])) == 1
    assert "sk-abc123xyz" not in result.stdout


def test_freeze_rejects_raw_source_change_hidden_by_redaction(tmp_path):
    """Raw source bytes, not only redacted artifact bytes, freeze the archive."""
    src = tmp_path / "sessions"
    session = src / "2026" / "07" / "29" / "session-raw-source.jsonl"
    events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
        {"type": "user", "content": "hello", "api_key": "first-secret-value"},
    ]
    _write_session(session, events)
    out = tmp_path / "out"

    _run("--freeze", "--redact", "--source", src, "--destination", out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"][0]["sourceSha256"] == hashlib.sha256(
        session.read_bytes()
    ).hexdigest()
    before = {
        path.relative_to(out): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in out.rglob("*")
        if path.is_file()
    }

    events[-1]["api_key"] = "second-secret-value"
    _write_session(session, events)
    result = _run(
        "--freeze", "--redact", "--source", src, "--destination", out, check=False
    )

    assert result.returncode != 0
    assert {
        path.relative_to(out): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in out.rglob("*")
        if path.is_file()
    } == before


def test_artifact_hash_and_content_use_one_source_snapshot(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("export_claude_history", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    src = tmp_path / "sessions"
    session = src / "Projects-K2B" / "session-race.jsonl"
    before_events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keith/Projects/K2B"}},
        {"type": "user", "content": "before snapshot"},
    ]
    after_events = [
        {"type": "session_meta", "payload": {"cwd": "/Users/keith/Projects/K2B"}},
        {"type": "user", "content": "after mutation"},
    ]
    _write_session(session, before_events)
    before_bytes = session.read_bytes()
    original_read_bytes = Path.read_bytes
    read_count = 0

    def mutate_after_read(path: Path) -> bytes:
        nonlocal read_count
        raw = original_read_bytes(path)
        if path == session:
            read_count += 1
            _write_session(session, after_events)
        return raw

    monkeypatch.setattr(Path, "read_bytes", mutate_after_read)
    _rel, text, _artifact_hash, source_hash = module._artifact_text(
        session, src, redact=True
    )

    assert read_count == 1
    assert source_hash == hashlib.sha256(before_bytes).hexdigest()
    assert "before snapshot" in text
    assert "after mutation" not in text


def test_freeze_fails_closed_on_malformed_candidate_session(tmp_path):
    src = tmp_path / "sessions"
    malformed = src / "Projects-K2B" / "session-malformed.jsonl"
    malformed.parent.mkdir(parents=True)
    malformed.write_text(
        '{"type":"session_meta","payload":{"cwd":"/Users/keith/Projects/K2B"}}\n'
        "{not-json}\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"

    result = _run("--freeze", "--source", src, "--destination", out, check=False)

    assert result.returncode == 2
    assert "malformed JSONL" in result.stderr
    assert not (out / "manifest.json").exists()


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_identical_freeze_revalidates_existing_artifact_integrity(tmp_path, damage):
    src = tmp_path / "sessions"
    _write_session(
        src / "2026" / "07" / "29" / "session-integrity.jsonl",
        [
            {"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}},
            {"type": "user", "content": "immutable source"},
        ],
    )
    out = tmp_path / "out"
    _run("--freeze", "--source", src, "--destination", out)
    artifact = next((out / "sessions").rglob("*.jsonl"))
    if damage == "missing":
        artifact.unlink()
    else:
        artifact.write_text("corrupt\n", encoding="utf-8")
    before = {
        path.relative_to(out): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in out.rglob("*")
        if path.is_file()
    }

    result = _run("--freeze", "--source", src, "--destination", out, check=False)

    assert result.returncode != 0
    assert "artifact" in result.stderr.lower() or "hash" in result.stderr.lower()
    assert {
        path.relative_to(out): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in out.rglob("*")
        if path.is_file()
    } == before


def test_project_scope_requires_exact_k2b_or_k2bi_name(tmp_path):
    src = tmp_path / "sessions"
    for project in ("K2B", "K2Bi", "K2B-Archive", "K2Bi-old"):
        _write_session(
            src / f"Projects-{project}" / "session.jsonl",
            [
                {
                    "type": "session_meta",
                    "payload": {"cwd": f"/Users/keithmbpm2/Projects/{project}"},
                }
            ],
        )
    out = tmp_path / "out"

    _run(
        "--freeze",
        "--source",
        src,
        "--destination",
        out,
        "--projects",
        "K2B,K2Bi",
    )

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sourceSessionCount"] == 2
    assert all(
        "K2B-Archive" not in item["relativePath"]
        and "K2Bi-old" not in item["relativePath"]
        for item in manifest["artifacts"]
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
