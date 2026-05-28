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


def test_partial_items_rejected_still_succeeds(tmp_path, monkeypatch):
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
    skip_files = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    failure_files = sorted(
        (vault / ".staging" / "extraction-failures").glob("*.json")
    )
    staged = json.loads(written[0].read_text(encoding="utf-8"))
    assert len(written) == 1
    assert [item["dedupe_key"] for item in staged["items"]] == [
        "fact:alpha:status",
        "decision:beta:status",
    ]
    assert len(rejection_files) == 1
    assert skip_files == []
    assert failure_files == []
    rejection = json.loads(rejection_files[0].read_text(encoding="utf-8"))
    assert rejection["item_index"] == 2
    assert rejection["item"]["dedupe_key"] == "fact:bad-quote:status"
    assert "evidence_quote" in rejection["error"]


def test_job_a_skips_existing_valid_extraction_on_rerun(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )
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
                    "evidence_quote": "alpha fact one is true",
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


def test_job_a_removes_stale_extraction_before_slow_skip(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "old payload"}) + "\n",
        encoding="utf-8",
    )
    calls = 0

    def fake_extract(payload: str, _session_path: Path) -> dict | None:
        nonlocal calls
        calls += 1
        if calls == 2:
            return None
        return {
            "schema_version": "1.0",
            "items": [
                {
                    "kind": "fact",
                    "subject": "Session payload",
                    "predicate": "value",
                    "object": "old payload" if "old payload" in payload else "new payload",
                    "confidence": "high",
                    "evidence_quote": "old payload" if "old payload" in payload else "new payload",
                    "dedupe_key": "fact:session-payload:value",
                }
            ],
        }

    first = eod_capture.run_job_a(
        [session], vault_path=vault, run_date="2026-05-14", extract_func=fake_extract
    )
    assert len(first) == 1
    assert first[0].exists()

    session.write_text(
        json.dumps({"type": "user", "content": "new payload"}) + "\n",
        encoding="utf-8",
    )
    second = eod_capture.run_job_a(
        [session], vault_path=vault, run_date="2026-05-14", extract_func=fake_extract
    )

    assert second == []
    assert calls == 2
    assert not first[0].exists()


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
                # HKT-bucketed: 10:00 UTC on 5/13 = 18:00 HKT on 5/13, well
                # within HKT 5/13. Earlier value 23:59 UTC equals 07:59 HKT
                # next day, which the new HKT-aware bucketing (correctly)
                # treats as 5/14 -- not stale, not filtered.
                "timestamp": "2026-05-13T10:00:00Z",
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
                # HKT-bucketed: 10:00 UTC on 5/13 = 18:00 HKT on 5/13. With the
                # new HKT-aware bucketing, this content stamp puts the session
                # firmly in HKT 5/13, distinguishing it from the mtime below.
                "timestamp": "2026-05-13T10:00:00Z",
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


def test_prompt_forbids_feature_canonical_home():
    prompt = eod_capture.PROMPT_PATH.read_text(encoding="utf-8")

    assert "canonical_home MUST be one of" in prompt
    assert "wiki/context/shelves/semantic.md" in prompt
    assert "NEVER use as canonical_home" in prompt
    assert "wiki/concepts/feature_*.md" in prompt
    assert "wiki/concepts/Shipped/*.md" in prompt


def test_same_value_uses_exact_trimmed_comparison():
    assert eod_capture._same_value("2830 3709 ", "2830 3709")
    assert not eod_capture._same_value("Dr. Lo", "dr. lo")
    assert not eod_capture._same_value("2830  3709", "2830 3709")


def test_call_kimi_extractor_timeout_writes_slow_skip_no_failure(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "ae936545-df11-40b0-93d8-bc9da9cdc7f4.jsonl"
    session.write_text("large transcript\n", encoding="utf-8")
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)
    monkeypatch.setattr(eod_capture, "_post_telegram_alert", lambda *_args, **_kwargs: None, raising=False)
    calls: list[int] = []

    def fake_run(*_args, **_kwargs):
        calls.append(1)
        raise eod_capture.subprocess.TimeoutExpired(cmd="kimi", timeout=360)

    monkeypatch.setattr(eod_capture, "_run_extractor_process", fake_run)

    result = eod_capture.call_kimi_extractor(
        "payload",
        session,
        vault_path=vault,
        run_date="2026-05-16",
        transcript_sha256="payload-sha",
    )

    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    assert result is None
    assert len(calls) == 1
    assert len(skips) == 1
    assert failures == []
    skip = json.loads(skips[0].read_text(encoding="utf-8"))
    assert skip["reason"] == "slow_extraction"
    assert skip["session_path"] == str(session)
    assert skip["transcript_sha256"] == "payload-sha"


def test_call_kimi_extractor_timeout_posts_telegram_alert(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "ae936545-df11-40b0-93d8-bc9da9cdc7f4.jsonl"
    session.write_text("x" * 1024, encoding="utf-8")
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)
    alerts: list[tuple[str, Path]] = []

    def fake_post(message: str, *, session_path: Path) -> None:
        alerts.append((message, session_path))

    def fake_run(*_args, **_kwargs):
        raise eod_capture.subprocess.TimeoutExpired(cmd="kimi", timeout=360)

    monkeypatch.setattr(eod_capture, "_post_telegram_alert", fake_post, raising=False)
    monkeypatch.setattr(eod_capture, "_run_extractor_process", fake_run)

    eod_capture.call_kimi_extractor(
        "payload", session, vault_path=vault, run_date="2026-05-16"
    )

    assert len(alerts) == 1
    message, alert_session_path = alerts[0]
    assert alert_session_path == session
    assert "ae936545" in message
    assert "re-run /eod-capture 2026-05-16 locally" in message


def test_call_kimi_extractor_telegram_alert_failure_does_not_crash(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text("large transcript\n", encoding="utf-8")
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)

    def fake_post(*_args, **_kwargs) -> None:
        raise RuntimeError("telegram unavailable")

    def fake_run(*_args, **_kwargs):
        raise eod_capture.subprocess.TimeoutExpired(cmd="kimi", timeout=360)

    monkeypatch.setattr(eod_capture, "_post_telegram_alert", fake_post, raising=False)
    monkeypatch.setattr(eod_capture, "_run_extractor_process", fake_run)

    result = eod_capture.call_kimi_extractor(
        "payload", session, vault_path=vault, run_date="2026-05-16"
    )

    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    assert result is None
    assert len(skips) == 1
    assert failures == []


def test_call_kimi_extractor_360s_default_timeout(tmp_path, monkeypatch):
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)
    timeouts: list[int] = []

    def fake_run(_cmd, *, timeout):
        timeouts.append(timeout)
        return eod_capture.SimpleNamespace(
            args=["kimi"],
            returncode=0,
            stdout=json.dumps({"items": []}),
            stderr="",
            stdout_size=len(json.dumps({"items": []})),
        )

    monkeypatch.setattr(eod_capture, "_run_extractor_process", fake_run)

    data = eod_capture.call_kimi_extractor("payload", tmp_path / "session.jsonl")

    assert data["items"] == []
    assert timeouts == [360]


def test_call_kimi_extractor_transient_api_error_still_retries_3x(
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
        if calls < 3:
            return eod_capture.SimpleNamespace(
                args=["kimi"],
                returncode=1,
                stdout="",
                stderr="429 too many requests",
                stdout_size=0,
            )
        stdout = json.dumps(
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
        )
        return eod_capture.SimpleNamespace(
            args=["kimi"],
            returncode=0,
            stdout=stdout,
            stderr="",
            stdout_size=len(stdout),
        )

    monkeypatch.setattr(eod_capture, "_run_extractor_process", fake_run)

    data = eod_capture.call_kimi_extractor("payload", tmp_path / "session.jsonl")

    assert calls == 3
    assert data["items"][0]["dedupe_key"] == "person:dr-lo:phone"


def test_main_returns_0_when_only_outcome_is_slow_skip(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(json.dumps({"type": "user", "content": "large transcript"}) + "\n", encoding="utf-8")
    wrapper = tmp_path / "minimax-json-job.sh"
    wrapper.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setattr(eod_capture, "MINIMAX_JSON_JOB", wrapper)
    monkeypatch.setattr(eod_capture, "_post_telegram_alert", lambda *_args, **_kwargs: None, raising=False)

    def fake_run(*_args, **_kwargs):
        raise eod_capture.subprocess.TimeoutExpired(cmd="kimi", timeout=360)

    monkeypatch.setattr(eod_capture, "_run_extractor_process", fake_run)

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


def test_write_extraction_skip_accepts_reason_parameter(tmp_path):
    vault = tmp_path / "vault"
    session = tmp_path / "session.jsonl"
    path = eod_capture._write_extraction_skip(
        vault,
        session,
        run_date="2026-05-16",
        reason="slow_extraction",
        error=RuntimeError("timed out"),
    )

    skip = json.loads(path.read_text(encoding="utf-8"))
    assert skip["reason"] == "slow_extraction"
    assert skip["error"] == "RuntimeError: timed out"


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


def test_all_items_rejected_writes_skip_not_failure(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"})
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
                            "subject": "Alpha",
                            "predicate": "status",
                            "object": "true",
                            "confidence": "high",
                            "evidence_quote": "alpha fact one is true",
                            "dedupe_key": "fact:alpha:status",
                            "canonical_home": "wiki/concepts/feature_end-of-day-capture.md",
                        },
                        {
                            "kind": "decision",
                            "subject": "Beta",
                            "predicate": "status",
                            "object": "true",
                            "confidence": "high",
                            "evidence_quote": "alpha fact one is true",
                            "dedupe_key": "decision:beta:status",
                            "canonical_home": "wiki/concepts/Shipped/feature_old.md",
                        },
                        {
                            "kind": "learning",
                            "subject": "Gamma",
                            "predicate": "status",
                            "object": "true",
                            "confidence": "high",
                            "evidence_quote": "alpha fact one is true",
                            "dedupe_key": "learning:gamma:status",
                            "canonical_home": "wiki/concepts/feature_cron-readiness.md",
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    written = eod_capture.run_job_a([session], vault_path=vault, run_date="2026-05-14")

    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    rejections = sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))
    assert written == []
    assert len(skips) == 1
    assert failures == []
    assert len(rejections) == 3
    skip = json.loads(skips[0].read_text(encoding="utf-8"))
    assert skip["reason"] == "all_items_rejected_after_validation"
    assert "all extractor items rejected" in skip["error"]
    assert all("unsupported canonical_home" in p.read_text(encoding="utf-8") for p in rejections)


def test_all_items_rejected_for_bad_evidence_quote_now_skips(
    tmp_path, monkeypatch, capsys
):
    # Content-class validation failures (e.g. hallucinated evidence_quote that
    # the extractor invented from the system prompt) must take the SKIP path,
    # not the FAILURE path. A 2026-05-27 incident showed that halting job-b on
    # 2 hallucinated-evidence_quote sessions dropped 37 successful extractions
    # for the day. Infrastructure failures (missing wrapper, auth/API, parse
    # errors) still go to the failure path -- see test_other_value_errors_*.
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

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    rejections = sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))
    assert rc == 0
    assert failures == []
    assert len(skips) == 1
    assert len(rejections) == 1
    assert skips[0].name.startswith("2026-05-14_")
    skip = json.loads(skips[0].read_text(encoding="utf-8"))
    assert skip["reason"] == "all_items_rejected_after_validation"
    assert "all extractor items rejected" in skip["error"]
    rejection = json.loads(rejections[0].read_text(encoding="utf-8"))
    assert rejection["rejection_class"] == "content"
    assert "evidence_quote" in rejection["error"]
    issues = eod_capture._digest_health_issues(vault, "2026-05-14")
    assert any("all-items-rejected skips: 1" in issue for issue in issues)
    assert not any("extraction failures" in issue for issue in issues)


def test_schema_drift_masked_by_unsupported_canonical_home_still_fails(
    tmp_path, monkeypatch, capsys
):
    # Validation ordering: if an item has BOTH a schema drift (missing
    # dedupe_key) AND an unsupported canonical_home, the schema check must
    # raise first so the rejection is classified as schema, not content.
    # Otherwise an extractor regression could be masked behind a content
    # rejection and the cron pipeline would silently skip job-b's halt.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "alpha fact one is true",
                    # dedupe_key missing + bad canonical_home: schema must win
                    "canonical_home": "wiki/concepts/feature_other.md",
                }
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    rejections = sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))
    assert rc == 1
    assert skips == []
    assert len(failures) == 1
    rejection = json.loads(rejections[0].read_text(encoding="utf-8"))
    assert rejection["rejection_class"] == "schema"
    assert "dedupe_key" in rejection["error"]


def test_non_string_evidence_quote_fails_as_schema_not_content(
    tmp_path, monkeypatch, capsys
):
    # Type-drift attack: extractor returns evidence_quote as a dict/list. The
    # old code would str()-coerce that into a stringified repr, fail the
    # grounding check, and raise content-class -> skip path. The type guard
    # must catch this as schema drift -> failure path -> rc=1.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": {"text": "alpha fact one is true"},
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                }
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    rejections = sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))
    assert rc == 1
    assert skips == []
    assert len(failures) == 1
    rejection = json.loads(rejections[0].read_text(encoding="utf-8"))
    assert rejection["rejection_class"] == "schema"
    assert "non-string evidence_quote" in rejection["error"]


def test_non_string_canonical_home_fails_as_schema_not_content(
    tmp_path, monkeypatch, capsys
):
    # Type-drift attack via canonical_home as list. Same pattern: old code
    # would str()-coerce the list, fail equality check, raise content-class
    # UnsupportedCanonicalHomeError -> skip. Type guard must surface schema.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "alpha fact one is true",
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": ["wiki/context/shelves/semantic.md"],
                }
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    rejections = sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))
    assert rc == 1
    assert skips == []
    assert len(failures) == 1
    rejection = json.loads(rejections[0].read_text(encoding="utf-8"))
    assert rejection["rejection_class"] == "schema"
    assert "non-string canonical_home" in rejection["error"]


def test_partial_schema_drift_with_valid_survivor_still_fails(
    tmp_path, monkeypatch, capsys
):
    # Partial-rejection gap: extractor returns one valid item PLUS one
    # schema-drifted item. The valid item would otherwise be staged and
    # main() would return rc=0, silently dropping the malformed item.
    # Must surface a failure file so cron rc=1 fires and extractor
    # regression doesn't hide behind a survivor.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true and beta fact two is also true"})
        + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "alpha fact one is true",
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                },
                {
                    # schema drift: missing dedupe_key
                    "kind": "fact",
                    "subject": "Beta",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "beta fact two is also true",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                },
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    rejections = sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))
    captured = capsys.readouterr()
    assert rc == 1
    assert skips == []
    assert len(failures) == 1
    assert len(rejections) == 1
    rejection = json.loads(rejections[0].read_text(encoding="utf-8"))
    assert rejection["rejection_class"] == "schema"
    assert "schema-invalid" in failures[0].read_text(encoding="utf-8")
    assert "extraction failure(s) written" in captured.err


def test_partial_content_drift_with_valid_survivor_succeeds(
    tmp_path, monkeypatch, capsys
):
    # Counter-test: same partial-rejection shape but the rejected item is
    # CONTENT-class (hallucinated evidence_quote). The valid item should
    # still stage and main() should return rc=0 -- content-class partial
    # rejections are non-fatal because they're not extractor regression.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"})
        + "\n",
        encoding="utf-8",
    )

    def mixed_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "alpha fact one is true",
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                },
                {
                    "kind": "fact",
                    "subject": "Beta",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    # content-class: evidence_quote not in transcript
                    "evidence_quote": "totally fabricated text not in payload",
                    "dedupe_key": "fact:beta:status",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                },
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", mixed_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    extractions = sorted((vault / ".staging" / "extractions").glob("*.json"))
    assert rc == 0
    assert failures == []
    assert skips == []
    assert len(extractions) == 1
    staged = json.loads(extractions[0].read_text(encoding="utf-8"))
    assert [item["dedupe_key"] for item in staged["items"]] == ["fact:alpha:status"]


def test_cached_extraction_with_missing_evidence_quote_is_reextracted(
    tmp_path, monkeypatch
):
    # Cached-extraction bypass guard: a previously-staged .staging/extractions
    # file written under the old (looser) schema with missing evidence_quote
    # MUST NOT be treated as valid on re-run, even if schema_version and
    # transcript_sha256 match. _existing_valid_extraction must re-validate
    # the evidence_quote contract against the payload.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    # Pre-stage a "valid-looking" extraction without evidence_quote.
    payload = eod_capture.strip_transcript(session)
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    out_dir = vault / ".staging" / "extractions"
    out_dir.mkdir(parents=True, exist_ok=True)
    stale = {
        "schema_version": eod_capture.EXTRACTION_SCHEMA_VERSION,
        "session_path": str(session),
        "source_app": "claude_code",
        "transcript_sha256": sha,
        "items": [
            {
                "kind": "fact",
                "subject": "Alpha",
                "predicate": "status",
                "object": "true",
                "confidence": "high",
                "dedupe_key": "fact:alpha:status",
                # evidence_quote intentionally missing -- pre-tightening shape
            }
        ],
    }
    session_id = eod_capture._safe_session_id(session)
    (out_dir / f"2026-05-14_{session_id}.json").write_text(
        json.dumps(stale), encoding="utf-8"
    )

    calls = 0

    def fake_extract(_payload: str, _session_path: Path) -> dict:
        nonlocal calls
        calls += 1
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "alpha fact one is true",
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                }
            ]
        }

    written = eod_capture.run_job_a(
        [session],
        vault_path=vault,
        run_date="2026-05-14",
        extract_func=fake_extract,
    )

    # The extractor MUST have been called (cache was rejected) and the new
    # extraction MUST have a non-empty evidence_quote.
    assert calls == 1
    assert len(written) == 1
    fresh = json.loads(written[0].read_text(encoding="utf-8"))
    assert fresh["items"][0]["evidence_quote"] == "alpha fact one is true"


def test_missing_evidence_quote_plus_bad_canonical_home_fails_as_schema(
    tmp_path, monkeypatch, capsys
):
    # An item with NO evidence_quote field AND a bad canonical_home must
    # fail as schema (missing evidence_quote), not skip as content (bad
    # canonical_home). The previous evidence_quote validator silently
    # coerced missing -> empty and skipped the length/grounding checks.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": "wiki/concepts/feature_other.md",
                    # evidence_quote intentionally missing
                }
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    rejections = sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))
    assert rc == 1
    assert skips == []
    assert len(failures) == 1
    rejection = json.loads(rejections[0].read_text(encoding="utf-8"))
    assert rejection["rejection_class"] == "schema"
    assert "missing evidence_quote" in rejection["error"]


def test_empty_evidence_quote_plus_bad_canonical_home_fails_as_schema(
    tmp_path, monkeypatch, capsys
):
    # Same as above but with empty-string evidence_quote rather than missing.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "   ",
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": "wiki/concepts/feature_other.md",
                }
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    rejections = sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))
    assert rc == 1
    assert skips == []
    assert len(failures) == 1
    rejection = json.loads(rejections[0].read_text(encoding="utf-8"))
    assert rejection["rejection_class"] == "schema"
    assert "empty evidence_quote" in rejection["error"]


def test_unsupported_canonical_home_does_not_mask_too_short_evidence_quote(
    tmp_path, monkeypatch, capsys
):
    # Cross-validator ordering: item has BOTH an unsupported canonical_home
    # (content-class) AND a too-short evidence_quote (schema-class). The
    # schema check from _validate_item_evidence_quote must raise first so the
    # rejection is classified as schema. Previously the canonical_home raise
    # in _validate_extraction_item happened first and masked the quote drift.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "ok",
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": "wiki/concepts/feature_other.md",
                }
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    rejections = sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))
    assert rc == 1
    assert skips == []
    assert len(failures) == 1
    rejection = json.loads(rejections[0].read_text(encoding="utf-8"))
    assert rejection["rejection_class"] == "schema"
    assert "too short" in rejection["error"]


def test_rejection_class_persisted_in_extraction_rejections_json(
    tmp_path, monkeypatch, capsys
):
    # Audit/replay contract: extraction-rejections JSON files must carry
    # rejection_class so downstream consumers (and post-hoc audits) can verify
    # the skip-vs-fail decision without re-parsing error strings.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    # Two sessions: one content-class (bad canonical_home), one schema-class
    # (missing dedupe_key). The session with bad canonical_home alone yields
    # rejection_class=content (item is otherwise well-formed).
    content_session = tmp_path / "content.jsonl"
    content_session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )
    schema_session = tmp_path / "schema.jsonl"
    schema_session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def per_session_extract(_payload: str, session_path: Path) -> dict:
        if "content" in str(session_path):
            return {
                "items": [
                    {
                        "kind": "fact",
                        "subject": "Alpha",
                        "predicate": "status",
                        "object": "true",
                        "confidence": "high",
                        "evidence_quote": "alpha fact one is true",
                        "dedupe_key": "fact:alpha:status",
                        "canonical_home": "wiki/concepts/feature_other.md",
                    }
                ]
            }
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "alpha fact one is true",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                    # dedupe_key missing -> schema-class
                }
            ]
        }

    eod_capture.run_job_a(
        [content_session, schema_session],
        vault_path=vault,
        run_date="2026-05-14",
        extract_func=per_session_extract,
    )

    rejection_files = sorted(
        (vault / ".staging" / "extraction-rejections").glob("*.json")
    )
    assert len(rejection_files) == 2
    classes = sorted(
        json.loads(p.read_text(encoding="utf-8"))["rejection_class"]
        for p in rejection_files
    )
    assert classes == ["content", "schema"]


def test_phrase_collision_unsupported_canonical_home_in_dedupe_key(
    tmp_path, monkeypatch, capsys
):
    # Phrase-collision attack: extractor returns an item whose dedupe_key
    # literally contains 'unsupported canonical_home'. The rejection error
    # string echoes the value, but the rejection class is SCHEMA (invalid
    # dedupe_key), not content. The typed-exception classifier must NOT skip.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "alpha fact one is true",
                    "dedupe_key": "unsupported canonical_home",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                }
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    assert rc == 1
    assert skips == []
    assert len(failures) == 1


def test_phrase_collision_is_not_present_in_unsupported_field_name(
    tmp_path, monkeypatch, capsys
):
    # Phrase-collision attack via extra field name. Extractor returns an
    # item with an unsupported field literally named with the content-class
    # phrase. The error is schema-class (unsupported field), so MUST fail.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "alpha fact one is true",
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                    "is not present in stripped transcript": "boom",
                }
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    assert rc == 1
    assert skips == []
    assert len(failures) == 1


def test_main_job_a_returns_nonzero_on_evidence_quote_control_char(
    tmp_path, monkeypatch, capsys
):
    # Schema-class evidence_quote failure: extractor injected a non-whitespace
    # control character. Error string contains 'evidence_quote' but is NOT
    # content-class (the extractor's output is malformed). Must route to
    # failure path so cron rc=1 fires.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "alpha fact\x00one is true",
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                }
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    assert rc == 1
    assert skips == []
    assert len(failures) == 1
    assert "control character" in (
        sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))[0]
    ).read_text(encoding="utf-8")


def test_main_job_a_returns_nonzero_on_evidence_quote_too_short(
    tmp_path, monkeypatch, capsys
):
    # Schema-class evidence_quote failure: extractor returned a too-short
    # quote. Error string contains 'evidence_quote' but is NOT content-class.
    # Must route to failure path so cron rc=1 fires.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "ok",
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": "wiki/context/shelves/semantic.md",
                }
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    assert rc == 1
    assert skips == []
    assert len(failures) == 1
    assert "too short" in (
        sorted((vault / ".staging" / "extraction-rejections").glob("*.json"))[0]
    ).read_text(encoding="utf-8")


def test_main_job_a_returns_nonzero_on_all_items_schema_drift(
    tmp_path, monkeypatch, capsys
):
    # If the extractor drifts from the items spec (here: every item is missing
    # dedupe_key), all items get rejected by _filter_extraction_items but the
    # rejection class is schema/contract, NOT content/groundability. Those
    # MUST take the failure path so cron rc=1 halts job-b and the extractor
    # regression surfaces immediately. The skip path is reserved for content
    # rejections (evidence_quote hallucination, unsupported canonical_home).
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def drifted_extract(*_args, **_kwargs):
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "alpha fact one is true",
                    # dedupe_key intentionally missing -> schema drift
                }
            ]
        }

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", drifted_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    captured = capsys.readouterr()
    assert rc == 1
    assert skips == []
    assert len(failures) == 1
    assert failures[0].name.startswith("2026-05-14_")
    assert "all extractor items rejected" in failures[0].read_text(encoding="utf-8")
    assert "extraction failure(s) written" in captured.err
    issues = eod_capture._digest_health_issues(vault, "2026-05-14")
    assert any("extraction failures: 1" in issue for issue in issues)


def test_main_job_a_returns_nonzero_on_infrastructure_failure(
    tmp_path, monkeypatch, capsys
):
    # Counter-test to the skip path: a genuine infrastructure failure
    # (extractor wrapper missing -> RuntimeError) MUST still produce rc=1
    # so cron skips job-b and the operator sees a hard signal. This protects
    # against future "rc=0 catches everything" regressions.
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def failing_extract(*_args, **_kwargs):
        raise RuntimeError("kimi wrapper not found at /opt/kimi/bin/kimi")

    monkeypatch.setattr(eod_capture, "call_kimi_extractor", failing_extract)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    captured = capsys.readouterr()
    assert rc == 1
    assert skips == []
    assert len(failures) == 1
    assert failures[0].name.startswith("2026-05-14_")
    assert "extraction failure(s) written" in captured.err
    issues = eod_capture._digest_health_issues(vault, "2026-05-14")
    assert any("extraction failures: 1" in issue for issue in issues)


def test_main_returns_0_when_all_items_rejected(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
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
                            "subject": "Alpha",
                            "predicate": "status",
                            "object": "true",
                            "confidence": "high",
                            "evidence_quote": "alpha fact one is true",
                            "dedupe_key": "fact:alpha:status",
                            "canonical_home": "wiki/concepts/feature_end-of-day-capture.md",
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(eod_capture.subprocess, "run", fake_run)

    rc = eod_capture.main(
        [
            "job-a",
            "--date",
            "2026-05-14",
            "--vault",
            str(vault),
            "--session",
            str(session),
        ]
    )

    assert rc == 0
    assert sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    assert not sorted((vault / ".staging" / "extraction-failures").glob("*.json"))


def test_all_items_rejected_skip_preserves_transcript_sha(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(
        json.dumps({"type": "user", "content": "alpha fact one is true"}) + "\n",
        encoding="utf-8",
    )

    def invalid_extract(_payload: str, _session_path: Path) -> dict:
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "Alpha",
                    "predicate": "status",
                    "object": "true",
                    "confidence": "high",
                    "evidence_quote": "alpha fact one is true",
                    "dedupe_key": "fact:alpha:status",
                    "canonical_home": "wiki/concepts/feature_end-of-day-capture.md",
                }
            ]
        }

    written = eod_capture.run_job_a(
        [session],
        vault_path=vault,
        run_date="2026-05-14",
        extract_func=invalid_extract,
    )

    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    assert written == []
    assert len(skips) == 1
    skip = json.loads(skips[0].read_text(encoding="utf-8"))
    assert skip["transcript_sha256"] == hashlib.sha256(
        eod_capture.strip_transcript(session).encode("utf-8")
    ).hexdigest()


def test_other_value_errors_still_fail(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    session.write_text(json.dumps({"type": "user", "content": "ok"}) + "\n", encoding="utf-8")

    def malformed_extract(_payload: str, _session_path: Path) -> dict:
        return {"items": "not-a-list"}

    written = eod_capture.run_job_a(
        [session],
        vault_path=vault,
        run_date="2026-05-14",
        extract_func=malformed_extract,
    )

    skips = sorted((vault / ".staging" / "extraction-skips").glob("*.json"))
    failures = sorted((vault / ".staging" / "extraction-failures").glob("*.json"))
    assert written == []
    assert skips == []
    assert len(failures) == 1
    failure = json.loads(failures[0].read_text(encoding="utf-8"))
    assert "non-list items" in failure["error"]


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


def _write_staged_item(vault: Path, item: dict, *, filename: str = "2026-05-14_s1.json") -> None:
    staging = vault / ".staging" / "extractions"
    staging.mkdir(parents=True)
    (staging / filename).write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "session_path": "/tmp/s1.jsonl",
                "source_app": "claude_code",
                "items": [item],
            }
        ),
        encoding="utf-8",
    )


def _write_staged_high_confidence_fact(vault: Path, *, predicate: str) -> None:
    normalized_key = "-".join(predicate.strip().split())
    _write_staged_item(
        vault,
        {
            "kind": "fact",
            "subtype": "project_status",
            "subject": "Keith",
            "predicate": predicate,
            "object": "K2B",
            "scope": "K2B",
            "confidence": "high",
            "evidence_quote": "Keith works at K2B",
            "speaker_source": "keith",
            "dedupe_key": f"person:keith:{normalized_key}",
            "canonical_home": "wiki/context/shelves/semantic.md",
        },
    )


def _write_staged_profile_fact(
    vault: Path,
    *,
    subject: str,
    predicate: str,
    object_value: str,
    dedupe_key: str,
    quote: str,
) -> None:
    _write_staged_item(
        vault,
        {
            "kind": "fact",
            "subtype": "profile",
            "subject": subject,
            "predicate": predicate,
            "object": object_value,
            "scope": "Personal",
            "confidence": "high",
            "evidence_quote": quote,
            "speaker_source": "keith",
            "dedupe_key": dedupe_key,
            "canonical_home": "wiki/context/shelves/semantic.md",
        },
    )


def test_predicate_with_space_normalized(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    _write_staged_high_confidence_fact(vault, predicate="works at")

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    captured = capsys.readouterr()
    semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
        encoding="utf-8"
    )
    assert summary["auto_written"] == 1
    assert summary["errors"] == 0
    assert "predicate:works_at" in semantic
    assert "works_at:K2B" in semantic
    assert "predicate:works at" not in semantic
    assert not list((vault / "review").glob("eod-error_*.md"))
    assert '[shelf-writer] predicate-normalized: "works at" -> "works_at"' in captured.err


def test_predicate_with_space_normalized_rerun_dedupes_without_conflict(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    _write_staged_high_confidence_fact(vault, predicate="works at")

    first = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")
    second = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    capsys.readouterr()
    semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
        encoding="utf-8"
    )
    assert first["auto_written"] == 1
    assert second["auto_written"] == 0
    assert second["deduped"] == 1
    assert second["conflicts"] == 0
    assert second["errors"] == 0
    assert semantic.count("dedupe_key:person:keith:works-at") == 1
    assert not list((vault / ".staging" / "pending-conflicts").glob("*.json"))
    assert not list((vault / "review").glob("eod-error_*.md"))


def test_legacy_predicate_with_space_dedupes_against_normalized(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        semantic_rows=[
            "- 2026-05-13 | project_status | keith | subject:Keith | "
            "predicate:works at | works at:K2B | dedupe_key:person:keith:works-at"
        ],
    )
    _write_staged_high_confidence_fact(vault, predicate="works_at")

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    capsys.readouterr()
    assert summary["auto_written"] == 0
    assert summary["deduped"] == 1
    assert summary["conflicts"] == 0
    assert summary["errors"] == 0
    assert not list((vault / ".staging" / "pending-conflicts").glob("*.json"))
    assert not list((vault / "review").glob("eod-error_*.md"))


def test_legacy_predicate_with_multiple_spaces_dedupes_against_normalized(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        semantic_rows=[
            "- 2026-05-13 | project_status | keith | subject:Keith | "
            "predicate:works   at | works   at:K2B | dedupe_key:person:keith:works-at"
        ],
    )
    _write_staged_high_confidence_fact(vault, predicate="works_at")

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    capsys.readouterr()
    assert summary["auto_written"] == 0
    assert summary["deduped"] == 1
    assert summary["conflicts"] == 0
    assert summary["errors"] == 0


def test_snake_case_dedupe_key_dedupes_preexisting_hyphen_key(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        semantic_rows=[
            "- 2026-05-13 | project_status | keith | subject:Keith | "
            "predicate:works_at | works_at:K2B | dedupe_key:person:keith:works-at"
        ],
    )
    _write_staged_high_confidence_fact(vault, predicate="works_at")

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    capsys.readouterr()
    semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
        encoding="utf-8"
    )
    assert summary["auto_written"] == 0
    assert summary["deduped"] == 1
    assert summary["conflicts"] == 0
    assert summary["errors"] == 0
    assert semantic.count("dedupe_key:person:keith:works-at") == 1
    assert "dedupe_key:person:keith:works_at" not in semantic


def test_legacy_predicate_with_hyphen_dedupes_against_underscore(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        semantic_rows=[
            "- 2026-05-13 | project_status | keith | subject:Keith | "
            "predicate:works-at | works-at:K2B | dedupe_key:person:keith:works-at"
        ],
    )
    _write_staged_high_confidence_fact(vault, predicate="works_at")

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    capsys.readouterr()
    assert summary["auto_written"] == 0
    assert summary["deduped"] == 1
    assert summary["conflicts"] == 0
    assert summary["errors"] == 0
    assert not list((vault / ".staging" / "pending-conflicts").glob("*.json"))


def test_non_predicate_segment_hyphen_does_not_alias(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        semantic_rows=[
            "- 2026-05-13 | profile | dr-lo-hak-keung | "
            "subject:Dr Lo Hak Keung | predicate:phone | phone:+85211112222 | "
            "dedupe_key:person:dr-lo-hak-keung:phone"
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
                        "subtype": "profile",
                        "subject": "Dr Lo Hak Keung",
                        "predicate": "phone",
                        "object": "+85233334444",
                        "scope": "Personal",
                        "confidence": "high",
                        "evidence_quote": "Dr Lo Hak Keung phone is +85233334444",
                        "speaker_source": "keith",
                        "dedupe_key": "person:dr_lo_hak_keung:phone",
                        "canonical_home": "wiki/context/shelves/semantic.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    capsys.readouterr()
    semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
        encoding="utf-8"
    )
    assert summary["auto_written"] == 1
    assert summary["deduped"] == 0
    assert summary["conflicts"] == 0
    assert summary["errors"] == 0
    assert "dedupe_key:person:dr-lo-hak-keung:phone" in semantic
    assert "dedupe_key:person:dr_lo_hak_keung:phone" in semantic


def test_non_predicate_segment_underscore_does_not_alias_reverse(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        semantic_rows=[
            "- 2026-05-13 | profile | dr_lo_hak_keung | "
            "subject:Dr Lo Hak Keung | predicate:phone | phone:+85211112222 | "
            "dedupe_key:person:dr_lo_hak_keung:phone"
        ],
    )
    _write_staged_profile_fact(
        vault,
        subject="Dr Lo Hak Keung",
        predicate="phone",
        object_value="+85233334444",
        quote="Dr Lo Hak Keung phone is +85233334444",
        dedupe_key="person:dr-lo-hak-keung:phone",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    capsys.readouterr()
    semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
        encoding="utf-8"
    )
    assert summary["auto_written"] == 1
    assert summary["deduped"] == 0
    assert summary["conflicts"] == 0
    assert summary["errors"] == 0
    assert "dedupe_key:person:dr_lo_hak_keung:phone" in semantic
    assert "dedupe_key:person:dr-lo-hak-keung:phone" in semantic


def test_predicate_alias_with_hyphenated_subject_still_dedupes(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        semantic_rows=[
            "- 2026-05-13 | profile | dr-lo-hak-keung | "
            "subject:Dr Lo Hak Keung | predicate:works-at | works-at:CUHK | "
            "dedupe_key:person:dr-lo-hak-keung:works-at"
        ],
    )
    _write_staged_profile_fact(
        vault,
        subject="Dr Lo Hak Keung",
        predicate="works_at",
        object_value="CUHK",
        quote="Dr Lo Hak Keung works at CUHK",
        dedupe_key="person:dr-lo-hak-keung:works_at",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    capsys.readouterr()
    assert summary["auto_written"] == 0
    assert summary["deduped"] == 1
    assert summary["conflicts"] == 0
    assert summary["errors"] == 0


def test_predicate_segment_aliasing_still_works(tmp_path, capsys):
    for legacy_predicate in ("lives in", "lives-in"):
        vault = tmp_path / legacy_predicate.replace(" ", "_")
        _write_minimal_vault(
            vault,
            semantic_rows=[
                "- 2026-05-13 | project_status | keith | subject:Keith | "
                f"predicate:{legacy_predicate} | {legacy_predicate}:K2B | "
                "dedupe_key:person:keith:lives-in"
            ],
        )
        _write_staged_high_confidence_fact(vault, predicate="lives_in")

        summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

        capsys.readouterr()
        semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
            encoding="utf-8"
        )
        assert summary["auto_written"] == 0
        assert summary["deduped"] == 1
        assert summary["conflicts"] == 0
        assert summary["errors"] == 0
        assert semantic.count("dedupe_key:person:keith:lives-in") == 1


def test_dedupe_key_aliases_scope_to_predicate_segment():
    aliases = eod_capture._dedupe_key_aliases("person:dr-lo-hak-keung:works_at")

    assert "person:dr-lo-hak-keung:works-at" in aliases
    assert "person:dr_lo_hak_keung:works_at" not in aliases
    assert "person:dr_lo_hak_keung:works-at" not in aliases


def test_dedupe_key_alias_requires_compatible_row_predicate(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(
        vault,
        semantic_rows=[
            "- 2026-05-13 | project_status | keith | subject:Keith | "
            "predicate:foo | foo:old | dedupe_key:person:keith:foo-bar"
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
                        "subtype": "project_status",
                        "subject": "Keith",
                        "predicate": "foo_bar",
                        "object": "new",
                        "scope": "K2B",
                        "confidence": "high",
                        "evidence_quote": "Keith foo bar new",
                        "speaker_source": "keith",
                        "dedupe_key": "person:keith:foo_bar",
                        "canonical_home": "wiki/context/shelves/semantic.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    capsys.readouterr()
    semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
        encoding="utf-8"
    )
    assert summary["auto_written"] == 1
    assert summary["deduped"] == 0
    assert summary["conflicts"] == 0
    assert summary["errors"] == 0
    assert "dedupe_key:person:keith:foo-bar" in semantic
    assert "dedupe_key:person:keith:foo_bar" in semantic


def test_predicate_without_space_unchanged(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    _write_staged_high_confidence_fact(vault, predicate="phone")

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    captured = capsys.readouterr()
    semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
        encoding="utf-8"
    )
    assert summary["auto_written"] == 1
    assert "predicate:phone" in semantic
    assert "phone:K2B" in semantic
    assert "predicate-normalized" not in captured.err


def test_predicate_multiple_spaces_normalized(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    _write_staged_high_confidence_fact(vault, predicate="works   at")

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    captured = capsys.readouterr()
    semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
        encoding="utf-8"
    )
    assert summary["auto_written"] == 1
    assert "predicate:works_at" in semantic
    assert "works_at:K2B" in semantic
    assert '[shelf-writer] predicate-normalized: "works   at" -> "works_at"' in captured.err


def test_predicate_leading_trailing_whitespace_stripped(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    _write_staged_high_confidence_fact(vault, predicate="  works at  ")

    summary = eod_capture.reconcile_extractions(vault, run_date="2026-05-14")

    captured = capsys.readouterr()
    semantic = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_text(
        encoding="utf-8"
    )
    assert summary["auto_written"] == 1
    assert "predicate:works_at" in semantic
    assert "works_at:K2B" in semantic
    assert '[shelf-writer] predicate-normalized: "  works at  " -> "works_at"' in captured.err


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


def test_digest_message_includes_slow_extraction_skips(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "ae936545-df11-40b0-93d8-bc9da9cdc7f4.jsonl"
    session.write_text("large transcript\n", encoding="utf-8")
    eod_capture._write_extraction_skip(
        vault,
        session,
        run_date="2026-05-14",
        reason="slow_extraction",
        error=RuntimeError("timed out"),
    )

    message = eod_capture.build_digest_message(
        vault, run_date="2026-05-14", summary={"errors": 0}
    )

    assert "slow extraction skips: 1" in message
    assert "ae936545" in message
    assert "slow_extraction" in message


def test_digest_health_issues_include_slow_extraction_skips(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging").mkdir(parents=True, exist_ok=True)
    (vault / ".staging" / "eod-capture-summary-2026-05-14.json").write_text(
        json.dumps({"processed_files": 1, "errors": 0}),
        encoding="utf-8",
    )
    session = tmp_path / "session.jsonl"
    eod_capture._write_extraction_skip(
        vault,
        session,
        run_date="2026-05-14",
        reason="slow_extraction",
        error=RuntimeError("timed out"),
    )

    assert "slow extraction skips: 1" in eod_capture._digest_health_issues(
        vault, "2026-05-14"
    )


def test_digest_health_issues_include_all_items_rejected_skips(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging").mkdir(parents=True, exist_ok=True)
    (vault / ".staging" / "eod-capture-summary-2026-05-14.json").write_text(
        json.dumps({"processed_files": 1, "errors": 0}),
        encoding="utf-8",
    )
    session = tmp_path / "session.jsonl"
    eod_capture._write_extraction_skip(
        vault,
        session,
        run_date="2026-05-14",
        reason="all_items_rejected_after_validation",
        error=RuntimeError("all extractor items rejected"),
    )

    assert "all-items-rejected skips: 1" in eod_capture._digest_health_issues(
        vault, "2026-05-14"
    )


def test_digest_health_issues_flags_zero_floor_breach(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging").mkdir(parents=True, exist_ok=True)
    (vault / ".staging" / "eod-capture-summary-2026-05-19.json").write_text(
        json.dumps({"processed_files": 0, "errors": 0}),
        encoding="utf-8",
    )
    fake_session = tmp_path / "fake-session.jsonl"
    fake_session.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        eod_capture, "discover_session_paths", lambda **_: [fake_session]
    )

    issues = eod_capture._digest_health_issues(vault, "2026-05-19")
    assert any("zero-floor breach" in i for i in issues), issues
    assert any("E-2026-05-20-001" in i for i in issues), issues


def test_digest_health_issues_no_zero_floor_breach_on_quiet_day(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging").mkdir(parents=True, exist_ok=True)
    (vault / ".staging" / "eod-capture-summary-2026-05-19.json").write_text(
        json.dumps({"processed_files": 0, "errors": 0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(eod_capture, "discover_session_paths", lambda **_: [])

    issues = eod_capture._digest_health_issues(vault, "2026-05-19")
    assert not any("zero-floor breach" in i for i in issues), issues


def test_digest_health_issues_flags_partial_miss(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging").mkdir(parents=True, exist_ok=True)
    (vault / ".staging" / "eod-capture-summary-2026-05-19.json").write_text(
        json.dumps({"processed_files": 3, "errors": 0}),
        encoding="utf-8",
    )
    fake_sessions = [tmp_path / f"fake-{i}.jsonl" for i in range(25)]
    for session in fake_sessions:
        session.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        eod_capture, "discover_session_paths", lambda **_: fake_sessions
    )

    issues = eod_capture._digest_health_issues(vault, "2026-05-19")
    assert any("partial-miss suspected" in i for i in issues), issues
    assert any("3 processed" in i for i in issues), issues
    assert any("25 candidates" in i for i in issues), issues
    assert not any("zero-floor breach" in i for i in issues), issues


def test_digest_health_issues_no_partial_miss_when_fully_processed(
    tmp_path, monkeypatch
):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    (vault / ".staging").mkdir(parents=True, exist_ok=True)
    (vault / ".staging" / "eod-capture-summary-2026-05-19.json").write_text(
        json.dumps({"processed_files": 5, "errors": 0}),
        encoding="utf-8",
    )
    fake_sessions = [tmp_path / f"fake-{i}.jsonl" for i in range(5)]
    for session in fake_sessions:
        session.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        eod_capture, "discover_session_paths", lambda **_: fake_sessions
    )

    issues = eod_capture._digest_health_issues(vault, "2026-05-19")
    assert not any("partial-miss suspected" in i for i in issues), issues


def test_parse_event_date_buckets_by_hkt_for_utc_z_timestamp():
    # 16:30 UTC on 2026-05-19 = 00:30 HKT on 2026-05-20. Per the HKT convention,
    # this session belongs to the 2026-05-20 HKT bucket, not 2026-05-19.
    assert eod_capture._parse_event_date("2026-05-19T16:30:00Z") == "2026-05-20"


def test_parse_event_date_buckets_by_hkt_for_naive_timestamp_treated_as_utc():
    # Naive ISO string without offset is treated as UTC (degrade-gracefully).
    # 17:00 UTC = 01:00 HKT next day.
    assert eod_capture._parse_event_date("2026-05-19T17:00:00") == "2026-05-20"


def test_parse_event_date_naive_timestamp_emits_stderr_warning_and_dedupes(capsys):
    # Naive timestamps violate convention Rule 4. We degrade gracefully (treat
    # as UTC) but emit a stderr audit so leaks are visible. Rate-limited to one
    # warning per unique format shape per process.
    eod_capture._NAIVE_TIMESTAMP_WARNED_FORMATS.clear()
    capsys.readouterr()  # drop any pre-test stderr

    eod_capture._parse_event_date("2026-05-19T17:00:00")
    first = capsys.readouterr().err
    assert "naive timestamp treated as UTC" in first
    assert "Rule 4 violation" in first
    assert "2026-05-19T17:00:00" in first

    # Second call with same format shape -> no new warning (dedup).
    eod_capture._parse_event_date("2026-05-20T18:30:00")
    assert capsys.readouterr().err == ""

    # Different format shape (microseconds) -> new warning.
    eod_capture._parse_event_date("2026-05-20T18:30:00.123456")
    third = capsys.readouterr().err
    assert "naive timestamp treated as UTC" in third
    assert "2026-05-20T18:30:00.123456" in third


def test_parse_event_date_passes_through_date_only_string():
    # Date-only strings are assumed to already be HKT-bucketed (per the
    # canonical YYYY-MM-DD shape) and pass through unchanged.
    assert eod_capture._parse_event_date("2026-05-19") == "2026-05-19"


def test_parse_event_date_buckets_epoch_int_by_hkt():
    # Epoch 1779206400 = 2026-05-19 16:00:00 UTC = 2026-05-20 00:00:00 HKT.
    assert eod_capture._parse_event_date(1779206400) == "2026-05-20"


def test_default_eod_date_hkt_returns_yesterday_hkt():
    # K2B convention: EOD CLI subcommands process "the day that just ended."
    # Default to yesterday HKT, not today, so direct invocations without --date
    # match the cron wrapper's behavior.
    from datetime import datetime, timedelta

    actual = eod_capture._default_eod_date_hkt()
    expected = (datetime.now(eod_capture.HKT) - timedelta(days=1)).date().isoformat()
    assert actual == expected


def test_today_alias_preserved_for_back_compat():
    # _today is a deprecated alias kept until external callers are confirmed
    # absent. Should return identical value to _default_eod_date_hkt().
    assert eod_capture._today() == eod_capture._default_eod_date_hkt()


def test_digest_message_includes_all_items_rejected_skips(tmp_path):
    vault = tmp_path / "vault"
    _write_minimal_vault(vault)
    session = tmp_path / "session.jsonl"
    eod_capture._write_extraction_skip(
        vault,
        session,
        run_date="2026-05-14",
        reason="all_items_rejected_after_validation",
        error=RuntimeError("all extractor items rejected"),
    )

    message = eod_capture.build_digest_message(
        vault, run_date="2026-05-14", summary={"errors": 0}
    )

    assert "all-items-rejected skips: 1" in message
    assert "all_items_rejected_after_validation" in message


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
    monkeypatch.setattr(eod_capture, "discover_session_paths", lambda **_: [])

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
    monkeypatch.setattr(eod_capture, "discover_session_paths", lambda **_: [])

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
    monkeypatch.setattr(eod_capture, "discover_session_paths", lambda **_: [])

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


def test_build_send_telegram_cmd_no_env_falls_back_to_dm():
    cmd = eod_capture._build_send_telegram_cmd(message="hi", env={})
    assert cmd[-1] == "hi"
    assert "--chat-id" not in cmd
    assert "--thread-id" not in cmd


def test_build_send_telegram_cmd_with_alerts_env_injects_flags():
    env = {"K2B_ALERTS_CHAT_ID": "-1003966532428", "K2B_EOD_THREAD_ID": "53"}
    cmd = eod_capture._build_send_telegram_cmd(message="hi", env=env)
    assert "--chat-id" in cmd
    assert cmd[cmd.index("--chat-id") + 1] == "-1003966532428"
    assert "--thread-id" in cmd
    assert cmd[cmd.index("--thread-id") + 1] == "53"
    assert cmd[-1] == "hi"


def test_build_send_telegram_cmd_partial_env_falls_back_to_dm():
    # Only one of the two vars set -- treat as misconfigured, fall back to DM
    cmd = eod_capture._build_send_telegram_cmd(
        message="hi", env={"K2B_ALERTS_CHAT_ID": "-1003966532428"}
    )
    assert "--chat-id" not in cmd
    assert "--thread-id" not in cmd

    cmd = eod_capture._build_send_telegram_cmd(
        message="hi", env={"K2B_EOD_THREAD_ID": "53"}
    )
    assert "--chat-id" not in cmd
    assert "--thread-id" not in cmd


def test_build_send_telegram_cmd_file_path_includes_flags_when_env_set(tmp_path):
    digest = tmp_path / "digest.txt"
    digest.write_text("body", encoding="utf-8")
    env = {"K2B_ALERTS_CHAT_ID": "-1003966532428", "K2B_EOD_THREAD_ID": "53"}
    cmd = eod_capture._build_send_telegram_cmd(file_path=digest, env=env)
    assert "--chat-id" in cmd
    assert "--thread-id" in cmd
    assert "--file" in cmd
    assert cmd[cmd.index("--file") + 1] == str(digest)


def test_build_send_telegram_cmd_whitespace_env_treated_as_unset():
    cmd = eod_capture._build_send_telegram_cmd(
        message="hi",
        env={"K2B_ALERTS_CHAT_ID": "  ", "K2B_EOD_THREAD_ID": "53"},
    )
    assert "--chat-id" not in cmd


def test_build_send_telegram_cmd_requires_message_or_file():
    with pytest.raises(ValueError):
        eod_capture._build_send_telegram_cmd(env={})
