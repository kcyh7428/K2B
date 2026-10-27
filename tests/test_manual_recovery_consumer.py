"""Independent consumer checks. Only invented temporary data is exercised."""
import json
import hashlib
import os
import subprocess
import time
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
import manual_recovery_contract as contract


def calibrated_window(stage, measured=0.1, kind="metadata", seconds=60):
    record = {"version": 1, "stages": {stage: {
        "kind": kind, "measured_seconds": measured, "receipt_sha256": "2" * 64}}}
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return contract.Window(seconds, calibration=record, trusted_calibration_sha256=digest)


@pytest.fixture
def component_root():
    configured = os.environ.get("K2B_STANDARD_COMPONENT_ROOT")
    assert configured, "explicit frozen component root required"
    return Path(configured)


@pytest.fixture
def component(component_root):
    return contract.load_component(component_root / contract.P2_TEST_REL,
                                   component_root / contract.P2_METHOD_REL)


def selection_fixture(component, tmp_path):
    root = tmp_path / "source"
    (root / "history").mkdir(parents=True)
    (root / "history/retained.txt").write_bytes(b"invented evidence")
    window = contract.Window(60)
    selection = contract.prepare_selection(component, root, ["history/retained.txt"], [], window)
    return root, window, selection


def test_selection_control_is_json_serializable(component, tmp_path):
    _, _, selection = selection_fixture(component, tmp_path)
    assert json.loads(json.dumps(selection))["files"]


def test_parent_membership_is_not_mutable_under_original_binding(component, tmp_path):
    root, window, selection = selection_fixture(component, tmp_path)
    ledger = component.shasum_files([str(root / "history/retained.txt")])
    bound = contract.bind_hashes(component, selection, ledger, window)
    (root / "history/new.txt").write_bytes(b"new evidence")
    bound["membership"]["history"].append("new.txt")
    bound["membership"]["history"].sort()
    with pytest.raises(contract.ContractError):
        contract.check_source_unchanged(component, bound, ledger, window)


def test_root_membership_change_refuses(component, tmp_path):
    root, window, selection = selection_fixture(component, tmp_path)
    ledger = component.shasum_files([str(root / "history/retained.txt")])
    bound = contract.bind_hashes(component, selection, ledger, window)
    (root / "new-root-evidence.txt").write_bytes(b"new evidence")
    with pytest.raises(contract.ContractError):
        contract.check_source_unchanged(component, bound, ledger, window)


def test_existing_files_are_not_proof_of_a_delivered_commit(component_root):
    with pytest.raises(contract.ContractError):
        contract.load_component(component_root / contract.P2_TEST_REL,
                                component_root / contract.P2_METHOD_REL,
                                execution=True, delivered_repo=str(component_root),
                                delivered_commit="a" * 40)


def test_component_symlink_ancestor_refuses(component_root, tmp_path):
    link = tmp_path / "component-alias"
    link.symlink_to(component_root, target_is_directory=True)
    with pytest.raises(contract.ContractError):
        contract.load_component(link / contract.P2_TEST_REL, link / contract.P2_METHOD_REL)


def test_supervisor_drains_result_larger_than_pipe():
    payload = {"value": "x" * (2 * 1024 * 1024)}
    assert contract.supervise_step("large", lambda: payload,
                                   calibrated_window("large", seconds=30), 10, 0.1) == payload


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_window_rejects_nonfinite_clock(value):
    with pytest.raises(contract.ContractError):
        contract.Window(60, clock=lambda: value)


def test_member_count_includes_root_files_and_parents(component, tmp_path):
    _, _, selection = selection_fixture(component, tmp_path)
    assert selection["profile"]["member_count"] == 3


def test_empty_parent_is_bound_to_source_membership(component, tmp_path):
    root, window, _ = selection_fixture(component, tmp_path)
    (root / "empty").mkdir()
    selection = contract.prepare_selection(component, root, ["history/retained.txt"],
                                           ["empty"], window)
    ledger = component.shasum_files([str(root / "history/retained.txt")])
    bound = contract.bind_hashes(component, selection, ledger, window)
    (root / "empty/unexpected.txt").write_bytes(b"invented")
    with pytest.raises(contract.ContractError):
        contract.check_source_unchanged(component, bound, ledger, window)


def test_delivered_component_requires_exact_committed_blobs(component_root, tmp_path):
    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}

    def git(*args, data=None):
        return subprocess.run(["/usr/bin/git", "-C", str(repo), *args], input=data,
                              env=env, check=True, capture_output=True,
                              timeout=10).stdout.strip()

    git("init", "--template=", "-q")
    for rel in [contract.P2_TEST_REL, contract.P2_METHOD_REL]:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((component_root / rel).read_bytes())
    git("add", "--", contract.P2_TEST_REL, contract.P2_METHOD_REL)
    tree = git("write-tree")
    commit = git("commit-tree", tree.decode(), data=b"Invented component fixture\n").decode()
    result = contract.load_component(repo / contract.P2_TEST_REL,
                                     repo / contract.P2_METHOD_REL,
                                     execution=True, delivered_repo=str(repo),
                                     delivered_commit=commit)
    assert callable(result.source_signature)
    (repo / contract.P2_TEST_REL).write_bytes(b"# changed fixture\n")
    with pytest.raises(contract.ContractError):
        contract.load_component(repo / contract.P2_TEST_REL, repo / contract.P2_METHOD_REL,
                                execution=True, delivered_repo=str(repo),
                                delivered_commit=commit)


def test_destination_checks_long_members_beyond_shorter_prefix(component, tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    name = "f" * 200
    (root / name).write_bytes(b"invented")
    window = contract.Window(60)
    selection = contract.prepare_selection(component, root, [name], [], window)
    remaining = 850 - len(str(tmp_path).encode()) - 1
    parts = []
    while remaining > 240:
        parts.append("p" * 240)
        remaining -= 241
    if remaining:
        parts.append("q" * remaining)
    prefix = "/".join(parts)
    assert len(str(tmp_path / prefix).encode()) < 900
    assert len(str(tmp_path / prefix / name).encode()) > 900
    with pytest.raises(contract.ContractError):
        contract.validate_destination_profile(selection, tmp_path, prefix, window)


def test_destination_cannot_be_inside_selected_source(component, tmp_path):
    root, window, selection = selection_fixture(component, tmp_path)
    with pytest.raises(contract.ContractError):
        contract.validate_destination_profile(selection, root, "inactive", window)


def test_source_prefix_components_are_inside_the_same_envelope(component, tmp_path):
    source = tmp_path / ("s" * 250)
    source.mkdir()
    (source / "file.txt").write_bytes(b"invented")
    with pytest.raises(contract.ContractError):
        contract.prepare_selection(component, source, ["file.txt"], [], contract.Window(60))


def test_existing_destination_prefix_components_are_bounded(component, tmp_path):
    _, window, selection = selection_fixture(component, tmp_path)
    parent = tmp_path / ("d" * 250)
    parent.mkdir()
    with pytest.raises(contract.ContractError):
        contract.validate_destination_profile(selection, parent, "fresh", window)


@pytest.mark.parametrize("sibling", ["s" * 250, "unselected\nname\\.txt"])
def test_unselected_names_remain_observations_not_archive_members(component, tmp_path, sibling):
    root, window, _ = selection_fixture(component, tmp_path)
    (root / sibling).write_bytes(b"invented unselected data")
    selection = contract.prepare_selection(component, root, ["history/retained.txt"], [], window)
    ledger = component.shasum_files([str(root / "history/retained.txt")])
    bound = contract.bind_hashes(component, selection, ledger, window)
    assert sibling in bound["membership"]["."]
    assert [row["rel"] for row in bound["files"]] == ["history/retained.txt"]
    assert contract.check_source_unchanged(component, bound, ledger, window)["unchanged"]
    (root / sibling).unlink()
    with pytest.raises(contract.ContractError, match="source_drift"):
        contract.check_source_unchanged(component, bound, ledger, window)


def test_unbound_and_changed_calibration_cannot_start_phase():
    with pytest.raises(contract.ContractError, match="calibration_missing"):
        contract.Window(60).begin_phase("hash", 30, 0)
    window = calibrated_window("hash", measured=1, kind="hash")
    with pytest.raises(contract.ContractError, match="calibration_mismatch"):
        window.begin_phase("hash", 120, 0)
    with pytest.raises(contract.ContractError, match="calibration_mismatch"):
        window.begin_phase("hash", 121, 1)
    assert window.begin_phase("hash", 30, 1) > 0


def test_step_cannot_run_before_parent_accepts_group(monkeypatch, tmp_path):
    started = tmp_path / "function-started"

    def function():
        started.write_bytes(b"started")
        os.fork()
        time.sleep(3)

    # Simulate parent scheduling that never consumes the readiness byte.
    def never_read(*args):
        time.sleep(0.01)
        return [], [], []

    monkeypatch.setattr(contract.select, "select", never_read)
    with pytest.raises(contract.ContractError, match="step_timeout"):
        contract.supervise_step("handshake", function,
                                calibrated_window("handshake"), 0.5, 0.1)
    assert not started.exists()


def test_step_exception_is_distinct_from_timeout():
    def crash():
        raise RuntimeError("invented private error must not be returned")

    with pytest.raises(contract.ContractError, match="^step_failed$"):
        contract.supervise_step("crash", crash, calibrated_window("crash"), 10, 0.1)


def test_empty_and_alias_only_parents_survive_exact_p2_restore(component, tmp_path):
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    (source / "nested/empty").mkdir(parents=True)
    (source / "alias-parent").mkdir()
    (source / "target.txt").write_bytes(b"invented target")
    (source / "alias-parent/link").symlink_to("../target.txt")
    window = contract.Window(60)
    selection = contract.prepare_selection(component, source, ["target.txt"],
                                           ["nested/empty"], window,
                                           alias={"path": "alias-parent/link", "target": "../target.txt"})
    bound = contract.bind_hashes(component, selection,
                                 component.shasum_files([str(source / "target.txt")]), window)
    entries = tmp_path / "entries.nul"
    entries.write_bytes(b"".join(os.fsencode(rel) + b"\0" for rel in sorted(
        set(bound["parent_modes"]) | {"target.txt"})))
    archive = tmp_path / "archive.tar"
    component.tar_create(archive, source, entries)
    contract.inspect_archive(component, archive, bound, component.shasum_one(archive), window)
    dest = tmp_path / "inactive"
    dest.mkdir(mode=0o700)
    component.tar_extract(archive, dest)
    out = component.shasum_files([str(dest / "target.txt")])
    assert contract.compare_restore(component, bound, dest, out, window)["verified"]
    control = contract.alias_control(bound)
    # Correct bytes with wrong metadata must refuse before creating the alias.
    target = dest / "target.txt"
    mode = target.stat().st_mode & 0o777
    target.chmod(mode ^ 0o100)
    with pytest.raises(contract.ContractError, match="alias_target_metadata"):
        contract.restore_declared_alias(component, bound, dest, control,
                                        hashlib.sha256(control).hexdigest(), window)
    assert not os.path.lexists(dest / "alias-parent/link")
    target.chmod(mode)
    assert contract.restore_declared_alias(component, bound, dest, control,
                                           hashlib.sha256(control).hexdigest(), window)["restored"]
    assert os.readlink(dest / "alias-parent/link") == "../target.txt"
