from __future__ import annotations

import json
import os
import sys
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import eod_capture  # noqa: E402


def _write_minimal_vault(vault: Path, semantic_rows: list[str] | None = None) -> None:
    semantic_rows = semantic_rows or []
    shelves = vault / "wiki" / "context" / "shelves"
    shelves.mkdir(parents=True)
    (vault / "System" / "memory").mkdir(parents=True)
    (vault / "wiki").mkdir(parents=True, exist_ok=True)
    (vault / "review").mkdir(parents=True, exist_ok=True)
    (vault / "wiki" / "log.md").write_text("log header\n", encoding="utf-8")
    (vault / "System" / "memory" / "self_improve_learnings.md").write_text(
        "# Learnings\n", encoding="utf-8"
    )
    rows_text = "\n".join(semantic_rows)
    if rows_text:
        rows_text += "\n"
    (shelves / "semantic.md").write_text(
        "---\n"
        "tags: [context, shelf, semantic, washing-machine]\n"
        "type: shelf\n"
        "shelf: semantic\n"
        f"row-count: {len(semantic_rows)}\n"
        "---\n\n"
        "# Semantic shelf\n\n"
        "## Rows\n\n"
        f"{rows_text}",
        encoding="utf-8",
    )


def test_safe_session_id_keeps_rollout_id_but_adds_path_hash(tmp_path):
    a = tmp_path / "a" / "rollout-abc123.jsonl"
    b = tmp_path / "b" / "rollout-abc123.jsonl"
    a.parent.mkdir()
    b.parent.mkdir()

    ida = eod_capture._safe_session_id(a)
    idb = eod_capture._safe_session_id(b)

    assert ida.startswith("abc123_")
    assert idb.startswith("abc123_")
    assert ida != idb


def test_strip_codex_session_keeps_user_text_and_truncates_tool_output(tmp_path):
    session = tmp_path / "rollout.jsonl"
    long_output = "x" * 700
    session.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"},
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "item": {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": "my doctor's phone is 2830 3709",
                                    }
                                ],
                            }
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "item": {
                                "type": "function_call_output",
                                "call_id": "call_1",
                                "output": long_output,
                            }
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    stripped = eod_capture.strip_transcript(session)

    assert "my doctor's phone is 2830 3709" in stripped
    assert "[truncated, 700 chars]" in stripped
    assert long_output not in stripped


def test_strip_codex_session_handles_payload_as_message_item(tmp_path):
    session = tmp_path / "rollout-real-shape.jsonl"
    session.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "developer",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "developer instructions are not memory",
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "# AGENTS.md instructions for /Users/keithmbpm2/Projects/K2B\n\n<INSTRUCTIONS>\nK2B -- Agent Onboarding Guide\n</INSTRUCTIONS>",
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "tell me your understand about the '/goal'",
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "K2B treats /goal as a build-time controller.",
                                }
                            ],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    stripped = eod_capture.strip_transcript(session)

    assert "tell me your understand about the '/goal'" in stripped
    assert "K2B treats /goal as a build-time controller." in stripped
    assert "developer instructions are not memory" not in stripped
    assert "K2B -- Agent Onboarding Guide" not in stripped


def test_strip_transcript_rejects_malformed_json_line(tmp_path):
    session = tmp_path / "broken.jsonl"
    session.write_text('{"type": "user", "content": "ok"}\n{not json\n', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON in transcript"):
        eod_capture.strip_transcript(session)


def test_strip_transcript_truncates_large_tool_call_arguments(tmp_path):
    session = tmp_path / "tool-call.jsonl"
    long_arg = "x" * 700
    session.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "item": {
                        "type": "function_call",
                        "name": "write_file",
                        "arguments": {"content": long_arg},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    stripped = eod_capture.strip_transcript(session)

    assert "[truncated," in stripped
    assert long_arg not in stripped


def test_job_a_stages_extraction_json_from_sandbox_transcript(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "message": {"content": "doctor phone 2830 3709"}})
        + "\n",
        encoding="utf-8",
    )

    def fake_extract(_payload: str, session_path: Path) -> dict:
        return {
            "schema_version": "1.0",
            "session_path": str(session_path),
            "source_app": "claude_code",
            "items": [],
        }

    written = eod_capture.run_job_a(
        [session], vault_path=vault, run_date="2026-05-14", extract_func=fake_extract
    )

    assert len(written) == 1
    assert written[0].parent == vault / ".staging" / "extractions"
    assert json.loads(written[0].read_text(encoding="utf-8"))["source_app"] == "claude_code"
    lock_files = list((vault / ".staging" / "extraction-locks").glob("*.lock"))
    assert lock_files == []


def test_job_a_writes_failure_marker_and_continues(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    good = tmp_path / "good.jsonl"
    bad = tmp_path / "bad.jsonl"
    good.write_text(json.dumps({"type": "user", "content": "ok"}) + "\n", encoding="utf-8")
    bad.write_text(json.dumps({"type": "user", "content": "boom"}) + "\n", encoding="utf-8")

    def flaky_extract(payload: str, _session_path: Path) -> dict:
        if "boom" in payload:
            raise RuntimeError("extractor unavailable")
        return {"items": []}

    written = eod_capture.run_job_a(
        [good, bad], vault_path=vault, run_date="2026-05-14", extract_func=flaky_extract
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    assert len(written) == 1
    assert len(failures) == 1
    failure = json.loads(failures[0].read_text(encoding="utf-8"))
    assert failure["session_path"] == str(bad)
    assert "extractor unavailable" in failure["error"]
    assert failure["transcript_sha256"] == hashlib.sha256(
        eod_capture.strip_transcript(bad).encode("utf-8")
    ).hexdigest()


def test_job_a_writes_skip_not_failure_for_empty_stripped_transcript(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "empty.jsonl"
    session.write_text("", encoding="utf-8")

    written = eod_capture.run_job_a(
        [session], vault_path=vault, run_date="2026-05-16"
    )

    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    assert written == []
    assert len(skips) == 1
    assert failures == []
    skip = json.loads(skips[0].read_text(encoding="utf-8"))
    assert skip["session_path"] == str(session)
    assert skip["reason"] == "empty_stripped_transcript"
    assert skip["error"] == "ValueError: empty stripped transcript"
    assert "transcript_sha256" not in skip


def test_job_a_main_returns_zero_when_only_empty_stripped_sessions_skip(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "empty.jsonl"
    session.write_text("", encoding="utf-8")

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-16",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    assert rc == 0
    assert sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    assert not sorted((vault / ".staging" / "extraction-failures").glob("*.json"))


def test_job_a_keeps_valid_items_and_rejects_bad_items_from_mixed_kimi_response(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    payload_text = (
        "alpha fact one is true. beta fact two is true. gamma fact three is true."
    )
    session.write_text(
        json.dumps({"type": "user", "content": payload_text}) + "\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)

    def fake_run(*_args, **_kwargs):
        return eod_capture.subprocess.CompletedProcess(
            args=["kimi"],
            returncode=0,
            stdout=json.dumps(
                {
                    "schema_version": "1.0",
                    "items": [
                        {
                            "kind": "fact",
                            "subject": "Alpha",
                            "predicate": "status",
                            "object": "true",
                            "confidence": "high",
                            "evidence_quote": "alpha fact one is true",
                            "dedupe_key": "fact:alpha:status",
                        },
                        {
                            "kind": "decision",
                            "subject": "Beta",
                            "predicate": "status",
                            "object": "true",
                            "confidence": "high",
                            "evidence_quote": "beta fact two is true",
                            "dedupe_key": "decision:beta:status",
                        },
                        {
                            "kind": "learning",
                            "subject": "Gamma",
                            "predicate": "status",
                            "object": "true",
                            "confidence": "high",
                            "evidence_quote": "gamma fact three is true",
                            "dedupe_key": "learning:gamma:status",
                        },
                        {
                            "kind": "fact",
                            "subject": "Bad quote",
                            "predicate": "status",
                            "object": "bad",
                            "confidence": "high",
                            "evidence_quote": "not present in payload",
                            "dedupe_key": "fact:bad-quote:status",
                        },
                    ],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    written = eod_capture.run_job_a([session], vault_path=vault, run_date="2026-05-14")

    rejection_files = sorted(
        (vault / ".staging" / "extraction-rejections").glob("*.json")
    )
    staged = json.loads(written[0].read_text(encoding="utf-8"))
    assert len(written) == 1
    assert [item["dedupe_key"] for item in staged["items"]] == [
        "fact:alpha:status",
        "decision:beta:status",
        "learning:gamma:status",
    ]
    assert len(rejection_files) == 1
    rejection = json.loads(rejection_files[0].read_text(encoding="utf-8"))
    assert rejection["item_index"] == 3
    assert rejection["item"]["dedupe_key"] == "fact:bad-quote:status"
    assert "evidence_quote" in rejection["error"]


def test_job_a_skips_existing_valid_extraction_on_rerun(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(json.dumps({"type": "user", "content": "ok"}) + "\n", encoding="utf-8")
    calls = 0

    def fake_extract(_payload: str, _session_path: Path) -> dict:
        nonlocal calls
        calls += 1
        return {
            "schema_version": "1.0",
            "items": [
                {
                    "kind": "fact",
                    "subject": "A",
                    "predicate": "status",
                    "object": "B",
                    "confidence": "high",
                    "dedupe_key": "fact:a:status",
                }
            ],
        }

    first = eod_capture.run_job_a(
        [session], vault_path=vault, run_date="2026-05-14", extract_func=fake_extract
    )
    second = eod_capture.run_job_a(
        [session], vault_path=vault, run_date="2026-05-14", extract_func=fake_extract
    )

    assert len(first) == 1
    assert second == []
    assert calls == 1


def test_job_a_reextracts_when_transcript_content_changes(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "old payload"}) + "\n",
        encoding="utf-8",
    )
    calls = 0

    def fake_extract(payload: str, _session_path: Path) -> dict:
        nonlocal calls
        calls += 1
        value = "new payload" if "new payload" in payload else "old payload"
        return {
            "schema_version": "1.0",
            "items": [
                {
                    "kind": "fact",
                    "subject": "Session payload",
                    "predicate": "value",
                    "object": value,
                    "confidence": "high",
                    "evidence_quote": value,
                    "dedupe_key": "fact:session-payload:value",
                }
            ],
        }

    first = eod_capture.run_job_a(
        [session], vault_path=vault, run_date="2026-05-14", extract_func=fake_extract
    )

    assert len(first) == 1
    first_data = json.loads(first[0].read_text(encoding="utf-8"))
    assert "old" in first_data["items"][0]["object"]

    session.write_text(
        json.dumps({"type": "user", "content": "new payload"}) + "\n",
        encoding="utf-8",
    )
    second = eod_capture.run_job_a(
        [session], vault_path=vault, run_date="2026-05-14", extract_func=fake_extract
    )

    assert len(second) == 1
    assert calls == 2
    data = json.loads(second[0].read_text(encoding="utf-8"))
    assert "new" in data["items"][0]["object"]


def test_job_a_reextracts_when_cached_schema_version_is_stale(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(json.dumps({"type": "user", "content": "ok"}) + "\n", encoding="utf-8")
    payload = eod_capture.strip_transcript(session)
    session_id = eod_capture._safe_session_id(session)
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / f"2026-05-14_{session_id}.json").write_text(
        json.dumps(
            {
                "schema_version": "0.9",
                "transcript_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    calls = 0

    def fake_extract(_payload: str, _session_path: Path) -> dict:
        nonlocal calls
        calls += 1
        return {"schema_version": "1.0", "items": []}

    written = eod_capture.run_job_a(
        [session], vault_path=vault, run_date="2026-05-14", extract_func=fake_extract
    )

    assert len(written) == 1
    assert calls == 1


def test_discover_session_paths_filters_to_k2b_and_run_date(tmp_path):
    claude_root = tmp_path / ".claude" / "projects"
    codex_root = tmp_path / ".codex" / "sessions"
    k2b_claude = claude_root / "-Users-keithmbpm2-Projects-K2B" / "a.jsonl"
    other_claude = claude_root / "-Users-keithmbpm2-Projects-Other" / "b.jsonl"
    k2b_codex = codex_root / "2026" / "05" / "14" / "rollout-k2b.jsonl"
    stale_codex_inside_day = codex_root / "2026" / "05" / "14" / "rollout-stale.jsonl"
    no_timestamp_stale_codex = codex_root / "2026" / "05" / "14" / "rollout-no-ts.jsonl"
    old_codex = codex_root / "2026" / "05" / "13" / "rollout-old.jsonl"
    for p in (
        k2b_claude,
        other_claude,
        k2b_codex,
        stale_codex_inside_day,
        no_timestamp_stale_codex,
        old_codex,
    ):
        p.parent.mkdir(parents=True, exist_ok=True)
    k2b_claude.write_text(
        json.dumps({"cwd": "/Users/keithmbpm2/Projects/K2B", "type": "user", "content": "x"})
        + "\n",
        encoding="utf-8",
    )
    other_claude.write_text(
        json.dumps({"cwd": "/Users/keithmbpm2/Projects/Other", "type": "user", "content": "x"})
        + "\n",
        encoding="utf-8",
    )
    k2b_codex.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    old_codex.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"}})
        + "\n",
        encoding="utf-8",
    )
    stale_codex_inside_day.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "timestamp": "2026-05-13T23:59:00Z",
                "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    no_timestamp_stale_codex.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"cwd": "/Users/keithmbpm2/Projects/K2B"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ts = datetime(2026, 5, 14, 10, tzinfo=timezone.utc).timestamp()
    old_ts = datetime(2026, 5, 13, 10, tzinfo=timezone.utc).timestamp()
    os.utime(k2b_claude, (ts, ts))
    os.utime(other_claude, (ts, ts))
    os.utime(k2b_codex, (ts, ts))
    os.utime(no_timestamp_stale_codex, (old_ts, old_ts))

    found = eod_capture.discover_session_paths(
        run_date="2026-05-14", claude_root=claude_root, codex_root=codex_root
    )

    assert found == [k2b_claude, k2b_codex]
    assert old_codex not in found
    assert stale_codex_inside_day not in found
    assert no_timestamp_stale_codex not in found


def test_discover_session_paths_prefers_content_timestamp_over_mtime(tmp_path):
    claude_root = tmp_path / ".claude" / "projects"
    codex_root = tmp_path / ".codex" / "sessions"
    session = claude_root / "-Users-keithmbpm2-Projects-K2B" / "old-content.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(
        json.dumps(
            {
                "cwd": "/Users/keithmbpm2/Projects/K2B",
                "timestamp": "2026-05-13T23:59:00Z",
                "type": "user",
                "content": "x",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    touched_today = datetime(2026, 5, 14, 10, tzinfo=timezone.utc).timestamp()
    os.utime(session, (touched_today, touched_today))

    found_today = eod_capture.discover_session_paths(
        run_date="2026-05-14", claude_root=claude_root, codex_root=codex_root
    )
    found_content_day = eod_capture.discover_session_paths(
        run_date="2026-05-13", claude_root=claude_root, codex_root=codex_root
    )

    assert session not in found_today
    assert found_content_day == [session]


def test_validate_extraction_allows_pipe_delimiter_in_semantic_values(tmp_path):
    session = tmp_path / "session.jsonl"
    data = eod_capture.validate_extraction(
        {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Dr. Lo | St. Paul's",
                    "predicate": "phone",
                    "object": "2830 | 3709",
                    "confidence": "high",
                    "evidence_quote": "Dr. Lo | St. Paul's phone is 2830 | 3709",
                    "dedupe_key": "person:dr-lo:phone",
                }
            ]
        },
        session,
    )

    assert data["items"][0]["object"] == "2830 | 3709"


def test_validate_extraction_rejects_pipe_delimiter_for_structural_fields(tmp_path):
    session = tmp_path / "session.jsonl"
    with pytest.raises(ValueError, match="pipe delimiter"):
        eod_capture.validate_extraction(
            {
                "items": [
                    {
                        "kind": "fact",
                        "subject": "Dr. Lo",
                        "predicate": "phone|status",
                        "object": "2830 3709",
                        "confidence": "high",
                        "dedupe_key": "person:dr-lo:phone",
                    }
                ]
            },
            session,
        )


def test_validate_extraction_rejects_control_whitespace_for_semantic_values(tmp_path):
    session = tmp_path / "session.jsonl"
    with pytest.raises(ValueError, match="control whitespace"):
        eod_capture.validate_extraction(
            {
                "items": [
                    {
                        "kind": "fact",
                        "subject": "Dr. Lo",
                        "predicate": "phone",
                        "object": "2830\n3709",
                        "confidence": "high",
                        "dedupe_key": "person:dr-lo:phone",
                    }
                ]
            },
            session,
        )


def test_validate_extraction_allows_whitespace_controls_in_evidence_quote(tmp_path):
    session = tmp_path / "session.jsonl"
    data = eod_capture.validate_extraction(
        {
            "items": [
                {
                    "kind": "fact",
                    "subject": "EOD capture",
                    "predicate": "status",
                    "object": "ready",
                    "confidence": "high",
                    "evidence_quote": "EOD\tcapture\nis\rready",
                    "dedupe_key": "fact:eod-capture:status",
                }
            ]
        },
        session,
    )

    assert data["items"][0]["evidence_quote"] == "EOD\tcapture\nis\rready"


def test_validate_extraction_rejects_non_whitespace_control_in_evidence_quote(tmp_path):
    session = tmp_path / "session.jsonl"
    with pytest.raises(ValueError, match="unsupported control character"):
        eod_capture.validate_extraction(
            {
                "items": [
                    {
                        "kind": "fact",
                        "subject": "EOD capture",
                        "predicate": "status",
                        "object": "ready",
                        "confidence": "high",
                        "evidence_quote": "EOD capture\x00is ready",
                        "dedupe_key": "fact:eod-capture:status",
                    }
                ]
            },
            session,
        )


def test_validate_extraction_requires_dedupe_key_for_fact_or_decision(tmp_path):
    session = tmp_path / "session.jsonl"
    with pytest.raises(ValueError, match="missing dedupe_key"):
        eod_capture.validate_extraction(
            {
                "items": [
                    {
                        "kind": "decision",
                        "subject": "EOD capture",
                        "predicate": "status",
                        "object": "ship now",
                        "confidence": "high",
                    }
                ]
            },
            session,
        )


def test_validate_extraction_rejects_unsupported_canonical_home(tmp_path):
    session = tmp_path / "session.jsonl"
    with pytest.raises(ValueError, match="unsupported canonical_home"):
        eod_capture.validate_extraction(
            {
                "items": [
                    {
                        "kind": "fact",
                        "subject": "Dr. Lo",
                        "predicate": "phone",
                        "object": "2830 3709",
                        "confidence": "high",
                        "dedupe_key": "person:dr-lo:phone",
                        "canonical_home": "../../../etc/passwd",
                    }
                ]
            },
            session,
        )


def test_validate_extraction_rejects_unsupported_speaker_source(tmp_path):
    session = tmp_path / "session.jsonl"
    with pytest.raises(ValueError, match="unsupported speaker_source"):
        eod_capture.validate_extraction(
            {
                "items": [
                    {
                        "kind": "fact",
                        "subject": "Dr. Lo",
                        "predicate": "phone",
                        "object": "2830 3709",
                        "confidence": "high",
                        "dedupe_key": "person:dr-lo:phone",
                        "speaker_source": "tool_output",
                    }
                ]
            },
            session,
        )


def test_validate_extraction_rejects_extra_item_fields(tmp_path):
    session = tmp_path / "session.jsonl"
    with pytest.raises(ValueError, match="unsupported field"):
        eod_capture.validate_extraction(
            {
                "items": [
                    {
                        "kind": "fact",
                        "subject": "Dr. Lo",
                        "predicate": "phone",
                        "object": "2830 3709",
                        "confidence": "high",
                        "dedupe_key": "person:dr-lo:phone",
                        "unexpected_path": "/tmp/outside",
                    }
                ]
            },
            session,
        )


def test_pipe_field_extractor_preserves_colons_in_values():
    line = "- 2026-05-14 | note | thing | evidence_quote:He said: do it now | dedupe_key:fact:thing:note"

    assert eod_capture._extract_pipe_field(line, "evidence_quote") == "He said: do it now"
    assert eod_capture._extract_pipe_field(line, "dedupe_key") == "fact:thing:note"


def test_pipe_field_extractor_preserves_escaped_pipes_in_values():
    line = "- 2026-05-14 | note | thing | evidence_quote:alpha\\|beta\\|gamma | dedupe_key:fact:thing:note"

    assert eod_capture._extract_pipe_field(line, "evidence_quote") == "alpha\\|beta\\|gamma"
    assert eod_capture._extract_pipe_field(line, "dedupe_key") == "fact:thing:note"


def test_extractor_prompt_requires_verbatim_quotes():
    prompt = eod_capture.PROMPT_PATH.read_text(encoding="utf-8")

    assert "character-for-character" in prompt
    assert "not paraphrase" in prompt


def test_same_value_uses_exact_trimmed_comparison():
    assert eod_capture._same_value("2830 3709 ", "2830 3709")
    assert not eod_capture._same_value("Dr. Lo", "dr. lo")
    assert not eod_capture._same_value("2830  3709", "2830 3709")


def test_call_kimi_extractor_retries_transient_timeout(tmp_path, monkeypatch):
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)
    monkeypatch.setattr(eod_capture.time, "sleep", lambda _seconds: None)
    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise eod_capture.subprocess.TimeoutExpired(cmd="kimi", timeout=180)
        return eod_capture.subprocess.CompletedProcess(
            args=["kimi"],
            returncode=0,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "kind": "fact",
                            "subject": "Dr. Lo",
                            "predicate": "phone",
                            "object": "2830 3709",
                            "confidence": "high",
                            "dedupe_key": "person:dr-lo:phone",
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    data = eod_capture.call_kimi_extractor("payload", tmp_path / "session.jsonl")

    assert calls == 2
    assert data["items"][0]["dedupe_key"] == "person:dr-lo:phone"


def test_call_kimi_extractor_retries_invalid_json_from_success_exit(tmp_path, monkeypatch):
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)
    monkeypatch.setattr(eod_capture.time, "sleep", lambda _seconds: None)
    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return eod_capture.subprocess.CompletedProcess(
                args=["kimi"], returncode=0, stdout="not json", stderr=""
            )
        return eod_capture.subprocess.CompletedProcess(
            args=["kimi"],
            returncode=0,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "kind": "fact",
                            "subject": "Dr. Lo",
                            "predicate": "phone",
                            "object": "2830 3709",
                            "confidence": "high",
                            "dedupe_key": "person:dr-lo:phone",
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    data = eod_capture.call_kimi_extractor("payload", tmp_path / "session.jsonl")

    assert calls == 2
    assert data["items"][0]["dedupe_key"] == "person:dr-lo:phone"


def test_call_kimi_extractor_fails_fast_on_permanent_model_error(tmp_path, monkeypatch):
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)
    monkeypatch.setattr(eod_capture.time, "sleep", lambda _seconds: None)
    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return eod_capture.subprocess.CompletedProcess(
            args=["kimi"],
            returncode=1,
            stdout="",
            stderr="model not found",
        )

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="model not found"):
        eod_capture.call_kimi_extractor("payload", tmp_path / "session.jsonl")

    assert calls == 1


def test_call_kimi_extractor_retries_rate_limit(tmp_path, monkeypatch):
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)
    monkeypatch.setattr(eod_capture.time, "sleep", lambda _seconds: None)
    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return eod_capture.subprocess.CompletedProcess(
                args=["kimi"], returncode=1, stdout="", stderr="429 rate limit"
            )
        return eod_capture.subprocess.CompletedProcess(
            args=["kimi"],
            returncode=0,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "kind": "fact",
                            "subject": "Dr. Lo",
                            "predicate": "phone",
                            "object": "2830 3709",
                            "confidence": "high",
                            "dedupe_key": "person:dr-lo:phone",
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    data = eod_capture.call_kimi_extractor("payload", tmp_path / "session.jsonl")

    assert calls == 2
    assert data["items"][0]["dedupe_key"] == "person:dr-lo:phone"


def test_run_extractor_process_does_not_read_oversized_stdout(tmp_path, monkeypatch):
    monkeypatch.setattr(eod_capture, "MAX_EXTRACTOR_STDOUT_BYTES", 10)

    proc = eod_capture._run_extractor_process(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 32)"],
        timeout=10,
    )

    assert proc.returncode == 0
    assert proc.stdout_size == 32
    assert proc.stdout == ""


def test_call_kimi_extractor_prioritizes_transient_marker_in_mixed_stderr(
    tmp_path, monkeypatch
):
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)
    monkeypatch.setattr(eod_capture.time, "sleep", lambda _seconds: None)
    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return eod_capture.subprocess.CompletedProcess(
                args=["kimi"],
                returncode=1,
                stdout="",
                stderr="429 rate limit; request id req_401_retry",
            )
        return eod_capture.subprocess.CompletedProcess(
            args=["kimi"],
            returncode=0,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "kind": "fact",
                            "subject": "Dr. Lo",
                            "predicate": "phone",
                            "object": "2830 3709",
                            "confidence": "high",
                            "dedupe_key": "person:dr-lo:phone",
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    data = eod_capture.call_kimi_extractor("payload", tmp_path / "session.jsonl")

    assert calls == 2
    assert data["items"][0]["dedupe_key"] == "person:dr-lo:phone"


def test_job_a_writes_failure_when_all_items_rejected_for_bad_evidence_quote(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "my doctor's phone is 2830 3709"})
        + "\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)

    def fake_run(*_args, **_kwargs):
        return eod_capture.subprocess.CompletedProcess(
            args=["kimi"],
            returncode=0,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "kind": "fact",
                            "subject": "Dr. Lo",
                            "predicate": "phone",
                            "object": "2830 3709",
                            "confidence": "high",
                            "evidence_quote": "not in transcript",
                            "dedupe_key": "person:dr-lo:phone",
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    written = eod_capture.run_job_a([session], vault_path=vault, run_date="2026-05-14")

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    rejections = sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))
    assert written == []
    assert len(failures) == 1
    assert len(rejections) == 1
    assert "all extractor items rejected" in failures[0].read_text(encoding="utf-8")
    assert "evidence_quote" in rejections[0].read_text(encoding="utf-8")


def test_call_kimi_extractor_accepts_evidence_quote_with_normalized_whitespace(
    tmp_path, monkeypatch
):
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)

    def fake_run(*_args, **_kwargs):
        return eod_capture.subprocess.CompletedProcess(
            args=["kimi"],
            returncode=0,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "kind": "fact",
                            "subject": "EOD capture",
                            "predicate": "status",
                            "object": "ready",
                            "confidence": "high",
                            "evidence_quote": "EOD\tcapture\nis\rready",
                            "dedupe_key": "fact:eod-capture:status",
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    data = eod_capture.call_kimi_extractor(
        "Keith said EOD capture is ready", tmp_path / "session.jsonl"
    )

    assert data["items"][0]["evidence_quote"] == "EOD\tcapture\nis\rready"


def test_verify_evidence_quotes_rejects_too_short_evidence_quote(tmp_path):
    with pytest.raises(ValueError, match="too short"):
        eod_capture.verify_evidence_quotes(
            {
                "items": [
                    {
                        "kind": "fact",
                        "subject": "Status",
                        "predicate": "answer",
                        "object": "ok",
                        "confidence": "high",
                        "evidence_quote": "ok",
                        "dedupe_key": "status:answer",
                    }
                ]
            },
            "ok",
            session_path=tmp_path / "session.jsonl",
        )


def test_reconcile_auto_writes_fact_and_skips_preference(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / "2026-05-14_s1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "claude_code",
                "items": [
                    {
                        "kind": "fact",
                        "subtype": "person_contact",
                        "subject": "Dr. Lo Hak Keung",
                        "predicate": "phone",
                        "object": "2830 3709",
                        "scope": "K2B",
                        "confidence": "high",
                        "evidence_quote": "my doctor's phone is 2830 3709",
                        "speaker_source": "keith",
                        "dedupe_key": "person:dr-lo-hak-keung:phone",
                        "canonical_home": "wiki/context/shelves/semantic.md",
                    },
                    {
                        "kind": "preference",
                        "subject": "writing style",
                        "predicate": "prefers",
                        "object": "plain English over jargon",
                        "confidence": "high",
                        "evidence_quote": "I prefer plain English over jargon",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
        encoding="utf-8"
    )
    learnings = (vault / "System" / "memory" / "self_improve_learnings.md").read_text(
        encoding="utf-8"
    )
    assert summary["auto_written"] == 1
    assert summary["preferences_seen"] == 1
    assert summary["skipped_preferences"] == 1
    assert "Dr. Lo Hak Keung" in semantic
    assert "phone:2830 3709" in semantic
    assert "plain English over jargon" not in semantic
    assert "plain English over jargon" not in learnings
    review_text = "\n".join(p.read_text(encoding="utf-8") for p in (vault / "review").glob("*.md"))
    assert "plain English over jargon" not in review_text


def test_binary_mvp_sandbox_fact_preference_and_conflict(tmp_path):
    run_date = "2026-05-14"
    preference_text = "I prefer plain English over jargon"
    fact_quote = (
        "my doctor's phone is 2830 3709, Dr. Lo Hak Keung, "
        "St. Paul's Hospital, Causeway Bay"
    )
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": str(ROOT)}})
        + "\n"
        + json.dumps(
            {
                "type": "message",
                "role": "user",
                "content": f"{fact_quote}. {preference_text}.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_extract(_payload: str, _session_path: Path) -> dict:
        return {
            "items": [
                {
                    "kind": "fact",
                    "subtype": "person_contact",
                    "subject": "Dr. Lo Hak Keung",
                    "predicate": "phone",
                    "object": "2830 3709",
                    "scope": "K2B",
                    "confidence": "high",
                    "evidence_quote": fact_quote,
                    "speaker_source": "keith",
                    "dedupe_key": "person:dr-lo-hak-keung:phone",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                }
            ]
        }

    clean_vault = tmp_path / "clean-vault"
    _write_minimal_vault(clean_vault)
    written = eod_capture.run_job_a(
        [session],
        vault_path=clean_vault,
        run_date=run_date,
        extract_func=fake_extract,
    )
    assert len(written) == 1
    summary = eod_capture.reconcile_extractions(clean_vault, run_date=run_date)

    semantic = (
        clean_vault / "wiki" / "context" / "shelves" / "semantic.md"
    ).read_text(encoding="utf-8")
    assert summary["auto_written"] == 1
    assert semantic.count("dedupe_key:person:dr-lo-hak-keung:phone") == 1
    assert "Dr. Lo Hak Keung" in semantic
    assert "phone:2830 3709" in semantic
    for path in clean_vault.rglob("*"):
        if path.is_file():
            assert preference_text not in path.read_text(encoding="utf-8", errors="ignore")

    conflict_vault = tmp_path / "conflict-vault"
    _write_minimal_vault(
        conflict_vault,
        [
            "- 2026-05-13 | person_contact | dr-lo-hak-keung | "
            "subject:Dr. Lo Hak Keung | predicate:phone | phone:2840 3709 | "
            "dedupe_key:person:dr-lo-hak-keung:phone"
        ],
    )
    eod_capture.run_job_a(
        [session],
        vault_path=conflict_vault,
        run_date=run_date,
        extract_func=fake_extract,
    )
    conflict_summary = eod_capture.reconcile_extractions(
        conflict_vault, run_date=run_date
    )
    conflict_files = sorted(
        (conflict_vault / ".staging" / "pending-conflicts").glob("*.json")
    )
    assert conflict_summary["conflicts"] == 1
    assert len(conflict_files) == 1
    conflict = json.loads(conflict_files[0].read_text(encoding="utf-8"))
    assert conflict["existing_value"] == "2840 3709"
    assert conflict["new_value"] == "2830 3709"

    env = os.environ.copy()
    env.update(
        {
            "K2B_LOOP_CANDIDATES": str(tmp_path / "missing-observer-candidates.md"),
            "K2B_LOOP_REVIEW_DIR": str(tmp_path / "missing-review"),
            "K2B_LOOP_CONFLICTS_DIR": str(
                conflict_vault / ".staging" / "pending-conflicts"
            ),
            "K2B_LOOP_RESEARCH_DIR": str(tmp_path / "missing-research"),
        }
    )
    rendered = eod_capture.subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "loop" / "loop_render.py")],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )
    assert "conflict" in rendered.stdout.lower()
    assert "Dr. Lo Hak Keung" in rendered.stdout


def test_reconcile_low_confidence_surfaces_in_top_level_review_queue(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / "2026-05-14_s1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "codex_desktop",
                "items": [
                    {
                        "kind": "decision",
                        "subject": "End-of-day capture",
                        "predicate": "status",
                        "object": "ship now",
                        "scope": "K2B",
                        "confidence": "medium",
                        "evidence_quote": "let's do it now",
                        "dedupe_key": "decision:eod-capture:status",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    review_files = sorted((vault / "review").glob("*.md"))
    assert summary["low_confidence"] == 1
    assert len(review_files) == 1
    assert review_files[0].name.startswith("eod-low-confidence_2026-05-14_")
    assert "review-action: pending" in review_files[0].read_text(encoding="utf-8")


def test_reconcile_learning_is_idempotent_by_dedupe_key(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / "2026-05-14_s1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "claude_code",
                "items": [
                    {
                        "kind": "learning",
                        "subject": "loop routing",
                        "predicate": "rule",
                        "object": "Conflicts must route through the unified loop index",
                        "scope": "K2B",
                        "confidence": "high",
                        "evidence_quote": "one renderer, one applier",
                        "dedupe_key": "learning:loop-routing:unified-conflicts",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    first = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")
    second = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    learnings = (vault / "System" / "memory" / "self_improve_learnings.md").read_text(
        encoding="utf-8"
    )
    assert first["auto_written"] == 1
    assert second["deduped"] == 1
    assert learnings.count("learning:loop-routing:unified-conflicts") == 1


def test_reconcile_learning_does_not_dedupe_by_substring_rule(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    learnings_path = vault / "System" / "memory" / "self_improve_learnings.md"
    learnings_path.write_text(
        "# Learnings\n\n"
        "### L-2026-05-14-001\n"
        "- **Dedupe key:** learning:loop-routing:conflicts\n"
        "- **Learning:** loop routing conflicts stay unified\n",
        encoding="utf-8",
    )
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / "2026-05-14_s1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "claude_code",
                "items": [
                    {
                        "kind": "learning",
                        "subject": "loop routing",
                        "predicate": "rule",
                        "object": "loop routing",
                        "scope": "K2B",
                        "confidence": "high",
                        "dedupe_key": "learning:loop-routing:short-rule",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    learnings = learnings_path.read_text(encoding="utf-8")
    assert summary["auto_written"] == 1
    assert "learning:loop-routing:short-rule" in learnings


def test_reconcile_learning_dedupe_key_match_is_exact(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    learnings_path = vault / "System" / "memory" / "self_improve_learnings.md"
    learnings_path.write_text(
        "# Learnings\n\n"
        "### L-2026-05-14-001\n"
        "- **Dedupe key:** learning:loop-routing:conflicts-unified\n"
        "- **Learning:** conflicts stay unified\n",
        encoding="utf-8",
    )
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / "2026-05-14_s1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "claude_code",
                "items": [
                    {
                        "kind": "learning",
                        "subject": "loop routing",
                        "predicate": "rule",
                        "object": "conflicts",
                        "scope": "K2B",
                        "confidence": "high",
                        "dedupe_key": "learning:loop-routing:conflicts",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    learnings = learnings_path.read_text(encoding="utf-8")
    assert summary["auto_written"] == 1
    assert "learning:loop-routing:conflicts\n" in learnings


def test_reconcile_malformed_item_is_written_to_review_queue(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / "2026-05-14_s1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "claude_code",
                "items": [None],
            }
        ),
        encoding="utf-8",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    review_files = sorted((vault / "review").glob("eod-error_*.md"))
    assert summary["errors"] == 1
    assert len(review_files) == 1
    assert "malformed extraction item" in review_files[0].read_text(encoding="utf-8")


def test_reconcile_item_error_continues_and_writes_summary(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / "2026-05-14_s1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "claude_code",
                "items": [
                    {
                        "kind": "fact",
                        "subject": "Bad item",
                        "predicate": "note|bad",
                        "object": "contains invalid structural delimiter",
                        "confidence": "high",
                        "dedupe_key": "fact:bad-item:note",
                    },
                    {
                        "kind": "learning",
                        "subject": "EOD capture",
                        "predicate": "rule",
                        "object": "Continue reconciling after one bad extracted item",
                        "confidence": "high",
                        "dedupe_key": "learning:eod-capture:continue-after-error",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    review_files = sorted((vault / "review").glob("eod-error_*.md"))
    summary_file = vault / ".staging" / "eod-capture-summary-2026-05-14.json"
    learnings = (vault / "System" / "memory" / "self_improve_learnings.md").read_text(
        encoding="utf-8"
    )
    assert summary["errors"] == 1
    assert summary["auto_written"] == 1
    assert summary_file.exists()
    assert len(review_files) == 1
    assert "pipe delimiter" in review_files[0].read_text(encoding="utf-8")
    assert "Continue reconciling after one bad extracted item" in learnings


def test_reconcile_conflict_writes_pending_conflict_without_overwrite(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        [
            "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone"
        ],
    )
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / "2026-05-14_s1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "claude_code",
                "items": [
                    {
                        "kind": "fact",
                        "subtype": "person_contact",
                        "subject": "Dr. Lo Hak Keung",
                        "predicate": "phone",
                        "object": "2830 3709",
                        "scope": "K2B",
                        "confidence": "high",
                        "evidence_quote": "my doctor's phone is 2830 3709",
                        "speaker_source": "keith",
                        "dedupe_key": "person:dr-lo-hak-keung:phone",
                        "canonical_home": "wiki/context/shelves/semantic.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
        encoding="utf-8"
    )
    conflicts = list((vault / ".staging" / "pending-conflicts").glob("*.json"))
    assert summary["conflicts"] == 1
    assert "phone:2840 3709" in semantic
    assert "phone:2830 3709" not in semantic
    assert len(conflicts) == 1
    conflict = json.loads(conflicts[0].read_text(encoding="utf-8"))
    assert conflict["existing_value"] == "2840 3709"
    assert conflict["new_value"] == "2830 3709"
    assert conflict["dedupe_key"] == "person:dr-lo-hak-keung:phone"
    assert conflict["existing_line_hash"]


def test_reconcile_conflict_dedupes_existing_pending_conflict_for_run(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        [
            "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone"
        ],
    )
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    for session_id in ("s1", "s2"):
        (staging / f"2026-05-14_{session_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "session_path": f"/tmp/{session_id}.jsonl",
                    "source_app": "claude_code",
                    "items": [
                        {
                            "kind": "fact",
                            "subject": "Dr. Lo Hak Keung",
                            "predicate": "phone",
                            "object": "2830 3709",
                            "confidence": "high",
                            "dedupe_key": "person:dr-lo-hak-keung:phone",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    conflicts = list((vault / ".staging" / "pending-conflicts").glob("*.json"))
    assert summary["conflicts"] == 1
    assert summary["deduped"] == 1
    assert len(conflicts) == 1


def test_reconcile_checks_conflicts_with_pending_lock_before_semantic_lock(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        [
            "- 2026-05-14 | contact | dr-lo-hak-keung | name:Dr. Lo Hak Keung | phone:2840 3709 | dedupe_key:person:dr-lo-hak-keung:phone"
        ],
    )
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / "2026-05-14_s1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "claude_code",
                "items": [
                    {
                        "kind": "fact",
                        "subject": "Dr. Lo Hak Keung",
                        "predicate": "phone",
                        "object": "2830 3709",
                        "confidence": "high",
                        "dedupe_key": "person:dr-lo-hak-keung:phone",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    held = {"pending": False, "semantic": False}
    real_file_lock = eod_capture._file_lock
    real_write_conflict_locked = eod_capture._write_conflict_locked

    @contextmanager
    def fake_semantic_lock(path):
        assert path.name == "k2b-shelf-semantic.lock"
        assert held["pending"] is True
        held["semantic"] = True
        try:
            yield
        finally:
            held["semantic"] = False

    @contextmanager
    def fake_file_lock(path):
        if path.name == ".pending-conflicts.lock":
            held["pending"] = True
            try:
                yield
            finally:
                held["pending"] = False
            return
        with real_file_lock(path):
            yield

    def spy_write_conflict_locked(*args, **kwargs):
        assert held["pending"] is True
        assert held["semantic"] is True
        return real_write_conflict_locked(*args, **kwargs)

    monkeypatch.setattr(eod_capture, "_file_lock", fake_file_lock)
    monkeypatch.setattr(eod_capture, "_shell_compatible_lock", fake_semantic_lock)
    monkeypatch.setattr(eod_capture, "_write_conflict_locked", spy_write_conflict_locked)

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    assert summary["conflicts"] == 1


def test_reconcile_duplicate_semantic_dedupe_key_surfaces_error(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        [
            "- 2026-05-14 | contact | dr-lo-1 | name:Dr. Lo | phone:2840 3709 | dedupe_key:person:dr-lo:phone",
            "- 2026-05-14 | contact | dr-lo-2 | name:Dr. Lo | phone:2830 3709 | dedupe_key:person:dr-lo:phone",
        ],
    )
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / "2026-05-14_s1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "claude_code",
                "items": [
                    {
                        "kind": "fact",
                        "subject": "Dr. Lo",
                        "predicate": "phone",
                        "object": "2830 3709",
                        "confidence": "high",
                        "dedupe_key": "person:dr-lo:phone",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    review_files = sorted((vault / "review").glob("eod-error_*.md"))
    assert summary["errors"] == 1
    assert summary["conflicts"] == 0
    assert len(review_files) == 1
    assert "duplicate dedupe_key in semantic.md" in review_files[0].read_text(
        encoding="utf-8"
    )


def test_reconcile_missing_wiki_log_writes_fallback(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / "wiki" / "log.md").unlink()

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    fallback = vault / ".staging" / "eod-log-failures" / "2026-05-14.jsonl"
    assert summary["processed_files"] == 0
    assert fallback.exists()
    assert "wiki log missing" in fallback.read_text(encoding="utf-8")


def test_reconcile_shelf_writer_data_error_surfaces_review_item(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / "2026-05-14_s1.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "claude_code",
                "items": [
                    {
                        "kind": "fact",
                        "subject": "Bad key",
                        "predicate": "status",
                        "object": "captured",
                        "confidence": "high",
                        "dedupe_key": "fact bad key",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    review_files = sorted((vault / "review").glob("eod-error_*.md"))
    assert summary["errors"] == 1
    assert len(review_files) == 1
    assert "invalid dedupe_key" in review_files[0].read_text(encoding="utf-8")


def test_digest_message_summarizes_run(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging" / "pending-conflicts").mkdir(parents=True)
    (vault / ".staging" / "extraction-failures").mkdir(parents=True)
    (vault / ".staging" / "eod-log-failures").mkdir(parents=True)
    (vault / ".staging" / "eod-log-failures" / "job-b.jsonl").write_text(
        "{}\n", encoding="utf-8"
    )
    (vault / ".staging" / "extraction-failures" / "2026-05-14_failed.json").write_text(
        json.dumps(
            {
                "session_path": "/tmp/failed-session.jsonl",
                "error": "RuntimeError: extractor timeout",
            }
        ),
        encoding="utf-8",
    )
    (vault / ".staging" / "pending-conflicts" / "2026-05-14_c1.json").write_text(
        json.dumps(
            {
                "conflict_id": "c1",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "new_value": "2830 3709",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )
    (vault / ".staging" / "pending-conflicts" / "2026-05-14_bad.json").write_text(
        "{not json", encoding="utf-8"
    )
    summary = {
        "processed_files": 2,
        "auto_written": 3,
        "low_confidence": 1,
        "conflicts": 1,
        "deduped": 4,
        "skipped_preferences": 2,
    }

    message = eod_capture.build_digest_message(vault, run_date="2026-05-14", summary=summary)

    assert "End-of-Day Capture 2026-05-14" in message
    assert "auto-written: 3" in message
    assert "conflicts: 1" in message
    assert "extraction failures: 1" in message
    assert "failed-session.jsonl: RuntimeError: extractor timeout" in message
    assert "log append failures: 1" in message
    assert "Dr. Lo Hak Keung phone" in message
    assert "unreadable conflicts: 1" in message


def test_digest_message_warns_when_summary_missing(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging" / "pending-conflicts").mkdir(parents=True)
    (vault / ".staging" / "pending-conflicts" / "2026-05-14_c1.json").write_text(
        json.dumps(
            {
                "conflict_id": "c1",
                "subject": "Dr. Lo Hak Keung",
                "predicate": "phone",
                "existing_value": "2840 3709",
                "new_value": "2830 3709",
                "source_session_path": "/tmp/s1.jsonl",
            }
        ),
        encoding="utf-8",
    )

    message = eod_capture.build_digest_message(vault, run_date="2026-05-14")

    assert "summary warning: missing summary file; counts may be incomplete" in message
    assert "conflicts: 1" in message
    assert "errors: 1" in message


def test_digest_message_warns_when_summary_corrupt(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging").mkdir(parents=True, exist_ok=True)
    (vault / ".staging" / "eod-capture-summary-2026-05-14.json").write_text(
        "{not json", encoding="utf-8"
    )

    message = eod_capture.build_digest_message(vault, run_date="2026-05-14")

    assert "summary warning: could not read summary file; counts may be incomplete" in message
    assert "errors: 1" in message


def test_digest_send_persists_failure_copy_when_telegram_send_fails(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging").mkdir(parents=True, exist_ok=True)
    (vault / ".staging" / "eod-capture-summary-2026-05-14.json").write_text(
        json.dumps({"processed_files": 1, "auto_written": 1, "errors": 0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(eod_capture.time, "sleep", lambda _seconds: None)

    def fake_run(*_args, **_kwargs):
        raise eod_capture.subprocess.CalledProcessError(returncode=42, cmd=["send"])

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    rc = eod_capture.main(
        ["digest", "--date", "2026-05-14", "--vault", str(vault), "--send"]
    )

    failure_path = (
        vault / ".staging" / "eod-digest-failures" / "2026-05-14_digest.txt"
    )
    assert rc == 42
    assert failure_path.exists()
    assert "End-of-Day Capture 2026-05-14" in failure_path.read_text(encoding="utf-8")


def test_digest_send_removes_failure_copy_after_success(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging").mkdir(parents=True, exist_ok=True)
    (vault / ".staging" / "eod-capture-summary-2026-05-14.json").write_text(
        json.dumps({"processed_files": 1, "auto_written": 1, "errors": 0}),
        encoding="utf-8",
    )

    def fake_run(*_args, **_kwargs):
        return eod_capture.subprocess.CompletedProcess(args=["send"], returncode=0)

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    rc = eod_capture.main(
        ["digest", "--date", "2026-05-14", "--vault", str(vault), "--send"]
    )

    assert rc == 0
    assert not (
        vault / ".staging" / "eod-digest-failures" / "2026-05-14_digest.txt"
    ).exists()


def test_digest_send_cleanup_failure_records_warning_and_returns_distinct_code(
    tmp_path, monkeypatch, capsys
):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging").mkdir(parents=True, exist_ok=True)
    (vault / ".staging" / "eod-capture-summary-2026-05-14.json").write_text(
        json.dumps({"processed_files": 1, "auto_written": 1, "errors": 0}),
        encoding="utf-8",
    )

    def fake_run(*_args, **_kwargs):
        return eod_capture.subprocess.CompletedProcess(args=["send"], returncode=0)

    original_unlink = eod_capture.Path.unlink

    def fake_unlink(self, *args, **kwargs):
        if str(self).endswith("2026-05-14_digest.txt"):
            raise OSError("readonly")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)
    monkeypatch.setattr(eod_capture.Path, "unlink", fake_unlink)

    rc = eod_capture.main(
        ["digest", "--date", "2026-05-14", "--vault", str(vault), "--send"]
    )

    captured = capsys.readouterr()
    warning_path = (
        vault / ".staging" / "eod-digest-cleanup-warnings" / "2026-05-14_cleanup.json"
    )
    warning = json.loads(warning_path.read_text(encoding="utf-8"))
    assert rc == 3
    assert warning["error_type"] == "OSError"
    assert warning["error_message"] == "readonly"
    assert warning["send_succeeded"] is True
    assert "digest send succeeded; cleanup warning recorded" in captured.err
    assert (
        vault / ".staging" / "eod-digest-failures" / "2026-05-14_digest.txt"
    ).exists()


def test_digest_send_returns_nonzero_after_successful_unhealthy_digest(
    tmp_path, monkeypatch, capsys
):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging").mkdir(parents=True, exist_ok=True)
    (vault / ".staging" / "eod-capture-summary-2026-05-14.json").write_text(
        json.dumps({"processed_files": 1, "auto_written": 1, "errors": 0}),
        encoding="utf-8",
    )
    (vault / ".staging" / "extraction-failures").mkdir(parents=True)
    (vault / ".staging" / "extraction-failures" / "2026-05-14_s1.json").write_text(
        json.dumps({"error": "extractor timeout"}),
        encoding="utf-8",
    )

    def fake_run(*_args, **_kwargs):
        return eod_capture.subprocess.CompletedProcess(args=["send"], returncode=0)

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    rc = eod_capture.main(
        ["digest", "--date", "2026-05-14", "--vault", str(vault), "--send"]
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert "digest sent with health issue(s): extraction failures: 1" in captured.err
    assert (
        vault / ".staging" / "eod-digest-failures" / "2026-05-14_digest.txt"
    ).exists()


def test_digest_message_reports_hidden_conflict_count(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    conflict_dir = vault / ".staging" / "pending-conflicts"
    conflict_dir.mkdir(parents=True)
    for idx in range(7):
        (conflict_dir / f"2026-05-14_c{idx}.json").write_text(
            json.dumps(
                {
                    "conflict_id": f"c{idx}",
                    "subject": f"Subject {idx}",
                    "predicate": "status",
                    "existing_value": "old",
                    "new_value": "new",
                    "source_session_path": "/tmp/s1.jsonl",
                }
            ),
            encoding="utf-8",
        )

    message = eod_capture.build_digest_message(
        vault,
        run_date="2026-05-14",
        summary={
            "processed_files": 1,
            "auto_written": 0,
            "low_confidence": 0,
            "conflicts": 7,
            "deduped": 0,
            "skipped_preferences": 0,
            "errors": 0,
        },
    )

    assert "... and 2 more pending conflict(s) not shown" in message


def test_failure_file_fingerprints_detect_same_path_content_change(tmp_path):
    failure_dir = tmp_path / "failures"
    failure_dir.mkdir()
    failure = failure_dir / "2026-05-14_s1.json"
    failure.write_text('{"error":"old"}\n', encoding="utf-8")
    before = eod_capture._failure_file_fingerprints(failure_dir, "2026-05-14")

    failure.write_text('{"error":"new"}\n', encoding="utf-8")
    after = eod_capture._failure_file_fingerprints(failure_dir, "2026-05-14")

    assert before[failure] != after[failure]
