"""Synthetic tests for P2-A read-only recovery preparation."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_DIR = REPO_ROOT / "scripts" / "lib"
CLI = REPO_ROOT / "scripts" / "k2b-recovery.py"

sys.path.insert(0, str(LIB_DIR))

from k2b_recovery import (  # noqa: E402
    MAX_FILE_BYTES,
    RecoveryError,
    manifest,
    verify_restore,
)


# ---- helpers ------------------------------------------------------------


def _write(root: Path, rel: str, data: bytes, mode: int = 0o644) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)
    return path


def _snapshot(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            out[rel] = ("link", os.readlink(path))
        elif path.is_dir():
            out[rel] = ("dir", stat_mode(path))
        else:
            out[rel] = ("file", hashlib.sha256(path.read_bytes()).hexdigest(), stat_mode(path))
    return out


def stat_mode(path: Path) -> int:
    return os.lstat(path).st_mode


def _cluster(tmp_path: Path):
    source = tmp_path / "source"
    restored = tmp_path / "restored"
    source.mkdir()
    restored.mkdir()
    _write(source, "wiki/note.md", b"# Note\nSee raw/evidence.txt\n", 0o644)
    _write(source, "raw/evidence.txt", b"synthetic evidence\n", 0o600)
    return source, restored


def _make_manifest(source: Path, paths):
    return manifest(source, paths)


def _materialize(restored: Path, source: Path, paths) -> None:
    for rel in paths:
        _write(restored, rel, (source / rel).read_bytes(), (source / rel).stat().st_mode & 0o777)


def _run_cli(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


# ---- happy path ---------------------------------------------------------


def test_manifest_and_verify_happy(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md", "raw/evidence.txt"]
    before = _snapshot(source)
    m = _make_manifest(source, paths)
    assert m["schema_version"] == 1
    assert m["source_root"] == str(source)
    assert [f["path"] for f in m["files"]] == sorted(paths)
    for entry in m["files"]:
        assert entry["size"] > 0
        assert len(entry["sha256"]) == 64
        assert isinstance(entry["mode"], int)
        assert isinstance(entry["mtime_ns"], int)
    assert _snapshot(source) == before, "source must remain unchanged"

    _materialize(restored, source, paths)
    result = verify_restore(m, restored)
    assert result["status"] == "verified"
    assert result["file_count"] == 2
    assert result["total_bytes"] == sum(f["size"] for f in m["files"])
    assert result["rehearsal_only"] is True


def test_empty_approved_file_roundtrip(tmp_path):
    source, restored = _cluster(tmp_path)
    _write(source, "raw/empty.txt", b"")
    m = manifest(source, ["raw/empty.txt"])
    _materialize(restored, source, ["raw/empty.txt"])
    assert verify_restore(m, restored)["total_bytes"] == 0


@pytest.mark.parametrize("rel", ["other/note.md", "System/local-state/a.txt", "wiki/a\n.txt", "wiki/a\x00.txt"])
def test_selection_scope_before_payload_read(tmp_path, monkeypatch, rel):
    source, _ = _cluster(tmp_path)
    if "\x00" not in rel:
        _write(source, rel, b"out of scope")
    import k2b_recovery as mod
    monkeypatch.setattr(mod, "_sha256_bounded", lambda *a: pytest.fail("read before scope validation"))
    with pytest.raises(RecoveryError):
        manifest(source, ["wiki/note.md", rel])


def test_manifest_digest_trailing_newline_rejected(tmp_path):
    source, restored = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    _materialize(restored, source, ["wiki/note.md"])
    m["files"][0]["sha256"] += "\n"
    with pytest.raises(RecoveryError, match="sha256"):
        verify_restore(m, restored)


def test_restore_walk_error_fails_closed(tmp_path, monkeypatch):
    source, restored = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    _materialize(restored, source, ["wiki/note.md"])
    import k2b_recovery as mod
    original = mod.os.walk

    def denied(root, **kwargs):
        if "onerror" in kwargs:
            kwargs["onerror"](PermissionError("synthetic unreadable directory"))
        yield from original(root, **kwargs)

    monkeypatch.setattr(mod.os, "walk", denied)
    with pytest.raises(RecoveryError, match="walk"):
        verify_restore(m, restored)


def test_restored_disk_nfd_name_matches_nfc_manifest(tmp_path):
    source, restored = _cluster(tmp_path)
    _write(source, "wiki/caf\u00e9.md", b"unicode fixture")
    m = manifest(source, ["wiki/caf\u00e9.md"])
    _write(restored, "wiki/cafe\u0301.md", b"unicode fixture")
    assert verify_restore(m, restored)["status"] == "verified"


def test_cli_usage_error_is_json():
    result = _run_cli([])
    assert result.returncode == 2
    assert json.loads(result.stderr)["status"] == "error"


def test_verification_reports_manifest_scope_without_source_freshness(tmp_path):
    source, restored = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    _materialize(restored, source, ["wiki/note.md"])
    (source / "wiki/note.md").write_text("a later edit")
    result = verify_restore(m, restored)
    assert result["source_freshness"] == "not_checked"
    expected = hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert result["manifest_sha256"] == expected


def test_hash_open_refuses_late_symlink(tmp_path):
    source, _ = _cluster(tmp_path)
    link = source / "raw/link.txt"
    link.symlink_to(source / "raw/evidence.txt")
    import k2b_recovery as mod
    with pytest.raises(RecoveryError):
        mod._sha256_bounded(link, MAX_FILE_BYTES)


def test_output_discloses_unchecked_mtime_and_independence(tmp_path):
    source, restored = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    (restored / "wiki").mkdir()
    os.link(source / "wiki/note.md", restored / "wiki/note.md")
    result = verify_restore(m, restored)
    assert result["mtime_checked"] is False
    assert result["independence_checked"] is False


def test_cli_manifest_open_uses_nofollow(tmp_path, monkeypatch, capsys):
    import argparse
    import importlib.util
    spec = importlib.util.spec_from_file_location("recovery_cli", CLI)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    source, restored = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    _materialize(restored, source, ["wiki/note.md"])
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(m))
    original = os.open
    calls = []

    def checked_open(path, flags, *args, **kwargs):
        if Path(path) == mf:
            assert flags & os.O_NOFOLLOW
            calls.append(path)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", checked_open)
    cli._cmd_verify(argparse.Namespace(manifest=str(mf), restored_root=str(restored)))
    assert calls
    assert json.loads(capsys.readouterr().out)["status"] == "verified"


def test_permissions_preserved(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["raw/evidence.txt"]
    m = _make_manifest(source, paths)
    assert m["files"][0]["mode"] == 0o600
    _materialize(restored, source, paths)
    verify_restore(m, restored)
    # Wrong mode must fail.
    os.chmod(restored / "raw/evidence.txt", 0o644)
    with pytest.raises(RecoveryError):
        verify_restore(m, restored)


def test_relative_reference_preserved(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md", "raw/evidence.txt"]
    m = _make_manifest(source, paths)
    _materialize(restored, source, paths)
    note = (restored / "wiki/note.md").read_text(encoding="utf-8")
    assert "raw/evidence.txt" in note
    assert (restored / "raw/evidence.txt").read_bytes() == (source / "raw/evidence.txt").read_bytes()
    verify_restore(m, restored)


# ---- source change during hashing ---------------------------------------


def test_changed_source_during_hash_detected(tmp_path, monkeypatch):
    source, restored = _cluster(tmp_path)
    paths = ["raw/evidence.txt"]
    import k2b_recovery as mod

    original = mod._sha256_bounded

    def flaky(path, limit):
        result = original(path, limit)
        # Mutate the file after hashing to simulate concurrent change.
        with open(path, "ab") as handle:
            handle.write(b"X")
        return result

    monkeypatch.setattr(mod, "_sha256_bounded", flaky)
    with pytest.raises(RecoveryError):
        manifest(source, paths)


def test_earlier_file_changed_while_later_hashes(tmp_path, monkeypatch):
    source, restored = _cluster(tmp_path)
    _write(source, "raw/zzz.txt", b"later\n", 0o644)
    paths = ["raw/evidence.txt", "raw/zzz.txt"]
    import k2b_recovery as mod

    original = mod._sha256_bounded
    calls = {"n": 0}

    def flaky(path, limit):
        calls["n"] += 1
        result = original(path, limit)
        if calls["n"] == 2:
            # After hashing the later file, mutate the earlier one.
            with open(source / "raw/evidence.txt", "ab") as handle:
                handle.write(b"Y")
        return result

    monkeypatch.setattr(mod, "_sha256_bounded", flaky)
    with pytest.raises(RecoveryError):
        manifest(source, paths)


# ---- restore content failures -------------------------------------------


def test_missing_and_altered_bytes(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md", "raw/evidence.txt"]
    m = _make_manifest(source, paths)
    _materialize(restored, source, paths)

    (restored / "wiki/note.md").unlink()
    with pytest.raises(RecoveryError):
        verify_restore(m, restored)

    _materialize(restored, source, paths)
    (restored / "raw/evidence.txt").write_bytes(b"tampered\n")
    with pytest.raises(RecoveryError):
        verify_restore(m, restored)


def test_unrelated_extra_file_untouched_on_failure(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md"]
    m = _make_manifest(source, paths)
    _materialize(restored, source, paths)
    extra = _write(restored, "unrelated.bin", b"keep me\n", 0o644)
    before = extra.read_bytes()
    with pytest.raises(RecoveryError):
        verify_restore(m, restored)
    assert extra.read_bytes() == before


# ---- path escapes and private state -------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "../escape.txt",
        "wiki/../../escape.txt",
        ".",
        "wiki/./note.md",
        "wiki\\note.md",
        "",
        ".env",
        "secrets/token.txt",
        "System/sessions/abc.json",
        "accounts/me.db",
        "node_modules/pkg/index.js",
        "credentials.pem",
        "store.sqlite3",
        "wiki/private/note.md",
        "outbox/mail.eml",
        "receipts/r.json",
    ],
)
def test_manifest_rejects_bad_paths(tmp_path, bad):
    source, _ = _cluster(tmp_path)
    with pytest.raises(RecoveryError):
        manifest(source, [bad])


def test_manifest_rejects_empty_list(tmp_path):
    source, _ = _cluster(tmp_path)
    with pytest.raises(RecoveryError):
        manifest(source, [])


def test_duplicate_and_case_collisions(tmp_path):
    source, _ = _cluster(tmp_path)
    with pytest.raises(RecoveryError):
        manifest(source, ["wiki/note.md", "wiki/note.md"])
    with pytest.raises(RecoveryError):
        manifest(source, ["wiki/note.md", "Wiki/note.md"])


def test_unicode_collisions(tmp_path):
    source, _ = _cluster(tmp_path)
    # NFD form should be rejected as noncanonical.
    nfd = "wiki/cafe\u0301.txt"
    with pytest.raises(RecoveryError):
        manifest(source, [nfd])


def test_private_state_rejected_before_read(tmp_path, monkeypatch):
    source, _ = _cluster(tmp_path)
    _write(source, "secrets/leak.txt", b"should not be read\n", 0o644)
    import k2b_recovery as mod

    def boom(*_a, **_k):
        raise AssertionError("payload must not be read for rejected inputs")

    monkeypatch.setattr(mod, "_sha256_bounded", boom)
    with pytest.raises(RecoveryError):
        manifest(source, ["raw/evidence.txt", "secrets/leak.txt"])


def test_verify_rejects_bad_manifest_paths_before_read(tmp_path, monkeypatch):
    source, restored = _cluster(tmp_path)
    bad = {
        "schema_version": 1,
        "source_root": str(source),
        "files": [
            {"path": "secrets/x.txt", "size": 1, "sha256": "0" * 64, "mode": 0o644, "mtime_ns": 0}
        ],
    }
    import k2b_recovery as mod

    def boom(*_a, **_k):
        raise AssertionError("payload must not be read for rejected manifest")

    monkeypatch.setattr(mod, "_sha256_bounded", boom)
    with pytest.raises(RecoveryError):
        verify_restore(bad, restored)


# ---- invalid schema and metadata ----------------------------------------


def _good_manifest(tmp_path):
    source, restored = _cluster(tmp_path)
    return source, restored, _make_manifest(source, ["raw/evidence.txt"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(schema_version=2),
        lambda m: m.update(schema_version="1"),
        lambda m: m.pop("schema_version"),
        lambda m: m.update(source_root="relative/root"),
        lambda m: m.update(files=[]),
        lambda m: m["files"][0].update(size=0),
        lambda m: m["files"][0].update(size=MAX_FILE_BYTES + 1),
        lambda m: m["files"][0].update(size="10"),
        lambda m: m["files"][0].update(sha256="xyz"),
        lambda m: m["files"][0].update(mode=-1),
        lambda m: m["files"][0].update(mtime_ns="now"),
        lambda m: m["files"][0].pop("mode"),
        lambda m: m["files"][0].update(extra=1),
    ],
)
def test_invalid_manifest_rejected(tmp_path, mutate):
    source, restored, good = _good_manifest(tmp_path)
    bad = json.loads(json.dumps(good))
    mutate(bad)
    with pytest.raises(RecoveryError):
        verify_restore(bad, restored)


def test_manifest_duplicate_json_paths_rejected(tmp_path):
    source, restored, good = _good_manifest(tmp_path)
    dup = json.loads(json.dumps(good))
    dup["files"].append(dict(dup["files"][0]))
    with pytest.raises(RecoveryError):
        verify_restore(dup, restored)


def test_verify_manifest_path_escape_rejected(tmp_path):
    source, restored, good = _good_manifest(tmp_path)
    bad = json.loads(json.dumps(good))
    bad["files"][0]["path"] = "../escape.txt"
    with pytest.raises(RecoveryError):
        verify_restore(bad, restored)


# ---- root and tree shape checks -----------------------------------------


def test_symlink_root_rejected(tmp_path):
    source, _ = _cluster(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(RecoveryError):
        manifest(link, ["raw/evidence.txt"])


def test_symlink_member_rejected(tmp_path):
    source, _ = _cluster(tmp_path)
    (source / "raw/link.txt").symlink_to(source / "raw/evidence.txt")
    with pytest.raises(RecoveryError):
        manifest(source, ["raw/link.txt"])


def test_restored_symlink_and_extra_dir_rejected(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md"]
    m = _make_manifest(source, paths)
    _materialize(restored, source, paths)
    (restored / "link.txt").symlink_to(source / "wiki/note.md")
    with pytest.raises(RecoveryError):
        verify_restore(m, restored)

    (restored / "link.txt").unlink()
    (restored / "empty_dir").mkdir()
    with pytest.raises(RecoveryError):
        verify_restore(m, restored)


@pytest.mark.parametrize("where", ["same", "child", "parent"])
def test_overlapping_roots_rejected(tmp_path, where):
    source, restored = _cluster(tmp_path)
    m = _make_manifest(source, ["raw/evidence.txt"])
    _materialize(restored, source, ["raw/evidence.txt"])
    if where == "same":
        target = source
    elif where == "child":
        target = source / "raw"
    else:
        target = tmp_path
    with pytest.raises(RecoveryError):
        verify_restore(m, target)


def test_oversized_source_rejected(tmp_path):
    source, restored = _cluster(tmp_path)
    big = source / "raw/big.bin"
    with open(big, "wb") as handle:
        handle.truncate(MAX_FILE_BYTES + 1)
    with pytest.raises(RecoveryError):
        manifest(source, ["raw/big.bin"])


# ---- CLI ----------------------------------------------------------------


def test_cli_manifest_and_verify_success(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md", "raw/evidence.txt"]
    before_source = _snapshot(source)
    result = _run_cli(
        [
            "manifest",
            "--root",
            str(source),
            "--approved-path",
            "wiki/note.md",
            "--approved-path",
            "raw/evidence.txt",
        ]
    )
    assert result.returncode == 0, result.stderr
    m = json.loads(result.stdout)
    assert m["schema_version"] == 1
    assert _snapshot(source) == before_source
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(result.stdout, encoding="utf-8")

    _materialize(restored, source, paths)
    before_restored = _snapshot(restored)
    verify = _run_cli(
        ["verify", "--manifest", str(manifest_file), "--restored-root", str(restored)]
    )
    assert verify.returncode == 0, verify.stderr
    out = json.loads(verify.stdout)
    assert out["status"] == "verified"
    assert out["rehearsal_only"] is True
    assert _snapshot(restored) == before_restored


def test_cli_failure_writes_nothing(tmp_path):
    source, _ = _cluster(tmp_path)
    before = _snapshot(tmp_path)
    result = _run_cli(
        ["manifest", "--root", str(source), "--approved-path", "secrets/x.txt"]
    )
    assert result.returncode != 0
    assert result.stdout == ""
    err = json.loads(result.stderr)
    assert err["status"] == "error"
    assert _snapshot(tmp_path) == before


def test_cli_duplicate_json_keys_rejected(tmp_path):
    source, restored = _cluster(tmp_path)
    _materialize(restored, source, ["raw/evidence.txt"])
    bad_manifest = tmp_path / "bad.json"
    bad_manifest.write_text(
        '{"schema_version": 1, "schema_version": 1, "source_root": "%s", "files": []}'
        % source,
        encoding="utf-8",
    )
    result = _run_cli(
        ["verify", "--manifest", str(bad_manifest), "--restored-root", str(restored)]
    )
    assert result.returncode != 0
    assert json.loads(result.stderr)["status"] == "error"
