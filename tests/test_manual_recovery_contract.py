"""Synthetic consumer-contract tests over invented temporary fixtures only.

Requires the frozen P2 component via K2B_STANDARD_COMPONENT_ROOT (a directory
with plans/2026-10-27_k2b-standard-tool-recovery-component.md and
tests/test_recovery_standard_tools.py) whose exact hashes must match the
fixed approved values; unavailable or mismatched roots fail rather than skip.

No production paths, real roots, network calls, installations or live runs.
All fixture data is invented and confined to tmp_path.
"""

import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "lib"))

import manual_recovery_contract as mrc  # noqa: E402

COMPONENT_ROOT = os.environ.get("K2B_STANDARD_COMPONENT_ROOT")
TEST_REL = "tests/test_recovery_standard_tools.py"
PLAN_REL = "plans/2026-10-27_k2b-standard-tool-recovery-component.md"


class FakeClock:
    def __init__(self, start=1000.0):
        self.t = float(start)

    def __call__(self):
        return self.t

    def advance(self, delta):
        self.t += float(delta)


def _calibrated_window(stages, seconds=600, clock=time.monotonic):
    # Invented calibration records exercise admission, not target readiness.
    record = {"version": 1, "stages": {
        name: {"kind": kind, "measured_seconds": measured, "receipt_sha256": "1" * 64}
        for name, (kind, measured) in stages.items()}}
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return mrc.Window(seconds, clock=clock, calibration=record,
                      trusted_calibration_sha256=digest)


@pytest.fixture(scope="session")

def component():
    root = COMPONENT_ROOT
    if not root:
        pytest.fail("K2B_STANDARD_COMPONENT_ROOT must be set to the frozen P2 directory")
    base = Path(root)
    test_file = base / TEST_REL
    plan_file = base / PLAN_REL
    if not test_file.is_file() or not plan_file.is_file():
        pytest.fail("K2B_STANDARD_COMPONENT_ROOT does not contain the frozen P2 files")
    return mrc.load_component(test_file, plan_file)


def _new_root(base: Path, name: str = "root") -> Path:
    p = base / name
    os.mkdir(str(p), 0o700)
    return p


def _write(path: Path, data: bytes, mode: int = 0o664) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(str(path), mode)
    return path


def _set_ns(path: Path, ns: int) -> None:
    os.utime(str(path), ns=(ns, ns))


def _build_broad_tree(base: Path, unicode_name="\u00e9t\u00e9.txt"):
    root = _new_root(base, "source_root")
    files = [
        ("root_note.txt", b"invented root note\n"),
        ("legacy/notes.txt", b"invented legacy notes\n"),
        (".stversions/secret.txt", b"invented hidden version\n"),
        ("private/receipts/r1.receipt", b"invented receipt\n"),
        ("assets/alpha.bin", bytes(range(256)) * 4),
        ("assets/beta.bin", b"B" * 64),
        (unicode_name, "utf-8 name content\n".encode("utf-8")),
    ]
    base_ns = 1_650_000_000_000_000_000
    for i, (rel, data) in enumerate(files):
        p = _write(root / rel, data)
        _set_ns(p, base_ns + i * 1_000_000_007)
    for dirpath, dirnames, _ in os.walk(str(root)):
        for d in dirnames:
            os.chmod(os.path.join(dirpath, d), 0o775)
    os.chmod(str(root), 0o775)
    return root, [rel for rel, _ in files]


def _prepare(component, base, alias=None, unicode_name="\u00e9t\u00e9.txt"):
    root, rels = _build_broad_tree(base, unicode_name)
    clock = FakeClock()
    window = mrc.Window(600, clock=clock)
    selection = mrc.prepare_selection(component, root, rels, [], window, alias=alias)
    return root, rels, selection, clock, window


# ---------------------------------------------------------------------------
# Component loading
# ---------------------------------------------------------------------------


def test_component_loads_with_exact_hashes(component):
    for name in ("source_signature", "admit_regular", "admit_mode", "tool_env",
                 "parse_ledger", "compare_ledgers"):
        assert callable(getattr(component, name))


def test_component_corruption_fails(tmp_path):
    if not COMPONENT_ROOT:
        pytest.fail("K2B_STANDARD_COMPONENT_ROOT must be set")
    base = Path(COMPONENT_ROOT)
    plan = base / PLAN_REL
    fake_test = tmp_path / "fake_test.py"
    fake_test.write_bytes(b"# corrupted\n")
    with pytest.raises(mrc.ContractError) as exc:
        mrc.load_component(fake_test, plan)
    assert exc.value.reason == "component_hash_mismatch"
    fake_plan = tmp_path / "fake_plan.md"
    fake_plan.write_bytes(b"# corrupted\n")
    with pytest.raises(mrc.ContractError) as exc:
        mrc.load_component(base / TEST_REL, fake_plan)
    assert exc.value.reason == "component_hash_mismatch"


def test_execution_requires_delivery_commit(component):
    if not COMPONENT_ROOT:
        pytest.fail("K2B_STANDARD_COMPONENT_ROOT must be set")
    base = Path(COMPONENT_ROOT)
    test_file = base / TEST_REL
    plan_file = base / PLAN_REL
    with pytest.raises(mrc.ContractError) as exc:
        mrc.load_component(test_file, plan_file, execution=True)
    assert exc.value.reason == "delivery_commit"
    with pytest.raises(mrc.ContractError) as exc:
        mrc.load_component(test_file, plan_file, execution=True,
                           delivered_repo="/invented/repo")
    assert exc.value.reason == "delivery_commit"
    with pytest.raises(mrc.ContractError) as exc:
        mrc.load_component(test_file, plan_file, execution=True,
                           delivered_commit="A" * 40,
                           delivered_repo="/invented/repo")
    assert exc.value.reason == "delivery_commit"
    with pytest.raises(mrc.ContractError) as exc:
        mrc.load_component(test_file, plan_file, execution=True,
                           delivered_commit="a" * 40,
                           delivered_repo="/invented/missing-repo")
    assert exc.value.reason == "delivered_repo"


def test_component_symlink_ancestor_refused(tmp_path):
    if not COMPONENT_ROOT:
        pytest.fail("K2B_STANDARD_COMPONENT_ROOT must be set")
    link = tmp_path / "link.py"
    os.symlink("/nonexistent", str(link))
    with pytest.raises(mrc.ContractError):
        mrc.load_component(link, tmp_path / "plan.md")


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


def test_window_bounds_and_single_deadline():
    with pytest.raises(mrc.ContractError):
        mrc.Window(0)
    with pytest.raises(mrc.ContractError):
        mrc.Window(-1)
    with pytest.raises(mrc.ContractError):
        mrc.Window(601)
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    w.check()
    assert w.remaining() == pytest.approx(600.0)
    clock.advance(300)
    assert w.remaining() == pytest.approx(300.0)
    clock.advance(300)
    with pytest.raises(mrc.ContractError) as exc:
        w.check()
    assert exc.value.reason == "window_expired"


def test_phase_allowance_and_effective_timeout():
    clock = FakeClock()
    w = _calibrated_window({"hash": ("hash", 10)}, clock=clock)
    eff = w.begin_phase("hash", 120, 10)
    assert eff == pytest.approx(120.0)
    with pytest.raises(mrc.ContractError) as exc:
        w.begin_phase("hash", 120, 10)
    assert exc.value.reason == "phase_reentry"
    clock.advance(100)
    assert w.effective_timeout("hash") == pytest.approx(20.0)
    clock.advance(500)
    with pytest.raises(mrc.ContractError):
        w.check()


def test_phase_requires_headroom():
    clock = FakeClock()
    w = _calibrated_window({"scan": ("metadata", 29), "late": ("metadata", 10)}, seconds=60, clock=clock)
    with pytest.raises(mrc.ContractError) as exc:
        w.begin_phase("scan", 60, 29)
    assert exc.value.reason == "insufficient_headroom"
    clock.advance(40)
    with pytest.raises(mrc.ContractError):
        w.begin_phase("late", 30, 10)


def test_shared_batch_budget_nonrenewable():
    clock = FakeClock()
    w = _calibrated_window({"hash_phase_1": ("hash", 5)}, clock=clock)
    w.begin_phase("hash_phase_1", 120, 5)
    for _ in range(3):
        with pytest.raises(mrc.ContractError):
            w.begin_phase("hash_phase_1", 120, 5)


# ---------------------------------------------------------------------------
# Selection preparation
# ---------------------------------------------------------------------------


def test_broad_hidden_legacy_private_selection(component, tmp_path):
    root, rels, selection, _, _ = _prepare(component, tmp_path)
    assert selection["profile"]["file_count"] == len(rels)
    assert selection["root_mode"] == 0o775
    digests = {entry["rel"] for entry in selection["files"]}
    assert ".stversions/secret.txt" in digests
    assert "private/receipts/r1.receipt" in digests
    recorded = selection["selection_digest"]
    assert isinstance(recorded, str) and len(recorded) == 64


def test_selection_rejects_absolute_and_traversal(component, tmp_path):
    root = _new_root(tmp_path, "r")
    _write(root / "ok.txt", b"ok")
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    for bad in (["/etc/passwd"], ["../escape"], ["a/../../b"], ["a\\b"],
                ["a\x01b"], [""], ["a//b"]):
        with pytest.raises(mrc.ContractError):
            mrc.prepare_selection(component, root, bad, [], w)


def test_selection_duplicate_and_normalization_collision(component, tmp_path):
    root = _new_root(tmp_path, "r")
    _write(root / "a.txt", b"a")
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.prepare_selection(component, root, ["a.txt", "a.txt"], [], w)
    assert exc.value.reason == "selection_duplicate"
    with pytest.raises(mrc.ContractError) as exc:
        mrc.prepare_selection(component, root, ["a.txt", "A.txt"], [], w)
    assert exc.value.reason == "selection_duplicate"


def test_selection_rejects_special_bits(component, tmp_path):
    root = _new_root(tmp_path, "r")
    p = _write(root / "s.bin", b"x", 0o4644)
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    with pytest.raises(mrc.ContractError):
        mrc.prepare_selection(component, root, ["s.bin"], [], w)
    os.chmod(str(p), 0o600)
    os.chmod(str(root), 0o1755)
    try:
        with pytest.raises(mrc.ContractError):
            mrc.prepare_selection(component, root, ["s.bin"], [], w)
    finally:
        os.chmod(str(root), 0o700)


def test_selection_empty_dirs_must_be_empty(component, tmp_path):
    root = _new_root(tmp_path, "r")
    _write(root / "f.txt", b"f")
    (root / "emptyish").mkdir()
    _write(root / "emptyish" / "child.txt", b"c")
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.prepare_selection(component, root, ["f.txt"], ["emptyish"], w)
    assert exc.value.reason == "empty_dir_not_empty"


def test_selection_requires_nonempty_file_list(component, tmp_path):
    root = _new_root(tmp_path, "r")
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.prepare_selection(component, root, [], [], w)
    assert exc.value.reason == "selection_empty"


def test_real_over_16mib_file_preserved(component, tmp_path):
    root = _new_root(tmp_path, "source_root")
    payload = b""
    seed = bytes(((i * 131) % 256) for i in range(4096))
    while len(payload) < 17_000_001:
        payload += seed
    payload = payload[:17_000_001]
    p = _write(root / "assets" / "big.bin", payload)
    _set_ns(p, 1_650_000_000_123_456_789)
    os.chmod(str(root / "assets"), 0o775)
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    selection = mrc.prepare_selection(component, root, ["assets/big.bin"], [], w)
    sig = selection["files"][0]["signature"]
    assert sig["size"] == 17_000_001
    assert sig["size"] > 16 * 1024 * 1024
    assert sig["mtime_ns"] == 1_650_000_000_123_456_789


def test_selection_profile_dimensions(component, tmp_path):
    root = _new_root(tmp_path, "r")
    _write(root / "a" / "b" / "long_name_file.txt", b"abcd")
    _write(root / "root.txt", b"xy")
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    selection = mrc.prepare_selection(component, root, ["a/b/long_name_file.txt", "root.txt"], [], w)
    profile = selection["profile"]
    assert profile["file_count"] == 2
    assert profile["total_bytes"] == 6
    assert profile["size_per_file"] == 4
    assert profile["member_count"] >= 3
    assert profile["component_bytes"] == len("long_name_file.txt")


def test_selection_requires_absolute_root(component, tmp_path):
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.prepare_selection(component, Path("relative"), ["x"], [], w)
    assert exc.value.reason == "root_not_absolute"


def test_selection_symlink_ancestor_refused(component, tmp_path):
    real = _new_root(tmp_path, "real")
    _write(real / "f.txt", b"f")
    link = tmp_path / "linkdir"
    os.symlink(str(real), str(link))
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.prepare_selection(component, link, ["f.txt"], [], w)
    assert exc.value.reason in ("root_symlink_ancestor", "path_symlink")


def test_hardlink_refused(component, tmp_path):
    root = _new_root(tmp_path, "r")
    a = _write(root / "a.txt", b"a")
    os.link(str(a), str(root / "b.txt"))
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    with pytest.raises(mrc.ContractError):
        mrc.prepare_selection(component, root, ["a.txt"], [], w)


def test_alias_validation_and_reconstruction(component, tmp_path):
    root = _new_root(tmp_path, "source_root")
    _write(root / "target.txt", b"invented target\n")
    os.symlink("target.txt", str(root / "alias.txt"))
    os.chmod(str(root), 0o775)
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    selection = mrc.prepare_selection(
        component, root, ["target.txt"], [], w,
        alias={"path": "alias.txt", "target": "target.txt"},
    )
    assert selection["alias"]["path"] == "alias.txt"
    assert selection["alias"]["target"] == "target.txt"


def test_alias_escaping_refused(component, tmp_path):
    root = _new_root(tmp_path, "source_root")
    _write(root / "target.txt", b"x")
    os.symlink("../outside.txt", str(root / "alias.txt"))
    os.chmod(str(root), 0o775)
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.prepare_selection(component, root, ["target.txt"], [], w,
                              alias={"path": "alias.txt", "target": "../outside.txt"})
    assert exc.value.reason in ("alias_escapes_root", "alias_target_ancestor",
                                "alias_target_not_regular")


def test_alias_target_not_selected_refused(component, tmp_path):
    root = _new_root(tmp_path, "source_root")
    _write(root / "target.txt", b"x")
    _write(root / "other.txt", b"y")
    os.symlink("other.txt", str(root / "alias.txt"))
    os.chmod(str(root), 0o775)
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.prepare_selection(component, root, ["target.txt"], [], w,
                              alias={"path": "alias.txt", "target": "other.txt"})
    assert exc.value.reason == "alias_target_not_selected"


def test_validate_destination_profile(component, tmp_path):
    root, rels, selection, clock, window = _prepare(component, tmp_path)
    parent = _new_root(tmp_path, "dest_parent")
    result = mrc.validate_destination_profile(selection, parent, "inactive_run_1", window)
    assert result["prefix"] == "inactive_run_1"
    assert result["dev"] == os.stat(str(parent)).st_dev
    assert result["available_bytes"] >= result["required_bytes"]
    occupied = parent / "occupied"
    occupied.mkdir()
    with pytest.raises(mrc.ContractError):
        mrc.validate_destination_profile(selection, parent, "occupied", window)


def test_validate_destination_profile_relative_parent(component, tmp_path):
    _, _, selection, _, window = _prepare(component, tmp_path)
    with pytest.raises(mrc.ContractError):
        mrc.validate_destination_profile(selection, Path("rel"), "p", window)


# ---------------------------------------------------------------------------
# Hash binding and drift
# ---------------------------------------------------------------------------


def test_bind_hashes_and_source_unchanged(component, tmp_path):
    root, rels, selection, clock, window = _prepare(component, tmp_path)
    abs_paths = [str(root / rel) for rel in rels]
    ledger = "".join("%s  %s\n" % (hashlib.sha256((root / rel).read_bytes()).hexdigest(),
                                     root / rel) for rel in rels)
    bound = mrc.bind_hashes(component, selection, ledger, window)
    assert len(bound["sha256"]) == len(rels)
    again = mrc.check_source_unchanged(component, bound, ledger, window)
    assert again["unchanged"] is True


def test_bind_hashes_detects_ctime_drift(component, tmp_path):
    root, rels, selection, clock, window = _prepare(component, tmp_path)
    ledger = "".join("%s  %s\n" % (hashlib.sha256((root / rel).read_bytes()).hexdigest(),
                                     root / rel) for rel in rels)
    bound = mrc.bind_hashes(component, selection, ledger, window)
    victim = root / rels[0]
    sig = os.lstat(str(victim))
    os.chmod(str(victim), 0o640)
    os.utime(str(victim), ns=(sig.st_mtime_ns, sig.st_mtime_ns))
    os.chmod(str(victim), 0o664)
    os.utime(str(victim), ns=(sig.st_mtime_ns, sig.st_mtime_ns))
    with pytest.raises(mrc.ContractError) as exc:
        mrc.check_source_unchanged(component, bound, ledger, window)
    assert exc.value.reason == "source_drift"


def test_check_source_unchanged_requires_binding(component, tmp_path):
    root, rels, selection, _, window = _prepare(component, tmp_path)
    ledger = ""
    with pytest.raises(mrc.ContractError) as exc:
        mrc.check_source_unchanged(component, selection, ledger, window)
    assert exc.value.reason == "selection_unbound"


def test_selection_corruption_detected(component, tmp_path):
    _, _, selection, _, window = _prepare(component, tmp_path)
    selection["profile"]["total_bytes"] += 1
    with pytest.raises(mrc.ContractError) as exc:
        mrc.bind_hashes(component, selection, "", window)
    assert exc.value.reason == "selection_corrupt"


def test_bool_not_accepted_as_int(component, tmp_path):
    _, _, selection, _, window = _prepare(component, tmp_path)
    selection["files"][0]["signature"]["size"] = True
    with pytest.raises(mrc.ContractError) as exc:
        mrc.validate_destination_profile(selection, tmp_path, "p", window)
    assert exc.value.reason in ("selection_corrupt")


# ---------------------------------------------------------------------------
# Standard-tool preservation via the actual P2 functions
# ---------------------------------------------------------------------------


def _require_tools(component):
    for path in ("/usr/bin/tar", "/usr/bin/xargs", "/usr/bin/shasum"):
        if not os.path.exists(path):
            pytest.fail("required standard tool missing: %s" % path)


@pytest.mark.skipif(sys.platform != "darwin", reason="BSD tar proof is macOS-specific")
@pytest.mark.parametrize("unicode_name,exact_restore", [
    ("e\u0301te\u0301.txt", True), ("\u00e9t\u00e9.txt", False)])
def test_actual_p2_roundtrip_broad_selection(component, tmp_path, unicode_name, exact_restore):
    _require_tools(component)
    root, rels, selection, clock, window = _prepare(component, tmp_path, unicode_name=unicode_name)
    run = _new_root(tmp_path, "run")
    control = _new_root(run, "control")
    entries = sorted({rel for rel in rels})
    for rel in list(rels):
        parts = rel.split("/")
        for i in range(1, len(parts)):
            entries.append("/".join(parts[:i]))
    entries.append(".")
    entries = sorted(set(entries))
    nul = b"".join(os.fsencode(e) + b"\0" for e in entries)
    entries_nul = control / "entries.nul"
    fd = os.open(str(entries_nul), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, nul)
    finally:
        os.close(fd)

    abs_paths = [str(root / rel) for rel in rels]
    r1_text = component.shasum_files(abs_paths)
    bound = mrc.bind_hashes(component, selection, r1_text, window)

    archive = control / "transfer.tar"
    component.tar_create(archive, root, entries_nul)
    archive_digest = component.shasum_one(archive)

    inspection = mrc.inspect_archive(component, archive, bound, archive_digest, window)
    assert inspection["archive_digest"] == archive_digest
    assert inspection["member_count"] == len(entries)
    assert inspection["archive_digest"] == component.shasum_one(archive)

    dest = _new_root(run, "inactive_restore")
    component.tar_extract(archive, dest)
    assert component.shasum_one(archive) == archive_digest

    out_files = []
    for dirpath, _, filenames in os.walk(str(dest)):
        for f in filenames:
            out_files.append(os.path.join(dirpath, f))
    out_text = component.shasum_files(out_files)
    if exact_restore:
        result = mrc.compare_restore(component, bound, dest, out_text, window)
        assert result["verified"] is True
    else:
        # The frozen BSD tar command decomposes this original NFC name.
        # Preserve that unavailable-method evidence, never normalize comparison.
        assert unicode_name in os.listdir(root)
        assert unicode_name not in os.listdir(dest)
        with pytest.raises(mrc.ContractError, match="restore_member_set"):
            mrc.compare_restore(component, bound, dest, out_text, window)

    r2_text = component.shasum_files(abs_paths)
    again = mrc.check_source_unchanged(component, bound, r2_text, window)
    assert again["unchanged"] is True


# ---------------------------------------------------------------------------
# Archive inspection negatives
# ---------------------------------------------------------------------------


def _simple_archive(component, tmp_path):
    root = _new_root(tmp_path, "src")
    _write(root / "f.txt", b"invented\n")
    os.chmod(str(root), 0o775)
    clock = FakeClock()
    window = mrc.Window(600, clock=clock)
    selection = mrc.prepare_selection(component, root, ["f.txt"], [], window)
    entries_nul = tmp_path / "e.nul"
    entries_nul.write_bytes(b".\0f.txt\0")
    abs_paths = [str(root / "f.txt")]
    ledger = component.shasum_files(abs_paths)
    bound = mrc.bind_hashes(component, selection, ledger, window)
    archive = tmp_path / "a.tar"
    component.tar_create(archive, root, entries_nul)
    digest = component.shasum_one(archive)
    return bound, archive, digest, window


@pytest.mark.skipif(sys.platform != "darwin", reason="BSD tar proof is macOS-specific")
def test_inspect_archive_digest_mismatch(component, tmp_path):
    _require_tools(component)
    bound, archive, digest, window = _simple_archive(component, tmp_path)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.inspect_archive(component, archive, bound, "0" * 64, window)
    assert exc.value.reason == "archive_digest_mismatch"


@pytest.mark.skipif(sys.platform != "darwin", reason="BSD tar proof is macOS-specific")
def test_inspect_archive_refuses_unexpected_member(component, tmp_path):
    _require_tools(component)
    bound, archive, digest, window = _simple_archive(component, tmp_path)
    extra = _write(tmp_path / "src2" / "x.txt", b"x")
    src2 = tmp_path / "src2"
    os.chmod(src2, bound["root_mode"])
    entries_nul = tmp_path / "e2.nul"
    entries_nul.write_bytes(b".\0x.txt\0")
    bad_archive = tmp_path / "bad.tar"
    component.tar_create(bad_archive, src2, entries_nul)
    bad_digest = component.shasum_one(bad_archive)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.inspect_archive(component, bad_archive, bound, bad_digest, window)
    assert exc.value.reason == "archive_member_unexpected"
    del extra


@pytest.mark.skipif(sys.platform != "darwin", reason="BSD tar proof is macOS-specific")
def test_inspect_archive_refuses_trailing_data(component, tmp_path):
    _require_tools(component)
    bound, archive, digest, window = _simple_archive(component, tmp_path)
    data = archive.read_bytes()
    with open(str(archive), "ab") as fh:
        fh.write(b"hidden trailing member")  # after the zero terminator
    new_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(mrc.ContractError) as exc:
        mrc.inspect_archive(component, archive, bound, new_digest, window)
    assert exc.value.reason in ("archive_trailing_data", "archive_bad_header")
    del data, digest


# ---------------------------------------------------------------------------
# Restore comparison negatives
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "darwin", reason="BSD tar proof is macOS-specific")
def test_compare_restore_detects_mode_mismatch(component, tmp_path):
    _require_tools(component)
    root = _new_root(tmp_path, "src")
    _write(root / "f.txt", b"invented\n")
    os.chmod(str(root), 0o775)
    clock = FakeClock()
    window = mrc.Window(600, clock=clock)
    selection = mrc.prepare_selection(component, root, ["f.txt"], [], window)
    ledger = component.shasum_files([str(root / "f.txt")])
    bound = mrc.bind_hashes(component, selection, ledger, window)
    dest = _new_root(tmp_path, "dest")
    _write(dest / "f.txt", b"invented\n", 0o644)
    os.chmod(str(dest), 0o775)
    out_text = component.shasum_files([str(dest / "f.txt")])
    with pytest.raises(mrc.ContractError) as exc:
        mrc.compare_restore(component, bound, dest, out_text, window)
    assert exc.value.reason == "restore_mode"


@pytest.mark.skipif(sys.platform != "darwin", reason="BSD tar proof is macOS-specific")
def test_compare_restore_detects_extra_member(component, tmp_path):
    _require_tools(component)
    root = _new_root(tmp_path, "src")
    _write(root / "f.txt", b"invented\n")
    os.chmod(str(root), 0o775)
    clock = FakeClock()
    window = mrc.Window(600, clock=clock)
    selection = mrc.prepare_selection(component, root, ["f.txt"], [], window)
    ledger = component.shasum_files([str(root / "f.txt")])
    bound = mrc.bind_hashes(component, selection, ledger, window)
    dest = _new_root(tmp_path, "dest")
    _write(dest / "f.txt", b"invented\n")
    _write(dest / "extra.txt", b"extra\n")
    os.chmod(str(dest), 0o775)
    out_text = component.shasum_files([str(dest / "f.txt"), str(dest / "extra.txt")])
    with pytest.raises(mrc.ContractError) as exc:
        mrc.compare_restore(component, bound, dest, out_text, window)
    assert exc.value.reason == "restore_member_set"


# ---------------------------------------------------------------------------
# Alias control
# ---------------------------------------------------------------------------


def test_alias_control_roundtrip(component, tmp_path):
    root = _new_root(tmp_path, "source_root")
    _write(root / "target.txt", b"target bytes\n")
    os.symlink("target.txt", str(root / "alias.txt"))
    os.chmod(str(root), 0o775)
    clock = FakeClock()
    window = mrc.Window(600, clock=clock)
    selection = mrc.prepare_selection(component, root, ["target.txt"], [], window,
                                      alias={"path": "alias.txt", "target": "target.txt"})
    ledger = component.shasum_files([str(root / "target.txt")])
    bound = mrc.bind_hashes(component, selection, ledger, window)
    control = mrc.alias_control(bound)
    digest = hashlib.sha256(control).hexdigest()

    inactive = _new_root(tmp_path, "inactive")
    _write(inactive / "target.txt", b"target bytes\n")
    _set_ns(inactive / "target.txt", bound["alias"]["target_signature"]["mtime_ns"])
    os.chmod(str(inactive), 0o775)
    result = mrc.restore_declared_alias(component, bound, inactive, control, digest, window)
    assert result["restored"] is True
    assert os.readlink(str(inactive / "alias.txt")) == "target.txt"


def test_alias_control_rejects_wrong_digest(component, tmp_path):
    root = _new_root(tmp_path, "source_root")
    _write(root / "target.txt", b"x")
    os.symlink("target.txt", str(root / "alias.txt"))
    os.chmod(str(root), 0o775)
    clock = FakeClock()
    window = mrc.Window(600, clock=clock)
    selection = mrc.prepare_selection(component, root, ["target.txt"], [], window,
                                      alias={"path": "alias.txt", "target": "target.txt"})
    ledger = component.shasum_files([str(root / "target.txt")])
    bound = mrc.bind_hashes(component, selection, ledger, window)
    control = mrc.alias_control(bound)
    inactive = _new_root(tmp_path, "inactive")
    _write(inactive / "target.txt", b"x")
    with pytest.raises(mrc.ContractError) as exc:
        mrc.restore_declared_alias(component, bound, inactive, control, "0" * 64, window)
    assert exc.value.reason == "control_digest_mismatch"
    assert not os.path.lexists(str(inactive / "alias.txt"))


def test_alias_control_rejects_occupied_path(component, tmp_path):
    root = _new_root(tmp_path, "source_root")
    _write(root / "target.txt", b"x")
    os.symlink("target.txt", str(root / "alias.txt"))
    os.chmod(str(root), 0o775)
    clock = FakeClock()
    window = mrc.Window(600, clock=clock)
    selection = mrc.prepare_selection(component, root, ["target.txt"], [], window,
                                      alias={"path": "alias.txt", "target": "target.txt"})
    ledger = component.shasum_files([str(root / "target.txt")])
    bound = mrc.bind_hashes(component, selection, ledger, window)
    control = mrc.alias_control(bound)
    digest = hashlib.sha256(control).hexdigest()
    inactive = _new_root(tmp_path, "inactive")
    _write(inactive / "target.txt", b"x")
    _write(inactive / "alias.txt", b"occupied")
    with pytest.raises(mrc.ContractError) as exc:
        mrc.restore_declared_alias(component, bound, inactive, control, digest, window)
    assert exc.value.reason == "alias_path_occupied"


# ---------------------------------------------------------------------------
# Process supervision
# ---------------------------------------------------------------------------

def _alive(pid: int) -> bool:
    try:
        out = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return False
    return out == (0, 0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_supervise_step_terminates_in_process_stall(tmp_path):
    marker = tmp_path / "late.marker"

    def stall():
        time.sleep(30)
        marker.write_bytes(b"late")
        return {"done": True}

    clock = FakeClock()
    window = _calibrated_window({"stall": ("metadata", 0.1)}, clock=clock)
    start = time.monotonic()
    with pytest.raises(mrc.ContractError) as exc:
        mrc.supervise_step("stall", stall, window, 0.5, 0.1)
    assert exc.value.reason in ("step_timeout", "cleanup_uncertain")
    assert time.monotonic() - start < 15
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_supervise_step_returns_result(tmp_path):
    def compute():
        return {"value": 42, "names": ["a", "b", "c"]}

    clock = FakeClock()
    window = _calibrated_window({"compute": ("metadata", 0.1)}, clock=clock)
    result = mrc.supervise_step("compute", compute, window, 30, 0.1)
    assert result == {"value": 42, "names": ["a", "b", "c"]}


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")
def test_supervise_step_kills_inherited_child(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    marker = tmp_path / "grandchild.marker"

    def fork_child():
        pid = os.fork()
        if pid == 0:
            time.sleep(2)
            marker.write_bytes(b"child-late")
            os._exit(0)
        pidfile.write_bytes(str(pid).encode("ascii"))
        time.sleep(30)
        return {"done": True}

    clock = FakeClock()
    window = _calibrated_window({"forkchild": ("metadata", 0.1)}, clock=clock)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.supervise_step("forkchild", fork_child, window, 0.5, 0.1)
    assert exc.value.reason in ("step_timeout", "cleanup_uncertain")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not pidfile.exists():
        time.sleep(0.02)
    assert pidfile.exists()
    grandchild = int(pidfile.read_bytes().decode("ascii"))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = _ps_state(grandchild)
        if not state or state.startswith("Z"):
            break
        time.sleep(0.02)
    else:
        raise AssertionError("inherited child survived supervision")
    time.sleep(2.2)
    assert not marker.exists()


def _ps_state(pid: int) -> str:
    state_file = Path("/proc/%d/stat" % pid)
    if state_file.exists():
        try:
            return state_file.read_text().split()[2]
        except OSError:
            return ""
    try:
        out = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return ""
    return "" if out != (0, 0) else "R"


def test_supervise_step_refuses_multithreaded_caller(tmp_path):
    import threading

    started = threading.Event()
    release = threading.Event()

    def hold():
        started.set()
        release.wait(5)

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    started.wait(5)
    try:
        clock = FakeClock()
        window = _calibrated_window({"mt": ("metadata", 0.1)}, clock=clock)
        with pytest.raises(mrc.ContractError) as exc:
            mrc.supervise_step("mt", lambda: {}, window, 10, 0.1)
        assert exc.value.reason == "multithreaded_caller"
        assert window.begin_phase("mt", 10, 0.1) > 0
    finally:
        release.set()
        t.join(5)


def test_supervise_step_rejects_non_callable():
    clock = FakeClock()
    window = mrc.Window(600, clock=clock)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.supervise_step("bad", "not-callable", window, 10, 0.1)
    assert exc.value.reason == "step_callable"


# ---------------------------------------------------------------------------
# Reason hygiene
# ---------------------------------------------------------------------------


def test_contract_error_is_sanitized(tmp_path):
    err = mrc.ContractError("selection_empty")
    assert str(err) == "selection_empty"
    assert err.reason == "selection_empty"
    with pytest.raises(mrc.ContractError):
        mrc.Window(0)


def test_contract_error_no_path_leak(component, tmp_path):
    root = _new_root(tmp_path, "secret_elsewhere")
    clock = FakeClock()
    w = mrc.Window(600, clock=clock)
    with pytest.raises(mrc.ContractError) as exc:
        mrc.prepare_selection(component, root, ["missing.txt"], [], w)
    assert str(root) not in str(exc.value)
