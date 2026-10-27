"""Independent coordinator checks for the bounded public contract."""
import copy
import contextlib
import hashlib
import io
import importlib.util
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import recovery_orchestration as subject


def observation():
    return {
        "schema_version": 1, "actor": "sjm", "observed_at_ns": 10_000_000_000,
        "state": "idle", "need_files": 0, "need_bytes": 0,
        "need_directories": 0, "errors": 0, "pull_errors": 0,
        "error_present": False,
        "locks": {name: ("not_applicable" if name in ("integration", "publication") else "clear") for name in
                  ("note_write", "compile_index", "integration", "wiki_log", "publication")},
        "lock_scope": "configured_witnesses_role_v1",
        "witness_specs": [{"name": name, "kind": "flock", "path": "/synthetic/" + name}
                          for name in subject.REQUIRED_LOCKS],
    }


@pytest.mark.parametrize("field,value", [
    ("need_files", 1), ("need_bytes", 1), ("errors", 1),
    ("pull_errors", 1), ("need_files", False), ("need_files", -1),
    ("state", "scanning"), ("error_present", True),
    ("observed_at_ns", 7_999_999_999), ("observed_at_ns", 10_500_000_001),
])
def test_unsafe_observation_cannot_become_freeze_evidence(field, value):
    record = observation()
    record[field] = value
    with pytest.raises(subject.OrchestrationError):
        subject.validate_observation(record, actor="sjm", now_ns=10_000_000_000)


def test_missing_counterpart_and_unknown_lock_are_refused():
    for record in (None, {}, {**observation(), "actor": "home"}):
        with pytest.raises(subject.OrchestrationError):
            subject.validate_observation(record, actor="sjm", now_ns=10_000_000_000)
    record = copy.deepcopy(observation())
    record["locks"]["integration"] = "unknown"
    with pytest.raises(subject.OrchestrationError):
        subject.validate_observation(record, actor="sjm", now_ns=10_000_000_000)


def test_archive_list_is_deterministic_and_contains_all_parents(tmp_path):
    for relative in ("wiki/z/note.md", "wiki/a/note.md"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode())
    first = subject.archive_plan(tmp_path, ["wiki/z/note.md", "wiki/a/note.md"])
    second = subject.archive_plan(tmp_path, ["wiki/a/note.md", "wiki/z/note.md"])
    assert first["entries"] == ["wiki", "wiki/a", "wiki/z", "wiki/a/note.md", "wiki/z/note.md"]
    assert subject.entries_nul(first) == subject.entries_nul(second)


def test_nas_fixed_stat_tokens_and_unsafe_configuration():
    command = subject.nas_command("maint@100.64.0.1", "/volume1/homes/maint", "stat_parent")
    assert command[0] == "ssh"
    remote = shlex.split(command[-1])
    assert remote[0] == "/usr/bin/stat"
    assert remote[-1] == "/volume1/homes/maint"
    for target, parent in (("maint@100.64.0.1;false", "/volume1/homes/maint"),
                           ("maint@100.64.0.1", "/volume1/homes/maint/../other")):
        with pytest.raises(subject.OrchestrationError):
            subject.nas_command(target, parent, "stat_parent")


def test_defaults_match_existing_integration_and_publication_owners(tmp_path):
    home = tmp_path / "operator"
    vault = home / "Projects/K2B-Vault"
    records = {row["name"]: row for row in subject.default_lock_specs(home, vault=vault)}
    assert Path(records["integration"]["path"]) == home / ".local/state/k2b/digestion/.integration.lock"
    expected_name = "k2b-memory-publication-" + hashlib.sha256(str(vault.resolve()).encode()).hexdigest() + ".lock"
    assert Path(records["publication"]["path"]) == Path("/tmp").resolve() / expected_name


def test_recomputed_list_cannot_omit_an_ancestor(tmp_path):
    path = tmp_path / "wiki/a/note.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"evidence")
    plan = subject.archive_plan(tmp_path, ["wiki/a/note.md"])
    plan["directories"] = [row for row in plan["directories"] if row["path"] != "wiki/a"]
    plan["entries"] = [row for row in plan["entries"] if row != "wiki/a"]
    plan["entries_nul_sha256"] = hashlib.sha256(b"".join(x.encode() + b"\0" for x in plan["entries"])).hexdigest()
    plan["plan_sha256"] = subject.plan_digest(plan)
    with pytest.raises(subject.OrchestrationError):
        subject.entries_nul(plan)


def test_rule_hash_and_stat_permissions_are_checked(tmp_path):
    path = tmp_path / "wiki/note.md"
    path.parent.mkdir()
    path.write_bytes(b"data")
    plan = subject.archive_plan(tmp_path, ["wiki/note.md"])
    plan["rule_sha256"] = "0" * 64
    plan["plan_sha256"] = subject.plan_digest(plan)
    with pytest.raises(subject.OrchestrationError):
        subject.entries_nul(plan)
    with pytest.raises(subject.OrchestrationError):
        subject.parse_stat_output("directory|1|1|888|1|1|1|1\n")


def test_child_capture_is_capped_during_read(monkeypatch):
    monkeypatch.setattr(subject, "PROBE_SJM_MAX_BYTES", 1024)
    with pytest.raises(subject.OrchestrationError):
        subject._run_probe([sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*4096)"], b"", 2)


def test_child_timeout_is_sanitized():
    with pytest.raises(subject.OrchestrationError):
        subject._run_probe([sys.executable, "-c", "import time;time.sleep(2)"], b"", 0.05)


def test_child_exactly_one_byte_over_cap_is_refused(monkeypatch):
    monkeypatch.setattr(subject, "PROBE_SJM_MAX_BYTES", 1024)
    with pytest.raises(subject.OrchestrationError):
        subject._run_probe([sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*1025)"], b"", 2)


def test_remote_target_cannot_be_an_option_or_another_home():
    for target, parent in (("-maint@100.64.0.1", "/volume1/homes/maint"),
                           ("maint@100.64.0.1", "/volume1/homes/other")):
        with pytest.raises(subject.OrchestrationError):
            subject.nas_command(target, parent, "stat_parent")


def test_hash_parser_refuses_multiple_trailing_lines():
    with pytest.raises(subject.OrchestrationError):
        subject.parse_hash_output("a" * 64 + "  /volume1/homes/maint/archive\n\n", "/volume1/homes/maint/archive")


def test_tar_builder_keeps_control_and_archive_outside_source(tmp_path):
    with pytest.raises(subject.OrchestrationError):
        subject.tar_create_argv(tmp_path / "source", tmp_path / "list", tmp_path / "source/archive.tar")


def test_pax_cycle_preserves_selected_bytes_modes_and_times(tmp_path):
    root = tmp_path / "source"
    source = root / "wiki/z/a/note.md"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"original evidence\n")
    (root / "wiki/untouched.md").write_bytes(b"not selected")
    source.chmod(0o640)
    os.utime(source, ns=(1_700_000_000_123_456_789,) * 2)
    for directory in (root / "wiki", root / "wiki/z", source.parent):
        directory.chmod(0o750)
        os.utime(directory, ns=(1_700_000_000_987_654_321,) * 2)
    plan = subject.archive_plan(root, ["wiki/z/a/note.md"])
    entries = tmp_path / "entries.nul"
    entries.write_bytes(subject.entries_nul(plan))
    archive = tmp_path / "fixture.tar"
    restored = tmp_path / "restore"
    restored.mkdir(mode=0o700)
    subprocess.run(subject.tar_create_argv(root, entries, archive), env=dict(os.environ, COPYFILE_DISABLE="1"), check=True, capture_output=True)
    subprocess.run(subject.tar_extract_argv(archive, restored), check=True, capture_output=True)
    copy = restored / "wiki/z/a/note.md"
    assert copy.read_bytes() == source.read_bytes()
    assert copy.stat().st_mtime_ns == source.stat().st_mtime_ns
    assert stat.S_IMODE(copy.stat().st_mode) == 0o640
    assert not (restored / "wiki/untouched.md").exists()
    for record in plan["directories"]:
        item = (restored / record["path"]).stat()
        assert stat.S_IMODE(item.st_mode) == record["mode"]
        assert item.st_mtime_ns == record["mtime_ns"]


def test_configuration_mutation_during_read_is_refused(tmp_path, monkeypatch):
    config = tmp_path / "config.xml"
    config.write_bytes(b"<configuration/>")
    real_read = subject.os.read
    changed = False

    def read_then_change(fd, amount):
        nonlocal changed
        result = real_read(fd, amount)
        if not changed:
            config.write_bytes(b"<configuration changed='yes'/>")
            changed = True
        return result

    monkeypatch.setattr(subject.os, "read", read_then_change)
    with pytest.raises(subject.OrchestrationError):
        subject._read_config(config)


def test_tool_probe_requires_executable_regular_files(monkeypatch):
    captured = io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(os, "stat", lambda path: SimpleNamespace(st_mode=stat.S_IFDIR | 0o755))
        patch.setattr(os, "access", lambda path, mode: False)
        with contextlib.redirect_stdout(captured):
            exec(subject.REMOTE_TOOLS_SNIPPET, {})
    states = subject.parse_tools_output(captured.getvalue())
    assert all(value == "permission_unavailable" for value in states.values())


def test_list_supports_bounded_collection_metadata(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/k2b-recovery-orchestration.py"
    spec = importlib.util.spec_from_file_location("orchestration_cli_contract", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    path = tmp_path / "approved.json"
    payload = b'["wiki/note.md"]' + b" " * 70000
    path.write_bytes(payload)
    assert cli._read_approved_bounded(path) == payload
    path.write_bytes(b" " * (256 * 1024 + 1))
    with pytest.raises(subject.OrchestrationError):
        cli._read_approved_bounded(path)


def test_selection_count_bound_precedes_manifest_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "_load_manifest_module", lambda: pytest.fail("manifest read before count guard"))
    with pytest.raises(subject.OrchestrationError):
        subject.archive_plan(tmp_path, [f"wiki/note-{n}.md" for n in range(2001)])


def test_counterpart_receives_only_observation_code():
    payload = subject.build_probe_sjm_stdin()
    assert b"local_status('sjm')" in payload
    assert b"def archive_plan" not in payload
    assert b"def nas_command" not in payload


def test_payload_ignores_unlisted_top_level_statements(monkeypatch):
    source = subject._module_source()
    monkeypatch.setattr(subject, "_module_source", lambda: source.replace(
        "import fcntl", "raise RuntimeError('unlisted_statement')\nimport fcntl", 1))
    payload = subject.build_probe_sjm_stdin()
    assert b"unlisted_statement" not in payload
    namespace = {}
    exec(payload.rsplit(b"\nprint(", 1)[0], namespace)
    assert callable(namespace["local_status"])


def test_counterpart_receipt_binds_exact_payload(monkeypatch):
    payload = subject.build_probe_sjm_stdin()
    import json
    monkeypatch.setattr(subject, "_run_probe", lambda argv, data, timeout:
        subprocess.CompletedProcess(argv, 0, json.dumps(observation()).encode(), b""))
    monkeypatch.setattr(subject.time, "time_ns", lambda: 10_000_000_000)
    assert subject.probe_sjm()["payload_sha256"] == hashlib.sha256(payload).hexdigest()


def test_partial_manifest_metadata_is_refused(tmp_path, monkeypatch):
    for name in ("a", "b"):
        path = tmp_path / "wiki" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"content")
    monkeypatch.setattr(subject, "_load_manifest_module", lambda: SimpleNamespace(
        manifest=lambda *a: {"files": [{"path": "wiki/a", "size": 7}, "wiki/b"]}))
    with pytest.raises(subject.OrchestrationError):
        subject.archive_plan(tmp_path, ["wiki/a", "wiki/b"])


def test_approved_list_mutation_during_read_is_refused(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts/k2b-recovery-orchestration.py"
    spec = importlib.util.spec_from_file_location("orchestration_cli_mutation", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    path = tmp_path / "approved.json"
    path.write_bytes(b'["wiki/a"]')
    real_read = subject.os.read
    def change(fd, amount):
        value = real_read(fd, amount)
        path.write_bytes(b'["wiki/b"]')
        return value
    monkeypatch.setattr(subject.os, "read", change)
    with pytest.raises(subject.OrchestrationError):
        cli._read_approved_bounded(path)


def test_nas_stat_type_is_locale_independent():
    result = subject.parse_stat_output("41ed|100|100|755|5|77|1|42")
    assert result["type"] == "directory"
    assert subject.STAT_FORMAT.startswith("%f|")


def test_payload_pin_mismatch_refuses_before_ssh(monkeypatch):
    monkeypatch.setattr(subject, "_run_probe", lambda *a: pytest.fail("network before pin check"))
    with pytest.raises(subject.OrchestrationError, match="probe_payload_digest_mismatch"):
        subject.probe_sjm(expected_payload_sha256="0" * 64)


@pytest.mark.parametrize("field", ["mode", "mtime_ns", "root_mode", "total_bytes"])
def test_full_plan_metadata_is_digest_bound(tmp_path, field):
    path = tmp_path / "wiki" / "a"
    path.parent.mkdir()
    path.write_bytes(b"a")
    plan = subject.archive_plan(tmp_path, ["wiki/a"])
    if field in ("mode", "mtime_ns"):
        plan["directories"][0][field] += 1
    else:
        plan[field] += 1
    with pytest.raises(subject.OrchestrationError):
        subject.entries_nul(plan)


@pytest.mark.parametrize("port", ["0", "65536", "9" * 5000], ids=["zero", "range", "oversized"])
def test_gui_port_range_refuses_with_static_reason(port):
    with pytest.raises(subject.OrchestrationError, match="gui_endpoint_refused"):
        subject._fetch_status("http", "127.0.0.1:" + port, "synthetic", "synthetic")


def test_relative_approved_list_refuses_before_audit(monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts/k2b-recovery-orchestration.py"
    spec = importlib.util.spec_from_file_location("orchestration_cli_relative", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(subject, "_reject_symlink_ancestors", lambda *a, **k:
                        pytest.fail("relative list reached ancestry audit"))
    with pytest.raises(subject.OrchestrationError):
        cli._read_approved_bounded(Path("approved.json"))


def test_manual_large_evidence_streaming_and_pax_route(tmp_path):
    source = tmp_path / "source"
    parent = source / "wiki" / "history"
    parent.mkdir(parents=True)
    large = parent / "large.jsonl"
    with large.open("wb") as stream:
        for _ in range(17):
            stream.write(b"e" * (1024 * 1024))
    large.chmod(0o640)
    os.utime(large, ns=(1_700_000_000_123456789, 1_700_000_000_123456789))
    def digest(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    before = large.stat()
    expected_hash = digest(large)
    listing = tmp_path / "entries.nul"
    listing.write_bytes(b"wiki\0wiki/history\0wiki/history/large.jsonl\0")
    listing.chmod(0o600)
    archive = tmp_path / "large.tar"
    import re
    recipe = (Path(__file__).resolve().parents[1] / "plans/2026-10-27_manual-recovery-execution.md").read_text()
    fragment = re.search(r"```python\n(def create_archive_exclusively.*?)\n```", recipe, re.DOTALL).group(1)
    namespace = {"ro": subject}
    exec(fragment, namespace)
    list_digest = hashlib.sha256(listing.read_bytes()).hexdigest()
    assert namespace["create_archive_exclusively"](source, listing, archive, list_digest, before.st_size) == archive
    assert archive.stat().st_nlink == 1
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("large.tar.partial-*"))
    with pytest.raises(subject.OrchestrationError):
        namespace["create_archive_exclusively"](source, listing, archive, list_digest, before.st_size)
    after = large.stat()
    signature = lambda item: (item.st_dev, item.st_ino, item.st_size,
                              item.st_mtime_ns, item.st_ctime_ns, item.st_mode)
    assert signature(before) == signature(after)
    assert digest(large) == expected_hash
    restored = tmp_path / "restored"
    restored.mkdir()
    subprocess.run(subject.tar_extract_argv(archive, restored), check=True,
                   capture_output=True)
    result = restored / "wiki/history/large.jsonl"
    assert digest(result) == expected_hash
    assert result.stat().st_size == 17 * 1024 * 1024
    assert stat.S_IMODE(result.stat().st_mode) == stat.S_IMODE(before.st_mode)
    assert result.stat().st_mtime_ns == before.st_mtime_ns


def test_file_state_and_source_revalidation_are_bound(tmp_path):
    path = tmp_path / "wiki/a"
    path.parent.mkdir()
    path.write_bytes(b"before")
    plan = subject.archive_plan(tmp_path, ["wiki/a"])
    assert plan["file_metadata"][0]["sha256"] == hashlib.sha256(b"before").hexdigest()
    subject.validate_source_plan(tmp_path, plan)
    path.write_bytes(b"edited")
    with pytest.raises(subject.OrchestrationError):
        subject.validate_source_plan(tmp_path, plan)


@pytest.mark.parametrize("kind", ["mkdir", "flock"])
def test_missing_lock_parent_is_unknown(tmp_path, kind):
    probe = subject._probe_mkdir if kind == "mkdir" else subject._probe_flock
    assert probe(tmp_path / "missing-parent" / "lock") == "unknown"


def test_leading_dash_segments_refuse():
    with pytest.raises(subject.OrchestrationError):
        subject._validate_rel_path("wiki/-unsafe")


def role_fixture(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = tmp_path / "config.xml"
    config.write_text('<configuration><folder id="fixture" path="' + str(vault) +
        '"/><gui><address>127.0.0.1:8384</address><apikey>fixture</apikey></gui></configuration>')
    monkeypatch.setattr(subject, "_fetch_status", lambda *a: {
        "state": "idle", "error": "", "needFiles": 0, "needBytes": 0,
        "needDirectories": 0, "errors": 0, "pullErrors": 0})
    specs = [{"name": name, "kind": "flock", "path": str(tmp_path / (name + ".lock"))}
             for name in subject.REQUIRED_LOCKS]
    return dict(home=tmp_path, vault=vault, config_path=config, lock_specs=specs)


def test_sjm_home_only_witnesses_are_explicitly_inapplicable(tmp_path, monkeypatch):
    kwargs = role_fixture(tmp_path, monkeypatch)
    for spec in kwargs["lock_specs"]:
        if spec["name"] == "integration":
            spec["path"] = str(tmp_path / "absent-home-only-parent" / "integration.lock")
    result = subject.local_status("sjm", **kwargs)
    assert result["lock_scope"] == "configured_witnesses_role_v1"
    assert result["locks"]["integration"] == "not_applicable"
    assert result["locks"]["publication"] == "not_applicable"
    subject.validate_observation(result, actor="sjm", now_ns=result["observed_at_ns"])


def test_sjm_applicable_missing_parent_still_refuses(tmp_path, monkeypatch):
    kwargs = role_fixture(tmp_path, monkeypatch)
    kwargs["lock_specs"][0]["path"] = str(tmp_path / "missing" / "required.lock")
    result = subject.local_status("sjm", **kwargs)
    assert result["locks"]["note_write"] == "unknown"
    with pytest.raises(subject.OrchestrationError):
        subject.validate_observation(result, actor="sjm", now_ns=result["observed_at_ns"])


@pytest.mark.parametrize("witness", ["note_write", "compile_index", "wiki_log", "integration", "publication"])
def test_sjm_any_busy_writer_still_refuses(tmp_path, monkeypatch, witness):
    import fcntl
    kwargs = role_fixture(tmp_path, monkeypatch)
    lock = tmp_path / (witness + ".lock")
    lock.write_bytes(b"")
    with lock.open("rb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subject.local_status("sjm", **kwargs)
        assert result["locks"][witness] == "busy"
        with pytest.raises(subject.OrchestrationError):
            subject.validate_observation(result, actor="sjm", now_ns=result["observed_at_ns"])


def test_default_sjm_actor_cannot_be_asserted_on_home(monkeypatch):
    monkeypatch.setenv("USER", "keithcheung")
    monkeypatch.setenv("HOME", "/Users/keithcheung")
    with pytest.raises(subject.OrchestrationError, match="physical_actor_mismatch"):
        subject.local_status("sjm")


def test_unsupported_manifest_path_key_refuses_statically(tmp_path, monkeypatch):
    path = tmp_path / "wiki/a"
    path.parent.mkdir()
    path.write_bytes(b"a")
    monkeypatch.setattr(subject, "_load_manifest_module", lambda: SimpleNamespace(
        manifest=lambda *a: {"files": [{"relative_path": "wiki/a", "size": 1}]}))
    with pytest.raises(subject.OrchestrationError):
        subject.archive_plan(tmp_path, ["wiki/a"])


def test_archive_builder_refuses_existing_output_and_symlink_list(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    listing = tmp_path / "list"
    listing.write_bytes(b"wiki\0")
    archive = tmp_path / "output.tar"
    archive.write_bytes(b"preserve")
    with pytest.raises(subject.OrchestrationError):
        subject.tar_create_argv(root, listing, archive)
    archive.unlink()
    link = tmp_path / "list-link"
    link.symlink_to(listing)
    with pytest.raises(subject.OrchestrationError):
        subject.tar_create_argv(root, link, archive)


def test_relative_lock_override_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("K2B_NOTE_WRITE_LOCK", "relative.lock")
    with pytest.raises(subject.OrchestrationError):
        subject._normalize_lock_specs(None, tmp_path, tmp_path / "vault")


def test_observation_exposes_exact_witness_specs(tmp_path, monkeypatch):
    kwargs = role_fixture(tmp_path, monkeypatch)
    result = subject.local_status("sjm", **kwargs)
    assert result["witness_specs"] == kwargs["lock_specs"]


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32", "latin-1"])
def test_non_utf8_xml_guard_refuses(encoding):
    data = ('<?xml version="1.0" encoding="' + encoding + '"?><configuration/>').encode(encoding)
    with pytest.raises(subject.OrchestrationError):
        subject._reject_dtd(data)


def test_home_cli_refuses_nonpassing_status(monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts/k2b-recovery-orchestration.py"
    spec = importlib.util.spec_from_file_location("orchestration_cli_home_status", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    record = observation()
    record.update(actor="home", need_bytes=1)
    record["locks"] = {name: "clear" for name in subject.REQUIRED_LOCKS}
    monkeypatch.setattr(subject, "local_status", lambda *a: record)
    monkeypatch.setattr(subject.time, "time_ns", lambda: 10_000_000_000)
    with pytest.raises(subject.OrchestrationError):
        cli.cmd_status(["--actor", "home"])


def test_release_witness_binding_detects_different_absolute_paths():
    record = observation()
    expected = copy.deepcopy(record["witness_specs"])
    subject.validate_observation(record, actor="sjm", now_ns=10_000_000_000,
                                 expected_witness_specs=expected)
    record["witness_specs"][0]["path"] = "/other-valid-parent/other.lock"
    with pytest.raises(subject.OrchestrationError, match="observation_witness_binding_mismatch"):
        subject.validate_observation(record, actor="sjm", now_ns=10_000_000_000,
                                     expected_witness_specs=expected)


def test_unsafe_selection_refuses_before_manifest_call(tmp_path, monkeypatch):
    monkeypatch.setattr(subject, "_load_manifest_module", lambda: pytest.fail("unsafe path reached manifest"))
    with pytest.raises(subject.OrchestrationError):
        subject.archive_plan(tmp_path, ["../outside"])


def test_extract_builder_refuses_dirty_and_symlink_destinations(tmp_path):
    archive = tmp_path / "input.tar"
    archive.write_bytes(b"fixture")
    destination = tmp_path / "dirty"
    destination.mkdir()
    (destination / "preexisting").write_bytes(b"preserve")
    with pytest.raises(subject.OrchestrationError):
        subject.tar_extract_argv(archive, destination)
    link = tmp_path / "destination-link"
    link.symlink_to(destination)
    with pytest.raises(subject.OrchestrationError):
        subject.tar_extract_argv(archive, link)


@pytest.mark.parametrize("route", ["status", "counterpart"])
def test_cli_expected_witness_file_is_bound_and_mismatch_refuses(tmp_path, monkeypatch, capsys, route):
    import json
    script = Path(__file__).resolve().parents[1] / "scripts/k2b-recovery-orchestration.py"
    spec = importlib.util.spec_from_file_location("orchestration_cli_bound_" + route, script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    record = observation()
    if route == "status":
        record["actor"] = "home"
        record["locks"] = {name: "clear" for name in subject.REQUIRED_LOCKS}
    expected_file = tmp_path / "expected.json"
    expected_file.write_text(json.dumps(record["witness_specs"]))
    expected_file.chmod(0o600)
    monkeypatch.setattr(subject.time, "time_ns", lambda: 10_000_000_000)
    monkeypatch.setattr(subject, "local_status", lambda *a: record)
    monkeypatch.setattr(subject, "_run_probe", lambda argv, data, timeout:
        subprocess.CompletedProcess(argv, 0, json.dumps(record).encode(), b""))
    if route == "status":
        args = ["--actor", "home"]
        invoke = cli.cmd_status
    else:
        args = ["--payload-sha256", hashlib.sha256(subject.build_probe_sjm_stdin()).hexdigest()]
        invoke = cli.cmd_counterpart
    invoke(args + ["--expected-witness-specs", str(expected_file)])
    output = json.loads(capsys.readouterr().out)
    assert output["witness_binding"] == "expected_specs_matched"
    assert output["expected_witness_specs_sha256"] == hashlib.sha256(expected_file.read_bytes()).hexdigest()
    changed = copy.deepcopy(record["witness_specs"])
    changed[0]["path"] = "/different/required.lock"
    expected_file.write_text(json.dumps(changed))
    with pytest.raises(subject.OrchestrationError, match="observation_witness_binding_mismatch"):
        invoke(args + ["--expected-witness-specs", str(expected_file)])
    invoke(args)
    assert json.loads(capsys.readouterr().out)["witness_binding"] == "unbound_preflight"


@pytest.mark.parametrize("startup_seconds", [3.0, 4.5])
def test_slow_ssh_startup_does_not_age_a_later_remote_sample(monkeypatch, startup_seconds):
    import json
    clock = {"now": 10_000_000_000}
    request_start = clock["now"]
    def remote_after_startup(argv, payload, timeout):
        assert timeout == 5.0
        clock["now"] += int(startup_seconds * 1_000_000_000)
        record = observation()
        record["observed_at_ns"] = clock["now"]
        clock["now"] += 100_000_000
        return subprocess.CompletedProcess(argv, 0, json.dumps(record).encode(), b"")
    monkeypatch.setattr(subject, "_run_probe", remote_after_startup)
    monkeypatch.setattr(subject.time, "time_ns", lambda: clock["now"])
    result = subject.probe_sjm()
    assert result["received_at_ns"] - request_start > 2_000_000_000
    assert result["received_at_ns"] - result["observation"]["observed_at_ns"] == 100_000_000


def test_manual_refusal_retains_partial_without_publishing(tmp_path, monkeypatch):
    import re
    source = tmp_path / "source"
    (source / "wiki").mkdir(parents=True)
    (source / "wiki/a").write_bytes(b"a")
    listing = tmp_path / "entries"
    listing.write_bytes(b"wiki\0wiki/a\0")
    listing.chmod(0o600)
    expected_digest = hashlib.sha256(listing.read_bytes()).hexdigest()
    archive = tmp_path / "refused.tar"
    recipe = (Path(__file__).resolve().parents[1] / "plans/2026-10-27_manual-recovery-execution.md").read_text()
    fragment = re.search(r"```python\n(def create_archive_exclusively.*?)\n```", recipe, re.DOTALL).group(1)
    namespace = {"ro": subject}
    exec(fragment, namespace)
    real_run = subprocess.run
    def run_then_change_list(*args, **kwargs):
        result = real_run(*args, **kwargs)
        listing.write_bytes(b"wiki\0wiki/changed\0")
        return result
    monkeypatch.setattr(subprocess, "run", run_then_change_list)
    with pytest.raises(RuntimeError, match="archive_list_changed"):
        namespace["create_archive_exclusively"](source, listing, archive, expected_digest, 1)
    assert not archive.exists()
    partials = list(tmp_path.glob("refused.tar.partial-*"))
    assert len(partials) == 1 and partials[0].stat().st_size > 0
