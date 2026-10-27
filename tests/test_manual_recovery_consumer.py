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


def test_independent_operator_commit_substitution_refused():
    configured = os.environ.get("K2B_OPERATOR_COMPONENT_ROOT")
    assert configured, "explicit delivered operator component root required"
    with pytest.raises(contract.ContractError):
        contract.load_operator_components(Path(configured), commit="a" * 40)


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


@pytest.fixture(scope="module")
def operators():
    configured = os.environ.get("K2B_OPERATOR_COMPONENT_ROOT")
    assert configured, "explicit delivered operator component root required"
    return contract.load_operator_components(Path(configured), window=calibrated_window("operator-load"), allowance=10, measured=0.1)


def test_delivered_operator_loader_preserves_existing_modules(operators):
    sentinel = object()
    previous = sys.modules.get("recovery_nas_transport", sentinel)
    again = contract.load_operator_components(Path(os.environ["K2B_OPERATOR_COMPONENT_ROOT"]), window=calibrated_window("operator-load"), allowance=10, measured=0.1)
    assert sys.modules.get("recovery_nas_transport", sentinel) is previous
    assert again.transport is not operators.transport
    assert again.archive.transport is again.transport
    assert again.crypto.transport is again.transport
    assert again.operator_commit == contract.OPERATOR_COMMIT


def test_operator_load_has_one_aggregate_deadline(monkeypatch):
    def stalled(*args):
        time.sleep(10)
    monkeypatch.setattr(contract, "_git_tree_bytes", stalled)
    started = time.monotonic()
    with pytest.raises(contract.ContractError, match="^step_timeout$"):
        contract.load_operator_components(Path(os.environ["K2B_OPERATOR_COMPONENT_ROOT"]),
            window=calibrated_window("operator-load"), allowance=0.5, measured=0.1)
    assert time.monotonic() - started < 5


def test_operator_load_refuses_hash_calibration_kind():
    with pytest.raises(contract.ContractError, match="^calibration_kind$"):
        contract.load_operator_components(Path(os.environ["K2B_OPERATOR_COMPONENT_ROOT"]),
            window=calibrated_window("operator-load", kind="hash"), allowance=120, measured=0.1)


def test_operator_load_refuses_changed_current_and_restores_cache(tmp_path):
    configured = Path(os.environ["K2B_OPERATOR_COMPONENT_ROOT"])
    repo = tmp_path / "component-checkout"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", str(configured), str(repo)],
                   check=True, capture_output=True, timeout=15)
    subprocess.run(["git", "-C", str(repo), "checkout", "--detach", contract.OPERATOR_COMMIT],
                   check=True, capture_output=True, timeout=15)
    path = repo / "scripts/lib/recovery_exact_archive.py"
    path.write_bytes(path.read_bytes() + b"\n# invented mutation\n")
    previous = dict(sys.modules)
    with pytest.raises(contract.ContractError, match="component_hash_mismatch") as caught:
        contract.load_operator_components(repo, window=calibrated_window("operator-load"), allowance=10, measured=0.1)
    assert type(caught.value.__cause__.detail["manifest_index"]) is int
    for name in ["recovery_nas_transport", "recovery_exact_archive", "recovery_resource_holder"]:
        assert sys.modules.get(name) is previous.get(name)


def test_parent_owned_resources_survive_child_and_refuse_child_claim(operators, tmp_path):
    parent = tmp_path.resolve()
    parent.chmod(0o700)
    st = parent.stat()
    identity = {"dev": st.st_dev, "ino": st.st_ino, "uid": st.st_uid, "mode": 0o700}
    window = calibrated_window("owned")
    with operators.resources.open_parent(parent, identity, "1" * 64) as anchor:
        spec = operators.resources.ResourceSpec("note_write", parent / "note.lock.d", "mkdir", anchor)
        with operators.resources.ResourceHolder([spec], window) as holder:
            def child(view):
                try:
                    holder.evidence()
                except operators.resources.ResourceError as exc:
                    return {"child_pid": os.getpid(), "ownership_refused": exc.reason}
                raise AssertionError("child claimed parent")
            returned = contract.owned_step(operators, holder, "owned", child, window, 10, 0.1)
            assert returned["result"]["ownership_refused"] == "fork_refused"
            assert returned["ownership_before"]["pid"] == returned["ownership_after"]["pid"] == os.getpid()
            assert holder.evidence()["resources"][0]["fd"] >= 0
        assert holder.terminal_state == "released_clean"
        assert not spec.path.exists()


@pytest.mark.parametrize("disturbed", [False, True])
def test_owned_step_failure_retains_private_after_evidence(operators, tmp_path, monkeypatch, disturbed):
    parent = tmp_path.resolve()
    parent.chmod(0o700)
    st = parent.stat()
    identity = {"dev": st.st_dev, "ino": st.st_ino, "uid": st.st_uid, "mode": 0o700}
    window = calibrated_window("owned")
    with operators.resources.open_parent(parent, identity, "1" * 64) as anchor:
        spec = operators.resources.ResourceSpec("note_write", parent / "note.lock.d", "mkdir", anchor)
        with operators.resources.ResourceHolder([spec], window) as holder:
            def failed(*args):
                if disturbed:
                    spec.path.rename(parent / "displaced.lock.d")
                    spec.path.mkdir()
                raise contract.ContractError("step_timeout")
            monkeypatch.setattr(contract, "supervise_step", failed)
            try:
                with pytest.raises(contract.ContractError, match="^step_timeout$") as caught:
                    contract.owned_step(operators, holder, "owned", lambda view: {}, window, 10, 0.1)
                private = caught.value.__cause__
                assert isinstance(private, contract._OperatorRecord)
                assert str(private) == "owned_step_failure"
                assert caught.value.__dict__ == {"reason": "step_timeout"}
                assert private.detail["ownership_before"]["pid"] == os.getpid()
                if disturbed:
                    assert private.detail["ownership_after"] is None
                    assert private.detail["ownership_after_error"] == "holder_evidence"
                else:
                    assert private.detail["ownership_after"]["pid"] == os.getpid()
                    assert private.detail["ownership_after_error"] is None
            finally:
                # Synthetic test repair only, never an operator cleanup recipe.
                if disturbed:
                    spec.path.rmdir()
                    (parent / "displaced.lock.d").rename(spec.path)
        assert holder.terminal_state == "released_clean"


@pytest.mark.parametrize("stage,reason", [("create", "capacity_insufficient"), ("extract", "archive_member_count")])
def test_archive_static_refusal_survives_actual_supervisor(operators, tmp_path, monkeypatch, stage, reason):
    parent = tmp_path.resolve()
    parent.chmod(0o700)
    st = parent.stat()
    identity = {"dev": st.st_dev, "ino": st.st_ino, "uid": st.st_uid, "mode": 0o700}
    window = calibrated_window(stage)
    if stage == "create":
        def fail(*args):
            raise operators.archive.ArchiveError(reason)
        monkeypatch.setattr(operators.archive, "create_exact", fail)
        call = lambda view: contract.create_exact_archive(operators, None, None, None, view)
        expected = "archive_create_failed:" + reason
    else:
        def fail(*args):
            raise operators.extract.ExtractError(reason)
        monkeypatch.setattr(operators.extract, "extract_exact", fail)
        call = lambda view: contract.restore_exact_archive(operators, None, None, None, None, None, view)
        expected = "archive_extract_failed:" + reason
    with pytest.raises(contract.ContractError, match="^" + expected + "$"):
        call(window)
    with operators.resources.open_parent(parent, identity, "1" * 64) as anchor:
        spec = operators.resources.ResourceSpec("note_write", parent / "note.lock.d", "mkdir", anchor)
        with operators.resources.ResourceHolder([spec], window) as holder:
            with pytest.raises(contract.ContractError, match="^" + expected + "$") as caught:
                contract.owned_step(operators, holder, stage, call, window, 10, 0.1)
            assert caught.value.__cause__.detail["ownership_after"]["pid"] == os.getpid()
        assert holder.terminal_state == "released_clean"


@pytest.mark.parametrize("kind,reason", [("transport", "capacity_insufficient"), ("transport", "output_hash_mismatch"), ("crypto", "cleanup_failed"), ("sqlite", "logical_digest_mismatch"), ("archive", "source_signature"), ("extract", "archive_member_count")])
def test_pinned_static_error_details_survive_actual_supervisor(operators, kind, reason):
    def fail():
        if kind == "transport":
            raise operators.transport.TransportError(reason)
        if kind == "sqlite":
            raise operators.sqlite.RecoveryError(reason)
        if kind == "archive":
            raise operators.archive.ArchiveError(reason)
        if kind == "extract":
            raise operators.extract.ExtractError(reason)
        raise operators.crypto.CryptoError(reason, phase_reason="gpg_exit_nonzero", cleanup_reason="cleanup_exit")
    result = contract.supervise_step("static", lambda: contract._component_result(operators, fail),
                                     calibrated_window("static"), 10, 0.1)
    with pytest.raises(contract.ContractError, match="^" + kind + "_failed:" + reason + "$") as caught:
        contract._component_value(result)
    if kind == "crypto":
        assert caught.value.__cause__.detail["phase_reason"] == "gpg_exit_nonzero"
        assert caught.value.__cause__.detail["cleanup_reason"] == "cleanup_exit"


@pytest.mark.parametrize("helper,code", [("_verify_output", "fetch_changed"), ("_hash_fd", "output_hash_mismatch")])
def test_parent_output_checks_preserve_static_transport_code(operators, monkeypatch, helper, code):
    def fail(*args):
        raise operators.transport.TransportError(code)
    monkeypatch.setattr(operators.transport, helper, fail)
    call = (lambda: contract._verify_output(operators, None)) if helper == "_verify_output" else (lambda: contract._hash_fd(operators, None, None))
    prefix = "output_verify:" if helper == "_verify_output" else "output_hash:"
    with pytest.raises(contract.ContractError, match="^" + prefix + code + "$"):
        call()


def test_exact_producer_consumer_restores_original_unicode_and_empty_directories(operators, component, tmp_path):
    parent = tmp_path.resolve()
    parent.chmod(0o700)
    source = parent / "source"
    source.mkdir(mode=0o750)
    names = ["café.txt", "de\u0301compose.txt", "中文.txt", "space name.txt"]
    for i, name in enumerate(names):
        (source / name).write_bytes(("invented " + str(i)).encode())
        os.utime(source / name, ns=(1_650_000_000_000_000_001 + i,) * 2)
    (source / "empty").mkdir()
    standard_window = contract.Window(60)
    selection = contract.prepare_selection(component, source, names, ["empty"], standard_window)
    ledger = component.shasum_files([str(source / name) for name in names])
    bound = contract.bind_hashes(component, selection, ledger, standard_window)
    stage = parent / "stage"
    stage.mkdir(mode=0o700)
    window = calibrated_window("produce", kind="archive")
    with operators.transport.prepare_fetch_output(stage / "captured.tar") as output:
        report = contract.supervise_step("produce", lambda: contract.create_exact_archive(
            operators, component, bound, output, contract.phase_view(window, "produce")), window, 30, 0.1)
        assert contract.accept_local_output(operators, output, report,
            contract.phase_view(window, "produce"), kind="archive") is output
        destination = stage / "inactive"
        destination.mkdir(mode=0o700)
        extract_window = calibrated_window("extract", kind="archive")
        contract.supervise_step("extract", lambda: contract.restore_exact_archive(
            operators, component, output.path, bound, report["archive_digest"], destination,
            contract.phase_view(extract_window, "extract")), extract_window, 30, 0.1)
        assert sorted(os.listdir(destination)) == sorted([*names, "empty"])
        restored_ledger = component.shasum_files([str(destination / name) for name in names])
        assert contract.compare_restore(component, bound, destination, restored_ledger, standard_window)["verified"]
        assert contract.check_source_unchanged(component, bound,
            component.shasum_files([str(source / name) for name in names]), standard_window)["unchanged"]
        with output.path.open("ab") as changed:
            changed.write(b"corrupt")
        with pytest.raises(contract.ContractError):
            contract.accept_local_output(operators, output, report,
                contract.phase_view(window, "produce"), kind="archive")


@pytest.fixture
def fixed_local_transport(operators, tmp_path, monkeypatch):
    import pwd
    import shlex
    import socket
    root = tmp_path.resolve()
    root.chmod(0o700)
    user = pwd.getpwuid(os.getuid()).pw_name
    parent = root / user
    parent.mkdir(mode=0o700)
    st = parent.stat()
    binding = {"hostname": socket.gethostname(), "username": user, "uid": os.getuid(),
        "parent": {"dev": st.st_dev, "ino": st.st_ino, "uid": st.st_uid, "mode": 0o700}, "namespace": None}
    actual = subprocess.Popen
    def local(argv, **kwargs):
        if argv[0] != "ssh":
            assert Path(argv[0]).name in {"fixture-gpg", "fixture-gpgconf", "ps", "xargs"}
            return actual(argv, **kwargs)
        assert kwargs.get("shell") is False and "start_new_session" not in kwargs
        command = shlex.split(argv[-1])
        assert command[:2] == ["/usr/bin/python3", "-c"]
        code, config = command[2], json.loads(command[3])
        assert hashlib.sha256(code.encode()).hexdigest() == operators.transport.REMOTE_PROGRAM_SHA256
        config["parent"] = str(parent)
        code = code.replace("HOME_PREFIX = '/volume1/homes/'", "HOME_PREFIX = " + repr(str(root) + "/"))
        return actual([sys.executable, "-c", code, json.dumps(config)], **kwargs)
    monkeypatch.setattr(operators.transport.subprocess, "Popen", local)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/lib"))
    import recovery_orchestration
    return (recovery_orchestration, user + "@192.168.1.250", "/volume1/homes/" + user,
            "manual-invented", binding)


@pytest.fixture
def fixture_crypto(tmp_path):
    gpg = tmp_path.resolve() / "fixture-gpg"
    gpg.write_text("#!" + sys.executable + "\n" + '''import sys
args=sys.argv[1:]
assert '--no-symkey-cache' in args and args[args.index('--pinentry-mode')+1]=='ask'
assert not any(a.startswith('--passphrase') for a in args)
data=sys.stdin.buffer.read()
if '--decrypt' in args:
 assert data.startswith(b'fixture:')
 data=data[8:]
else:
 assert '--symmetric' in args and 'AES256' in args
 data=b'fixture:'+data
sys.stdout.buffer.write(data)
''')
    conf = tmp_path.resolve() / "fixture-gpgconf"
    conf.write_text("#!" + sys.executable + "\nimport sys\nassert sys.argv[3:]==['--kill','gpg-agent']\n")
    for path in [gpg, conf]: path.chmod(0o700)
    return gpg, hashlib.sha256(gpg.read_bytes()).hexdigest(), conf, hashlib.sha256(conf.read_bytes()).hexdigest()


@pytest.mark.parametrize("artifact", ["archive", "control"])
def test_two_artifacts_genuine_fetch_parent_acceptance_and_fixture_decrypt(
        operators, fixed_local_transport, fixture_crypto, tmp_path, artifact):
    args = fixed_local_transport
    root = tmp_path.resolve()
    plaintext = b"invented " + artifact.encode() + b" plaintext"
    original_hash = hashlib.sha256(plaintext).hexdigest()
    source = root / "plain"
    source.write_bytes(plaintext)
    source.chmod(0o600)
    encrypt_home = root / "encrypt-home"
    encrypt_home.mkdir(mode=0o700)
    with source.open("rb") as stream, operators.transport.prepare_fetch_output(root / "cipher") as cipher:
        enc_window = calibrated_window("encrypt")
        encrypted = contract.supervise_step("encrypt", lambda: operators.crypto.crypt(
            "encrypt", stream.fileno(), cipher, encrypt_home, *fixture_crypto,
            original_hash, None, contract.phase_view(enc_window, "encrypt")), enc_window, 30, 0.1)
        contract.accept_local_output(operators, cipher, encrypted, contract.phase_view(enc_window, "encrypt"), kind="crypto")
        created = operators.transport.create(*args, contract.Window(60))
        upload = operators.transport.upload(*args, created, cipher.fd, contract.Window(60), artifact=artifact)
        context = operators.transport.request_context(*args, artifact=artifact)
        with operators.transport.prepare_fetch_output(root / "fetched") as fetched:
            window = calibrated_window("fetch")
            result = contract.fetch_artifact(operators, *args, created, upload, fetched, window,
                artifact=artifact, expected_context_sha256=context,
                expected_cipher_sha256=encrypted["output_sha256"], expected_cipher_size=encrypted["output_size"],
                stage="fetch", allowance=30, measured=0.1)
            assert result is fetched and fetched.receipt["remote"]["artifact"] == artifact
            assert os.fstat(fetched.fd).st_ino != os.fstat(cipher.fd).st_ino
            with operators.transport.prepare_fetch_output(root / "recovered") as recovered:
                decrypt_home = root / "decrypt-home"
                decrypt_home.mkdir(mode=0o700)
                dec_window = calibrated_window("decrypt")
                assert contract.decrypt_artifact(operators, artifact, fetched, recovered, decrypt_home,
                    *fixture_crypto, original_hash, context, dec_window,
                    stage="decrypt", allowance=30, measured=0.1) is recovered
                assert os.pread(recovered.fd, len(plaintext), 0) == plaintext
                assert os.fstat(recovered.fd).st_size == len(plaintext)
            with operators.transport.prepare_fetch_output(root / "swapped") as swapped:
                with pytest.raises(contract.ContractError, match="artifact_mismatch"):
                    contract.decrypt_artifact(operators, "control" if artifact == "archive" else "archive",
                        fetched, swapped, root / "unused-home", *fixture_crypto,
                        original_hash, context, calibrated_window("wrong"), stage="wrong", allowance=30, measured=0.1)
                assert os.fstat(swapped.fd).st_size == 0


def test_missing_remote_slot_cannot_recover_cached_local_upload(operators, fixed_local_transport, tmp_path):
    args = fixed_local_transport
    root = tmp_path.resolve()
    cached = root / "cipher"
    cached.write_bytes(b"invented cached ciphertext")
    cached.chmod(0o600)
    created = operators.transport.create(*args, contract.Window(60))
    with cached.open("rb") as stream:
        upload = operators.transport.upload(*args, created, stream.fileno(), contract.Window(60))
    matches = list((root / args[4]["username"]).rglob("knowledge.tar.gpg"))
    assert len(matches) == 1
    matches[0].unlink()
    with operators.transport.prepare_fetch_output(root / "fetched") as output:
        with pytest.raises(contract.ContractError):
            contract.fetch_artifact(operators, *args, created, upload, output, calibrated_window("fetch"),
                artifact="archive", expected_context_sha256=operators.transport.request_context(*args),
                expected_cipher_sha256=upload["local"]["sha256"], expected_cipher_size=upload["local"]["size"],
                stage="fetch", allowance=30, measured=0.1)
        assert os.fstat(output.fd).st_size == 0 and cached.is_file()


def test_complete_two_artifact_method_uses_recovered_control_and_archive(
        operators, component, fixed_local_transport, fixture_crypto, tmp_path):
    from contextlib import ExitStack
    from test_manual_recovery_contract import _control_fixture, _calibrated_window
    args = fixed_local_transport
    root = tmp_path.resolve()
    collections, bindings, interval, _ = _control_fixture(component, root)
    source = Path(collections[0]["selection"]["root"])
    name = "café.txt"
    (source / name).write_bytes(b"invented exact-name content")
    (source / "declared-link").symlink_to(name)
    names = ["retained.txt", name]
    source_window = _calibrated_window({"produce": ("archive", 0.1)})
    started = time.time_ns()
    selection = contract.prepare_selection(component, source, names, [], source_window,
        alias={"path": "declared-link", "target": name})
    bound = contract.bind_hashes(component, selection,
        component.shasum_files([str(source / n) for n in names]), source_window)
    collections[0]["selection"] = bound
    collections[0]["alias_control_sha256"] = hashlib.sha256(contract.alias_control(bound)).hexdigest()
    stage = root / "stage"
    stage.mkdir(mode=0o700)
    with ExitStack() as stack:
        archive = stack.enter_context(operators.transport.prepare_fetch_output(stage / "original.tar"))
        st = stage.stat()
        anchor = stack.enter_context(operators.resources.open_parent(stage,
            {"dev": st.st_dev, "ino": st.st_ino, "uid": st.st_uid, "mode": 0o700}, "a" * 64))
        holder = operators.resources.ResourceHolder([
            operators.resources.ResourceSpec("note_write", stage / "note.lock.d", "mkdir", anchor)], source_window)
        with holder:
            produced = contract.owned_step(operators, holder, "produce", lambda view:
                contract.create_exact_archive(operators, component, bound, archive, view), source_window, 30, 0.1)
            archive_report = produced["result"]
            contract.accept_local_output(operators, archive, archive_report,
                contract.phase_view(source_window, "produce"), kind="archive")
            assert contract.check_source_unchanged(component, bound,
                component.shasum_files([str(source / n) for n in names]), source_window)["unchanged"]
        interval.update(started_at_ns=started, finished_at_ns=time.time_ns())
        control_raw = contract.encode_control(collections, bindings, interval, [])
        trusted_plaintext = {"archive": archive_report["archive_digest"],
                             "control": hashlib.sha256(control_raw).hexdigest()}
        control = stack.enter_context(operators.transport.prepare_fetch_output(stage / "original-control"))
        assert os.write(control.fd, control_raw) == len(control_raw)
        os.fsync(control.fd)
        stages = {"create": ("metadata", 0.1)}
        for kind in ["archive", "control"]:
            stages.update({kind + "-" + step: ("metadata", 0.1)
                           for step in ["encrypt", "upload", "fetch", "decrypt"]})
        transfer = _calibrated_window(stages)
        created = contract.supervise_step("create", lambda: operators.transport.create(
            *args, contract.phase_view(transfer, "create")), transfer, 30, 0.1)
        recovered = {}
        for kind, plain in [("archive", archive), ("control", control)]:
            cipher = stack.enter_context(operators.transport.prepare_fetch_output(stage / (kind + ".gpg")))
            encrypt_home = stage / (kind + "-encrypt-home")
            encrypt_home.mkdir(mode=0o700)
            phase = kind + "-encrypt"
            encrypted = contract.supervise_step(phase, lambda: operators.crypto.crypt(
                "encrypt", plain.fd, cipher, encrypt_home, *fixture_crypto,
                trusted_plaintext[kind], None, contract.phase_view(transfer, phase)), transfer, 30, 0.1)
            contract.accept_local_output(operators, cipher, encrypted,
                contract.phase_view(transfer, phase), kind="crypto")
            phase = kind + "-upload"
            upload = contract.supervise_step(phase, lambda: operators.transport.upload(*args,
                created, cipher.fd, contract.phase_view(transfer, phase), artifact=kind), transfer, 30, 0.1)
            context = operators.transport.request_context(*args, artifact=kind)
            fetched = stack.enter_context(operators.transport.prepare_fetch_output(stage / (kind + "-fetched.gpg")))
            contract.fetch_artifact(operators, *args, created, upload, fetched, transfer, artifact=kind,
                expected_context_sha256=context, expected_cipher_sha256=encrypted["output_sha256"],
                expected_cipher_size=encrypted["output_size"], stage=kind + "-fetch", allowance=30, measured=0.1)
            recovered[kind] = stack.enter_context(operators.transport.prepare_fetch_output(stage / (kind + "-decrypted")))
            decrypt_home = stage / (kind + "-decrypt-home")
            decrypt_home.mkdir(mode=0o700)
            contract.decrypt_artifact(operators, kind, fetched, recovered[kind], decrypt_home,
                *fixture_crypto, trusted_plaintext[kind], context, transfer,
                stage=kind + "-decrypt", allowance=30, measured=0.1)
        assert len(list((root / args[4]["username"]).rglob("*.gpg"))) == 2
        decoded = contract.decode_control(os.pread(recovered["control"].fd,
            os.fstat(recovered["control"].fd).st_size, 0), trusted_plaintext["control"])
        restored_selection = decoded["collections"][0]["selection"]
        destination = stage / "inactive"
        destination.mkdir(mode=0o700)
        restore_window = calibrated_window("restore", kind="archive")
        contract.supervise_step("restore", lambda: contract.restore_exact_archive(operators,
            component, recovered["archive"].path, restored_selection, trusted_plaintext["archive"],
            destination, contract.phase_view(restore_window, "restore")), restore_window, 30, 0.1)
        assert contract.compare_restore(component, restored_selection, destination,
            component.shasum_files([str(destination / n) for n in names]), restore_window)["verified"]
        alias_control = contract.alias_control(restored_selection)
        assert hashlib.sha256(alias_control).hexdigest() == decoded["collections"][0]["alias_control_sha256"]
        contract.restore_declared_alias(component, restored_selection, destination, alias_control,
            decoded["collections"][0]["alias_control_sha256"], restore_window)
        assert name in os.listdir(destination)
        assert os.readlink(destination / "declared-link") == name
