from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import eod_capture  # noqa: E402

automatic_memory = eod_capture.automatic_memory


@pytest.fixture(autouse=True)
def _codex_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / ".codex" / "sessions"))
    monkeypatch.setenv("K2B_CAPTURE_WRITER_ROLE", "home")


def _session(codex_root: Path, run_date: str, name: str) -> Path:
    year, month, day = run_date.split("-")
    path = codex_root / year / month / day / f"rollout-{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "cwd": "/Users/keithmbpm2/Projects/K2B",
                            "timestamp": f"{run_date}T09:00:00+08:00",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "item": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": f"capture {name}"}],
                            }
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _modern_session(codex_root: Path, run_date: str, name: str) -> Path:
    source = _session(codex_root, run_date, name)
    records = [json.loads(line) for line in source.read_text().splitlines()]
    records[0]["payload"].update(
        id=f"session-{name}",
        session_id=f"session-{name}",
        thread_source="user",
        source="vscode",
    )
    records.insert(
        1,
        {
            "type": "event_msg",
            "ordinal": 1,
            "payload": {"type": "task_started", "turn_id": "turn-1"},
        },
    )
    records[-1]["ordinal"] = 2
    records.append(
        {
            "type": "event_msg",
            "ordinal": 3,
            "timestamp": f"{run_date}T10:00:00+08:00",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-1",
                "completed_at": f"{run_date}T10:00:00+08:00",
            },
        }
    )
    source.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return source


def _minimal_vault(vault: Path) -> None:
    shelves = vault / "wiki" / "context" / "shelves"
    shelves.mkdir(parents=True)
    (vault / "System" / "memory").mkdir(parents=True)
    (vault / "review").mkdir(parents=True)
    (vault / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")
    (vault / "System" / "memory" / "self_improve_learnings.md").write_text(
        "# Learnings\n", encoding="utf-8"
    )
    (shelves / "semantic.md").write_text(
        "---\ntags: [context, shelf, semantic]\nrow-count: 0\n---\n\n# Semantic\n\n## Rows\n",
        encoding="utf-8",
    )


def _rebind_modern_bundle(bundle: dict) -> None:
    events = bundle["dialogue_events"]
    transcript = "\n\n".join(
        f"[{event.get('role')}]\n{event.get('text')}" for event in events
    ).strip()
    prefix_hash = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    bundle["transcript"] = transcript
    bundle["transcript_sha256"] = prefix_hash
    bundle["completed_prefix_sha256"] = prefix_hash
    cursor_seed = json.dumps(
        [
            bundle.get("host_id"),
            bundle.get("session_id"),
            bundle.get("completed_turn_id"),
            [event.get("event_id") for event in events],
            prefix_hash,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    bundle["completed_cursor"] = "cursor:" + hashlib.sha256(
        cursor_seed.encode("utf-8")
    ).hexdigest()
    bundle["chunks"] = eod_capture._bounded_dialogue_chunks(
        transcript,
        cursor=bundle["completed_cursor"],
        max_chunk_chars=eod_capture.DEFAULT_DIALOGUE_CHUNK_CHARS,
    )


def _reviewed_memory_item(
    bundle: dict,
    *,
    value: str,
    kind: str = "fact",
    dedupe_key: str = "fact:k2b-memory:office",
) -> dict:
    user_event = next(
        event for event in bundle["dialogue_events"] if event["role"] == "user"
    )
    return {
        "raw_source_sha256": bundle["raw_source_sha256"],
        "transcript_sha256": bundle["transcript_sha256"],
        "completed_cursor": bundle["completed_cursor"],
        "completed_prefix_sha256": bundle["completed_prefix_sha256"],
        "completed_prefix_mode": "turn",
        "completed_date": bundle["completed_date"],
        "host_id": bundle["host_id"],
        "session_id": bundle["session_id"],
        "review_state": "reviewed",
        "items": [
            {
                "kind": kind,
                "confidence": "high",
                "subject": "K2B memory",
                "predicate": "office",
                "object": value,
                "scope": "K2B",
                "canonical_home": "wiki/context/shelves/semantic.md",
                "dedupe_key": dedupe_key,
                "evidence_quote": user_event["text"],
                "speaker_source": "keith",
                "evidence_event_id": user_event["event_id"],
            }
        ],
    }


def test_automatic_memory_rejects_earlier_turn_evidence_from_later_work_item(
    tmp_path: Path,
) -> None:
    sessions = tmp_path / "sessions"
    source = _modern_session(sessions, "2026-09-05", "old-evidence")
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            "".join(
                json.dumps(row) + "\n"
                for row in (
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_started", "turn_id": "turn-2"},
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Now discuss an unrelated report, please.",
                                }
                            ],
                        },
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-09-10T10:00:00+08:00",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": "turn-2",
                            "completed_at": "2026-09-10T10:00:00+08:00",
                        },
                    },
                )
            )
        )

    latest = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=sessions
    )
    assert latest["completed_turn_id"] == "turn-2"
    extraction = _reviewed_memory_item(latest, value="An old office decision")

    with pytest.raises(ValueError, match="completed turn"):
        eod_capture.build_reviewed_memory_bundles(
            latest, extraction, run_date="2026-09-10"
        )

    state = tmp_path / "state"
    worklist = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=sessions,
        since="2026-09-05",
        through="2026-09-10",
        writer_role="home",
        limit=10,
    )
    later_work = next(
        item
        for item in worklist["items"]
        if json.loads(
            Path(item["source_bundle_path"]).read_text(encoding="utf-8")
        )["completed_turn_id"]
        == "turn-2"
    )
    result = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=later_work["work_id"],
        reviewed=extraction,
        writer_role="home",
    )
    assert result["status"] == "retryable"
    assert not list((state / "extraction-receipts").glob("*.json"))
    assert not list((state / "outbox").glob("*.json"))


def test_discover_session_paths_between_catches_each_missed_day(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex" / "sessions"
    older = _session(codex_root, "2026-09-05", "older")
    newer = _session(codex_root, "2026-09-07", "newer")
    _session(codex_root, "2026-09-08", "outside")

    discovered = eod_capture.discover_session_paths_between(
        since="2026-09-05", through="2026-09-07", codex_root=codex_root
    )

    assert discovered == {
        "2026-09-05": [older],
        "2026-09-06": [],
        "2026-09-07": [newer],
    }


def test_build_source_bundle_rejects_an_unstable_initial_source_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "unstable-snapshot")
    original_parse = eod_capture.parse_completed_dialogue

    def parse_then_change(path: Path, **kwargs: object) -> dict:
        parsed = original_parse(path, **kwargs)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        return parsed

    monkeypatch.setattr(eod_capture, "parse_completed_dialogue", parse_then_change)

    with pytest.raises(ValueError, match="source changed"):
        eod_capture.build_source_bundle(
            source, source_host="home", codex_root=root
        )


def test_mismatched_task_completion_is_reported_in_memory_worklist(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "mismatched-completion")
    records = [json.loads(line) for line in source.read_text().splitlines()]
    records[-1]["payload"]["turn_id"] = "turn-other"
    source.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    parsed = eod_capture.parse_completed_dialogue(source, source_host="home")
    worklist = eod_capture.build_memory_worklist(
        state_root=tmp_path / "state",
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
    )

    assert parsed["completed_prefixes"] == []
    assert parsed["parse_diagnostics"] == [
        {
            "reason": "task_complete_turn_id_mismatch",
            "line_no": len(records),
            "active_turn_id": "turn-1",
            "completion_turn_id": "turn-other",
            "completed_date": "2026-09-05",
        }
    ]
    assert worklist["status"] == "needs_attention"
    assert worklist["selected"] == 0
    assert worklist["parse_exception_counts"] == {
        "task_complete_turn_id_mismatch": 1
    }


def test_memory_worklist_contains_source_unavailable_and_keeps_other_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    missing = _modern_session(root, "2026-09-05", "a-vanished-source")
    available = _modern_session(root, "2026-09-05", "b-available-source")
    original_discover = eod_capture.discover_session_paths_between

    def discover_then_remove(**kwargs: object) -> dict[str, list[Path]]:
        discovered = original_discover(**kwargs)
        missing.unlink()
        return discovered

    monkeypatch.setattr(
        eod_capture, "discover_session_paths_between", discover_then_remove
    )

    worklist = eod_capture.build_memory_worklist(
        state_root=tmp_path / "state",
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=10,
    )

    assert worklist["status"] == "ready_with_exceptions"
    assert worklist["selected"] == 1
    assert worklist["parse_exception_counts"] == {"source_unavailable": 1}
    assert worklist["items"][0]["session_path"] == str(available)
    assert not list((tmp_path / "state" / "extraction-receipts").glob("*.json"))


def test_discovery_includes_late_completed_turn_in_old_active_session(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "old-active")
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "ordinal": 2,
                    "payload": {"type": "task_started", "turn_id": "late-turn"},
                }
            )
            + "\n"
        )
        handle.write(
            json.dumps(
                {
                    "type": "response_item",
                    "ordinal": 3,
                    "payload": {
                        "id": "late-user-message",
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "A late completed decision."}
                        ],
                    },
                }
            )
            + "\n"
        )
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "ordinal": 4,
                    "timestamp": "2026-09-10T18:00:00+08:00",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "late-turn",
                        "completed_at": "2026-09-10T18:00:00+08:00",
                    },
                }
            )
            + "\n"
        )

    assert eod_capture.discover_session_paths(
        run_date="2026-09-10", codex_root=root
    ) == [source]


@pytest.mark.parametrize(
    ("thread_source", "source_value"),
    [
        ("subagent", {"subagent": {"thread_spawn": {"depth": 1}}}),
        ("automation", "vscode"),
        ("imported", {"imported": {"bundle_id": "bundle-1"}}),
    ],
)
def test_discovery_excludes_worker_and_imported_sessions(
    tmp_path: Path, thread_source: str, source_value: object
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", thread_source)
    records = [json.loads(line) for line in source.read_text().splitlines()]
    records[0]["payload"]["thread_source"] = thread_source
    records[0]["payload"]["source"] = source_value
    source.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    assert eod_capture.discover_session_paths(
        run_date="2026-09-05", codex_root=root
    ) == []


@pytest.mark.parametrize(
    ("source_value", "eligible", "reason", "provenance"),
    [
        ("subagent", False, "worker_origin", "known_worker"),
        ("automation", False, "worker_origin", "known_worker"),
        ("imported", False, "imported_source", "known_import"),
        ("important-user-client", True, None, "unknown"),
    ],
)
def test_source_origin_matching_is_exact_and_unknown_is_observable(
    tmp_path: Path,
    source_value: str,
    eligible: bool,
    reason: str | None,
    provenance: str,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", source_value)
    records = [json.loads(line) for line in source.read_text().splitlines()]
    records[0]["payload"]["source"] = source_value
    source.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    parsed = eod_capture.parse_completed_dialogue(source, source_host="home")

    assert parsed["eligible"] is eligible
    assert parsed["exclusion_reason"] == reason
    assert parsed["provenance_status"] == provenance


def test_ordinary_thread_with_unknown_structured_source_stays_unknown(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "unknown-structured-source")
    records = [json.loads(line) for line in source.read_text().splitlines()]
    records[0]["payload"].update(
        thread_source="user",
        source={"mystery_origin": {"version": 1}},
    )
    source.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    parsed = eod_capture.parse_completed_dialogue(source, source_host="home")

    assert parsed["eligible"] is True
    assert parsed["exclusion_reason"] is None
    assert parsed["provenance_status"] == "unknown"


def test_legacy_prefix_survives_first_incomplete_and_completed_modern_turn(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "mixed-history")
    legacy = eod_capture.parse_completed_dialogue(source, source_host="home")
    legacy_prefix = legacy["completed_prefixes"][0]
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    [legacy_artifact] = eod_capture.run_job_a(
        [source], vault_path=vault, run_date="2026-09-05", codex_root=root,
        extract_func=lambda *_args: {"items": []},
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    legacy_receipt = vault / ".staging" / "reconciled" / legacy_artifact.name

    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "modern-1"},
                }
            )
            + "\n"
        )
        handle.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "unfinished modern"}],
                    },
                }
            )
            + "\n"
        )
    incomplete = eod_capture.parse_completed_dialogue(source, source_host="home")
    assert incomplete["completed_prefixes"] == [legacy_prefix]
    assert source in eod_capture.discover_session_paths(
        run_date="2026-09-05", codex_root=root
    )
    assert eod_capture.capture_status(
        vault, since="2026-09-05", through="2026-09-05", codex_root=root
    )["sessions"][0]["status"] == "reconciled"

    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": "2026-09-10T10:00:00+08:00",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "modern-1",
                        "completed_at": "2026-09-10T10:00:00+08:00",
                    },
                }
            )
            + "\n"
        )
    completed = eod_capture.parse_completed_dialogue(source, source_host="home")
    assert completed["completed_prefixes"][0] == legacy_prefix
    assert "capture mixed-history" in completed["completed_prefixes"][-1]["transcript"]
    assert "unfinished modern" in completed["completed_prefixes"][-1]["transcript"]
    assert source in eod_capture.discover_session_paths(
        run_date="2026-09-05", codex_root=root
    )
    assert source in eod_capture.discover_session_paths(
        run_date="2026-09-10", codex_root=root
    )
    assert legacy_artifact.exists() and legacy_receipt.exists()
    assert eod_capture.capture_status(
        vault, since="2026-09-05", through="2026-09-05", codex_root=root
    )["sessions"][0]["status"] == "reconciled"


def test_generated_legacy_slice_pair_remains_reusable_after_modern_marker(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "legacy-slice-transition")
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    calls = 0

    def extract_fact(_payload: str, _source: Path) -> dict:
        nonlocal calls
        calls += 1
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "legacy slice transition",
                    "predicate": "status",
                    "object": "reusable",
                    "confidence": "high",
                    "evidence_quote": "capture legacy-slice-transition",
                    "speaker_source": "keith",
                    "dedupe_key": "fact:legacy-slice-transition:status",
                }
            ]
        }

    [artifact] = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=extract_fact,
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    receipt = vault / ".staging" / "reconciled" / artifact.name
    before = (artifact.read_bytes(), receipt.read_bytes())
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "modern-1"},
                }
            )
            + "\n"
        )

    replay = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=extract_fact,
    )
    status = eod_capture.capture_status(
        vault, since="2026-09-05", through="2026-09-05", codex_root=root
    )

    assert replay == []
    assert calls == 1
    assert artifact.read_bytes() == before[0]
    assert receipt.read_bytes() == before[1]
    assert status["sessions"][0]["status"] == "reconciled"


@pytest.mark.parametrize("tamper", ["extraction_bytes", "receipt_session"])
def test_legacy_transition_compatibility_rejects_a_tampered_extraction_pair(
    tmp_path: Path, tamper: str
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "legacy-pair-tamper")
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    [artifact] = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: {"items": []},
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    if tamper == "extraction_bytes":
        tampered = json.loads(artifact.read_text(encoding="utf-8"))
        tampered["reviewed_empty"] = True
        artifact.write_text(json.dumps(tampered), encoding="utf-8")
    else:
        receipt_path = vault / ".staging" / "reconciled" / artifact.name
        tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
        tampered["session_path"] = "/wrong/session.jsonl"
        receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "modern-1"},
                }
            )
            + "\n"
        )

    status = eod_capture.capture_status(
        vault, since="2026-09-05", through="2026-09-05", codex_root=root
    )

    assert status["sessions"][0]["status"] == "waiting"
    assert status["sessions"][0]["retryable"] is True


def test_precheckpoint_legacy_pair_without_cursor_survives_modern_growth(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "precheckpoint-legacy-pair")
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    [artifact] = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: {"items": []},
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    receipt_path = vault / ".staging" / "reconciled" / artifact.name
    old_only_fields = {
        "source_host",
        "host_id",
        "session_id",
        "completed_cursor",
        "completed_prefix_sha256",
        "completed_at",
        "completed_date",
        "completed_prefix_mode",
        "completed_turn_id",
        "dialogue_events",
    }
    extraction = json.loads(artifact.read_text(encoding="utf-8"))
    for field in old_only_fields:
        extraction.pop(field, None)
    artifact.write_text(json.dumps(extraction), encoding="utf-8")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for field in old_only_fields:
        receipt.pop(field, None)
    receipt["extraction_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "modern-1"},
                }
            )
            + "\n"
        )

    status = eod_capture.capture_status(
        vault, since="2026-09-05", through="2026-09-05", codex_root=root
    )

    assert status["sessions"][0]["status"] == "reconciled"
    assert status["sessions"][0]["retryable"] is False


def test_same_day_completed_append_keeps_both_artifact_receipt_pairs(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "immutable-pairs")
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    first = eod_capture.run_job_a(
        [source], vault_path=vault, run_date="2026-09-05", codex_root=root,
        extract_func=lambda _payload, _path: {"items": []},
    )[0]
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    first_receipt = vault / ".staging" / "reconciled" / first.name
    first_bytes = first.read_bytes()
    receipt_bytes = first_receipt.read_bytes()

    with source.open("a", encoding="utf-8") as handle:
        for record in (
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-2"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "second completion"}]}},
            {"type": "event_msg", "timestamp": "2026-09-05T12:00:00+08:00", "payload": {"type": "task_complete", "turn_id": "turn-2", "completed_at": "2026-09-05T12:00:00+08:00"}},
        ):
            handle.write(json.dumps(record) + "\n")
    second = eod_capture.run_job_a(
        [source], vault_path=vault, run_date="2026-09-05", codex_root=root,
        extract_func=lambda _payload, _path: {"items": []},
    )[0]
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert second != first
    assert first.read_bytes() == first_bytes
    assert first_receipt.read_bytes() == receipt_bytes
    assert second.exists()
    assert (vault / ".staging" / "reconciled" / second.name).exists()


def test_later_modern_failure_keeps_earlier_artifact_pair_and_reports_failed(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "later-failure")
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    first = eod_capture.run_job_a(
        [source], vault_path=vault, run_date="2026-09-05", codex_root=root,
        extract_func=lambda _payload, _path: {"items": []},
    )[0]
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    first_receipt = vault / ".staging" / "reconciled" / first.name
    before = (first.read_bytes(), first_receipt.read_bytes())
    with source.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-2"}}) + "\n")
        handle.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "later turn"}]}}) + "\n")
        handle.write(json.dumps({"type": "event_msg", "timestamp": "2026-09-05T12:00:00+08:00", "payload": {"type": "task_complete", "turn_id": "turn-2", "completed_at": "2026-09-05T12:00:00+08:00"}}) + "\n")
    eod_capture.run_job_a(
        [source], vault_path=vault, run_date="2026-09-05", codex_root=root,
        extract_func=lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    status = eod_capture.capture_status(
        vault, since="2026-09-05", through="2026-09-05", codex_root=root
    )

    assert first.read_bytes() == before[0]
    assert first_receipt.read_bytes() == before[1]
    assert status["sessions"][0]["status"] == "failed"


@pytest.mark.parametrize(
    ("outcome", "directory"),
    [
        ("failure", "extraction-failures"),
        ("skip", "extraction-skips"),
        ("quarantine", "eod-quarantine"),
    ],
)
def test_modern_diagnostic_writers_keep_the_attempted_cursor(
    tmp_path: Path, outcome: str, directory: str
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", f"attempt-{outcome}")
    with source.open("a", encoding="utf-8") as handle:
        for record in (
            {
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-2"},
            },
            {
                "type": "response_item",
                "payload": {
                    "id": "turn-2-user",
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "second completion"}],
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-09-05T12:00:00+08:00",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "turn-2",
                    "completed_at": "2026-09-05T12:00:00+08:00",
                },
            },
        ):
            handle.write(json.dumps(record) + "\n")
    prefixes = eod_capture.parse_completed_dialogue(
        source, source_host="home"
    )["completed_prefixes"]
    attempted = prefixes[0]
    assert attempted["cursor"] != prefixes[-1]["cursor"]
    if outcome == "failure":
        extract = lambda *_args: (_ for _ in ()).throw(RuntimeError("offline"))
    elif outcome == "skip":
        extract = lambda *_args: {
            "items": [
                {
                    "kind": "fact",
                    "subject": "bad",
                    "predicate": "status",
                    "object": "bad",
                    "confidence": "high",
                    "dedupe_key": "fact:bad:status",
                    "evidence_quote": "not in the attempted transcript",
                    "speaker_source": "keith",
                    "evidence_event_id": "event:missing",
                }
            ]
        }
    else:
        extract = lambda *_args: {"items": [{"kind": "unsupported"}]}
    vault = tmp_path / "vault"
    _minimal_vault(vault)

    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        completed_cursor=attempted["cursor"],
        extract_func=extract,
    )

    [diagnostic_path] = (vault / ".staging" / directory).glob("*.json")
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["completed_cursor"] == attempted["cursor"]


def test_explicit_legacy_failure_is_cleared_by_success_for_the_same_cursor(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "legacy-cursor-diagnostic")
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "modern-1"},
                }
            )
            + "\n"
        )
    parsed = eod_capture.parse_completed_dialogue(source, source_host="home")
    legacy_prefix = parsed["completed_prefixes"][0]
    assert legacy_prefix["mode"] == "legacy_whole_source"
    vault = tmp_path / "vault"
    _minimal_vault(vault)

    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    [failure] = (vault / ".staging" / "extraction-failures").glob("*.json")
    failure_data = json.loads(failure.read_text(encoding="utf-8"))
    assert failure_data["completed_cursor"] == legacy_prefix["cursor"]

    [artifact] = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: {"items": []},
    )

    assert "__cursor-" in artifact.name
    assert list((vault / ".staging" / "extraction-failures").glob("*.json")) == []


def test_mixed_schema_quarantine_keeps_cursor_selected_before_concurrent_completion(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "mixed-schema-race")
    attempted = eod_capture.parse_completed_dialogue(
        source, source_host="home"
    )["completed_prefixes"][0]
    user_event = attempted["events"][0]

    def append_then_extract(_payload: str, path: Path) -> dict:
        with path.open("a", encoding="utf-8") as handle:
            for record in (
                {
                    "type": "event_msg",
                    "payload": {"type": "task_started", "turn_id": "turn-2"},
                },
                {
                    "type": "response_item",
                    "payload": {
                        "id": "turn-2-user",
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "concurrent completion"}
                        ],
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-09-05T12:00:00+08:00",
                    "payload": {
                        "type": "task_complete",
                        "turn_id": "turn-2",
                        "completed_at": "2026-09-05T12:00:00+08:00",
                    },
                },
            ):
                handle.write(json.dumps(record) + "\n")
        return {
            "items": [
                {
                    "kind": "fact",
                    "subject": "capture",
                    "predicate": "status",
                    "object": "valid survivor",
                    "confidence": "high",
                    "evidence_quote": user_event["text"],
                    "speaker_source": "keith",
                    "evidence_event_id": user_event["event_id"],
                    "dedupe_key": "fact:capture:status",
                },
                {"kind": "unsupported"},
            ]
        }

    vault = tmp_path / "vault"
    _minimal_vault(vault)
    written = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=append_then_extract,
    )
    latest = eod_capture.parse_completed_dialogue(
        source, source_host="home"
    )["completed_prefixes"][-1]
    [quarantine_path] = (vault / ".staging" / "eod-quarantine").glob("*.json")
    quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))

    assert len(written) == 1
    assert latest["cursor"] != attempted["cursor"]
    assert quarantine["completed_cursor"] == attempted["cursor"]


def test_modern_skip_and_complete_malformed_record_are_truthful_status(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    skip_source = _modern_session(root, "2026-09-05", "modern-skip")
    malformed = _modern_session(root, "2026-09-05", "modern-malformed")
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    eod_capture.run_job_a(
        [skip_source], vault_path=vault, run_date="2026-09-05", codex_root=root,
        extract_func=lambda payload, _path: {
            "items": [{
                "kind": "fact", "subject": "bad", "predicate": "status",
                "object": "bad", "confidence": "high", "dedupe_key": "fact:bad:status",
                "evidence_quote": "not in the transcript", "speaker_source": "keith",
                "evidence_event_id": "event:missing",
            }]
        },
    )
    with malformed.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"response_item",BROKEN}\n')

    status = eod_capture.capture_status(
        vault, since="2026-09-05", through="2026-09-05", codex_root=root
    )
    by_path = {item["session_path"]: item for item in status["sessions"]}
    assert by_path[str(skip_source)]["status"] == "skipped"
    assert by_path[str(skip_source)]["retryable"] is True
    assert by_path[str(malformed)]["status"] == "failed"
    assert by_path[str(malformed)]["error"] == "source_parse_error"


def test_malformed_late_activity_is_visible_on_its_later_mtime_date(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "malformed-later-date")
    with source.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"response_item",BROKEN}\n')
    late = datetime.fromisoformat("2026-09-06T10:00:00+08:00").timestamp()
    os.utime(source, (late, late))
    vault = tmp_path / "vault"
    _minimal_vault(vault)

    status = eod_capture.capture_status(
        vault, since="2026-09-06", through="2026-09-06", codex_root=root
    )

    assert status["counts"]["discovered"] == 1
    assert status["sessions"][0]["status"] == "failed"
    assert status["sessions"][0]["error"] == "source_parse_error"


def test_reconciled_completed_prefix_survives_incomplete_append(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "stable-prefix")
    records = [json.loads(line) for line in source.read_text().splitlines()]
    records[0]["payload"].update(
        id="stable-prefix-session",
        session_id="stable-prefix-session",
        thread_source="user",
        source="vscode",
    )
    records.insert(
        1,
        {
            "type": "event_msg",
            "ordinal": 1,
            "payload": {"type": "task_started", "turn_id": "turn-1"},
        },
    )
    records[-1]["ordinal"] = 2
    records.append(
        {
            "type": "event_msg",
            "ordinal": 3,
            "timestamp": "2026-09-05T10:00:00+08:00",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-1",
                "completed_at": "2026-09-05T10:00:00+08:00",
            },
        }
    )
    source.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    calls = 0

    def empty_extract(_payload: str, _source: Path) -> dict:
        nonlocal calls
        calls += 1
        return {"schema_version": "1.0", "items": []}

    written = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        extract_func=empty_extract,
        codex_root=root,
    )
    extraction = json.loads(written[0].read_text())
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    receipt = json.loads(
        next((vault / ".staging" / "reconciled").glob("*.json")).read_text()
    )

    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "ordinal": 4,
                    "payload": {"type": "task_started", "turn_id": "turn-2"},
                }
            )
            + "\n"
        )
        handle.write('{"type":"response_item","payload":')

    replay = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        extract_func=empty_extract,
        codex_root=root,
    )
    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=root,
    )

    assert replay == []
    assert calls == 1
    assert receipt["completed_cursor"] == extraction["completed_cursor"]
    assert status["sessions"][0]["status"] == "reconciled"


@pytest.mark.parametrize("project,accepted", [("K2B", True), ("K2Bi", True), ("K2B-other", False), ("Service Motion", False)])
def test_discovery_scopes_codex_worktrees(tmp_path: Path, project: str, accepted: bool) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "worktree")
    source.write_text(source.read_text().replace(
        "/Users/keithmbpm2/Projects/K2B",
        f"/Users/keithmbpm2/.codex/worktrees/f8a5/{project}",
    ))
    found = eod_capture.discover_session_paths(run_date="2026-09-05", codex_root=root)
    assert found == ([source] if accepted else [])


def test_discovery_uses_hkt_day_not_utc_directory(tmp_path: Path) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-04", "midnight")
    source.write_text(source.read_text().replace("2026-09-04T09:00:00+08:00", "2026-09-04T17:00:00Z"))
    assert eod_capture.discover_session_paths(run_date="2026-09-05", codex_root=root) == [source]


def test_discovery_rejects_outside_symlink_before_reading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True)
    outside = _session(tmp_path / "outside", "2026-09-05", "external")
    link = root / "2026" / "09" / "05" / "rollout-linked.jsonl"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    original_scope = eod_capture._is_k2b_scope

    def guarded_scope(path: Path) -> bool:
        assert path != outside.resolve(), "outside symlink target was opened for scope detection"
        return original_scope(path)

    monkeypatch.setattr(eod_capture, "_is_k2b_scope", guarded_scope)

    assert eod_capture.discover_session_paths(
        run_date="2026-09-05", codex_root=root
    ) == []
    assert eod_capture.discover_session_paths_between(
        since="2026-09-05", through="2026-09-05", codex_root=root
    ) == {"2026-09-05": []}


def test_discovery_rejects_date_ranges_over_ten_years(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="10-year safety bound"):
        eod_capture.discover_session_paths_between(
            since="2016-09-01",
            through="2026-09-10",
            codex_root=tmp_path / ".codex" / "sessions",
        )


@pytest.mark.parametrize("role", ["home", "sjm-source-only"])
def test_discover_command_only_writes_local_status(tmp_path, monkeypatch, role):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "queued")
    original = source.read_bytes()
    vault = tmp_path / "vault"
    state = tmp_path / "state" / "capture-status.json"
    monkeypatch.setenv("K2B_CAPTURE_STATUS_FILE", str(state))
    def forbidden(*args, **kwargs):
        pytest.fail("discovery must never invoke extraction or reconciliation")
    monkeypatch.setattr(eod_capture, "run_job_a", forbidden)
    monkeypatch.setattr(eod_capture, "reconcile_extractions", forbidden)
    rc = eod_capture.main(["discover", "--since", "2026-09-05", "--through", "2026-09-06", "--vault", str(vault), "--writer-role", role])
    assert rc == 0
    status = json.loads(state.read_text())
    assert status["counts"]["waiting"] == 1
    assert status["capture_mode"] == "interactive"
    assert status["writer_role"] == role
    assert status["automatic_extraction"] == "disabled"
    assert source.read_bytes() == original
    assert not vault.exists()
    assert state.stat().st_mode & 0o077 == 0


def test_stage_reviewed_binds_manual_extraction_to_unchanged_source(tmp_path, monkeypatch):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "interactive")
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({
        "raw_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "transcript_sha256": hashlib.sha256(eod_capture.strip_transcript(source).encode()).hexdigest(),
        "items": [],
        "reviewed_empty": True,
    }))
    def forbidden(*args, **kwargs):
        pytest.fail("reviewed capture must not invoke provider")
    monkeypatch.setattr(eod_capture, "call_kimi_extractor", forbidden)
    args = ["stage-reviewed", "--date", "2026-09-05", "--vault", str(vault), "--session", str(source), "--reviewed-json", str(reviewed)]
    assert eod_capture.main(args) == 0
    assert len(list((vault / ".staging" / "extractions").glob("*.json"))) == 1
    assert eod_capture.main(args) == 0
    assert len(list((vault / ".staging" / "extractions").glob("*.json"))) == 1
    assert eod_capture.reconcile_extractions(
        vault, run_date="2026-09-05"
    )["reconciled_files"] == 1
    receipt = json.loads(
        next((vault / ".staging" / "reconciled").glob("*.json")).read_text()
    )
    assert receipt["status"] == "reviewed_empty"
    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=root,
    )
    assert status["sessions"][0]["status"] == "skipped"
    assert status["sessions"][0]["retryable"] is False
    source.write_text(source.read_text() + "\n")
    assert eod_capture.main(args) == 2


def test_export_and_stage_reviewed_remote_bundle_is_scoped_private_and_provider_free(tmp_path, monkeypatch):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "remote")
    with source.open("a") as handle:
        handle.write(json.dumps({"type": "response_item", "payload": {"item": {
            "type": "function_call_output", "output": "tool-secret-should-not-travel"}}}) + "\n")
        handle.write(json.dumps({"type": "response_item", "payload": {"item": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Decision confirmed for counterpart recall; API_KEY=fixture-secret-value"}]}}}) + "\n")
    bundle_path = tmp_path / "private" / "sjm-bundle.json"
    assert eod_capture.main(["export-source", "--source-host", "sjm", "--session", str(source), "--codex-root", str(root), "--output", str(bundle_path)]) == 0
    bundle = json.loads(bundle_path.read_text())
    assert bundle["source_host"] == "sjm"
    assert bundle["session_path"] == str(source)
    assert bundle["raw_source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert bundle["transcript_sha256"] == hashlib.sha256(bundle["transcript"].encode()).hexdigest()
    assert "Decision confirmed for counterpart recall" in bundle["transcript"]
    assert "fixture-secret-value" not in bundle["transcript"]
    assert "tool-secret-should-not-travel" not in bundle["transcript"]
    assert bundle_path.stat().st_mode & 0o077 == 0

    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({
        "raw_source_sha256": bundle["raw_source_sha256"],
        "transcript_sha256": bundle["transcript_sha256"],
        "items": [{
            "kind": "fact", "confidence": "high", "subject": "K2B capture",
            "predicate": "uses", "object": "counterpart recall",
                "scope": "K2B", "canonical_home": "wiki/context/shelves/semantic.md",
            "dedupe_key": "fact:k2b-capture:counterpart-recall",
            "evidence_quote": "Decision confirmed for counterpart recall",
            "speaker_source": "assistant_confirmed",
        }],
    }))
    monkeypatch.setenv("K2B_CAPTURE_WRITER_ROLE", "home")
    monkeypatch.setattr(eod_capture, "call_kimi_extractor", lambda *_a, **_k: pytest.fail("provider invoked"))
    original_discover = eod_capture.discover_session_paths
    monkeypatch.setattr(
        eod_capture,
        "discover_session_paths",
        lambda **_kwargs: pytest.fail("bundle staging performed live discovery"),
    )
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    args = ["stage-reviewed", "--date", "2026-09-05", "--vault", str(vault), "--source-bundle", str(bundle_path), "--reviewed-json", str(reviewed)]
    assert eod_capture.main(args) == 0
    extraction = json.loads(next((vault / ".staging/extractions").glob("*.json")).read_text())
    assert extraction["source_host"] == "sjm"
    assert extraction["session_path"] == str(source)
    corrected = json.loads(reviewed.read_text())
    corrected["items"][0]["object"] = "counterpart recall after manual correction"
    reviewed.write_text(json.dumps(corrected))
    assert eod_capture.main(args) == 0
    extractions = [
        json.loads(path.read_text())
        for path in (vault / ".staging/extractions").glob("*.json")
    ]
    assert len(extractions) == 2
    assert any(
        extraction["items"][0]["object"]
        == "counterpart recall after manual correction"
        for extraction in extractions
    )
    assert all(extraction["run_date"] == "2026-09-05" for extraction in extractions)
    monkeypatch.setattr(eod_capture, "discover_session_paths", original_discover)
    assert eod_capture.reconcile_extractions(vault, run_date="2026-09-05")["reconciled_files"] == 2
    receipts = [
        json.loads(path.read_text())
        for path in (vault / ".staging/reconciled").glob("*.json")
    ]
    assert len(receipts) == 2
    assert all(receipt["source_host"] == "sjm" for receipt in receipts)


def test_stage_reviewed_remote_bundle_rejects_hash_or_date_drift(tmp_path):
    bundle = tmp_path / "bundle.json"
    payload = "[user]\nA sufficiently long source statement"
    base = {
        "bundle_schema_version": 1, "source_host": "sjm", "source_app": "codex_desktop",
        "session_path": "/Users/keithcheung/.codex/sessions/2026/09/05/rollout-test.jsonl",
        "run_date": "2026-09-05", "raw_source_sha256": "a" * 64,
        "transcript": payload, "transcript_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({"raw_source_sha256": "a" * 64, "transcript_sha256": base["transcript_sha256"], "items": []}))
    for mutate in (lambda x: x.update(transcript_sha256="b" * 64), lambda x: x.update(run_date="2026-09-04")):
        candidate = dict(base)
        mutate(candidate)
        bundle.write_text(json.dumps(candidate))
        vault = tmp_path / candidate["run_date"]
        assert eod_capture.main(["stage-reviewed", "--date", "2026-09-05", "--vault", str(vault), "--source-bundle", str(bundle), "--reviewed-json", str(reviewed)]) == 2
        assert not vault.exists()


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("completed_cursor", "cursor:" + "f" * 64),
        ("host_id", "host:home"),
        ("session_id", "wrong-session"),
        ("completed_prefix_sha256", "f" * 64),
        ("completed_prefix_mode", "legacy_whole_source"),
        ("completed_date", "2026-09-04"),
    ],
)
def test_modern_transport_rejects_reviewed_provenance_mismatch(
    tmp_path: Path, field: str, bad_value: str
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "bundle-binding")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    reviewed = {
        "raw_source_sha256": bundle["raw_source_sha256"],
        "transcript_sha256": bundle["transcript_sha256"],
        "items": [],
        "reviewed_empty": True,
        field: bad_value,
    }

    with pytest.raises(ValueError, match=field):
        eod_capture.stage_reviewed_bundle(
            bundle,
            reviewed,
            vault_path=tmp_path / "vault",
            run_date="2026-09-05",
        )


@pytest.mark.parametrize(
    "case",
    [
        "missing_mode",
        "null_mode",
        "unknown_mode",
        "null_completed_at",
        "malformed_completed_at",
        "date_only_completed_at",
        "naive_completed_at",
        "timestamp_date_mismatch",
        "empty_session_id",
        "missing_event_id",
        "unexpected_event_field",
        "invalid_event_role",
        "empty_event_text",
        "missing_event_turn_id",
        "completed_turn_without_event",
    ],
)
def test_modern_transport_rejects_malformed_completed_prefix_provenance(
    tmp_path: Path, case: str
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", f"malformed-{case}")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    if case == "missing_mode":
        bundle.pop("completed_prefix_mode")
    elif case == "null_mode":
        bundle["completed_prefix_mode"] = None
    elif case == "unknown_mode":
        bundle["completed_prefix_mode"] = "future_mode"
    elif case == "null_completed_at":
        bundle["completed_at"] = None
    elif case == "malformed_completed_at":
        bundle["completed_at"] = "not-a-timestamp"
    elif case == "date_only_completed_at":
        bundle["completed_at"] = "2026-09-05"
    elif case == "naive_completed_at":
        bundle["completed_at"] = "2026-09-05T10:00:00"
    elif case == "timestamp_date_mismatch":
        bundle["completed_at"] = "2026-09-06T10:00:00+08:00"
    elif case == "empty_session_id":
        bundle["session_id"] = ""
    elif case == "missing_event_id":
        bundle["dialogue_events"][0].pop("event_id")
    elif case == "unexpected_event_field":
        bundle["dialogue_events"][0]["unexpected"] = True
    elif case == "invalid_event_role":
        bundle["dialogue_events"][0]["role"] = "tool"
    elif case == "empty_event_text":
        bundle["dialogue_events"][0]["text"] = ""
    elif case == "missing_event_turn_id":
        bundle["dialogue_events"][0].pop("turn_id")
    else:
        bundle["completed_turn_id"] = "turn-never-observed"
    _rebind_modern_bundle(bundle)
    reviewed = {
        "raw_source_sha256": bundle["raw_source_sha256"],
        "transcript_sha256": bundle["transcript_sha256"],
        "items": [],
        "reviewed_empty": True,
    }

    with pytest.raises(ValueError, match="source bundle"):
        eod_capture.stage_reviewed_bundle(
            bundle,
            reviewed,
            vault_path=tmp_path / "vault",
            run_date="2026-09-05",
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda bundle: bundle.update(chunks=None),
        lambda bundle: bundle.update(chunks=[]),
        lambda bundle: bundle.update(chunks=[None]),
        lambda bundle: bundle["chunks"][0].update(index=True),
        lambda bundle: bundle["chunks"][0].update(text="different text"),
        lambda bundle: bundle["chunks"][0].update(chunk_id="chunk:" + "f" * 64),
        lambda bundle: bundle["chunks"][0].update(unexpected=True),
    ],
    ids=(
        "null-container",
        "empty-container",
        "non-object-chunk",
        "boolean-index",
        "wrong-text",
        "wrong-id",
        "unexpected-field",
    ),
)
def test_modern_transport_rejects_chunks_not_bound_to_the_transcript(
    tmp_path: Path, mutate
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "chunk-binding")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    mutate(bundle)
    reviewed = {
        "raw_source_sha256": bundle["raw_source_sha256"],
        "transcript_sha256": bundle["transcript_sha256"],
        "items": [],
        "reviewed_empty": True,
    }

    with pytest.raises(ValueError, match="chunks"):
        eod_capture.stage_reviewed_bundle(
            bundle,
            reviewed,
            vault_path=tmp_path / "vault",
            run_date="2026-09-05",
        )


def test_stage_reviewed_bundle_rejects_existing_artifact_conflict(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "staged-conflict")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    reviewed = {
        "raw_source_sha256": bundle["raw_source_sha256"],
        "transcript_sha256": bundle["transcript_sha256"],
        "items": [],
        "reviewed_empty": True,
    }
    vault = tmp_path / "vault"
    first = eod_capture.stage_reviewed_bundle(
        bundle, reviewed, vault_path=vault, run_date="2026-09-05"
    )
    corrupted = json.loads(first.read_text(encoding="utf-8"))
    corrupted["completed_prefix_sha256"] = "f" * 64
    first.write_text(json.dumps(corrupted), encoding="utf-8")
    before = first.read_bytes()

    with pytest.raises(RuntimeError, match="conflicts"):
        eod_capture.stage_reviewed_bundle(
            bundle, reviewed, vault_path=vault, run_date="2026-09-05"
        )

    assert first.read_bytes() == before


def test_stage_reviewed_bundle_ignores_export_timestamp_only_on_replay(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "staged-timestamp-replay")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    reviewed = {
        "raw_source_sha256": bundle["raw_source_sha256"],
        "transcript_sha256": bundle["transcript_sha256"],
        "items": [],
        "reviewed_empty": True,
        "exported_at": "2026-09-05T10:00:00+00:00",
    }
    vault = tmp_path / "vault"
    first = eod_capture.stage_reviewed_bundle(
        bundle, reviewed, vault_path=vault, run_date="2026-09-05"
    )
    before = first.read_bytes()

    replay = eod_capture.stage_reviewed_bundle(
        bundle,
        {**reviewed, "exported_at": "2026-09-05T10:01:00+00:00"},
        vault_path=vault,
        run_date="2026-09-05",
    )

    assert replay == first
    assert replay.read_bytes() == before


def test_modern_transport_review_revisions_are_immutable_and_replayable(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "review-revisions")
    bundle = eod_capture.build_source_bundle(source, source_host="sjm", codex_root=root)
    user_event = bundle["dialogue_events"][0]
    base_item = {
        "kind": "fact", "subject": "capture", "predicate": "status",
        "object": "first review", "confidence": "high",
        "evidence_quote": user_event["text"], "speaker_source": "keith",
        "evidence_event_id": user_event["event_id"],
        "dedupe_key": "fact:capture:status",
    }
    common = {
        "raw_source_sha256": bundle["raw_source_sha256"],
        "transcript_sha256": bundle["transcript_sha256"],
    }
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    first = eod_capture.stage_reviewed_bundle(
        bundle, {**common, "items": [base_item]}, vault_path=vault,
        run_date="2026-09-05",
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    first_receipt = vault / ".staging" / "reconciled" / first.name
    before = (first.read_bytes(), first_receipt.read_bytes())
    corrected_item = {**base_item, "object": "corrected review"}
    second = eod_capture.stage_reviewed_bundle(
        bundle, {**common, "items": [corrected_item]}, vault_path=vault,
        run_date="2026-09-05",
    )
    replay = eod_capture.stage_reviewed_bundle(
        bundle, {**common, "items": [corrected_item]}, vault_path=vault,
        run_date="2026-09-05",
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert second != first
    assert replay == second
    assert first.read_bytes() == before[0]
    assert first_receipt.read_bytes() == before[1]
    assert (vault / ".staging" / "reconciled" / second.name).exists()


def test_unreconciled_review_revision_is_not_masked_by_older_receipt(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "revision-status")
    bundle = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=root
    )
    common = {
        "raw_source_sha256": bundle["raw_source_sha256"],
        "transcript_sha256": bundle["transcript_sha256"],
    }
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    first = eod_capture.stage_reviewed_bundle(
        bundle,
        {**common, "items": [], "reviewed_empty": True},
        vault_path=vault,
        run_date="2026-09-05",
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    user_event = bundle["dialogue_events"][0]
    second = eod_capture.stage_reviewed_bundle(
        bundle,
        {
            **common,
            "items": [
                {
                    "kind": "fact",
                    "subject": "capture",
                    "predicate": "status",
                    "object": "corrected",
                    "confidence": "high",
                    "evidence_quote": user_event["text"],
                    "speaker_source": "keith",
                    "evidence_event_id": user_event["event_id"],
                    "dedupe_key": "fact:capture:status",
                }
            ],
        },
        vault_path=vault,
        run_date="2026-09-05",
    )
    first_data = json.loads(first.read_text(encoding="utf-8"))
    second_data = json.loads(second.read_text(encoding="utf-8"))

    status = eod_capture.capture_status(
        vault, since="2026-09-05", through="2026-09-05", codex_root=root
    )

    assert first_data["review_revision"] != second_data["review_revision"]
    assert status["sessions"][0]["status"] == "waiting"
    assert status["sessions"][0]["retryable"] is True


def test_local_review_revision_is_not_skipped_by_prior_cache(tmp_path: Path) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "local-revision-cache")
    prefix = eod_capture.parse_completed_dialogue(
        source, source_host="home"
    )["completed_prefixes"][0]
    user_event = prefix["events"][0]
    reviewed = tmp_path / "reviewed.json"
    common = {
        "raw_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "transcript_sha256": prefix["prefix_sha256"],
        "completed_cursor": prefix["cursor"],
        "completed_prefix_sha256": prefix["prefix_sha256"],
        "completed_prefix_mode": "turn",
        "completed_date": "2026-09-05",
        "host_id": "host:home",
        "session_id": "session-local-revision-cache",
    }
    item = {
        "kind": "fact",
        "subject": "capture",
        "predicate": "status",
        "object": "first",
        "confidence": "high",
        "evidence_quote": user_event["text"],
        "speaker_source": "keith",
        "evidence_event_id": user_event["event_id"],
        "dedupe_key": "fact:capture:status",
    }
    reviewed.write_text(json.dumps({**common, "items": [item]}), encoding="utf-8")
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    args = [
        "stage-reviewed",
        "--date",
        "2026-09-05",
        "--vault",
        str(vault),
        "--session",
        str(source),
        "--codex-root",
        str(root),
        "--reviewed-json",
        str(reviewed),
    ]
    assert eod_capture.main(args) == 0
    corrected = {**item, "object": "corrected"}
    reviewed.write_text(
        json.dumps({**common, "items": [corrected]}), encoding="utf-8"
    )

    assert eod_capture.main(args) == 0

    extractions = sorted((vault / ".staging" / "extractions").glob("*.json"))
    assert len(extractions) == 2
    revisions = {
        json.loads(path.read_text(encoding="utf-8"))["review_revision"]
        for path in extractions
    }
    assert len(revisions) == 2


def test_local_review_selects_historical_modern_cursor_after_later_growth(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "historical-review")
    first_prefix = eod_capture.parse_completed_dialogue(
        source, source_host="home"
    )["completed_prefixes"][0]
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(
        json.dumps(
            {
                "raw_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "transcript_sha256": first_prefix["prefix_sha256"],
                "completed_cursor": first_prefix["cursor"],
                "completed_prefix_sha256": first_prefix["prefix_sha256"],
                "completed_prefix_mode": "turn",
                "completed_date": "2026-09-05",
                "host_id": "host:home",
                "session_id": "session-historical-review",
                "items": [],
                "reviewed_empty": True,
            }
        ),
        encoding="utf-8",
    )
    with source.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-2"}}) + "\n")
        handle.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "later completed turn"}]}}) + "\n")
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    args = [
        "stage-reviewed", "--date", "2026-09-05", "--vault", str(vault),
        "--session", str(source), "--codex-root", str(root),
        "--reviewed-json", str(reviewed),
    ]
    assert eod_capture.main(args) == 0

    with source.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "event_msg", "timestamp": "2026-09-06T12:00:00+08:00", "payload": {"type": "task_complete", "turn_id": "turn-2", "completed_at": "2026-09-06T12:00:00+08:00"}}) + "\n")

    rc = eod_capture.main(args)

    assert rc == 0
    staged = json.loads(next((vault / ".staging" / "extractions").glob("*.json")).read_text())
    assert staged["completed_cursor"] == first_prefix["cursor"]


def test_source_only_host_cannot_reconcile(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("K2B_CAPTURE_WRITER_ROLE", "sjm-source-only")
    with pytest.raises(PermissionError, match="home"):
        eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    with pytest.raises(PermissionError, match="home"):
        eod_capture.run_job_a([], vault_path=vault, run_date="2026-09-05")
    assert not vault.exists()


def test_stage_reviewed_command_rejects_non_home_writer(tmp_path, monkeypatch):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "source-only-review")
    payload = eod_capture.strip_transcript(source)
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(
        json.dumps(
            {
                "raw_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "transcript_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "items": [],
            }
        )
    )
    vault = tmp_path / "vault"
    monkeypatch.setenv("K2B_CAPTURE_WRITER_ROLE", "sjm-source-only")

    rc = eod_capture.main(
        [
            "stage-reviewed",
            "--date",
            "2026-09-05",
            "--vault",
            str(vault),
            "--session",
            str(source),
            "--codex-root",
            str(root),
            "--reviewed-json",
            str(reviewed),
        ]
    )

    assert rc == 2
    assert not vault.exists()


def test_reviewed_memory_adapter_sets_reviewed_state_only_after_source_validation(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "memory-adapter")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    reviewed = _reviewed_memory_item(bundle, value="Use the Hengqin office.")
    reviewed["items"][0]["evidence_quote"] = "A caller-asserted quote not in source"

    with pytest.raises(ValueError, match="invalid or ungrounded"):
        eod_capture.queue_reviewed_memory(
            bundle,
            reviewed,
            state_root=tmp_path / "sjm-state",
            run_date="2026-09-05",
            writer_role="sjm-source-only",
        )

    assert not (tmp_path / "sjm-state" / "outbox").exists()
    assert bundle["provenance_status"] == "known_user"
    assert bundle["thread_source"] == "user"


def test_reviewed_empty_memory_is_explicit_without_claiming_queue_success(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "reviewed-empty-memory")
    bundle = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=root
    )
    reviewed = _reviewed_memory_item(bundle, value="unused")
    reviewed["items"] = []
    reviewed["reviewed_empty"] = True

    result = eod_capture.queue_reviewed_memory(
        bundle,
        reviewed,
        state_root=tmp_path / "home-state",
        run_date="2026-09-05",
        writer_role="home",
    )

    assert result == {
        "status": "reviewed_empty",
        "input_items": 0,
        "queued_items": 0,
        "filtered_items": 0,
        "filter_reasons": {},
        "deliveries": [],
    }
    assert not (tmp_path / "home-state" / "outbox").exists()


@pytest.mark.parametrize(
    ("kind", "confidence", "reason"),
    [
        ("fact", "medium", "confidence_not_high"),
        ("learning", "high", "unsupported_memory_kind"),
    ],
)
def test_all_filtered_reviewed_memory_is_an_explicit_nonzero_cli_outcome(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    kind: str,
    confidence: str,
    reason: str,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", f"filtered-{kind}-{confidence}")
    bundle = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=root
    )
    reviewed = _reviewed_memory_item(bundle, value="Do not lose this item.")
    reviewed["items"][0]["kind"] = kind
    reviewed["items"][0]["confidence"] = confidence
    bundle_path = tmp_path / "bundle.json"
    reviewed_path = tmp_path / "reviewed.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    reviewed_path.write_text(json.dumps(reviewed), encoding="utf-8")

    rc = eod_capture.main(
        [
            "memory-queue-reviewed",
            "--date",
            "2026-09-05",
            "--source-bundle",
            str(bundle_path),
            "--reviewed-json",
            str(reviewed_path),
            "--state-root",
            str(tmp_path / "home-state"),
            "--writer-role",
            "home",
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert rc == 3
    assert result == {
        "status": "not_queued",
        "input_items": 1,
        "queued_items": 0,
        "filtered_items": 1,
        "filter_reasons": {reason: 1},
        "deliveries": [],
    }
    assert not (tmp_path / "home-state" / "outbox").exists()


def test_partially_filtered_reviewed_memory_reports_queued_and_filtered_counts(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "partially-filtered-memory")
    bundle = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=root
    )
    reviewed = _reviewed_memory_item(bundle, value="Keep the high item.")
    filtered_item = dict(reviewed["items"][0])
    filtered_item.update(
        {
            "object": "Do not silently discard the medium item.",
            "confidence": "medium",
            "dedupe_key": "fact:k2b-memory:medium-item",
        }
    )
    reviewed["items"].append(filtered_item)

    result = eod_capture.queue_reviewed_memory(
        bundle,
        reviewed,
        state_root=tmp_path / "home-state",
        run_date="2026-09-05",
        writer_role="home",
    )

    assert result["status"] == "queued_with_filtered_items"
    assert result["input_items"] == 2
    assert result["queued_items"] == 1
    assert result["filtered_items"] == 1
    assert result["filter_reasons"] == {"confidence_not_high": 1}
    assert len(result["deliveries"]) == 1


def test_public_memory_flow_survives_offline_acceptance_and_replay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "public-flow")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    reviewed = _reviewed_memory_item(bundle, value="Use the Hengqin office.")
    bundle_path = tmp_path / "bundle.json"
    reviewed_path = tmp_path / "reviewed.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    reviewed_path.write_text(json.dumps(reviewed), encoding="utf-8")
    sjm_state = tmp_path / "sjm-state"
    home_state = tmp_path / "home-state"

    rc = eod_capture.main(
        [
            "memory-queue-reviewed",
            "--date",
            "2026-09-05",
            "--source-bundle",
            str(bundle_path),
            "--reviewed-json",
            str(reviewed_path),
            "--state-root",
            str(sjm_state),
            "--writer-role",
            "sjm-source-only",
        ]
    )
    queued = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert queued["status"] == "queued"
    assert len(queued["deliveries"]) == 1
    delivery_id = queued["deliveries"][0]["delivery_id"]

    def offline(_envelope: dict) -> dict:
        raise ConnectionError("synthetic Home offline")

    first = eod_capture.deliver_memory_outbox(
        sjm_state, offline, now="2026-09-12T07:00:00+00:00"
    )
    assert first == {
        "accepted": 0,
        "failed": 0,
        "offline": 1,
        "exhausted": 0,
        "local_ack_failed": 0,
        "local_state_failed": 0,
    }
    assert not home_state.exists()

    delivered = eod_capture.deliver_memory_outbox(
        sjm_state,
        lambda envelope: eod_capture.accept_memory_envelope(
            home_state, envelope, writer_role="home"
        ),
        now="2026-09-12T07:01:00+00:00",
    )
    assert delivered["accepted"] == 1
    envelope_path = sjm_state / "outbox" / f"{delivery_id}.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    replay = eod_capture.accept_memory_envelope(
        home_state, envelope, writer_role="home"
    )
    assert replay["duplicate"] is True

    first_reconcile = eod_capture.reconcile_memory_delivery(
        home_state, delivery_id, writer_role="home"
    )
    replay_reconcile = eod_capture.reconcile_memory_delivery(
        home_state, delivery_id, writer_role="home"
    )
    assert first_reconcile["status"] == "reconciled"
    assert replay_reconcile["status"] == "reconciled"
    assert replay_reconcile["duplicate"] is True

    key = queued["deliveries"][0]["keys"][0]
    assert eod_capture.main(
        [
            "memory-recall",
            "--state-root",
            str(home_state),
            "--writer-role",
            "home",
            "--key",
            key,
        ]
    ) == 0
    recalled = json.loads(capsys.readouterr().out)
    assert recalled["status"] == "current"
    assert recalled["value"] == "Use the Hengqin office."
    assert recalled["citation"]["event_id"].startswith("event:")

    assert eod_capture.main(
        [
            "memory-status",
            "--state-root",
            str(sjm_state),
            "--writer-role",
            "sjm-source-only",
        ]
    ) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["automatic_runner"] == "unverified"
    assert status["production_activation"] == "disabled"
    assert status["outbox"]["counts"]["accepted"] == 1


def test_delivery_acknowledgement_requires_exact_shape_and_source_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "ack-matrix")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    reviewed = _reviewed_memory_item(bundle, value="Use the Hengqin office.")
    template = eod_capture.queue_reviewed_memory(
        bundle,
        reviewed,
        state_root=tmp_path / "template",
        run_date="2026-09-05",
        writer_role="sjm-source-only",
    )["deliveries"][0]
    valid = {
        "status": "accepted_not_reconciled",
        "delivery_id": template["delivery_id"],
        "content_id": template["content_id"],
        "duplicate": False,
    }
    malformed = [
        None,
        True,
        7,
        "accepted_not_reconciled",
        [],
        {},
        {"status": "accepted_not_reconciled"},
        {**valid, "delivery_id": "delivery:" + "0" * 64},
        {**valid, "content_id": "bundle:" + "0" * 64},
        {**valid, "duplicate": "false"},
        {**valid, "extra": "unsupported"},
    ]

    for index, receipt in enumerate(malformed):
        state = tmp_path / f"invalid-ack-{index}"
        eod_capture.queue_reviewed_memory(
            bundle,
            reviewed,
            state_root=state,
            run_date="2026-09-05",
            writer_role="sjm-source-only",
        )
        result = eod_capture.deliver_memory_outbox(
            state, lambda _envelope, value=receipt: value
        )
        assert result == {
            "accepted": 0,
            "failed": 1,
            "offline": 0,
            "exhausted": 0,
            "local_ack_failed": 0,
            "local_state_failed": 0,
        }
        status = eod_capture.memory_status(
            state, writer_role="sjm-source-only"
        )
        assert status["outbox"]["counts"]["failed"] == 1


def test_home_acceptance_local_ack_failure_is_not_reported_as_home_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "local-ack-failure")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    state = tmp_path / "sjm-state"
    queued = eod_capture.queue_reviewed_memory(
        bundle,
        _reviewed_memory_item(bundle, value="Use the Hengqin office."),
        state_root=state,
        run_date="2026-09-05",
        writer_role="sjm-source-only",
    )["deliveries"][0]
    home = tmp_path / "home-state"
    original = automatic_memory.record_delivery_attempt

    def fail_local_ack(*args: object, **kwargs: object) -> dict:
        if kwargs.get("outcome") == "accepted":
            raise OSError("synthetic local acknowledgement disk failure")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(automatic_memory, "record_delivery_attempt", fail_local_ack)
    result = eod_capture.deliver_memory_outbox(
        state,
        lambda envelope: eod_capture.accept_memory_envelope(
            home, envelope, writer_role="home"
        ),
    )

    assert result == {
        "accepted": 0,
        "failed": 0,
        "offline": 0,
        "exhausted": 0,
        "local_ack_failed": 1,
        "local_state_failed": 0,
    }
    persisted = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
    assert persisted["status"] == "pending"
    assert persisted["attempt_count"] == 0
    assert len(list((home / "acceptance").glob("*.json"))) == 1


def test_transport_failure_local_state_failure_is_surfaced_without_false_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "local-state-failure")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    state = tmp_path / "sjm-state"
    queued = eod_capture.queue_reviewed_memory(
        bundle,
        _reviewed_memory_item(bundle, value="Use the Hengqin office."),
        state_root=state,
        run_date="2026-09-05",
        writer_role="sjm-source-only",
    )["deliveries"][0]

    monkeypatch.setattr(
        automatic_memory,
        "record_delivery_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("synthetic local retry-state disk failure")
        ),
    )
    result = eod_capture.deliver_memory_outbox(
        state,
        lambda _envelope: (_ for _ in ()).throw(ConnectionError("Home offline")),
    )

    assert result == {
        "accepted": 0,
        "failed": 0,
        "offline": 0,
        "exhausted": 0,
        "local_ack_failed": 0,
        "local_state_failed": 1,
    }
    persisted = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
    assert persisted["status"] == "pending"
    assert persisted["attempt_count"] == 0


@pytest.mark.parametrize("concurrent_status", ["accepted", "exhausted"])
def test_concurrent_terminal_delivery_state_is_reported_without_batch_crash(
    tmp_path: Path, concurrent_status: str
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", f"concurrent-{concurrent_status}")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    state = tmp_path / "sjm-state"
    queued = eod_capture.queue_reviewed_memory(
        bundle,
        _reviewed_memory_item(bundle, value="Use the Hengqin office."),
        state_root=state,
        run_date="2026-09-05",
        writer_role="sjm-source-only",
        max_attempts=1,
    )["deliveries"][0]

    def concurrent_change(_envelope: dict) -> dict:
        if concurrent_status == "accepted":
            automatic_memory.record_delivery_attempt(
                state, queued["delivery_id"], outcome="accepted"
            )
        else:
            automatic_memory.record_delivery_attempt(
                state,
                queued["delivery_id"],
                outcome="offline",
                error_code="home_unreachable",
            )
        raise ConnectionError("synthetic losing delivery call")

    result = eod_capture.deliver_memory_outbox(state, concurrent_change)

    assert result[concurrent_status] == 1
    assert result["local_state_failed"] == 0
    persisted = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
    assert persisted["status"] == concurrent_status


def test_public_memory_reconciliation_recovers_after_apply_before_receipt_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "reconcile-crash")
    bundle = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=root
    )
    home_root = tmp_path / "home-state"
    queued = eod_capture.queue_reviewed_memory(
        bundle,
        _reviewed_memory_item(bundle, value="Use the Hengqin office."),
        state_root=tmp_path / "home-outbox",
        run_date="2026-09-05",
        writer_role="home",
    )["deliveries"][0]
    envelope = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
    eod_capture.accept_memory_envelope(home_root, envelope, writer_role="home")

    original_write = eod_capture._atomic_write_json
    crashed = False

    def crash_before_receipt(path: Path, data: dict) -> None:
        nonlocal crashed
        if path.parent.name == "reconciliation" and not crashed:
            crashed = True
            raise OSError("synthetic crash after memory apply")
        original_write(path, data)

    monkeypatch.setattr(eod_capture, "_atomic_write_json", crash_before_receipt)
    with pytest.raises(OSError, match="synthetic crash"):
        eod_capture.reconcile_memory_delivery(
            home_root, queued["delivery_id"], writer_role="home"
        )
    assert (home_root / "memory.json").exists()
    assert not (
        home_root / "reconciliation" / f"{queued['delivery_id']}.json"
    ).exists()

    recovered = eod_capture.reconcile_memory_delivery(
        home_root, queued["delivery_id"], writer_role="home"
    )
    assert recovered["status"] == "reconciled"
    assert recovered["applied"] == 0
    assert recovered["duplicates"] == 1


def test_reconciliation_receipt_binds_target_and_repairs_deleted_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "target-binding")
    bundle = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=root
    )
    home_root = tmp_path / "home-state"
    queued = eod_capture.queue_reviewed_memory(
        bundle,
        _reviewed_memory_item(bundle, value="Use the Hengqin office."),
        state_root=tmp_path / "home-outbox",
        run_date="2026-09-05",
        writer_role="home",
    )["deliveries"][0]
    envelope = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
    eod_capture.accept_memory_envelope(home_root, envelope, writer_role="home")
    target = home_root / "memory.json"
    eod_capture.reconcile_memory_delivery(
        home_root, queued["delivery_id"], writer_role="home", memory_path=target
    )
    target.unlink()

    repaired = eod_capture.reconcile_memory_delivery(
        home_root, queued["delivery_id"], writer_role="home", memory_path=target
    )
    assert repaired["duplicate"] is True
    assert repaired["repair_applied"] == 1
    assert eod_capture.recall_memory(
        home_root, queued["keys"][0], writer_role="home", memory_path=target
    )["value"] == "Use the Hengqin office."

    other = tmp_path / "different-memory.json"
    with pytest.raises(ValueError, match="different memory target"):
        eod_capture.reconcile_memory_delivery(
            home_root,
            queued["delivery_id"],
            writer_role="home",
            memory_path=other,
        )
    assert not other.exists()


def test_reconciliation_replay_rejects_malformed_bound_memory_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "malformed-target")
    bundle = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=root
    )
    home_root = tmp_path / "home-state"
    queued = eod_capture.queue_reviewed_memory(
        bundle,
        _reviewed_memory_item(bundle, value="Use the Hengqin office."),
        state_root=tmp_path / "home-outbox",
        run_date="2026-09-05",
        writer_role="home",
    )["deliveries"][0]
    envelope = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
    eod_capture.accept_memory_envelope(home_root, envelope, writer_role="home")
    target = home_root / "memory.json"
    eod_capture.reconcile_memory_delivery(
        home_root, queued["delivery_id"], writer_role="home", memory_path=target
    )
    target.write_text('{"schema_version":1,"records":[]}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="records must be an object"):
        eod_capture.reconcile_memory_delivery(
            home_root, queued["delivery_id"], writer_role="home", memory_path=target
        )
    assert target.read_text(encoding="utf-8") == '{"schema_version":1,"records":[]}'


def test_public_memory_replay_rejects_receipt_not_bound_to_home_inbox(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "receipt-binding")
    bundle = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=root
    )
    home_root = tmp_path / "home-state"
    queued = eod_capture.queue_reviewed_memory(
        bundle,
        _reviewed_memory_item(bundle, value="Use the Hengqin office."),
        state_root=tmp_path / "home-outbox",
        run_date="2026-09-05",
        writer_role="home",
    )["deliveries"][0]
    envelope = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
    eod_capture.accept_memory_envelope(home_root, envelope, writer_role="home")
    receipt_path = (
        home_root / "reconciliation" / f"{queued['delivery_id']}.json"
    )
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "delivery_id": queued["delivery_id"],
                "content_id": "bundle:" + "0" * 64,
                "status": "reconciled",
                "applied": 1,
                "duplicates": 0,
                "keys": queued["keys"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="receipt is malformed"):
        eod_capture.reconcile_memory_delivery(
            home_root, queued["delivery_id"], writer_role="home"
        )
    assert not (home_root / "memory.json").exists()


def test_sjm_read_only_recall_matrix_never_writes_shared_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    old_source = _modern_session(root, "2026-09-05", "readonly-old")
    new_source = _modern_session(root, "2026-09-06", "readonly-new")
    shared = tmp_path / "synthetic-read-only-vault"
    shared.mkdir()
    shared_memory = shared / "automatic-memory-current.json"

    def durable_state(source: Path, value: str, run_date: str, suffix: str) -> str:
        bundle = eod_capture.build_source_bundle(
            source, source_host="home", codex_root=root
        )
        state = tmp_path / f"home-{suffix}"
        queued = eod_capture.queue_reviewed_memory(
            bundle,
            _reviewed_memory_item(bundle, value=value),
            state_root=tmp_path / f"outbox-{suffix}",
            run_date=run_date,
            writer_role="home",
        )["deliveries"][0]
        envelope = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
        eod_capture.accept_memory_envelope(state, envelope, writer_role="home")
        eod_capture.reconcile_memory_delivery(
            state, queued["delivery_id"], writer_role="home"
        )
        return (state / "memory.json").read_text(encoding="utf-8")

    old_state = durable_state(old_source, "Use the Macau office.", "2026-09-05", "old")
    new_state = durable_state(new_source, "Use the Hengqin office.", "2026-09-06", "new")
    shared_memory.write_text(old_state, encoding="utf-8")
    key = eod_capture.queue_reviewed_memory(
        eod_capture.build_source_bundle(old_source, source_host="home", codex_root=root),
        _reviewed_memory_item(
            eod_capture.build_source_bundle(old_source, source_host="home", codex_root=root),
            value="Use the Macau office.",
        ),
        state_root=tmp_path / "key-outbox",
        run_date="2026-09-05",
        writer_role="home",
    )["deliveries"][0]["keys"][0]

    before = set(shared.iterdir())
    existing = eod_capture.recall_memory(
        tmp_path / "sjm-local",
        key,
        writer_role="sjm-source-only",
        provisional_root=tmp_path / "sjm-provisional",
        memory_path=shared_memory,
    )
    assert existing["status"] == "current"
    assert set(shared.iterdir()) == before

    missing_path = shared / "missing.json"
    missing = eod_capture.recall_memory(
        tmp_path / "sjm-local",
        key,
        writer_role="sjm-source-only",
        provisional_root=tmp_path / "sjm-provisional",
        memory_path=missing_path,
    )
    assert missing["status"] == "missing"
    assert set(shared.iterdir()) == before

    malformed_path = shared / "malformed.json"
    malformed_path.write_text("{not-json", encoding="utf-8")
    malformed_before = set(shared.iterdir())
    with pytest.raises(RuntimeError, match="unreadable or malformed"):
        eod_capture.recall_memory(
            tmp_path / "sjm-local",
            key,
            writer_role="sjm-source-only",
            provisional_root=tmp_path / "sjm-provisional",
            memory_path=malformed_path,
        )
    assert set(shared.iterdir()) == malformed_before

    replacement = tmp_path / "replacement.json"
    replacement.write_text(new_state, encoding="utf-8")
    original_read_text = Path.read_text
    replaced = False

    def replace_before_read(path: Path, *args: object, **kwargs: object) -> str:
        nonlocal replaced
        if path == shared_memory and not replaced:
            replaced = True
            os.replace(replacement, shared_memory)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", replace_before_read)
    replaced_result = eod_capture.recall_memory(
        tmp_path / "sjm-local",
        key,
        writer_role="sjm-source-only",
        provisional_root=tmp_path / "sjm-provisional",
        memory_path=shared_memory,
    )
    assert replaced_result["value"] == "Use the Hengqin office."
    assert not (shared / ".automatic-memory-current.json.lock").exists()


def test_public_memory_recall_orders_home_and_provisional_by_source_time(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    old_source = _modern_session(root, "2026-09-05", "old-sjm")
    new_source = _modern_session(root, "2026-09-07", "new-home")
    old_bundle = eod_capture.build_source_bundle(
        old_source, source_host="sjm", codex_root=root
    )
    new_bundle = eod_capture.build_source_bundle(
        new_source, source_host="home", codex_root=root
    )
    old_state = tmp_path / "old-sjm-state"
    new_state = tmp_path / "new-home-outbox"
    home_state = tmp_path / "home-state"
    old = eod_capture.queue_reviewed_memory(
        old_bundle,
        _reviewed_memory_item(old_bundle, value="Use the Macau office."),
        state_root=old_state,
        run_date="2026-09-05",
        writer_role="sjm-source-only",
    )["deliveries"][0]
    new = eod_capture.queue_reviewed_memory(
        new_bundle,
        _reviewed_memory_item(new_bundle, value="Use the Hengqin office."),
        state_root=new_state,
        run_date="2026-09-07",
        writer_role="home",
    )["deliveries"][0]

    new_envelope = json.loads(Path(new["path"]).read_text(encoding="utf-8"))
    eod_capture.accept_memory_envelope(home_state, new_envelope, writer_role="home")
    eod_capture.reconcile_memory_delivery(
        home_state, new["delivery_id"], writer_role="home"
    )
    before_old_delivery = eod_capture.recall_memory(
        home_state,
        new["keys"][0],
        writer_role="sjm-source-only",
        provisional_root=old_state,
    )
    assert before_old_delivery["status"] == "current"
    assert before_old_delivery["value"] == "Use the Hengqin office."

    old_envelope = json.loads(Path(old["path"]).read_text(encoding="utf-8"))
    eod_capture.accept_memory_envelope(home_state, old_envelope, writer_role="home")
    eod_capture.reconcile_memory_delivery(
        home_state, old["delivery_id"], writer_role="home"
    )
    after_old_delivery = eod_capture.recall_memory(
        home_state, new["keys"][0], writer_role="home"
    )
    assert after_old_delivery["status"] == "current"
    assert after_old_delivery["value"] == "Use the Hengqin office."
    assert after_old_delivery["history"][0]["value"] == "Use the Macau office."

    latest_source = _modern_session(root, "2026-09-09", "latest-sjm")
    latest_bundle = eod_capture.build_source_bundle(
        latest_source, source_host="sjm", codex_root=root
    )
    latest_state = tmp_path / "latest-sjm-state"
    eod_capture.queue_reviewed_memory(
        latest_bundle,
        _reviewed_memory_item(latest_bundle, value="Use the Cotai office."),
        state_root=latest_state,
        run_date="2026-09-09",
        writer_role="sjm-source-only",
    )
    provisional = eod_capture.recall_memory(
        home_state,
        new["keys"][0],
        writer_role="sjm-source-only",
        provisional_root=latest_state,
    )
    assert provisional["status"] == "pending_home"
    assert provisional["value"] == "Use the Cotai office."
    assert provisional["home_current"]["value"] == "Use the Hengqin office."


def test_twenty_seeded_reviewed_items_recall_with_citations_from_shared_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "twenty-seed")
    bundle = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=root
    )
    user_event = next(
        event for event in bundle["dialogue_events"] if event["role"] == "user"
    )
    kinds = ("fact", "decision", "preference", "commitment")
    reviewed = _reviewed_memory_item(bundle, value="placeholder")
    reviewed["items"] = [
        {
            "kind": kinds[index % len(kinds)],
            "confidence": "high",
            "subject": f"Seed {index + 1}",
            "predicate": "current_value",
            "object": f"Seed value {index + 1}",
            "scope": "K2B",
            "canonical_home": "wiki/context/shelves/semantic.md",
            "dedupe_key": f"{kinds[index % len(kinds)]}:stage1-seed:item-{index + 1}",
            "evidence_quote": user_event["text"],
            "speaker_source": "keith",
            "evidence_event_id": user_event["event_id"],
        }
        for index in range(20)
    ]
    home_root = tmp_path / "home-state"
    queued = eod_capture.queue_reviewed_memory(
        bundle,
        reviewed,
        state_root=tmp_path / "home-outbox",
        run_date="2026-09-05",
        writer_role="home",
    )["deliveries"][0]
    envelope = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
    eod_capture.accept_memory_envelope(home_root, envelope, writer_role="home")
    reconciled = eod_capture.reconcile_memory_delivery(
        home_root, queued["delivery_id"], writer_role="home"
    )
    assert reconciled["applied"] == 20

    writes: dict[str, str] = {}
    eod_capture.publish_shared_recall(
        home_root,
        writer_role="home",
        write_func=lambda relative_path, content: writes.__setitem__(
            relative_path, content
        ),
    )
    shared_state = tmp_path / "shared-memory.json"
    shared_state.write_text(
        writes["System/memory/automatic-memory-current.json"], encoding="utf-8"
    )
    results = [
        eod_capture.recall_memory(
            tmp_path / "sjm-state",
            key,
            writer_role="sjm-source-only",
            provisional_root=tmp_path / "empty-provisional",
            memory_path=shared_state,
        )
        for key in queued["keys"]
    ]
    assert len(results) == 20
    assert {result["kind"] for result in results} == set(kinds)
    assert all(result["status"] == "current" for result in results)
    assert all(result["citation"]["event_id"] == user_event["event_id"] for result in results)
    assert {result["value"] for result in results} == {
        f"Seed value {index}" for index in range(1, 21)
    }


def test_shared_publication_json_and_markdown_use_one_validated_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "home-state"
    old_source = _modern_session(
        tmp_path / "old-sessions", "2026-09-05", "old-publication"
    )
    old_bundle = eod_capture.build_source_bundle(
        old_source, source_host="home", codex_root=tmp_path / "old-sessions"
    )
    old_queued = eod_capture.queue_reviewed_memory(
        old_bundle,
        _reviewed_memory_item(old_bundle, value="Old office"),
        state_root=tmp_path / "old-outbox",
        run_date="2026-09-05",
        writer_role="home",
    )["deliveries"][0]
    old = json.loads(Path(old_queued["path"]).read_text(encoding="utf-8"))
    automatic_memory.apply_reviewed_envelope(state / "memory.json", old)
    new_source = _modern_session(
        tmp_path / "new-sessions", "2026-09-10", "new-publication"
    )
    new_bundle = eod_capture.build_source_bundle(
        new_source, source_host="home", codex_root=tmp_path / "new-sessions"
    )
    new_queued = eod_capture.queue_reviewed_memory(
        new_bundle,
        _reviewed_memory_item(new_bundle, value="New office"),
        state_root=tmp_path / "new-outbox",
        run_date="2026-09-10",
        writer_role="home",
    )["deliveries"][0]
    newer = json.loads(Path(new_queued["path"]).read_text(encoding="utf-8"))
    original_snapshot = automatic_memory.memory_snapshot

    def replace_after_snapshot(path: Path) -> dict:
        snapshot = original_snapshot(path)
        automatic_memory.apply_reviewed_envelope(path, newer)
        return snapshot

    monkeypatch.setattr(automatic_memory, "memory_snapshot", replace_after_snapshot)
    writes: dict[str, str] = {}

    eod_capture.publish_shared_recall(
        state,
        writer_role="home",
        write_func=lambda relative_path, content: writes.__setitem__(
            relative_path, content
        ),
    )

    published_json = writes["System/memory/automatic-memory-current.json"]
    published_markdown = writes["wiki/context/context_automatic-memory-recall.md"]
    assert "Old office" in published_json
    assert "Old office" in published_markdown
    assert "New office" not in published_json
    assert "New office" not in published_markdown


def test_memory_worklist_is_bounded_stable_and_excludes_worker_sources(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    first = _modern_session(root, "2026-09-05", "worklist-a")
    _modern_session(root, "2026-09-05", "worklist-b")
    worker = _modern_session(root, "2026-09-05", "worklist-worker")
    worker_rows = [json.loads(line) for line in worker.read_text().splitlines()]
    worker_rows[0]["payload"]["thread_source"] = "worker"
    worker.write_text(
        "\n".join(json.dumps(row) for row in worker_rows) + "\n",
        encoding="utf-8",
    )
    state = tmp_path / "memory-state"

    first_batch = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
    )
    replay = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
    )
    full = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=100,
    )

    assert first_batch["status"] == "ready"
    assert first_batch["selected"] == 1
    assert replay["items"] == first_batch["items"]
    assert full["selected"] == 2
    assert all("worker" not in item["session_path"] for item in full["items"])
    item = first_batch["items"][0]
    assert item["session_path"] == str(first)
    assert item["work_id"].startswith("work:")
    assert Path(item["source_bundle_path"]).is_file()
    assert Path(item["work_record_path"]).is_file()


def test_memory_worklist_selects_each_completed_prefix_inside_requested_range(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "historical-prefix")
    with source.open("a", encoding="utf-8") as handle:
        for row in (
            {
                "type": "event_msg",
                "ordinal": 4,
                "timestamp": "2026-09-10T09:00:00+08:00",
                "payload": {"type": "task_started", "turn_id": "turn-2"},
            },
            {
                "type": "response_item",
                "ordinal": 5,
                "timestamp": "2026-09-10T09:01:00+08:00",
                "payload": {
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "later correction outside range"}
                        ],
                    }
                },
            },
            {
                "type": "event_msg",
                "ordinal": 6,
                "timestamp": "2026-09-10T09:02:00+08:00",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "turn-2",
                    "completed_at": "2026-09-10T09:02:00+08:00",
                },
            },
        ):
            handle.write(json.dumps(row) + "\n")

    result = eod_capture.build_memory_worklist(
        state_root=tmp_path / "state",
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=10,
    )

    assert result["selected"] == 1
    bundle = json.loads(
        Path(result["items"][0]["source_bundle_path"]).read_text(encoding="utf-8")
    )
    assert bundle["completed_date"] == "2026-09-05"
    assert "later correction outside range" not in bundle["transcript"]


def test_record_memory_extraction_queues_once_and_receipts_exact_work(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    _modern_session(root, "2026-09-05", "record-extraction")
    state = tmp_path / "memory-state"
    work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
    )["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    reviewed = _reviewed_memory_item(bundle, value="Use native bounded digestion.")

    first = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=reviewed,
        writer_role="home",
    )
    replay = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=reviewed,
        writer_role="home",
    )

    assert first["status"] == "queued"
    assert first["duplicate"] is False
    assert replay == {**first, "duplicate": True}
    assert len(list((state / "outbox").glob("*.json"))) == 1
    assert len(list((state / "extraction-receipts").glob("*.json"))) == 1


def test_completed_memory_extraction_conflict_is_distinct_and_read_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / ".codex" / "sessions"
    _modern_session(root, "2026-09-05", "completed-conflict")
    state = tmp_path / "state"
    work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
    )["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    original = _reviewed_memory_item(bundle, value="Original completed value")
    completed = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=original,
        writer_role="home",
    )
    conflicting = _reviewed_memory_item(bundle, value="Conflicting replay value")
    reviewed_path = tmp_path / "conflicting.json"
    reviewed_path.write_text(json.dumps(conflicting), encoding="utf-8")
    before = {
        path.relative_to(state): path.read_bytes()
        for path in state.rglob("*")
        if path.is_file()
    }

    rc = eod_capture.main(
        [
            "memory-record-extraction",
            "--state-root",
            str(state),
            "--work-id",
            work["work_id"],
            "--reviewed-json",
            str(reviewed_path),
            "--writer-role",
            "home",
        ]
    )
    stderr = capsys.readouterr().err

    assert rc == 2
    assert "completed memory extraction conflicts with existing receipt" in stderr
    assert completed["receipt_path"] in stderr
    assert {
        path.relative_to(state): path.read_bytes()
        for path in state.rglob("*")
        if path.is_file()
    } == before


def test_damaged_completed_receipt_wins_over_conflicting_caller_diagnostic(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    _modern_session(root, "2026-09-05", "damaged-before-conflict")
    state = tmp_path / "state"
    work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
    )["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    original = _reviewed_memory_item(bundle, value="Original durable value")
    completed = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=original,
        writer_role="home",
    )
    receipt = json.loads(Path(completed["receipt_path"]).read_text(encoding="utf-8"))
    artifact = state / receipt["extraction_artifact_relpath"]
    damaged = json.loads(artifact.read_text(encoding="utf-8"))
    damaged["items"][0]["object"] = "Damaged durable value"
    artifact.write_text(json.dumps(damaged), encoding="utf-8")
    conflicting = _reviewed_memory_item(bundle, value="Different caller value")

    with pytest.raises(RuntimeError, match="artifact hash conflicts"):
        eod_capture.record_memory_extraction(
            state_root=state,
            work_id=work["work_id"],
            reviewed=conflicting,
            writer_role="home",
        )


def test_receipted_earlier_prefix_does_not_block_later_appended_turn(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "append-after-receipt")
    state = tmp_path / "state"
    first_work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=10,
    )["items"][0]
    first_bundle = json.loads(
        Path(first_work["source_bundle_path"]).read_text(encoding="utf-8")
    )
    recorded = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=first_work["work_id"],
        reviewed=_reviewed_memory_item(first_bundle, value="Original turn fact"),
        writer_role="home",
    )
    assert recorded["status"] == "queued"

    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            "".join(
                json.dumps(row) + "\n"
                for row in (
                    {
                        "type": "event_msg",
                        "payload": {"type": "task_started", "turn_id": "turn-2"},
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "This later completed turn must remain discoverable.",
                                }
                            ],
                        },
                    },
                    {
                        "type": "event_msg",
                        "timestamp": "2026-09-10T10:00:00+08:00",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": "turn-2",
                            "completed_at": "2026-09-10T10:00:00+08:00",
                        },
                    },
                )
            )
        )

    resumed = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-10",
        writer_role="home",
        limit=10,
    )

    assert resumed["skipped_receipted"] == 1
    assert resumed["selected"] == 1
    later_bundle = json.loads(
        Path(resumed["items"][0]["source_bundle_path"]).read_text(encoding="utf-8")
    )
    assert later_bundle["completed_turn_id"] == "turn-2"


def test_extraction_receipt_crash_replays_retained_artifact_before_queue_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    _modern_session(root, "2026-09-05", "artifact-before-queue")
    state = tmp_path / "state"
    work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
    )["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    original_reviewed = _reviewed_memory_item(
        bundle, value="Retain the first validated extraction."
    )
    conflicting_reviewed = _reviewed_memory_item(
        bundle,
        value="Do not enqueue this conflicting replay.",
        dedupe_key="fact:k2b-memory:conflicting-replay",
    )
    original_write = eod_capture._atomic_write_json

    def interrupt_receipt(path: Path, value: object) -> None:
        if path.parent.name == "extraction-receipts":
            raise OSError("synthetic receipt interruption")
        original_write(path, value)

    monkeypatch.setattr(eod_capture, "_atomic_write_json", interrupt_receipt)
    with pytest.raises(OSError, match="receipt interruption"):
        eod_capture.record_memory_extraction(
            state_root=state,
            work_id=work["work_id"],
            reviewed=original_reviewed,
            writer_role="home",
        )
    monkeypatch.setattr(eod_capture, "_atomic_write_json", original_write)
    assert len(list((state / "outbox").glob("*.json"))) == 1
    assert len(list((state / "extraction-artifacts").glob("*.json"))) == 1
    assert not list((state / "extraction-receipts").glob("*.json"))

    replay = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=conflicting_reviewed,
        writer_role="home",
    )

    assert replay["status"] == "queued"
    assert len(list((state / "outbox").glob("*.json"))) == 1
    retained = automatic_memory.load_outbox_envelope(
        state, replay["deliveries"][0]["delivery_id"]
    )
    assert retained["bundle"]["items"][0]["value"] == (
        "Retain the first validated extraction."
    )
    artifact = json.loads(
        next((state / "extraction-artifacts").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert artifact == original_reviewed


def test_all_filtered_extraction_retries_with_backoff_then_needs_attention(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    _modern_session(root, "2026-09-05", "bounded-filtered")
    state = tmp_path / "state"
    work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
        now="2026-09-05T10:00:00+08:00",
    )["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    reviewed = _reviewed_memory_item(bundle, value="Low-confidence fixture")
    reviewed["items"][0]["confidence"] = "medium"

    first = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=reviewed,
        writer_role="home",
        now="2026-09-05T10:00:00+08:00",
    )
    assert first["status"] == "retryable"
    assert first["attempt_count"] == 1
    assert first["retry_after_seconds"] == 60
    waiting = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
        now="2026-09-05T10:00:59+08:00",
    )
    assert waiting["selected"] == 0
    assert waiting["exception_counts"] == {
        "needs_attention": 0,
        "retry_backoff": 1,
    }

    second = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=reviewed,
        writer_role="home",
        now="2026-09-05T10:01:00+08:00",
    )
    assert second["attempt_count"] == 2
    assert second["retry_after_seconds"] == 120
    terminal = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=reviewed,
        writer_role="home",
        now="2026-09-05T10:03:00+08:00",
    )
    assert terminal["status"] == "needs_attention"
    assert terminal["attempt_count"] == 3
    assert terminal["retry_after_seconds"] is None

    final = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
        now="2026-09-06T10:00:00+08:00",
    )
    assert final["selected"] == 0
    assert final["exception_counts"] == {
        "needs_attention": 1,
        "retry_backoff": 0,
    }
    assert not list((state / "extraction-receipts").glob("*.json"))
    assert not list((state / "outbox").glob("*.json"))


def test_all_filtered_retry_can_accept_a_corrected_queueable_extraction(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    _modern_session(root, "2026-09-05", "corrected-filtered-retry")
    state = tmp_path / "state"
    work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
        now="2026-09-05T10:00:00+08:00",
    )["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    filtered = _reviewed_memory_item(bundle, value="Correctable extraction")
    filtered["items"][0]["confidence"] = "medium"
    corrected = _reviewed_memory_item(bundle, value="Corrected high-confidence fact")

    first = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=filtered,
        writer_role="home",
        now="2026-09-05T10:00:00+08:00",
    )
    assert first["status"] == "retryable"
    assert not list((state / "extraction-artifacts").glob("*.json"))

    second = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=corrected,
        writer_role="home",
        now="2026-09-05T10:01:00+08:00",
    )

    assert second["status"] == "queued"
    assert second["duplicate"] is False
    assert len(list((state / "outbox").glob("*.json"))) == 1
    [artifact] = (state / "extraction-artifacts").glob("*.json")
    assert json.loads(artifact.read_text(encoding="utf-8")) == corrected


def test_truncated_extraction_receipt_cannot_suppress_worklist_or_replay(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    _modern_session(root, "2026-09-05", "truncated-receipt")
    state = tmp_path / "state"
    work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
    )["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    reviewed = _reviewed_memory_item(bundle, value="Receipt-bound value")
    result = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=reviewed,
        writer_role="home",
    )
    Path(result["receipt_path"]).write_text(
        json.dumps(
            {
                "work_id": work["work_id"],
                "completed_prefix_sha256": bundle["completed_prefix_sha256"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="receipt"):
        eod_capture.build_memory_worklist(
            state_root=state,
            codex_root=root,
            since="2026-09-05",
            through="2026-09-05",
            writer_role="home",
            limit=1,
        )
    with pytest.raises(RuntimeError, match="receipt"):
        eod_capture.record_memory_extraction(
            state_root=state,
            work_id=work["work_id"],
            reviewed=reviewed,
            writer_role="home",
        )


def test_extraction_receipt_binds_immutable_artifact_and_durable_delivery(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    _modern_session(root, "2026-09-05", "receipt-bindings")
    state = tmp_path / "state"
    work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
    )["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    reviewed = _reviewed_memory_item(bundle, value="Immutable extraction")
    result = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=reviewed,
        writer_role="home",
    )
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    artifact = state / receipt["extraction_artifact_relpath"]
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == receipt[
        "extraction_artifact_sha256"
    ]
    delivery_path = Path(result["deliveries"][0]["path"])
    delivery_path.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="delivery"):
        eod_capture.build_memory_worklist(
            state_root=state,
            codex_root=root,
            since="2026-09-05",
            through="2026-09-05",
            writer_role="home",
            limit=1,
        )


def test_extraction_receipt_binds_artifact_content_to_reviewed_hash(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    _modern_session(root, "2026-09-05", "receipt-content-binding")
    state = tmp_path / "state"
    work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
    )["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    reviewed = _reviewed_memory_item(bundle, value="Immutable extraction")
    result = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=reviewed,
        writer_role="home",
    )
    receipt_path = Path(result["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    artifact_path = state / receipt["extraction_artifact_relpath"]
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["items"][0]["object"] = "Substituted extraction"
    eod_capture._atomic_write_json(artifact_path, artifact)
    receipt["extraction_artifact_sha256"] = hashlib.sha256(
        artifact_path.read_bytes()
    ).hexdigest()
    eod_capture._atomic_write_json(receipt_path, receipt)

    with pytest.raises(RuntimeError, match="reviewed hash"):
        eod_capture.build_memory_worklist(
            state_root=state,
            codex_root=root,
            since="2026-09-05",
            through="2026-09-05",
            writer_role="home",
            limit=1,
        )


def test_extraction_receipt_rejects_unrelated_valid_delivery_substitution(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    _modern_session(root, "2026-09-05", "receipt-delivery-binding")
    state = tmp_path / "state"
    work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
    )["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    reviewed = _reviewed_memory_item(bundle, value="Receipt-owned extraction")
    result = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=reviewed,
        writer_role="home",
    )
    unrelated_reviewed = _reviewed_memory_item(
        bundle,
        value="Different but valid extraction",
        dedupe_key="fact:k2b-memory:unrelated",
    )
    unrelated = eod_capture.queue_reviewed_memory(
        bundle,
        unrelated_reviewed,
        state_root=state,
        run_date="2026-09-05",
        writer_role="home",
    )
    receipt_path = Path(result["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["result"]["deliveries"] = unrelated["deliveries"]
    receipt["result"]["queue_result"] = unrelated
    eod_capture._atomic_write_json(receipt_path, receipt)

    with pytest.raises(RuntimeError, match="source-derived delivery"):
        eod_capture.build_memory_worklist(
            state_root=state,
            codex_root=root,
            since="2026-09-05",
            through="2026-09-05",
            writer_role="home",
            limit=1,
        )


def test_invalid_memory_extraction_is_retryable_without_success_receipt(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    _modern_session(root, "2026-09-05", "retryable-extraction")
    state = tmp_path / "memory-state"
    work = eod_capture.build_memory_worklist(
        state_root=state,
        codex_root=root,
        since="2026-09-05",
        through="2026-09-05",
        writer_role="home",
        limit=1,
    )["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    reviewed = _reviewed_memory_item(bundle, value="Ungrounded suggestion")
    reviewed["items"][0]["evidence_quote"] = "quote absent from source"

    result = eod_capture.record_memory_extraction(
        state_root=state,
        work_id=work["work_id"],
        reviewed=reviewed,
        writer_role="home",
    )

    assert result["status"] == "retryable"
    assert result["reason"] == "invalid_reviewed_extraction"
    assert not list((state / "extraction-receipts").glob("*.json"))
    assert len(list((state / "extraction-diagnostics").glob("*.json"))) == 1
    assert not list((state / "outbox").glob("*.json"))


def test_sjm_delivery_commands_list_export_and_record_exact_ack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "transport-commands")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    state = tmp_path / "sjm-state"
    queued = eod_capture.queue_reviewed_memory(
        bundle,
        _reviewed_memory_item(bundle, value="Transport this source."),
        state_root=state,
        run_date="2026-09-05",
        writer_role="sjm-source-only",
    )["deliveries"][0]

    assert eod_capture.main(
        [
            "memory-list-deliverable",
            "--state-root",
            str(state),
            "--writer-role",
            "sjm-source-only",
            "--limit",
            "1",
        ]
    ) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["deliveries"][0]["delivery_id"] == queued["delivery_id"]

    assert eod_capture.main(
        [
            "memory-export-envelope",
            "--state-root",
            str(state),
            "--writer-role",
            "sjm-source-only",
            "--delivery-id",
            queued["delivery_id"],
        ]
    ) == 0
    envelope = json.loads(capsys.readouterr().out)
    home = tmp_path / "home-state"
    ack = eod_capture.accept_memory_envelope(home, envelope, writer_role="home")
    ack_path = tmp_path / "ack.json"
    ack_path.write_text(json.dumps(ack), encoding="utf-8")

    assert eod_capture.main(
        [
            "memory-record-ack",
            "--state-root",
            str(state),
            "--writer-role",
            "sjm-source-only",
            "--ack-json",
            str(ack_path),
        ]
    ) == 0
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["status"] == "accepted"
    assert recorded["delivery_id"] == queued["delivery_id"]


def _fake_sjm_transport(state: Path):
    def run(args: list[str], input_payload: object | None = None) -> object:
        command = args[0]
        if command == "memory-list-deliverable":
            limit = int(args[args.index("--limit") + 1])
            now = args[args.index("--now") + 1] if "--now" in args else None
            return eod_capture.list_memory_deliverables(
                state,
                writer_role="sjm-source-only",
                limit=limit,
                now=now,
            )
        if command == "memory-export-envelope":
            delivery_id = args[args.index("--delivery-id") + 1]
            return eod_capture.export_memory_envelope(
                state,
                delivery_id,
                writer_role="sjm-source-only",
                now=(args[args.index("--now") + 1] if "--now" in args else None),
            )
        if command == "memory-record-ack":
            return eod_capture.record_memory_acknowledgement(
                state,
                input_payload,
                writer_role="sjm-source-only",
                now=(args[args.index("--now") + 1] if "--now" in args else None),
            )
        raise AssertionError(f"unexpected transport command: {command}")

    return run


def test_injected_sjm_pull_recovers_after_ack_transport_interruption(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "pull-replay")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    sjm_state = tmp_path / "sjm-state"
    queued = eod_capture.queue_reviewed_memory(
        bundle,
        _reviewed_memory_item(bundle, value="Replay this acknowledgement."),
        state_root=sjm_state,
        run_date="2026-09-05",
        writer_role="sjm-source-only",
    )["deliveries"][0]
    base_transport = _fake_sjm_transport(sjm_state)
    interrupted = False

    def flaky_transport(args: list[str], input_payload: object | None = None) -> object:
        nonlocal interrupted
        if args[0] == "memory-record-ack" and not interrupted:
            interrupted = True
            raise ConnectionError("synthetic acknowledgement interruption")
        return base_transport(args, input_payload)

    home = tmp_path / "home-state"
    first = eod_capture.pull_sjm_memory(
        home,
        writer_role="home",
        transport_func=flaky_transport,
        limit=10,
    )
    second = eod_capture.pull_sjm_memory(
        home,
        writer_role="home",
        transport_func=base_transport,
        limit=10,
    )

    assert first["ack_failed"] == 1
    assert first["accepted"] == 0
    assert second["accepted"] == 1
    assert second["duplicate_acceptances"] == 1
    assert second["delivery_ids"] == [queued["delivery_id"]]
    persisted = automatic_memory.load_outbox_envelope(
        sjm_state, queued["delivery_id"]
    )
    assert persisted["status"] == "accepted"


def test_default_sjm_transport_is_fixed_quoted_noninteractive_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def run(command: list[str], **kwargs: object) -> object:
        calls.append({"command": command, **kwargs})
        return type(
            "Completed",
            (),
            {"returncode": 0, "stdout": '{"status":"idle","deliveries":[]}', "stderr": ""},
        )()

    monkeypatch.setattr(eod_capture.subprocess, "run", run)
    result = eod_capture._default_sjm_memory_transport(
        ["memory-list-deliverable", "--now", "value with spaces; false"]
    )

    assert result["status"] == "idle"
    command = calls[0]["command"]
    assert command[:7] == [
        "/usr/bin/ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "sjm-ai",
        command[6],
    ]
    assert len(command) == 7
    assert command[6].startswith('exec "$HOME/Projects/K2B/venv/washing-machine/bin/python"')
    assert '"$HOME/Projects/K2B/scripts/eod-capture.py"' in command[6]
    assert "'value with spaces; false'" in command[6]
    assert calls[0]["timeout"] == eod_capture.DEFAULT_MEMORY_TRANSPORT_TIMEOUT_SECONDS

    def oversized(_command: list[str], **_kwargs: object) -> object:
        return type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "x" * 8_000_001, "stderr": ""},
        )()

    monkeypatch.setattr(eod_capture.subprocess, "run", oversized)
    with pytest.raises(ValueError, match="output limit"):
        eod_capture._default_sjm_memory_transport(["memory-list-deliverable"])


def test_home_drain_reconciles_local_and_injected_sjm_sources_without_publish(
    tmp_path: Path,
) -> None:
    home_sessions = tmp_path / "home-sessions"
    home_source = _modern_session(home_sessions, "2026-09-05", "home-drain")
    home_bundle = eod_capture.build_source_bundle(
        home_source, source_host="home", codex_root=home_sessions
    )
    home = tmp_path / "home-state"
    eod_capture.queue_reviewed_memory(
        home_bundle,
        _reviewed_memory_item(
            home_bundle,
            value="Home current value.",
            dedupe_key="fact:k2b-memory:home-drain",
        ),
        state_root=home,
        run_date="2026-09-05",
        writer_role="home",
    )

    sjm_sessions = tmp_path / "sjm-sessions"
    sjm_source = _modern_session(sjm_sessions, "2026-09-05", "sjm-drain")
    sjm_bundle = eod_capture.build_source_bundle(
        sjm_source, source_host="sjm", codex_root=sjm_sessions
    )
    sjm_state = tmp_path / "sjm-state"
    eod_capture.queue_reviewed_memory(
        sjm_bundle,
        _reviewed_memory_item(
            sjm_bundle,
            value="SJM current value.",
            dedupe_key="fact:k2b-memory:sjm-drain",
        ),
        state_root=sjm_state,
        run_date="2026-09-05",
        writer_role="sjm-source-only",
    )

    result = eod_capture.drain_home_memory(
        home,
        writer_role="home",
        pull_sjm=True,
        transport_func=_fake_sjm_transport(sjm_state),
        limit=10,
        publish=False,
    )

    assert result["status"] == "reconciled"
    assert result["local_delivery"]["accepted"] == 1
    assert result["sjm_delivery"]["accepted"] == 1
    assert result["reconciled"] == 2
    assert result["publication"] == "disabled"
    snapshot = automatic_memory.memory_snapshot(home / "memory.json")
    assert len(snapshot["records"]) == 2


def test_home_drain_advances_past_reconciled_receipts_with_bounded_limit(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home-state"
    for index in range(2):
        sessions = tmp_path / f"sessions-{index}"
        source = _modern_session(
            sessions, "2026-09-05", f"bounded-drain-{index}"
        )
        bundle = eod_capture.build_source_bundle(
            source, source_host="home", codex_root=sessions
        )
        queued = eod_capture.queue_reviewed_memory(
            bundle,
            _reviewed_memory_item(
                bundle,
                value=f"Bounded value {index}",
                dedupe_key=f"fact:k2b-memory:bounded-{index}",
            ),
            state_root=home,
            run_date="2026-09-05",
            writer_role="home",
        )["deliveries"][0]
        envelope = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
        eod_capture.accept_memory_envelope(home, envelope, writer_role="home")
        automatic_memory.record_delivery_attempt(
            home, queued["delivery_id"], outcome="accepted"
        )

    first = eod_capture.drain_home_memory(
        home, writer_role="home", limit=1, publish=False
    )
    second = eod_capture.drain_home_memory(
        home, writer_role="home", limit=1, publish=False
    )

    assert first["reconciled"] == 1
    assert second["reconciled"] == 1
    assert len(automatic_memory.memory_snapshot(home / "memory.json")["records"]) == 2


def test_publisher_boundary_is_lazy_and_receives_exact_public_contract(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    vault = tmp_path / "vault"
    memory_path = state / "memory.json"
    calls: list[dict] = []

    def fake_publisher(**kwargs: object) -> dict:
        calls.append(kwargs)
        return {"status": "published", "duplicate": False}

    preflight = lambda _vault: {"policy_ledger_sha256": "a" * 64, "syncthing": "idle"}
    result = eod_capture.publish_memory_state(
        memory_state_path=memory_path,
        state_root=state,
        vault_root=vault,
        writer_role="home",
        preflight=preflight,
        publisher_func=fake_publisher,
    )

    assert result["status"] == "published"
    assert calls == [
        {
            "memory_state_path": memory_path,
            "state_root": state,
            "vault_root": vault,
            "writer_role": "home",
            "preflight": preflight,
        }
    ]


def test_memory_status_reports_work_extraction_and_publication_stages(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    for directory in (
        "worklist",
        "extraction-receipts",
        "extraction-diagnostics",
        "publication",
    ):
        path = state / directory
        path.mkdir(parents=True)
        (path / "one.json").write_text("{}", encoding="utf-8")

    status = eod_capture.memory_status(state, writer_role="home")

    assert status["worklist_files"] == 1
    assert status["extraction_receipt_files"] == 1
    assert status["extraction_diagnostic_files"] == 1
    assert status["publication_receipt_files"] == 1


def test_native_automatic_memory_prompt_uses_public_flow_without_publication() -> None:
    prompt = (
        ROOT / "scripts" / "prompts" / "automatic-memory-native.md"
    ).read_text(encoding="utf-8")

    for command in (
        "memory-worklist",
        "memory-record-extraction",
        "memory-home-drain",
        "--pull-sjm",
    ):
        assert command in prompt
    assert "--publish" not in prompt
    assert "cron" not in prompt.lower()


def test_memory_recall_help_makes_home_pending_recall_explicit() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "eod-capture.py"),
            "memory-recall",
            "--help",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Home recall is durable-only by default" in " ".join(
        result.stdout.split()
    )
    assert "--provisional-root" in result.stdout


def test_fresh_process_public_flow_recalls_twenty_seeded_items(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    seeded = (
        ("fact", "The fixture office color is Green."),
        ("fact", "The fixture digest window contains twenty items."),
        ("fact", "The fixture source host is the Home Mac."),
        ("fact", "The fixture recovery queue is machine-local."),
        ("fact", "The fixture recall artifact includes citations."),
        ("decision", "For fixture decision one, use bounded retries."),
        ("decision", "For fixture decision two, keep Home as the writer."),
        ("decision", "For fixture decision three, preserve source order."),
        ("decision", "For fixture decision four, exclude worker sessions."),
        ("decision", "For fixture decision five, publish only after preflight."),
        ("preference", "For fixture preference one, keep updates concise."),
        ("preference", "For fixture preference two, cite the exact event."),
        ("preference", "For fixture preference three, avoid silent fallback."),
        ("preference", "For fixture preference four, retain prior versions."),
        ("preference", "For fixture preference five, report ambiguity."),
        ("commitment", "For fixture commitment one, retry an offline delivery."),
        ("commitment", "For fixture commitment two, verify the durable receipt."),
        ("commitment", "For fixture commitment three, preserve crash replay."),
        ("commitment", "For fixture commitment four, test SJM read-only recall."),
        ("commitment", "For fixture commitment five, keep activation disabled."),
    )
    source = _modern_session(root, "2026-09-05", "fresh-process-twenty")
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    user_row = next(
        row
        for row in rows
        if row.get("type") == "response_item"
        and row.get("payload", {}).get("item", {}).get("role") == "user"
    )
    user_row["payload"]["item"]["content"][0]["text"] = "\n".join(
        value for _kind, value in seeded
    )
    source.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    state = tmp_path / "home-state"
    command = [sys.executable, str(ROOT / "scripts" / "eod-capture.py")]

    work_run = subprocess.run(
        [
            *command,
            "memory-worklist",
            "--since",
            "2026-09-05",
            "--through",
            "2026-09-05",
            "--codex-root",
            str(root),
            "--state-root",
            str(state),
            "--writer-role",
            "home",
            "--limit",
            "1",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert work_run.returncode == 0, work_run.stderr
    work = json.loads(work_run.stdout)["items"][0]
    bundle = json.loads(Path(work["source_bundle_path"]).read_text(encoding="utf-8"))
    user_event = next(
        event for event in bundle["dialogue_events"] if event["role"] == "user"
    )
    reviewed = _reviewed_memory_item(bundle, value="placeholder")
    reviewed["items"] = [
        {
            **reviewed["items"][0],
            "kind": kind,
            "subject": f"Fresh seed {index + 1}",
            "predicate": "current_value",
            "object": value,
            "dedupe_key": f"{kind}:fresh:item-{index + 1}",
            "evidence_quote": value,
            "evidence_event_id": user_event["event_id"],
        }
        for index, (kind, value) in enumerate(seeded)
    ]
    reviewed_path = tmp_path / "reviewed.json"
    reviewed_path.write_text(json.dumps(reviewed), encoding="utf-8")

    record_run = subprocess.run(
        [
            *command,
            "memory-record-extraction",
            "--state-root",
            str(state),
            "--work-id",
            work["work_id"],
            "--reviewed-json",
            str(reviewed_path),
            "--writer-role",
            "home",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert record_run.returncode == 0, record_run.stderr
    recorded = json.loads(record_run.stdout)
    assert recorded["status"] == "queued"

    drain_run = subprocess.run(
        [
            *command,
            "memory-home-drain",
            "--state-root",
            str(state),
            "--writer-role",
            "home",
            "--limit",
            "10",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert drain_run.returncode == 0, drain_run.stderr
    assert json.loads(drain_run.stdout)["reconciled"] == 1

    vault = tmp_path / "vault"
    (vault / "wiki/context").mkdir(parents=True)
    (vault / "wiki/context/index.md").write_text(
        "# Context\nLast updated: 2026-09-01 | Entries: 0\n\n"
        "## K2B System\n\n| Page | Summary | Updated |\n"
        "|------|---------|---------|\n",
        encoding="utf-8",
    )
    (vault / "wiki/index.md").write_text(
        "# Wiki\n| Folder | Purpose | Entries |\n|---|---|---|\n"
        "| [context/](context/index.md) | Context | 0 |\n\n"
        "**Total wiki pages: 0**\n",
        encoding="utf-8",
    )
    (vault / "wiki/log.md").write_text("# Log\n", encoding="utf-8")
    policy = vault / "wiki/context/policy-ledger.jsonl"
    policy.write_text(
        '{"type":"guard","scope":"*","action":"read_state",'
        '"rule":"Poll before acting."}\n',
        encoding="utf-8",
    )
    policy_hash = hashlib.sha256(policy.read_bytes()).hexdigest()
    publication = eod_capture.publish_memory_state(
        memory_state_path=state / "memory.json",
        state_root=state,
        vault_root=vault,
        writer_role="home",
        preflight=lambda _vault: {
            "policy_ledger_sha256": policy_hash,
            "syncthing": "idle",
        },
    )
    assert publication["status"] == "published"
    shared_memory = vault / "System/memory/automatic-memory-current.json"

    keys = recorded["deliveries"][0]["keys"]
    recalls = []
    for key in keys:
        recall_run = subprocess.run(
            [
                *command,
                "memory-recall",
                "--state-root",
                str(tmp_path / "sjm-state"),
                "--writer-role",
                "sjm-source-only",
                "--memory-path",
                str(shared_memory),
                "--key",
                key,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert recall_run.returncode == 0, recall_run.stderr
        recalls.append(json.loads(recall_run.stdout))
    assert len(recalls) == 20
    assert all(item["status"] == "current" for item in recalls)
    assert all(item["citation"]["event_id"] == user_event["event_id"] for item in recalls)
    assert {item["kind"] for item in recalls} == {
        "fact",
        "decision",
        "preference",
        "commitment",
    }
    assert {item["value"] for item in recalls} == {
        value for _kind, value in seeded
    }


def test_failed_memory_acceptance_and_shared_publish_remain_home_gated(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _modern_session(root, "2026-09-05", "failed-accept")
    bundle = eod_capture.build_source_bundle(
        source, source_host="sjm", codex_root=root
    )
    queued = eod_capture.queue_reviewed_memory(
        bundle,
        _reviewed_memory_item(bundle, value="Use the Hengqin office."),
        state_root=tmp_path / "sjm-state",
        run_date="2026-09-05",
        writer_role="sjm-source-only",
    )["deliveries"][0]
    envelope = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
    envelope["bundle"]["items"][0]["value"] = "tampered"
    home_state = tmp_path / "home-state"

    with pytest.raises(ValueError, match="content identity"):
        eod_capture.accept_memory_envelope(home_state, envelope, writer_role="home")
    assert not home_state.exists()

    valid_envelope = json.loads(Path(queued["path"]).read_text(encoding="utf-8"))
    eod_capture.accept_memory_envelope(home_state, valid_envelope, writer_role="home")
    eod_capture.reconcile_memory_delivery(
        home_state, queued["delivery_id"], writer_role="home"
    )
    writes: dict[str, str] = {}
    publication = eod_capture.publish_shared_recall(
        home_state,
        writer_role="home",
        write_func=lambda relative_path, content: writes.__setitem__(
            relative_path, content
        ),
    )
    assert publication["status"] == "published_by_injected_home_writer"
    assert set(writes) == {
        "System/memory/automatic-memory-current.json",
        "wiki/context/context_automatic-memory-recall.md",
    }
    assert "Use the Hengqin office." in writes[
        "wiki/context/context_automatic-memory-recall.md"
    ]
    published_state = tmp_path / "synced-automatic-memory.json"
    published_state.write_text(
        writes["System/memory/automatic-memory-current.json"], encoding="utf-8"
    )
    recalled_on_sjm = eod_capture.recall_memory(
        tmp_path / "sjm-state",
        queued["keys"][0],
        writer_role="sjm-source-only",
        provisional_root=tmp_path / "sjm-empty-outbox",
        memory_path=published_state,
    )
    assert recalled_on_sjm["status"] == "current"
    assert recalled_on_sjm["value"] == "Use the Hengqin office."
    assert recalled_on_sjm["citation"]["event_id"].startswith("event:")

    with pytest.raises(PermissionError, match="home writer"):
        eod_capture.publish_shared_recall(
            home_state,
            writer_role="sjm-source-only",
            write_func=lambda *_args: pytest.fail("SJM writer invoked"),
        )


@pytest.mark.parametrize("role", ["HOME", "home ", "future-role", "sjm-source-only"])
def test_unknown_or_source_only_writer_roles_fail_closed(tmp_path, monkeypatch, role):
    vault = tmp_path / "vault"
    monkeypatch.setenv("K2B_CAPTURE_WRITER_ROLE", role)

    with pytest.raises(PermissionError, match="home"):
        eod_capture.run_job_a([], vault_path=vault, run_date="2026-09-05")
    with pytest.raises(PermissionError, match="home"):
        eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert not vault.exists()


def test_unset_writer_role_only_allows_known_home_account(tmp_path, monkeypatch):
    monkeypatch.delenv("K2B_CAPTURE_WRITER_ROLE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "unknown-account"))
    assert eod_capture._source_only_writer() is True

    monkeypatch.setenv("HOME", str(tmp_path / "keithmbpm2"))
    assert eod_capture._source_only_writer() is False


def test_job_a_skips_source_changed_while_stripping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "changing")
    vault = tmp_path / "vault"
    stale = (
        vault
        / ".staging"
        / "extractions"
        / f"2026-09-05_{eod_capture._safe_session_id(source)}.json"
    )
    stale.parent.mkdir(parents=True)
    stale.write_text('{"stale": true}\n')
    original_parse = eod_capture.parse_completed_dialogue

    def mutate_after_read(path: Path, **kwargs) -> dict:
        parsed = original_parse(path, **kwargs)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        return parsed

    monkeypatch.setattr(eod_capture, "parse_completed_dialogue", mutate_after_read)

    written = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: pytest.fail("extractor saw a changing source"),
    )

    assert written == []
    skip = json.loads(
        next((vault / ".staging" / "extraction-skips").glob("*.json")).read_text()
    )
    assert skip["reason"] == "source_changed_during_read"
    assert not stale.exists()


def test_job_a_detects_modify_then_restore_during_strip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "restored")
    original_bytes = source.read_bytes()
    original_parse = eod_capture.parse_completed_dialogue
    vault = tmp_path / "vault"

    def modify_then_restore(path: Path, **kwargs) -> dict:
        parsed = original_parse(path, **kwargs)
        before = path.stat()
        path.write_bytes(original_bytes + b"temporary change\n")
        path.write_bytes(original_bytes)
        os.utime(
            path,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )
        return parsed

    monkeypatch.setattr(eod_capture, "parse_completed_dialogue", modify_then_restore)

    written = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: pytest.fail("extractor saw a modified source"),
    )

    assert written == []
    assert source.read_bytes() == original_bytes
    skip = json.loads(
        next((vault / ".staging" / "extraction-skips").glob("*.json")).read_text()
    )
    assert skip["reason"] == "source_changed_during_read"


def test_job_a_rejects_source_changed_while_extractor_runs(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "changes-during-extraction")
    vault = tmp_path / "vault"

    def mutate_source(_payload: str, path: Path) -> dict:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        return {"items": []}

    written = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=mutate_source,
    )

    assert written == []
    assert list((vault / ".staging" / "extractions").glob("*.json")) == []
    skip = json.loads(
        next((vault / ".staging" / "extraction-skips").glob("*.json")).read_text()
    )
    assert skip["reason"] == "source_changed_during_extraction"


def test_source_bundle_detects_modify_then_restore_during_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "bundle-restored")
    original_bytes = source.read_bytes()
    original_parse = eod_capture.parse_completed_dialogue

    def modify_then_restore(path: Path, **kwargs) -> dict:
        parsed = original_parse(path, **kwargs)
        before = path.stat()
        path.write_bytes(original_bytes + b"temporary change\n")
        path.write_bytes(original_bytes)
        os.utime(
            path,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )
        return parsed

    monkeypatch.setattr(eod_capture, "parse_completed_dialogue", modify_then_restore)

    with pytest.raises(ValueError, match="source changed"):
        eod_capture.build_source_bundle(
            source, source_host="home", codex_root=root
        )


def test_transport_redacts_github_tokens_before_and_after_truncation(tmp_path: Path) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "token-redaction")
    oauth = "gho_" + "a" * 30
    fine_grained = "github_pat_" + "b" * 30
    digest = "0123456789abcdef" * 4
    hex_secret = "abcdef0123456789" * 4
    base64_secret = "AbCd0123+EfGh4567/" * 3 + "Z9"
    boundary = "x" * 3989 + " " + fine_grained
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        f"oauth={oauth}\nsha256={digest}\n"
                                        f"WEBHOOK_SECRET={hex_secret}\n"
                                        'PASSWORD="correct horse battery staple"\n'
                                        '--client-secret "quoted option secret"\n'
                                        "Cookie: session=short secret; theme=dark\n"
                                        "X-API-Key: short-header-secret\n"
                                        f"opaque={base64_secret}\n{boundary}"
                                    ),
                                }
                            ],
                        }
                    },
                }
            )
            + "\n"
        )

    transport = eod_capture.strip_dialogue_for_transport(source)

    assert oauth not in transport
    assert fine_grained not in transport
    assert "gho_" not in transport
    assert "github_pat_" not in transport
    assert base64_secret not in transport
    assert digest in transport
    assert hex_secret not in transport
    assert "correct horse battery staple" not in transport
    assert "quoted option secret" not in transport
    assert "session=short secret" not in transport
    assert "short-header-secret" not in transport
    assert transport.count("[REDACTED]") >= 8


def test_transport_ignores_partial_jsonl_record(tmp_path: Path) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "partial")
    with source.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"response_item","payload":')

    transport = eod_capture.strip_dialogue_for_transport(source)

    assert "capture partial" in transport


def test_capture_status_lock_times_out_instead_of_hanging(tmp_path, monkeypatch):
    status_file = tmp_path / "state" / "capture-status.json"
    monkeypatch.setenv("K2B_CAPTURE_STATUS_LOCK_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setattr(
        eod_capture.fcntl,
        "flock",
        lambda *_args: (_ for _ in ()).throw(BlockingIOError()),
    )
    with pytest.raises(TimeoutError, match="remained busy"):
        with eod_capture._capture_status_lock(status_file):
            pytest.fail("busy lock must not be entered")


def test_capture_status_lock_rejects_nan_timeout(tmp_path, monkeypatch):
    status_file = tmp_path / "state" / "capture-status.json"
    monkeypatch.setenv("K2B_CAPTURE_STATUS_LOCK_TIMEOUT_SECONDS", "nan")
    with eod_capture._capture_status_lock(status_file):
        assert status_file.parent.joinpath("discovery.lock").exists()


@pytest.mark.parametrize("items", [None, "missing", {}, "not-a-list", []])
def test_stage_reviewed_rejects_incomplete_review_before_staging(tmp_path, items):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "incomplete")
    data = {
        "raw_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "transcript_sha256": hashlib.sha256(eod_capture.strip_transcript(source).encode()).hexdigest(),
    }
    if items != "missing":
        data["items"] = items
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps(data))
    vault = tmp_path / "vault"
    assert eod_capture.main(["stage-reviewed", "--date", "2026-09-05", "--vault", str(vault), "--session", str(source), "--reviewed-json", str(reviewed)]) == 2
    assert not vault.exists()


def test_stage_reviewed_local_rejects_missing_transcript_hash(tmp_path):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "missing-hash")
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({"raw_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "items": []}))
    vault = tmp_path / "vault"
    assert eod_capture.main(["stage-reviewed", "--date", "2026-09-05", "--vault", str(vault), "--session", str(source), "--reviewed-json", str(reviewed)]) == 2
    assert not vault.exists()


def test_reconcile_writes_source_receipt_only_after_clean_extraction(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "receipt")
    written = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: {"items": []},
    )

    assert len(written) == 1
    assert list((vault / ".staging").glob("reconciled/*.json")) == []

    result = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    receipts = list((vault / ".staging" / "reconciled").glob("*.json"))
    assert result["reconciled_files"] == 1
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["session_path"] == str(source)
    assert receipt["raw_source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert receipt["status"] == "reconciled"

    replay = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    assert replay["processed_files"] == 1
    assert replay["reconciled_files"] == 1
    assert replay["auto_written"] == 0
    assert replay["deduped"] == 0


def test_malformed_extraction_does_not_write_reconciled_receipt(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    extraction_dir = vault / ".staging" / "extractions"
    extraction_dir.mkdir(parents=True)
    (extraction_dir / "2026-09-05_broken.json").write_text("{broken", encoding="utf-8")

    result = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert result["errors"] == 1
    assert result["reconciled_files"] == 0
    assert list((vault / ".staging").glob("reconciled/*.json")) == []


def test_non_utf8_extraction_is_quarantined_once(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "broken")
    extraction = (
        vault
        / ".staging"
        / "extractions"
        / f"2026-09-05_{eod_capture._safe_session_id(source)}.json"
    )
    extraction.parent.mkdir(parents=True)
    raw_bytes = b"\xffnot-utf8"
    extraction.write_bytes(raw_bytes)

    first = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    second = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert first["errors"] == 1
    assert second["errors"] == 0
    assert not extraction.exists()
    metadata = json.loads(
        (vault / ".staging" / "eod-quarantine" / extraction.name).read_text()
    )
    assert metadata["reason"] == "invalid_utf8_extraction"
    assert metadata["payload_sha256"] == hashlib.sha256(raw_bytes).hexdigest()
    assert Path(metadata["payload_path"]).read_bytes() == raw_bytes
    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
    )
    assert status["sessions"][0]["status"] == "failed"
    assert status["counts"]["failed"] == 1


def test_diagnostic_extraction_artifacts_redact_nested_secrets(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "rollout-secret.jsonl"
    secret = "gho_" + "s" * 30
    quarantine = eod_capture._write_extraction_quarantine(
        vault,
        source,
        run_date="2026-09-05",
        reason="fixture",
        error="fixture",
        extractor_output={"nested": [f"token={secret}"]},
        rejections=[{"rejection_class": "content", "item": {"object": secret}}],
    )
    rejection = eod_capture._write_extraction_rejection(
        vault,
        source,
        run_date="2026-09-05",
        rejection={
            "item_index": 0,
            "rejection_class": "content",
            "error": f"Cookie: session={secret}",
            "item": {"object": secret},
        },
    )

    for artifact in (quarantine, rejection):
        text = artifact.read_text()
        assert secret not in text
        assert "[REDACTED]" in text


def test_all_extraction_writes_hold_global_reconcile_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "shared-lock")
    held: list[Path] = []
    extraction_writes = 0
    original_atomic_write = eod_capture._atomic_write_json

    @contextmanager
    def tracking_lock(path: Path):
        held.append(path)
        try:
            yield
        finally:
            held.remove(path)

    def guarded_atomic_write(path: Path, data: dict) -> None:
        nonlocal extraction_writes
        if path.parent.name == "extractions":
            extraction_writes += 1
            assert eod_capture._reconcile_lock_path(vault) in held
        original_atomic_write(path, data)

    monkeypatch.setattr(eod_capture, "_file_lock", tracking_lock)
    monkeypatch.setattr(eod_capture, "_atomic_write_json", guarded_atomic_write)

    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: {"items": []},
    )
    bundle = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=root
    )
    eod_capture.stage_reviewed_bundle(
        bundle,
        {
            "raw_source_sha256": bundle["raw_source_sha256"],
            "transcript_sha256": bundle["transcript_sha256"],
            "items": [],
            "reviewed_empty": True,
        },
        vault_path=vault,
        run_date="2026-09-05",
    )

    assert extraction_writes == 2


def test_post_move_quarantine_failure_is_per_file_and_recovery_pointer_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "valid-after-corrupt")
    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: {"items": []},
    )
    corrupt = vault / ".staging" / "extractions" / "2026-09-05_000-corrupt.json"
    raw_bytes = b"\xffinvalid"
    corrupt.write_bytes(raw_bytes)
    original_atomic_write = eod_capture._atomic_write_json
    metadata_writes = 0

    def fail_final_metadata(path: Path, data: dict) -> None:
        nonlocal metadata_writes
        if path == vault / ".staging" / "eod-quarantine" / corrupt.name:
            metadata_writes += 1
            if metadata_writes == 2:
                raise OSError("fixture final metadata failure")
        original_atomic_write(path, data)

    monkeypatch.setattr(eod_capture, "_atomic_write_json", fail_final_metadata)

    result = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert result["processed_files"] == 2
    assert result["errors"] == 1
    assert result["reconciled_files"] == 1
    metadata = json.loads(
        (vault / ".staging" / "eod-quarantine" / corrupt.name).read_text()
    )
    assert metadata["payload_move"] == "pending"
    assert Path(metadata["payload_path"]).read_bytes() == raw_bytes
    assert not corrupt.exists()


def test_receipt_and_error_review_failure_does_not_abort_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "receipt-double-failure")
    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: {"items": []},
    )
    monkeypatch.setattr(
        eod_capture,
        "_write_reconciliation_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt full")),
    )
    monkeypatch.setattr(
        eod_capture,
        "_write_error_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("review full")),
    )

    result = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert result["processed_files"] == 1
    assert result["errors"] == 1
    assert result["reconciled_files"] == 0
    assert (vault / ".staging" / "eod-capture-summary-2026-09-05.json").exists()


def test_error_reviews_with_different_secrets_do_not_overwrite(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "source.json"
    first_secret = "gho_" + "a" * 30
    second_secret = "gho_" + "b" * 30

    eod_capture._write_error_review(
        vault,
        run_date="2026-09-05",
        source_file=source,
        reason="fixture",
        payload={"token": first_secret},
    )
    eod_capture._write_error_review(
        vault,
        run_date="2026-09-05",
        source_file=source,
        reason="fixture",
        payload={"token": second_secret},
    )

    reviews = sorted((vault / "review").glob("eod-error_*.md"))
    assert len(reviews) == 2
    for review in reviews:
        text = review.read_text()
        assert first_secret not in text
        assert second_secret not in text
        assert "[REDACTED]" in text


def test_capture_status_prefers_matching_receipt_over_stale_failure(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "status")

    def fail(_payload: str, _path: Path) -> dict:
        raise RuntimeError("provider offline")

    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=fail,
    )
    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: {"items": []},
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert list((vault / ".staging" / "extraction-failures").glob("*.json")) == []

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="home",
    )

    assert status["counts"] == {
        "discovered": 1,
        "waiting": 0,
        "failed": 0,
        "skipped": 0,
        "reconciled": 1,
    }
    assert status["sessions"][0]["status"] == "reconciled"
    assert status["sessions"][0]["retryable"] is False
    assert status["last_reconciled"] == "2026-09-05"
    assert status["run_status"] == "complete"
    assert status["counts_valid"] is True


def test_capture_status_defensively_prefers_matching_failure_over_bare_extraction(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "diagnostic-precedence")
    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda *_args: {"items": []},
    )
    raw_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    eod_capture._write_extraction_failure(
        vault,
        source,
        run_date="2026-09-05",
        error=OSError("diagnostic cleanup failed"),
        raw_source_sha256=raw_hash,
        codex_root=codex_root,
    )

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
    )

    assert status["sessions"][0]["status"] == "failed"
    assert status["sessions"][0]["extracted"] is True
    assert status["sessions"][0]["retryable"] is True


def test_capture_status_rejects_receipt_when_present_extraction_changed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "changed-extraction")
    [extraction] = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: {"items": []},
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    changed = json.loads(extraction.read_text(encoding="utf-8"))
    changed["manual_change"] = True
    extraction.write_text(json.dumps(changed), encoding="utf-8")

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
    )

    assert status["sessions"][0]["status"] == "waiting"
    assert status["sessions"][0]["retryable"] is True
    assert status["last_reconciled"] is None


def test_capture_status_rejects_receipt_when_extraction_is_missing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "missing-extraction")
    [extraction] = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: {"items": []},
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    extraction.unlink()

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
    )

    assert status["sessions"][0]["status"] == "waiting"
    assert status["sessions"][0]["retryable"] is True
    assert status["last_reconciled"] is None


def test_capture_status_reports_provider_failure_as_retryable(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "retry")

    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="sjm-source-only",
    )

    assert status["counts"]["failed"] == 1
    assert status["sessions"][0]["status"] == "failed"
    assert status["sessions"][0]["retryable"] is True
    assert status["reconciliation"] == "disabled_on_source_only_host"


def test_capture_status_hashes_session_as_a_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "streamed-hash")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    def reject_read_bytes(_path: Path) -> bytes:
        raise AssertionError("capture status must not load a whole source file")

    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)
    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="sjm-source-only",
    )

    assert status["sessions"][0]["raw_source_sha256"] == expected


def test_capture_status_preserves_retryable_evidence_for_unreadable_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "unreadable")
    original_hash = eod_capture._sha256_file

    def flaky_hash(path: Path, **kwargs: object) -> str:
        if path == source:
            raise OSError("transient source rotation")
        return original_hash(path, **kwargs)

    monkeypatch.setattr(eod_capture, "_sha256_file", flaky_hash)
    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="sjm-source-only",
    )

    session = status["sessions"][0]
    assert session["status"] == "failed"
    assert session["retryable"] is True
    assert session["error"] == "source_unreadable"
    assert session["raw_source_sha256"] is None


def test_capture_status_retries_transient_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "transient-read")
    original_hash = eod_capture._sha256_file
    calls = 0

    def flaky_hash(path: Path, **kwargs: object) -> str:
        nonlocal calls
        if path == source:
            calls += 1
            if calls < 3:
                raise OSError("temporary rotation")
        return original_hash(path, **kwargs)

    monkeypatch.setattr(eod_capture, "_sha256_file", flaky_hash)

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="sjm-source-only",
    )

    assert calls == 3
    assert status["sessions"][0]["status"] == "waiting"
    assert status["counts"]["failed"] == 0


def test_run_catch_up_can_stage_on_source_only_host_without_shared_hub_write(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "source-only")
    semantic_before = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_bytes()

    status_file = tmp_path / "local-state" / "capture-status.json"
    staging_before = sorted((vault / ".staging").rglob("*"))
    extractor_calls: list[Path] = []
    result = eod_capture.run_catch_up(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, path: extractor_calls.append(path) or {"items": []},
        reconcile=False,
        status_file=status_file,
        writer_role="sjm-source-only",
    )

    assert result["dates"]["2026-09-05"]["discovered"] == 1
    assert result["dates"]["2026-09-05"]["staged"] == 0
    assert result["dates"]["2026-09-05"]["pending_local"] == 1
    assert result["dates"]["2026-09-05"]["reconciled"] is False
    assert extractor_calls == []
    assert sorted((vault / ".staging").rglob("*")) == staging_before
    assert (vault / "wiki" / "context" / "shelves" / "semantic.md").read_bytes() == semantic_before
    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="sjm-source-only",
    )
    assert status["sessions"][0]["status"] == "waiting"
    assert status["sessions"][0]["raw_source_sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    persisted = json.loads(status_file.read_text(encoding="utf-8"))
    assert persisted["counts"]["waiting"] == 1
    assert persisted["writer_role"] == "sjm-source-only"
    assert persisted["last_extracted"] is None


def test_source_only_catch_up_rejects_status_path_inside_shared_vault(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    with pytest.raises(ValueError, match="outside the shared vault"):
        eod_capture.run_catch_up(
            vault,
            since="2026-09-05",
            through="2026-09-05",
            reconcile=False,
            status_file=vault / ".staging" / "capture-status.json",
            writer_role="sjm-source-only",
        )


def test_catch_up_failure_replaces_stale_status_with_failure_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    status_file = tmp_path / "state" / "capture-status.json"
    status_file.parent.mkdir(parents=True)
    status_file.write_text('{"run_status":"complete","counts_valid":true}\n')
    monkeypatch.setattr(
        eod_capture,
        "discover_session_paths_between",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("fixture discovery failed")),
    )

    with pytest.raises(RuntimeError, match="fixture discovery failed"):
        eod_capture.run_catch_up(
            vault,
            since="2026-09-05",
            through="2026-09-05",
            reconcile=True,
            status_file=status_file,
            writer_role="home",
        )

    persisted = json.loads(status_file.read_text())
    assert persisted["run_status"] == "failed"
    assert persisted["counts_valid"] is False
    assert persisted["counts"] is None
    assert "fixture discovery failed" in persisted["error"]


def test_home_catch_up_does_not_reconcile_empty_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    codex_root.mkdir(parents=True)
    reconcile_calls: list[str] = []

    monkeypatch.setattr(
        eod_capture,
        "reconcile_extractions",
        lambda _vault, *, run_date: reconcile_calls.append(run_date) or {},
    )
    result = eod_capture.run_catch_up(
        vault,
        since="2026-09-05",
        through="2026-09-06",
        codex_root=codex_root,
        reconcile=True,
        writer_role="home",
    )

    assert reconcile_calls == []
    assert result["dates"]["2026-09-05"]["reconciled"] is False
    assert result["dates"]["2026-09-06"]["reconciled"] is False


def test_home_catch_up_does_not_report_failed_reconciliation_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    _session(codex_root, "2026-09-05", "reconcile-error")
    status_file = tmp_path / "state" / "capture-status.json"

    monkeypatch.setattr(
        eod_capture,
        "reconcile_extractions",
        lambda _vault, *, run_date: {
            "errors": 1,
            "candidate_files": 1,
            "reconciled_files": 0,
        },
    )
    result = eod_capture.run_catch_up(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: {"items": []},
        reconcile=True,
        status_file=status_file,
        writer_role="home",
    )

    day = result["dates"]["2026-09-05"]
    assert day["reconciliation_attempted"] is True
    assert day["reconciled"] is False
    assert result["status"]["run_status"] == "failed"
    assert result["status"]["reconciliation_failures"] == [
        {
            "run_date": "2026-09-05",
            "errors": 1,
            "quarantined": 0,
            "reconciled": False,
        }
    ]
    assert json.loads(status_file.read_text())["run_status"] == "failed"


def test_catch_up_main_returns_nonzero_for_reconciliation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        eod_capture,
        "run_catch_up",
        lambda *_args, **_kwargs: {
            "status": {
                "counts": {"failed": 0},
                "run_status": "failed",
            }
        },
    )

    rc = eod_capture.main(
        [
            "catch-up",
            "--since",
            "2026-09-05",
            "--through",
            "2026-09-05",
            "--vault",
            str(tmp_path / "vault"),
        ]
    )

    assert rc == 1


def test_catch_up_main_reports_lock_timeout_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        eod_capture,
        "run_catch_up",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("writer busy")),
    )

    rc = eod_capture.main(
        [
            "catch-up",
            "--since",
            "2026-09-05",
            "--through",
            "2026-09-05",
            "--vault",
            str(tmp_path / "vault"),
        ]
    )

    assert rc == 1
    assert "writer busy" in capsys.readouterr().err
