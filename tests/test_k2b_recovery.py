"""Synthetic tests for P2-B read-only recovery preparation."""
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
        _write(restored, rel, (source / rel).read_bytes(), (source / rel).stat().st_mode & 0o7777)
    # Materialize exact source directory modes for root and ancestors.
    m = manifest(source, list(paths))
    os.chmod(restored, m["directory_modes"]["."])
    for key, mode in m["directory_modes"].items():
        if key == ".":
            continue
        (restored / key).mkdir(parents=True, exist_ok=True)
        os.chmod(restored / key, mode)


def _run_cli(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def _cli_auto_materialize(source: Path, restored: Path, paths) -> None:
    """Copy bytes and modes from source for CLI tests without importing manifest."""
    for rel in paths:
        src = source / rel
        dst = restored / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        os.chmod(dst, src.stat().st_mode & 0o7777)
    os.chmod(restored, os.stat(source).st_mode & 0o7777)
    for rel in paths:
        relpath = Path(rel)
        for parent in relpath.parents:
            if str(parent) == ".":
                break
            src_dir = source / parent
            dst_dir = restored / parent
            dst_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(dst_dir, os.stat(src_dir).st_mode & 0o7777)


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
    assert m["directory_modes"]["."] == (os.stat(source).st_mode & 0o7777)
    assert set(m["directory_modes"]) == {".", "wiki", "raw"}
    assert _snapshot(source) == before, "source must remain unchanged"

    _materialize(restored, source, paths)
    result = verify_restore(m, restored)
    assert result["status"] == "verified"
    assert result["file_count"] == 2
    assert result["total_bytes"] == sum(f["size"] for f in m["files"])
    assert result["rehearsal_only"] is True
    assert result["directory_modes_checked"] is True
    assert result["file_modes_checked"] is True


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
    os.chmod(restored, os.stat(source).st_mode & 0o7777)
    os.chmod(restored / "wiki", os.stat(source / "wiki").st_mode & 0o7777)
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
    expected = hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert result["manifest_sha256"] == expected
    assert result["canonical_manifest_sha256"] == expected


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
        "directory_modes": {".": 0o755, "secrets": 0o755},
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
    assert "directory_modes" in m
    assert _snapshot(source) == before_source
    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(result.stdout, encoding="utf-8")

    _cli_auto_materialize(source, restored, paths)
    before_restored = _snapshot(restored)
    verify = _run_cli(
        ["verify", "--manifest", str(manifest_file), "--restored-root", str(restored)]
    )
    assert verify.returncode == 0, verify.stderr
    out = json.loads(verify.stdout)
    assert out["status"] == "verified"
    assert out["rehearsal_only"] is True
    assert out["directory_modes_checked"] is True
    assert out["file_modes_checked"] is True
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


# ---- P2-B: manifest digest recipes --------------------------------------


def test_canonical_manifest_digest_stable_across_formatting(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md", "raw/evidence.txt"]
    m = _make_manifest(source, paths)
    _materialize(restored, source, paths)
    result = verify_restore(m, restored)
    recipe = hashlib.sha256(
        json.dumps(m, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert result["canonical_manifest_sha256"] == recipe
    assert result["manifest_sha256"] == recipe
    reordered = json.loads(json.dumps(m, indent=4))
    assert verify_restore(reordered, restored)["canonical_manifest_sha256"] == recipe


def test_cli_manifest_artifact_sha256_matches_bytes(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md"]
    _cli_auto_materialize(source, restored, paths)
    m = _make_manifest(source, paths)
    manifest_file = tmp_path / "manifest.json"
    # Write with non-canonical formatting and trailing whitespace/newline.
    artifact_bytes = (json.dumps(m, indent=4, ensure_ascii=False) + "\n\n").encode("utf-8")
    manifest_file.write_bytes(artifact_bytes)
    verify = _run_cli(
        ["verify", "--manifest", str(manifest_file), "--restored-root", str(restored)]
    )
    assert verify.returncode == 0, verify.stderr
    out = json.loads(verify.stdout)
    assert out["manifest_artifact_sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    canonical = hashlib.sha256(
        json.dumps(m, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert out["canonical_manifest_sha256"] == canonical
    assert out["manifest_artifact_sha256"] != out["canonical_manifest_sha256"]


def test_verify_manifest_sha256_alias_is_canonical(tmp_path):
    source, restored = _cluster(tmp_path)
    m = _make_manifest(source, ["raw/evidence.txt"])
    _materialize(restored, source, ["raw/evidence.txt"])
    result = verify_restore(m, restored)
    assert result["manifest_sha256"] == result["canonical_manifest_sha256"]


# ---- P2-B: directory modes ----------------------------------------------


def test_directory_mode_restrictive_roundtrip(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md", "raw/evidence.txt"]
    os.chmod(source, 0o700)
    os.chmod(source / "wiki", 0o750)
    os.chmod(source / "raw", 0o710)
    m = _make_manifest(source, paths)
    assert m["directory_modes"] == {".": 0o700, "wiki": 0o750, "raw": 0o710}
    _materialize(restored, source, paths)
    result = verify_restore(m, restored)
    assert result["directory_modes_checked"] is True


def test_nested_ancestor_directory_modes_recorded(tmp_path):
    source, restored = _cluster(tmp_path)
    _write(source, "wiki/sub/deep/note.md", b"nested\n", 0o640)
    os.chmod(source / "wiki"/ "sub", 0o755)
    os.chmod(source / "wiki"/ "sub" / "deep", 0o770)
    m = _make_manifest(source, ["wiki/sub/deep/note.md"])
    assert set(m["directory_modes"]) == {".", "wiki", "wiki/sub", "wiki/sub/deep"}
    _materialize(restored, source, ["wiki/sub/deep/note.md"])
    assert verify_restore(m, restored)["directory_modes_checked"] is True


@pytest.mark.parametrize("target", [".", "wiki"])
def test_directory_mode_mismatch_rejected(tmp_path, target):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md"]
    m = _make_manifest(source, paths)
    _materialize(restored, source, paths)
    os.chmod(restored if target == "." else restored / target, 0o701)
    with pytest.raises(RecoveryError, match="directory mode"):
        verify_restore(m, restored)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m["directory_modes"].pop("wiki"),
        lambda m: m["directory_modes"].update(extra=0o755),
        lambda m: m["directory_modes"].update({"wiki/sub": 0o755}),
        lambda m: m["directory_modes"].update({"/abs": 0o755}),
        lambda m: m["directory_modes"].update({"wiki/../x": 0o755}),
        lambda m: m["directory_modes"].update({"WiKi": 0o755}),
        lambda m: m["directory_modes"].update({"wiki": "755"}),
        lambda m: m["directory_modes"].update({"wiki": True}),
        lambda m: m["directory_modes"].update({"wiki": -1}),
        lambda m: m["directory_modes"].update({"wiki": 0o10000}),
        lambda m: m.update(directory_modes=[]),
    ],
)
def test_directory_modes_strictly_validated_before_payload_read(tmp_path, monkeypatch, mutate):
    source, restored = _cluster(tmp_path)
    m = _make_manifest(source, ["wiki/note.md"])
    bad = json.loads(json.dumps(m))
    mutate(bad)
    import k2b_recovery as mod

    def boom(*_a, **_k):
        raise AssertionError("payload must not be read for rejected directory_modes")

    monkeypatch.setattr(mod, "_sha256_bounded", boom)
    with pytest.raises(RecoveryError):
        verify_restore(bad, restored)


def test_legacy_schema1_without_directory_modes_accepted(tmp_path):
    source, restored = _cluster(tmp_path)
    m = _make_manifest(source, ["raw/evidence.txt"])
    legacy = json.loads(json.dumps(m))
    legacy.pop("directory_modes")
    _materialize(restored, source, ["raw/evidence.txt"])
    with pytest.raises(RecoveryError, match="legacy"):
        verify_restore(legacy, restored)
    result = verify_restore(legacy, restored, allow_legacy_no_directory_modes=True)
    assert result["status"] == "verified_legacy_no_directory_modes"
    assert result["directory_modes_checked"] is False
    assert result["file_modes_checked"] is True


def test_parent_mode_changed_during_hash_rejected(tmp_path, monkeypatch):
    source, restored = _cluster(tmp_path)
    m = _make_manifest(source, ["wiki/note.md"])
    _materialize(restored, source, ["wiki/note.md"])
    import k2b_recovery as mod

    original = mod._sha256_bounded

    def flaky(path, limit):
        result = original(path, limit)
        os.chmod(restored / "wiki", 0o700)
        return result

    monkeypatch.setattr(mod, "_sha256_bounded", flaky)
    with pytest.raises(RecoveryError):
        verify_restore(m, restored)


def test_source_parent_mode_changed_during_hash_rejected(tmp_path, monkeypatch):
    source, _ = _cluster(tmp_path)
    import k2b_recovery as mod
    original = mod._sha256_bounded

    def changed(path, limit):
        result = original(path, limit)
        os.chmod(source / "wiki", 0o700)
        return result

    os.chmod(source / "wiki", 0o755)
    monkeypatch.setattr(mod, "_sha256_bounded", changed)
    with pytest.raises(RecoveryError, match="directory mode"):
        manifest(source, ["wiki/note.md"])


def test_cli_manifest_size_ceiling(tmp_path):
    mf = tmp_path / "oversized.json"
    with mf.open("wb") as handle:
        handle.truncate(64 * 1024 * 1024 + 1)
    restored = tmp_path / "restored"
    restored.mkdir()
    result = _run_cli(["verify", "--manifest", str(mf), "--restored-root", str(restored)])
    assert result.returncode != 0
    evidence = json.loads(result.stderr)
    assert "manifest exceeds" in evidence["error"]
    assert "manifest_artifact_sha256" not in evidence
    assert "canonical_manifest_sha256" not in evidence


def test_cli_legacy_requires_explicit_opt_in(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md"]
    _materialize(restored, source, paths)
    m = manifest(source, paths)
    del m["directory_modes"]
    mf = tmp_path / "legacy.json"
    mf.write_text(json.dumps(m))
    args = ["verify", "--manifest", str(mf), "--restored-root", str(restored)]
    refused = _run_cli(args)
    assert refused.returncode != 0
    assert "legacy" in json.loads(refused.stderr)["error"]
    allowed = _run_cli(args + ["--allow-legacy-no-directory-modes"])
    assert allowed.returncode == 0, allowed.stderr
    assert json.loads(allowed.stdout)["directory_modes_checked"] is False


def test_cli_failed_verification_retains_manifest_identifiers(tmp_path):
    source, restored = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(m, indent=2) + "\n")
    result = _run_cli(["verify", "--manifest", str(mf), "--restored-root", str(restored)])
    assert result.returncode != 0
    error = json.loads(result.stderr)
    assert error["manifest_artifact_sha256"] == hashlib.sha256(mf.read_bytes()).hexdigest()
    assert error["canonical_manifest_sha256"] == hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_file_special_permission_bit_roundtrip(tmp_path):
    source, restored = _cluster(tmp_path)
    os.chmod(source / "wiki/note.md", 0o1644)
    m = manifest(source, ["wiki/note.md"])
    assert m["files"][0]["mode"] == 0o1644
    _materialize(restored, source, ["wiki/note.md"])
    assert verify_restore(m, restored)["file_modes_checked"] is True


def test_cli_malformed_json_binds_only_artifact(tmp_path):
    mf = tmp_path / "malformed.json"
    mf.write_bytes(b'{"invalid":')
    result = _run_cli(["verify", "--manifest", str(mf), "--restored-root", str(tmp_path)])
    assert result.returncode != 0
    evidence = json.loads(result.stderr)
    assert evidence["manifest_artifact_sha256"] == hashlib.sha256(mf.read_bytes()).hexdigest()
    assert "canonical_manifest_sha256" not in evidence


def test_cli_utf8_bom_keeps_exact_artifact_hash(tmp_path):
    source, restored = _cluster(tmp_path)
    _materialize(restored, source, ["wiki/note.md"])
    m = manifest(source, ["wiki/note.md"])
    mf = tmp_path / "bom.json"
    mf.write_bytes(b"\xef\xbb\xbf" + json.dumps(m).encode())
    result = _run_cli(["verify", "--manifest", str(mf), "--restored-root", str(restored)])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["manifest_artifact_sha256"] == hashlib.sha256(mf.read_bytes()).hexdigest()


def test_api_receipt_discloses_no_artifact_read(tmp_path):
    source, restored = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    _materialize(restored, source, ["wiki/note.md"])
    assert verify_restore(m, restored)["manifest_artifact_checked"] is False
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(m))
    result = _run_cli(["verify", "--manifest", str(mf), "--restored-root", str(restored)])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["manifest_artifact_checked"] is True


def test_cli_receipt_binds_verified_locations(tmp_path):
    source, restored = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    _materialize(restored, source, ["wiki/note.md"])
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(m))
    result = _run_cli(["verify", "--manifest", str(mf), "--restored-root", str(restored)])
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["manifest_path"] == str(mf.resolve())
    assert receipt["restored_root"] == str(restored.resolve())
    assert receipt["restored_root_device"] == restored.stat().st_dev


def test_manifest_generation_refuses_unverifiable_serialization(tmp_path, monkeypatch):
    import k2b_recovery as mod
    source, _ = _cluster(tmp_path)
    monkeypatch.setattr(mod, "MAX_MANIFEST_BYTES", 100)
    with pytest.raises(RecoveryError, match="serialized manifest"):
        manifest(source, ["wiki/note.md"])


def test_canonical_identity_preserves_file_array_order(tmp_path):
    source, restored = _cluster(tmp_path)
    paths = ["wiki/note.md", "raw/evidence.txt"]
    m = manifest(source, paths)
    _materialize(restored, source, paths)
    reordered = json.loads(json.dumps(m))
    reordered["files"].reverse()
    first = verify_restore(m, restored)
    second = verify_restore(reordered, restored)
    assert first["status"] == second["status"] == "verified"
    assert first["canonical_manifest_sha256"] != second["canonical_manifest_sha256"]


def test_nonstring_directory_key_raises_recovery_error(tmp_path):
    source, restored = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    m["directory_modes"].update({3: 0o755, "extra": 0o755})
    with pytest.raises(RecoveryError):
        verify_restore(m, restored)


def test_manifest_cli_uses_guarded_serialization(tmp_path):
    import k2b_recovery as mod
    source, _ = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    result = _run_cli(["manifest", "--root", str(source), "--approved-path", "wiki/note.md"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == mod.serialize_manifest(m)


def test_root_identity_changed_during_hash_rejected(tmp_path, monkeypatch):
    import k2b_recovery as mod
    from types import SimpleNamespace
    source, restored = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    _materialize(restored, source, ["wiki/note.md"])
    original_hash, original_stat = mod._sha256_bounded, mod._lstat
    changed = False

    def hashed(path, limit):
        nonlocal changed
        result = original_hash(path, limit)
        changed = True
        return result

    def observed(path):
        st = original_stat(path)
        if path == restored and changed:
            return SimpleNamespace(st_dev=st.st_dev, st_ino=st.st_ino+1, st_size=st.st_size, st_mtime_ns=st.st_mtime_ns, st_ctime_ns=st.st_ctime_ns, st_mode=st.st_mode)
        return st

    monkeypatch.setattr(mod, "_sha256_bounded", hashed)
    monkeypatch.setattr(mod, "_lstat", observed)
    with pytest.raises(RecoveryError, match="root"):
        verify_restore(m, restored)


def test_cli_read_race_binds_observed_bytes(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("race_cli", CLI)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    mf = tmp_path / "manifest.json"
    mf.write_bytes(b'{"synthetic": true}')
    original = cli._stat_signature
    calls = 0

    def changed_signature(st):
        nonlocal calls
        calls += 1
        return original(st) + (calls >= 4,)

    monkeypatch.setattr(cli, "_stat_signature", changed_signature)
    assert cli.main(["verify", "--manifest", str(mf), "--restored-root", str(tmp_path)]) == 1
    evidence = json.loads(capsys.readouterr().err)
    assert "changed while reading" in evidence["error"]
    assert evidence["observed_bytes_sha256"] == hashlib.sha256(mf.read_bytes()).hexdigest()
    assert "manifest_artifact_sha256" not in evidence
    assert "canonical_manifest_sha256" not in evidence


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_cli_nonstandard_json_constant_has_no_canonical_hash(tmp_path, constant):
    mf = tmp_path / "nonstandard.json"
    mf.write_text('{"schema_version": ' + constant + '}')
    result = _run_cli(["verify", "--manifest", str(mf), "--restored-root", str(tmp_path)])
    assert result.returncode != 0
    evidence = json.loads(result.stderr)
    assert "nonstandard JSON" in evidence["error"]
    assert evidence["manifest_artifact_sha256"] == hashlib.sha256(mf.read_bytes()).hexdigest()
    assert "canonical_manifest_sha256" not in evidence


def test_cli_float_overflow_has_typed_error(tmp_path):
    mf = tmp_path / "overflow.json"
    mf.write_text('{"schema_version": 1e400}')
    result = _run_cli(["verify", "--manifest", str(mf), "--restored-root", str(tmp_path)])
    evidence = json.loads(result.stderr)
    assert result.returncode != 0
    assert "out-of-range" in evidence["error"]
    assert not evidence["error"].startswith("ValueError:")
    assert "canonical_manifest_sha256" not in evidence


def test_source_mode_changed_during_serialization_rejected(tmp_path, monkeypatch):
    import k2b_recovery as mod
    source, _ = _cluster(tmp_path)
    os.chmod(source / "wiki", 0o755)
    original = mod.serialize_manifest

    def changed(data):
        output = original(data)
        os.chmod(source / "wiki", 0o700)
        return output

    monkeypatch.setattr(mod, "serialize_manifest", changed)
    with pytest.raises(RecoveryError, match="directory mode"):
        manifest(source, ["wiki/note.md"])


def test_source_file_changed_during_serialization_rejected(tmp_path, monkeypatch):
    import k2b_recovery as mod
    source, _ = _cluster(tmp_path)
    original = mod.serialize_manifest

    def changed(data):
        output = original(data)
        note = source / "wiki/note.md"
        before = note.stat()
        note.write_bytes(note.read_bytes()[::-1])
        os.utime(note, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000))
        return output

    monkeypatch.setattr(mod, "serialize_manifest", changed)
    with pytest.raises(RecoveryError, match="file changed"):
        manifest(source, ["wiki/note.md"])


def test_cli_manifest_disappears_before_final_identity_check(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("deleted_cli", CLI)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    source, restored = _cluster(tmp_path)
    m = manifest(source, ["wiki/note.md"])
    _materialize(restored, source, ["wiki/note.md"])
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(m))
    original = cli.verify_restore

    def removed(*args, **kwargs):
        result = original(*args, **kwargs)
        mf.unlink()
        return result

    monkeypatch.setattr(cli, "verify_restore", removed)
    assert cli.main(["verify", "--manifest", str(mf), "--restored-root", str(restored)]) == 1
    error = json.loads(capsys.readouterr().err)["error"]
    assert "identity check" in error
    assert not error.startswith("FileNotFoundError:")


def test_generation_final_recheck_is_explicitly_stat_level(tmp_path, monkeypatch):
    import k2b_recovery as mod
    source, _ = _cluster(tmp_path)
    note = source / "wiki/note.md"
    original_bytes = note.read_bytes()
    trusted_stat = note.lstat()
    original_stat, original_serializer = mod._lstat, mod.serialize_manifest

    def forged_stat(path):
        return trusted_stat if path == note else original_stat(path)

    def hidden_mutation(data):
        output = original_serializer(data)
        note.write_bytes(original_bytes[::-1])
        return output

    # Simulates a violated kernel-metadata assumption, not a supported writer.
    monkeypatch.setattr(mod, "_lstat", forged_stat)
    monkeypatch.setattr(mod, "serialize_manifest", hidden_mutation)
    m = manifest(source, ["wiki/note.md"])
    assert m["files"][0]["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert m["files"][0]["sha256"] != hashlib.sha256(note.read_bytes()).hexdigest()


def test_unexpected_restored_ancestor_directory_rejected(tmp_path):
    source, restored = _cluster(tmp_path)
    m = _make_manifest(source, ["wiki/note.md"])
    _materialize(restored, source, ["wiki/note.md"])
    (restored / "other").mkdir()
    with pytest.raises(RecoveryError):
        verify_restore(m, restored)
