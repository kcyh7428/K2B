"""Regression coverage for compressed history and contextual acceptance."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import eod_capture as e
from codex_sources import open_session, session_paths
import codex_sources


def zstd():
    executable = shutil.which("zstd")
    if executable is None:
        pytest.skip("zstd is required for compressed-source tests")
    return executable


def test_compressed_turn_identity_and_discovery(tmp_path, monkeypatch):
    root = tmp_path / "Projects/K2B/.codex/sessions"
    root.mkdir(parents=True)
    plain = root / "rollout-test.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": "session-fixed", "source": "cli", "cwd": "/Users/test/Projects/K2B"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
        {"type": "response_item", "payload": {"id": "u1", "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Remember the project decision."}]}},
        {"type": "event_msg", "timestamp": "2026-10-30T08:00:00Z", "payload": {"type": "task_complete", "turn_id": "t1"}},
    ]
    plain.write_text("".join(json.dumps(x) + "\n" for x in records))
    before = e.parse_completed_dialogue(plain, source_host="home")
    compressed = plain.with_suffix(".jsonl.zst")
    assert e._safe_session_id(plain) == e._safe_session_id(compressed)
    subprocess.run([zstd(), "-q", str(plain), "-o", str(compressed)], check=True)
    original = compressed.read_bytes()
    assert session_paths(root) == [plain]
    plain.unlink()
    assert session_paths(root) == [compressed]
    after = e.parse_completed_dialogue(compressed, source_host="home", source_byte_limit=compressed.stat().st_size)
    assert after["completed_prefixes"] == before["completed_prefixes"]
    backlog = e.memory_backlog(state_root=tmp_path / "state", codex_root=root,
                              since="2026-10-30", through="2026-10-30", writer_role="home")
    assert backlog["complete"] and backlog["pending_turns"] == 1
    assert e.discover_session_paths_between(since="2026-10-30", through="2026-10-30", codex_root=root)["2026-10-30"] == [compressed]
    assert compressed.read_bytes() == original
    assert not plain.exists()
    state = tmp_path / "state"
    work = e.build_memory_worklist(state_root=state, codex_root=root,
                                  since="2026-10-30", through="2026-10-30",
                                  writer_role="home", limit=1)["items"][0]
    ready = e.prepare_memory_extraction_input(state_root=state, work_id=work["work_id"], writer_role="home")
    view = json.loads(Path(ready["input_path"]).read_text())
    reviewed = {key: view[key] for key in ("raw_source_sha256", "transcript_sha256",
                "completed_prefix_sha256", "completed_cursor", "completed_prefix_mode",
                "completed_date", "host_id", "session_id")}
    reviewed.update(schema_version="1.0", review_state="reviewed", items=[], reviewed_empty=True)
    assert e.record_memory_extraction(state_root=state, work_id=work["work_id"], reviewed=reviewed, writer_role="home")["status"] == "reviewed_empty"
    assert e.build_memory_worklist(state_root=state, codex_root=root,
                                  since="2026-10-30", through="2026-10-30",
                                  writer_role="home", limit=1)["selected"] == 0
    healthy = root / "healthy.jsonl"
    healthy.write_text("".join(json.dumps(x) + "\n" for x in records).replace("session-fixed", "session-healthy"))
    original_parse = e.parse_completed_dialogue
    calls = []

    def changed_archive(path, **kwargs):
        calls.append(path)
        if path == compressed:
            raise e.SourceSnapshotChanged("compressed source snapshot changed")
        return original_parse(path, **kwargs)

    monkeypatch.setattr(e, "parse_completed_dialogue", changed_archive)
    outcome = e.build_memory_worklist(state_root=state, codex_root=root,
                                     since="2026-10-30", through="2026-10-30",
                                     writer_role="home", limit=1)
    assert outcome["selected"] == 1
    assert outcome["parse_exception_counts"]["source_changed_during_selection"] == 1
    assert calls.count(compressed) == calls.count(healthy) == 1


def test_corrupt_compression_and_expired_budget_fail(tmp_path):
    path = tmp_path / "bad.jsonl.zst"
    path.write_bytes(b"not a zstd frame")
    with pytest.raises(ValueError, match="decompression"):
        with open_session(path) as handle:
            handle.read()
    with pytest.raises(e.DialogueParseTimeout):
        e.parse_completed_dialogue(path, source_host="home", deadline=time.monotonic() - 1)


def test_decoder_honors_caller_budget_beyond_thirty_seconds(tmp_path, monkeypatch):
    source = tmp_path / "source.jsonl"
    source.write_bytes(b'{"message":"complete source"}\n')
    archive = source.with_suffix(".jsonl.zst")
    subprocess.run([zstd(), "-q", str(source), "-o", str(archive)], check=True)
    clock = [0]
    monkeypatch.setattr(codex_sources.time, "monotonic", lambda: clock[0])
    with open_session(archive, deadline=90) as reader:
        clock[0] = 40
        assert reader.read() == source.read_bytes()


def test_scan_budget_preserves_thirty_second_persistence_reserve(tmp_path):
    with pytest.raises(ValueError, match="1050"):
        e.build_memory_worklist(state_root=tmp_path / "state", codex_root=tmp_path,
                                since="2026-10-30", through="2026-10-30", writer_role="home",
                                max_scan_seconds=1051)


def test_legacy_plain_prefix_preserves_independent_content_date(tmp_path):
    source = tmp_path / "legacy.jsonl"
    prefix = json.dumps({"type": "session_meta", "payload": {"id": "legacy", "source": "cli", "cwd": "/Users/test/Projects/K2B"}}) + "\n"
    prefix += json.dumps({"type": "response_item", "payload": {"role": "user", "type": "message", "content": [{"type": "input_text", "text": "Retain the legacy prefix."}]}}) + "\n"
    source.write_text(prefix + json.dumps({"timestamp": "2026-10-30T08:00:00Z"}) + "\n")
    parsed = e.parse_completed_dialogue(source, source_host="home", source_byte_limit=len(prefix.encode()))
    assert parsed["completed_prefixes"][0]["completed_date"] == "2026-10-30"


@pytest.mark.parametrize("failure", ["missing", "startup"])
def test_missing_decoder_is_actionable_before_backlog_counts(tmp_path, monkeypatch, failure):
    archive = tmp_path / "source.jsonl.zst"
    archive.write_bytes(b"decoder dependency unavailable")
    if failure == "missing":
        monkeypatch.setattr(codex_sources.shutil, "which", lambda *args: None)
        monkeypatch.setattr(Path, "is_file", lambda *args: False)
    else:
        monkeypatch.setattr(codex_sources.shutil, "which", lambda *args: "/test/decoder")
        def cannot_start(*args, **kwargs):
            raise PermissionError("decoder is unavailable")
        monkeypatch.setattr(codex_sources.subprocess, "Popen", cannot_start)
    with pytest.raises(ValueError, match="decoder"):
        e.memory_backlog(state_root=tmp_path / "state", codex_root=tmp_path, since="2026-10-30", through="2026-10-30", writer_role="home")


def test_singular_discovery_has_aggregate_deadline(tmp_path):
    (tmp_path / "source.jsonl.zst").write_bytes(b"should not decode")
    with pytest.raises(ValueError, match="discovery deadline"):
        e.discover_session_paths(run_date="2026-10-30", codex_root=tmp_path, deadline=time.monotonic()-1)


def test_compressed_probe_timeout_is_partial_backlog_and_explicit_discovery_error(tmp_path, monkeypatch):
    archive = tmp_path / "source.jsonl.zst"
    archive.write_bytes(b"test replaces the probe")

    def timeout(*args, **kwargs):
        raise e.DialogueParseTimeout("compressed source scan deadline exceeded")

    monkeypatch.setattr(e, "_is_k2b_scope", timeout)
    result = e.memory_backlog(state_root=tmp_path / "state", codex_root=tmp_path,
                              since="2026-10-30", through="2026-10-30", writer_role="home")
    assert result["complete"] is False
    assert result["status"] == "needs_attention"
    assert result["timed_out_sources"] == 1
    with pytest.raises(ValueError, match="source probe timed out"):
        e.discover_session_paths(run_date="2026-10-30", codex_root=tmp_path)
    with pytest.raises(ValueError, match="source probe timed out"):
        e.discover_session_paths_between(since="2026-10-30", through="2026-10-30", codex_root=tmp_path)


def test_truncated_archive_cannot_become_empty_success(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps({"type": "session_meta", "payload": {"id": "s", "source": "cli", "cwd": "/Users/test/Projects/K2B"}}) + "\n" + "\n" * 10000)
    compressed = source.with_suffix(".jsonl.zst")
    subprocess.run([zstd(), "-q", str(source), "-o", str(compressed)], check=True)
    source.unlink()
    compressed.write_bytes(compressed.read_bytes()[:-1])
    with pytest.raises(ValueError, match="decompression"):
        e.build_memory_worklist(state_root=tmp_path / "state", codex_root=tmp_path, since="2026-10-30", through="2026-10-30", writer_role="home")


def test_appended_compressed_bytes_invalidate_whole_archive_snapshot(tmp_path):
    path = tmp_path / "source.jsonl.zst"
    path.write_bytes(b"initial archive snapshot")
    before = path.stat()
    digest = e._source_snapshot_hash(path, before.st_size)
    assert not e._source_snapshot_changed(path, before, digest)
    with path.open("ab") as handle:
        handle.write(b"appended frame")
    assert e._source_snapshot_changed(path, before, digest)


def test_compressed_line_limit_is_explicit_and_preserves_plain_behavior(tmp_path, monkeypatch):
    plain = tmp_path / "source.jsonl"
    plain.write_text(json.dumps({"payload": "x" * 200}) + "\n")
    compressed = plain.with_suffix(".jsonl.zst")
    subprocess.run([zstd(), "-q", str(plain), "-o", str(compressed)], check=True)
    monkeypatch.setattr(e, "MAX_COMPRESSED_RECORD_BYTES", 64)
    assert len(e._read_complete_jsonl_records(plain)) == 1
    assert e._read_complete_jsonl_records(plain)[0][2]
    assert e._read_complete_jsonl_records(plain, retain_raw=False)[0][2] == b""
    with pytest.raises(ValueError, match="line byte limit"):
        e._read_complete_jsonl_records(compressed)
    exact = json.dumps({"p": "ok"})
    monkeypatch.setattr(e, "MAX_COMPRESSED_RECORD_BYTES", len(exact))
    plain.write_bytes(exact.encode() + b"\r\n")
    subprocess.run([zstd(), "-f", "-q", str(plain), "-o", str(compressed)], check=True)
    assert len(e._read_complete_jsonl_records(compressed)) == 1


def test_metadata_probe_reads_only_a_bounded_prefix(tmp_path, monkeypatch):
    plain = tmp_path / "source.jsonl"
    plain.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/Users/test/Projects/K2B"}}) + "\n" + json.dumps({"blob": "x" * (1024 * 1024)}) + "\n")
    compressed = plain.with_suffix(".jsonl.zst")
    subprocess.run([zstd(), "-q", str(plain), "-o", str(compressed)], check=True)
    consumed = []
    totals = []
    original = codex_sources._DecodedSource.readinto

    def counted(reader, target):
        try:
            count = original(reader, target)
        finally:
            totals.append(reader.total)
        consumed.append(count)
        return count

    monkeypatch.setattr(codex_sources._DecodedSource, "readinto", counted)
    assert e.session_cwd(compressed) == "/Users/test/Projects/K2B"
    assert sum(consumed) <= 65536
    plain.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/Users/test/Projects/K2B", "padding": "x" * (256 * 1024)}}) + "\n")
    subprocess.run([zstd(), "-f", "-q", str(plain), "-o", str(compressed)], check=True)
    assert e.session_cwd(compressed) == e.session_cwd(plain)
    # Metadata spanning several records retains plain-source semantics.
    prefix = json.dumps({"padding": ""})
    first = json.dumps({"padding": "x" * (65535 - len(prefix))}) + "\n"
    assert len(first.encode()) == 65536
    plain.write_text(first + json.dumps({"cwd": "/Users/test/Projects/K2B"}) + "\n")
    subprocess.run([zstd(), "-f", "-q", str(plain), "-o", str(compressed)], check=True)
    assert e.session_cwd(compressed) == e.session_cwd(plain)
    monkeypatch.setattr(e, "MAX_COMPRESSED_RECORD_BYTES", 1024)
    with pytest.raises(ValueError, match="line byte limit"):
        e.session_cwd(compressed)


def test_plain_corruption_freezes_worklist_and_backlog_reports_error(tmp_path):
    plain = tmp_path / "source.jsonl"
    plain.write_text(json.dumps({"type": "session_meta", "payload": {"id": "s", "source": "cli", "cwd": "/Users/test/Projects/K2B"}}) + "\nnot JSON\n")
    with pytest.raises(ValueError, match="invalid JSON"):
        e.build_memory_worklist(state_root=tmp_path / "state", codex_root=tmp_path, since="2026-10-30", through="2026-10-30", writer_role="home")
    backlog = e.memory_backlog(state_root=tmp_path / "state", codex_root=tmp_path, since="2026-10-30", through="2026-10-30", writer_role="home")
    assert backlog["status"] == "needs_attention" and backlog["source_errors"] == 1


def test_date_directory_is_unknown_scope_and_valid_other_project_is_excluded(tmp_path):
    root = tmp_path / ".codex/sessions"
    folder = root / "2026/10/30"
    folder.mkdir(parents=True)
    plain = folder / "rollout-other.jsonl"
    plain.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": "/Users/test/Projects/Other"}}) + "\nnot JSON\n")
    archive = plain.with_suffix(".jsonl.zst")
    subprocess.run([zstd(), "-q", str(plain), "-o", str(archive)], check=True)
    plain.unlink()
    assert not e._path_implies_project_scope(archive)
    result = e.build_memory_worklist(state_root=tmp_path / "state", codex_root=root, since="2026-10-30", through="2026-10-30", writer_role="home")
    assert result["selected"] == 0 and not result["parse_exception_counts"]
    # Absence of a project name in a date-only path cannot classify an
    # unreadable header as non-K2B. The boundary must remain unverified.
    archive.write_bytes(b"unreadable header: project cannot be classified")
    with pytest.raises(ValueError, match="decompression"):
        e.build_memory_worklist(state_root=tmp_path / "state", codex_root=root, since="2026-10-30", through="2026-10-30", writer_role="home")


def test_undated_legacy_archive_cannot_enter_native_capture_window(tmp_path):
    plain = tmp_path / "source.jsonl"
    rows = [
        {"type": "session_meta", "payload": {"id": "legacy", "source": "cli", "cwd": "/Users/test/Projects/K2B"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Legacy conversation without turn or date markers."}]}},
    ]
    plain.write_text("".join(json.dumps(row) + "\n" for row in rows))
    compressed = plain.with_suffix(".jsonl.zst")
    subprocess.run([zstd(), "-q", str(plain), "-o", str(compressed)], check=True)
    plain.unlink()
    os.utime(compressed, (1790812800, 1790812800))
    result = e.build_memory_worklist(state_root=tmp_path / "state", codex_root=tmp_path, since="2026-09-13", through="2026-11-04", writer_role="home")
    assert result["selected"] == 0


@pytest.mark.parametrize("mutation", ["append", "corrupt", "persistence_timeout"])
def test_archive_change_between_bundle_writes_is_detected(tmp_path, monkeypatch, mutation):
    source = tmp_path / "source.jsonl"
    rows = [{"type": "session_meta", "payload": {"id": "two-turns", "source": "cli", "cwd": "/Users/test/Projects/K2B"}}]
    for i in range(2):
        rows.extend([
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": f"t{i}"}},
            {"type": "response_item", "payload": {"id": f"u{i}", "type": "message", "role": "user", "content": [{"type": "input_text", "text": f"Retain decision {i}."}]}},
            {"type": "event_msg", "timestamp": f"2026-10-30T08:0{i}:00Z", "payload": {"type": "task_complete", "turn_id": f"t{i}"}},
        ])
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    archive = source.with_suffix(".jsonl.zst")
    subprocess.run([zstd(), "-q", str(source), "-o", str(archive)], check=True)
    source.unlink()
    clock = [0]
    if mutation == "persistence_timeout":
        tail = tmp_path / "zzz.jsonl.zst"
        tail.write_bytes(archive.read_bytes())
        original_bundle = e._source_bundle_from_prefix
        def charge_scan(*args):
            bundle = original_bundle(*args)
            clock[0] = 121
            return bundle
        monkeypatch.setattr(e.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(e, "_source_bundle_from_prefix", charge_scan)
    empty_frame = subprocess.run([zstd(), "-q", "-c"], input=b"", capture_output=True, check=True).stdout
    original_write = e._atomic_write_json
    mutated = []

    def mutate_after_bundle(path, value):
        original_write(path, value)
        if path.parent.name == "source-bundles" and not mutated:
            if mutation == "persistence_timeout":
                clock[0] = 200
            elif mutation == "corrupt":
                archive.write_bytes(b"corrupted between scan and persistence")
            else:
                with archive.open("ab") as handle:
                    handle.write(empty_frame)
            mutated.append(True)

    monkeypatch.setattr(e, "_atomic_write_json", mutate_after_bundle)
    if mutation == "corrupt":
        with pytest.raises(ValueError, match="decompression"):
            e.build_memory_worklist(state_root=tmp_path / "state", codex_root=tmp_path, since="2026-10-30", through="2026-10-30", writer_role="home", limit=2)
        assert not list((tmp_path / "state/extraction-receipts").glob("*.json"))
        assert not list((tmp_path / "state/extraction-inputs").glob("*.json"))
        return
    result = e.build_memory_worklist(state_root=tmp_path / "state", codex_root=tmp_path, since="2026-10-30", through="2026-10-30", writer_role="home", limit=2)
    assert result["selected"] == 1
    if mutation == "persistence_timeout":
        assert result["parse_exception_counts"]["source_scan_time_budget"] == 1
        assert result["parse_exception_counts"]["source_persistence_time_budget"] == 1
        assert result["scan"]["first_unverified_source"] == str(tail)
        assert result["persistence"]["first_unverified_source"] == str(archive)
        return
    assert result["parse_exception_counts"]["source_changed_during_selection"] == 1


def acceptance():
    proposal = {"event_id": "p", "role": "assistant", "ordinal": 10, "text": "May I commit the reviewed changes and activate the MVP on SJM?"}
    user = {"event_id": "u", "role": "user", "ordinal": 11, "text": "yes\n"}
    item = dict(kind="decision", subject="MVP", predicate="status", object="Commit and activate approved",
                confidence="high", scope="K2B", dedupe_key="decision:mvp:activation", canonical_home="wiki/context/shelves/semantic.md",
                evidence_quote="yes", speaker_source="keith", evidence_event_id="u",
                acceptance_context={"event_id": "p", "quote": proposal["text"]})
    return item, [proposal, user]


def test_invalid_native_ordinal_cannot_merge_identical_distinct_messages():
    args = dict(item={}, event={"ordinal": None}, host_id="host:home", session_id="s", role="user", text="yes")
    assert e._stable_event_id(**args, line_no=3) != e._stable_event_id(**args, line_no=8)


def test_short_acceptance_retains_exact_user_and_proposal_evidence(tmp_path):
    item, events = acceptance()
    data, errors = e._filter_extraction_items({"items": [item]}, "[assistant]\n" + events[0]["text"] + "\n[user]\nyes", tmp_path / "source", dialogue_events=events, require_structured_attribution=True)
    assert not errors
    assert data["items"][0]["evidence_quote"] == "yes"
    assert data["items"][0]["acceptance_context"]["event_id"] == "p"
    qualified, linked = acceptance()
    qualified["evidence_quote"] = linked[1]["text"] = "yes, but do not activate"
    qualified["object"] = "Approve committing; do not activate."
    result, failures = e._filter_extraction_items({"items": [qualified]}, "yes, but do not activate", tmp_path / "source", dialogue_events=linked, require_structured_attribution=True)
    assert not failures and result["items"][0]["object"] == "Approve committing; do not activate."
    _, legacy_errors = e._filter_extraction_items({"items": [item]}, "yes", tmp_path / "legacy", dialogue_events=events)
    assert legacy_errors and "structured turn evidence" in legacy_errors[0]["error"]


@pytest.mark.parametrize("page_id", [None, "page-123"])
def test_short_acceptance_looks_past_standalone_page_state(tmp_path, page_id):
    item, events = acceptance()
    events[-1]["ordinal"] = 12
    ambient = dict(event_id="page-state", role="user", ordinal=11,
                   text="<external_codex_apps_open_page>" + json.dumps({"page_id": page_id})
                        + "</external_codex_apps_open_page>")
    events.insert(1, ambient)
    data, errors = e._filter_extraction_items(
        {"items": [item]}, "yes", tmp_path / "source",
        dialogue_events=events, require_structured_attribution=True,
    )
    assert not errors
    assert data["items"][0]["evidence_quote"] == "yes"
    assert data["items"][0]["acceptance_context"]["event_id"] == "p"
    assert events[1] == ambient


@pytest.mark.parametrize("text", [
    "Actually, do not activate yet.",
    '<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page> and no deployment',
    '<external_codex_apps_open_page>{"page_id":null,"answer":"no"}</external_codex_apps_open_page>',
    '<external_codex_apps_open_page>{"page_id":null,"page_id":"p"}</external_codex_apps_open_page>',
    '<external_codex_apps_open_page>{"page_id":false}</external_codex_apps_open_page>',
    '<external_codex_apps_open_page>{"page_id":""}</external_codex_apps_open_page>',
    '<external_codex_apps_open_page>{"page_id":123}</external_codex_apps_open_page>',
    '<external_codex_apps_open_page>{"page_id":null} trailing</external_codex_apps_open_page>',
    '<external_codex_apps_open_page>{broken}</external_codex_apps_open_page>',
    '<external_codex_apps_open_page>[]</external_codex_apps_open_page>',
    '<unknown_context>{"page_id":null}</unknown_context>',
])
def test_short_acceptance_cannot_skip_real_or_unrecognized_context(tmp_path, text):
    item, events = acceptance()
    events[-1]["ordinal"] = 12
    events.insert(1, dict(event_id="intervening", role="user", ordinal=11, text=text))
    with pytest.raises(ValueError):
        e._validate_item_evidence_quote(
            item, idx=0, normalized_payload="yes", session_path=tmp_path / "source",
            dialogue_events=events,
        )


@pytest.mark.parametrize("case", ["no_proposal", "assistant_metadata", "user_before_metadata"])
def test_page_state_does_not_create_or_replace_a_proposal(case):
    item, events = acceptance()
    events[-1]["ordinal"] = 14
    ambient = dict(event_id="ambient", role="user", ordinal=13,
                   text='<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>')
    events.insert(1, ambient)
    if case == "no_proposal":
        events.pop(0)
    elif case == "assistant_metadata":
        ambient["role"] = "assistant"
    else:
        events.insert(1, dict(event_id="interjection", role="user", ordinal=12,
                              text="Wait, change that proposal."))
    with pytest.raises(ValueError):
        e._validate_short_acceptance(item, events)


def test_page_state_cannot_be_the_acceptance_evidence():
    item, events = acceptance()
    events[-1]["text"] = '<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>'
    item["evidence_quote"] = events[-1]["text"]
    with pytest.raises(ValueError):
        e._validate_short_acceptance(item, events)


@pytest.mark.parametrize("cross_turn", [False, True])
def test_short_acceptance_cannot_skip_multiple_page_states(cross_turn):
    item, events = acceptance()
    events[0]["turn_id"] = "proposal-turn"
    events[-1].update(ordinal=14, turn_id="acceptance-turn")
    text = '<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>'
    events[1:1] = [
        dict(event_id="ambient-1", role="user", ordinal=12, text=text,
             turn_id="proposal-turn" if cross_turn else "acceptance-turn"),
        dict(event_id="ambient-2", role="user", ordinal=13, text=text,
             turn_id="acceptance-turn"),
    ]
    with pytest.raises(ValueError):
        e._validate_short_acceptance(item, events)


def test_contextual_replies_support_whitespace_and_explicit_full_acceptance(tmp_path):
    item, events = acceptance()
    for reply in ["yes  please", "Yes, commit and activate the MVP"]:
        item["evidence_quote"] = events[1]["text"] = reply
        e._validate_item_evidence_quote(item, idx=0, normalized_payload=e._normalize_evidence_text(reply), session_path=tmp_path / "source", dialogue_events=events)
    item["evidence_quote"] = events[1]["text"] = "yes please"
    e._validate_item_evidence_quote(item, idx=0, normalized_payload="yes please", session_path=tmp_path / "source", dialogue_events=events)
    item.pop("acceptance_context")
    # Existing ten-character quote artifacts retain their prior validation.
    e._validate_item_evidence_quote(item, idx=0, normalized_payload="yes please", session_path=tmp_path / "source", dialogue_events=events)


def test_proposal_quote_preserves_exact_crlf_and_long_context_error(tmp_path):
    item, events = acceptance()
    events[0]["text"] = "May I commit\r\nand activate the MVP?"
    item["acceptance_context"]["quote"] = events[0]["text"]
    e._validate_item_evidence_quote(item, idx=0, normalized_payload="yes", session_path=tmp_path / "source", dialogue_events=events)
    item["evidence_quote"] = events[1]["text"] = "Yes, commit and activate the MVP"
    item["acceptance_context"]["event_id"] = "invented"
    with pytest.raises(ValueError, match="invalid acceptance_context"):
        e._validate_item_evidence_quote(item, idx=0, normalized_payload=item["evidence_quote"], session_path=tmp_path / "source", dialogue_events=events)


@pytest.mark.parametrize("native_ordinal", ["absent", None, "10", 10.0, False])
@pytest.mark.parametrize("page_context", [False, True])
def test_acceptance_records_replays_and_validates_receipt(tmp_path, monkeypatch, native_ordinal, page_context):
    root = tmp_path / "sessions"
    root.mkdir()
    source = root / "rollout.jsonl"
    item, events = acceptance()
    rows = [{"type": "session_meta", "payload": {"id": "acceptance-session", "source": "cli", "cwd": "/Users/test/Projects/K2B"}}]
    for i, messages in enumerate(([dict(role="user", text="Prepare the MVP.", event_id="request"), events[0]], [events[1]])):
        rows.append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": f"t{i}"}})
        if i == 1 and page_context:
            messages.insert(0, dict(role="user", event_id="page-state", text='<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>'))
        for event in messages:
            rows.append({"type": "response_item", "payload": {"type": "message", "id": event["event_id"], "role": event["role"], "content": [{"type": "input_text" if event["role"] == "user" else "output_text", "text": event["text"]}]}})
            if native_ordinal != "absent":
                rows[-1]["ordinal"] = native_ordinal
        rows.append({"type": "event_msg", "timestamp": f"2026-10-30T08:0{i}:00Z", "payload": {"type": "task_complete", "turn_id": f"t{i}"}})
    source.write_text("".join(json.dumps(row) + "\n" for row in rows))
    state = tmp_path / "state"
    checks = []
    original_check = e._source_snapshot_changed

    def counted_check(*args):
        checks.append(args[0])
        return original_check(*args)

    monkeypatch.setattr(e, "_source_snapshot_changed", counted_check)
    work = e.build_memory_worklist(state_root=state, codex_root=root, since="2026-10-30", through="2026-10-30", writer_role="home", limit=2)["items"][1]
    assert len(checks) == 2
    ready = e.prepare_memory_extraction_input(state_root=state, work_id=work["work_id"], writer_role="home")
    view = json.loads(Path(ready["input_path"]).read_text())
    item["evidence_event_id"] = view["dialogue_events"][-1]["event_id"]
    item["acceptance_context"]["event_id"] = view["context_events"][-1]["event_id"]
    reviewed = {key: view[key] for key in ("raw_source_sha256", "transcript_sha256", "completed_prefix_sha256", "completed_cursor", "completed_prefix_mode", "completed_date", "host_id", "session_id")}
    reviewed.update(schema_version="1.0", review_state="reviewed", items=[item])
    assert e.record_memory_extraction(state_root=state, work_id=work["work_id"], reviewed=reviewed, writer_role="home")["status"] == "queued"
    assert e.record_memory_extraction(state_root=state, work_id=work["work_id"], reviewed=reviewed, writer_role="home")["duplicate"]
    receipt = json.loads((state / "extraction-receipts" / (work["work_id"] + ".json")).read_text())
    artifact = json.loads((state / receipt["extraction_artifact_relpath"]).read_text())
    bound_source = json.loads(Path(work["source_bundle_path"]).read_text())
    assert any(event["event_id"] == item["acceptance_context"]["event_id"] for event in bound_source["dialogue_events"])
    assert artifact["items"][0]["acceptance_context"] == item["acceptance_context"]
    # Native replay rehydrates events from the hash-validated source bundle.
    e._validate_memory_extraction_receipt(state, work["work_id"], bound_source, read_only=True)
    # Ordinary modern staging instead keeps its evidence events inline.
    staged = e._validated_reviewed_source_bundle(bound_source, reviewed, run_date="2026-10-30")
    assert staged["dialogue_events"]
    e.verify_evidence_quotes(staged, bound_source["transcript"], session_path=source)
    backlog = e.memory_backlog(state_root=state, codex_root=root, since="2026-10-30", through="2026-10-30", writer_role="home")
    assert backlog["extracted_turns"] == 1 and backlog["invalid_receipts"] == 0
    second = root / "zzz.jsonl"
    second.write_text(source.read_text().replace("acceptance-session", "another-session"))
    clock = [0]
    monkeypatch.setattr(e.time, "monotonic", lambda: clock[0])
    original_bundle = e._source_bundle_from_prefix

    def exhaust_budget(*args):
        bundle = original_bundle(*args)
        clock[0] = 121
        return bundle

    monkeypatch.setattr(e, "_source_bundle_from_prefix", exhaust_budget)
    partial = e.build_memory_worklist(state_root=tmp_path / "partial-state", codex_root=root, since="2026-10-30", through="2026-10-30", writer_role="home", limit=1)
    assert partial["selected"] == 1 and partial["status"] == "ready_with_exceptions"
    assert partial["parse_exception_counts"]["source_scan_time_budget"] == 1
    assert partial["scan"]["first_unverified_source"] == str(second)
    assert partial["scan"]["budget_exhausted"] is True


def test_full_rescan_finds_older_source_append_after_idle(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    state = tmp_path / "state"
    for name in ["a", "z"]:
        rows = [
            {"type": "session_meta", "payload": {"id": name, "source": "cli", "cwd": "/Users/test/Projects/K2B"}},
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t1"}},
            {"type": "response_item", "payload": {"id": "u1", "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Synthetic capture test."}]}},
            {"type": "event_msg", "timestamp": "2026-10-30T08:00:00Z", "payload": {"type": "task_complete", "turn_id": "t1"}},
        ]
        (root / f"{name}.jsonl").write_text("".join(json.dumps(row)+"\n" for row in rows))
    args = dict(state_root=state, codex_root=root, since="2026-10-30", through="2026-10-30", writer_role="home", limit=1)
    for _ in range(2):
        work = e.build_memory_worklist(**args)["items"][0]
        prepared = e.prepare_memory_extraction_input(state_root=state, work_id=work["work_id"], writer_role="home")
        view = json.loads(Path(prepared["input_path"]).read_text())
        reviewed = {key:view[key] for key in ("raw_source_sha256", "transcript_sha256", "completed_prefix_sha256", "completed_cursor", "completed_prefix_mode", "completed_date", "host_id", "session_id")}
        reviewed.update(schema_version="1.0", review_state="reviewed", items=[], reviewed_empty=True)
        e.record_memory_extraction(state_root=state, work_id=work["work_id"], reviewed=reviewed, writer_role="home")
    assert e.build_memory_worklist(**args)["status"] == "idle"
    with (root / "a.jsonl").open("a") as handle:
        for row in [
            {"type":"event_msg", "payload":{"type":"task_started", "turn_id":"t2"}},
            {"type":"response_item", "payload":{"id":"u2", "type":"message", "role":"user", "content":[{"type":"input_text", "text":"New turn appended to the older source."}]}},
            {"type":"event_msg", "timestamp":"2026-10-30T09:00:00Z", "payload":{"type":"task_complete", "turn_id":"t2"}},
        ]:
            handle.write(json.dumps(row)+"\n")
    resumed = e.build_memory_worklist(**args)
    assert resumed["selected"] == 1
    bundle = json.loads(Path(resumed["items"][0]["source_bundle_path"]).read_text())
    assert bundle["session_id"] == "a" and bundle["completed_turn_id"] == "t2"


@pytest.mark.parametrize("case", ["missing", "wrong_event", "invented_quote", "role", "order", "partial", "ambiguous"])
def test_short_acceptance_rejects_false_context(tmp_path, case):
    item, events = acceptance()
    if case == "missing": item.pop("acceptance_context")
    if case == "wrong_event": item["acceptance_context"]["event_id"] = "other"
    if case == "invented_quote": item["acceptance_context"]["quote"] = "Invented proposal text"
    if case == "role": events[0]["role"] = "user"
    if case == "order": events[0]["ordinal"] = 12
    if case == "partial": events[1]["text"] = "yes, but do not activate"
    if case == "ambiguous": events.insert(1, dict(event_id="other", role="assistant", ordinal=10, text="Another proposal"))
    with pytest.raises(ValueError):
        e._validate_item_evidence_quote(item, idx=0, normalized_payload="yes", session_path=tmp_path / "source", dialogue_events=events)
