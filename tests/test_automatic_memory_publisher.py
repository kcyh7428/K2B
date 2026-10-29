"""Exercise the real publisher with synthetic vaults; no live host mutations."""
from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import sys
import tempfile
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import automatic_memory
import eod_capture
from test_automatic_memory import _bundle


@pytest.fixture
def smb_environment(tmp_path, monkeypatch):
    p = publisher()
    vault, state = setup(tmp_path)
    binding = dict(p.SMB_STORAGE)
    mount = Path(binding['mount_point'])
    target = mount / binding['relative_target']
    original_resolve = Path.resolve
    original_dir = Path.is_dir
    original_symlink = Path.is_symlink
    monkeypatch.setattr(Path, 'resolve', lambda self, *a, **kw:
                        target / self.relative_to(vault) if self.is_relative_to(vault)
                        else self if self in (mount, target)
                        else original_resolve(self, *a, **kw))
    monkeypatch.setattr(Path, 'is_dir', lambda self:
                        True if self in (mount, target) else original_dir(self))
    monkeypatch.setattr(Path, 'is_symlink', lambda self:
                        False if self in (mount, target) else original_symlink(self))
    env = SimpleNamespace(p=p, vault=vault, state=state, binding=binding,
                          mount=mount, target=target, flags=0, writable=True,
                          output=f"/dev/disk on / (apfs, local)\n{binding['source']} on {mount} (smbfs, nodev, nosuid, mounted by keithmbpm2)\n")
    original_run = p.subprocess.run
    env.real_run = original_run
    def run(args, **kwargs):
        if args == ['/sbin/mount']:
            assert kwargs['timeout'] == 5 and kwargs['check']
            return SimpleNamespace(stdout=env.output)
        if args[:3] == [sys.executable, '-I', '-c']:
            assert kwargs['timeout'] == 5 and kwargs['check'] and kwargs['cwd'] == '/'
            # Execute the exact child probe with synthetic filesystem boundaries.
            with monkeypatch.context() as scoped:
                scoped.setattr(sys, 'argv', ['probe', *args[4:]])
                try:
                    exec(args[3], {})
                except SystemExit as exc:
                    raise p.subprocess.CalledProcessError(exc.code, args) from exc
            return SimpleNamespace(returncode=0)
        return original_run(args, **kwargs)
    monkeypatch.setattr(p.subprocess, 'run', run)
    monkeypatch.setattr(p.os, 'statvfs', lambda path: SimpleNamespace(f_flag=env.flags))
    monkeypatch.setattr(p.os, 'access', lambda path, mode: env.writable)
    return env


def test_smb_binding_ready(smb_environment):
    e = smb_environment
    assert e.p._smb_readiness(e.vault, e.binding) == {
        'storage': 'smb-ready', 'binding': e.binding}


@pytest.mark.parametrize('change', ['absent', 'source', 'mount', 'type', 'readonly',
                                   'nested', 'malformed', 'stat-readonly', 'access',
                                   'userless', 'alias', 'unrelated-malformed'])
def test_smb_failed_readiness_makes_no_publication_writes(smb_environment, change):
    e = smb_environment
    if change == 'absent': e.output = '/dev/disk on / (apfs, local)\n'
    if change == 'source': e.output = e.output.replace('192.168.50.67', '192.168.50.68')
    if change == 'mount': e.output = e.output.replace(str(e.mount), str(e.mount) + '-old')
    if change == 'type': e.output = e.output.replace('smbfs', 'nfs')
    if change == 'readonly': e.output = e.output.replace('smbfs', 'smbfs, read-only')
    if change == 'nested': e.output += f'/dev/disk on {e.target} (apfs, local)\n'
    if change == 'malformed': e.output = 'malformed\n'
    if change == 'stat-readonly': e.flags = e.p.os.ST_RDONLY
    if change == 'access': e.writable = False
    if change == 'userless': e.output = e.output.replace('cheung@', '')
    if change == 'alias': e.output = e.output.replace('192.168.50.67', 'NAS.local')
    if change == 'unrelated-malformed': e.output += 'unidentified mount\n'
    before = {str(f): f.read_bytes() for f in e.vault.rglob('*') if f.is_file()}
    with pytest.raises(PermissionError):
        e.p.publish(e.state/'memory.json', e.state, e.vault, 'home',
                    preflight=lambda v: e.p._smb_readiness(v, e.binding))
    assert before == {str(f): f.read_bytes() for f in e.vault.rglob('*') if f.is_file()}
    assert not (e.state/'.publication.lock').exists()


@pytest.mark.parametrize('bad', [None, [], 'smb', {}, {'kind': 'nfs'},
                               {'extra': True}, {'source': 'wrong'},
                               {'relative_target': '../K2B-Vault'}])
def test_smb_unauthorized_binding(smb_environment, bad):
    e = smb_environment
    value = {**e.binding, **bad} if isinstance(bad, dict) and bad else bad
    with pytest.raises(PermissionError):
        e.p._smb_readiness(e.vault, value)


@pytest.mark.parametrize('failure', ['missing', 'symlink', 'wrong-vault'])
def test_smb_path_refusal(smb_environment, monkeypatch, failure):
    e = smb_environment
    if failure == 'missing': monkeypatch.setattr(Path, 'is_dir', lambda self: False)
    if failure == 'symlink': monkeypatch.setattr(Path, 'is_symlink', lambda self: self == e.target)
    vault = e.vault if failure != 'wrong-vault' else Path('/wrong/vault')
    with pytest.raises(PermissionError): e.p._smb_readiness(vault, e.binding)


def test_smb_paths_with_spaces_are_parsed(smb_environment):
    e = smb_environment
    e.output += '/dev/disk on /Volumes/Unrelated Volume (apfs, local)\n'
    assert e.p._smb_readiness(e.vault, e.binding)['storage'] == 'smb-ready'


def test_stalled_readiness_child_timeout_without_writes(smb_environment, monkeypatch):
    """Prove a user-space stalled child is stopped, not arbitrary kernel I/O."""
    e = smb_environment
    def run(args, **kwargs):
        if args == ['/sbin/mount']: return SimpleNamespace(stdout=e.output)
        assert kwargs['timeout'] == 5
        return e.real_run([sys.executable, '-I', '-c', 'import time; time.sleep(20)'],
                        **{**kwargs, 'timeout': 0.1})
    monkeypatch.setattr(e.p.subprocess, 'run', run)
    started = time.monotonic()
    with pytest.raises(PermissionError, match='unavailable'):
        e.p.publish(e.state/'memory.json', e.state, e.vault, 'home',
                    preflight=lambda v: e.p._smb_readiness(v, e.binding))
    assert time.monotonic() - started < 5
    assert not (e.state/'.publication.lock').exists()
    assert not (e.vault/'System').exists()


def test_smb_publication_and_binding_recheck(tmp_path):
    p = publisher()
    vault, state = setup(tmp_path)
    evidence = {**ready(vault), 'storage': 'smb-ready', 'binding': dict(p.SMB_STORAGE)}
    evidence.pop('syncthing')
    result = p.publish(state/'memory.json', state, vault, 'home', preflight=lambda v: evidence)
    assert result['binding'] == p.SMB_STORAGE and result['storage'] == 'smb-ready'
    assert p.publish(state/'memory.json', state, vault, 'home', preflight=lambda v: evidence)['duplicate']


def test_smb_binding_drift_before_write(tmp_path):
    p = publisher()
    vault, state = setup(tmp_path)
    calls = []
    def preflight(v):
        binding = dict(p.SMB_STORAGE)
        if calls: binding['source'] = 'changed'
        calls.append(True)
        return {'policy_ledger_sha256': ready(v)['policy_ledger_sha256'],
                'storage': 'smb-ready', 'binding': binding}
    with pytest.raises(PermissionError, match='changed during staging'):
        p.publish(state/'memory.json', state, vault, 'home', preflight=preflight)
    assert not (vault/'System').exists()


@pytest.mark.parametrize('failure', [None, 'host', 'policy', 'paths', 'null-storage'])
def test_live_smb_authority_gates(smb_environment, monkeypatch, failure):
    e = smb_environment
    p = e.p
    home = Path('/Users/keithmbpm2')
    logical = home/'Projects/K2B-Vault'
    original_resolve = Path.resolve
    monkeypatch.setattr(Path, 'resolve', lambda self, *a, **kw:
                        e.target if self == logical else original_resolve(self, *a, **kw))
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: home))
    monkeypatch.setattr(p.getpass, 'getuser', lambda: 'keithcheung' if failure == 'host' else 'keithmbpm2')
    ledger = b'{"type":"guard"}\n'
    authority = dict(schema_version=1, enabled=True, vault_root=str(logical),
                     exclusive_paths=[p.JSON_REL, p.NOTE_REL],
                     policy_ledger_sha256=p._hash(ledger), storage=e.binding)
    if failure == 'policy': authority['policy_ledger_sha256'] = '0'*64
    if failure == 'paths': authority['exclusive_paths'] = []
    if failure == 'null-storage': authority['storage'] = None
    monkeypatch.setattr(Path, 'read_text', lambda self: json.dumps(authority))
    monkeypatch.setattr(Path, 'read_bytes', lambda self: ledger)
    if failure:
        with pytest.raises(PermissionError): p.live_preflight(e.vault)
    else:
        assert p.live_preflight(e.vault) == {
            'policy_ledger_sha256': p._hash(ledger), 'storage': 'smb-ready', 'binding': e.binding}


def publisher():
    assert importlib.util.find_spec("automatic_memory_publisher"), "safe publisher is missing"
    return importlib.import_module("automatic_memory_publisher")


def setup(tmp_path):
    vault, state = tmp_path / "vault", tmp_path / "state"
    (vault / "wiki/context").mkdir(parents=True)
    (vault / "wiki/context/index.md").write_text(
        "# Context\nLast updated: 2026-09-01 | Entries: 0\n\n## K2B System\n\n"
        "| Page | Summary | Updated |\n|------|---------|---------|\n")
    (vault / "wiki/index.md").write_text(
        "# Wiki\n| Folder | Purpose | Entries |\n|---|---|---|\n"
        "| [context/](context/index.md) | Context | 0 |\n\n**Total wiki pages: 0**\n")
    (vault / "wiki/log.md").write_text("# Log\n")
    (vault / "wiki/context/policy-ledger.jsonl").write_text(
        '{"type":"guard","scope":"*","action":"read_state","rule":"Poll before acting."}\n')
    envelope = automatic_memory.enqueue_bundle(state, _bundle())
    eod_capture.accept_memory_envelope(state, envelope, writer_role="home")
    eod_capture.reconcile_memory_delivery(state, envelope["delivery_id"], writer_role="home")
    return vault, state


def ready(vault):
    return {"policy_ledger_sha256": hashlib.sha256(
        (vault / "wiki/context/policy-ledger.jsonl").read_bytes()).hexdigest(),
        "syncthing": "idle"}


@pytest.fixture
def namespace_migration(tmp_path, monkeypatch):
    """Production preflight with invented authority, mount and local files only."""
    p = publisher()
    vault, state = setup(tmp_path)
    binding = {**p.SMB_STORAGE, "mount_point": str(tmp_path / "approved-mount")}
    mount = Path(binding["mount_point"])
    mount.mkdir()
    target = mount / binding["relative_target"]
    vault.rename(target)
    logical = Path("/Users/keithmbpm2/Projects/K2B-Vault")
    home = logical.parents[1]
    authority_path = home / ".local/state/k2b/automatic-memory/publication-authority.json"
    authority = dict(schema_version=1, enabled=True, vault_root=str(logical),
        exclusive_paths=[p.JSON_REL, p.NOTE_REL],
        policy_ledger_sha256=ready(target)["policy_ledger_sha256"], storage=binding)
    env = SimpleNamespace(p=p, vault=target, state=state, logical=logical,
        alias_target=target, binding=binding, authority=authority, user="keithmbpm2",
        mount_output=f"/dev/disk on / (apfs, local)\n{binding['source']} on {mount} (smbfs, nodev, nosuid)\n")
    resolve, read_text, run = Path.resolve, Path.read_text, p.subprocess.run
    monkeypatch.setattr(p, "SMB_STORAGE", binding)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(p.getpass, "getuser", lambda: env.user)
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **kw:
        env.alias_target if self == logical else resolve(self, *a, **kw))
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **kw:
        json.dumps(env.authority) if self == authority_path else read_text(self, *a, **kw))
    def local_append_log(vault, digest):
        # Log-helper behavior is covered separately; keep this family process-isolated.
        assert vault == target
        log = target / "wiki/log.md"
        marker = "automatic-memory snapshot=" + digest
        text = log.read_text()
        if marker not in text:
            log.write_text(text + marker + "\n")
    monkeypatch.setattr(p, "_append_log", local_append_log)
    def local_run(args, **kwargs):
        if args == ["/sbin/mount"]:
            return SimpleNamespace(stdout=env.mount_output, returncode=0)
        expected_probe = [sys.executable, "-I", "-c", p._SMB_PROBE,
                          str(env.alias_target), str(mount), str(target)]
        assert args == expected_probe, "unexpected subprocess in synthetic namespace fixture"
        assert env.alias_target.is_relative_to(tmp_path)
        assert kwargs["cwd"] == "/" and kwargs["timeout"] == 5 and kwargs["check"]
        return run(args, **kwargs)
    monkeypatch.setattr(p.subprocess, "run", local_run)
    return env


def _namespace_history(env, kind, monkeypatch):
    """Create invented pre-migration history, never read production receipts."""
    p, vault, state = env.p, env.vault, env.state
    if kind == "published":
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
        current = next((state / "publication").glob("*.json"))
        outer = json.loads(current.read_text())
        prior = outer
    else:
        with monkeypatch.context() as scoped:
            def interrupted(*args):
                raise OSError("invented interruption")
            scoped.setattr(p, "_append_log", interrupted)
            with pytest.raises(OSError, match="invented interruption"):
                p.publish(state / "memory.json", state, vault, "home", preflight=ready)
        current = next((state / "publication-pending").glob("*.json"))
        outer = json.loads(current.read_text())
        prior = outer["publication"]
    prior["vault_root"] = str(env.logical)
    identity = p._publication_id({k: v for k, v in prior.items() if k != "published_at"})
    legacy = current.with_name(identity + ".json")
    current.unlink()
    legacy.write_text(json.dumps(outer))
    newer = automatic_memory.enqueue_bundle(state, _bundle(
        completed_at="2026-09-10T10:00:00+08:00", cursor="turn:0002",
        value="The office is now Green.", quote="The office is now Green."))
    eod_capture.accept_memory_envelope(state, newer, writer_role="home")
    eod_capture.reconcile_memory_delivery(state, newer["delivery_id"], writer_role="home")
    return legacy


@pytest.mark.parametrize("kind", ["published", "staged"])
def test_historical_home_namespace_uses_verified_smb_without_rewriting_history(namespace_migration, monkeypatch, kind):
    env = namespace_migration
    legacy = _namespace_history(env, kind, monkeypatch)
    original = legacy.read_bytes()
    historical = json.loads(original)
    if kind == "staged":
        historical = historical["publication"]
    assert legacy.stem == env.p._publication_id(
        {k: v for k, v in historical.items() if k != "published_at"})
    result = env.p.publish(env.state / "memory.json", env.state, env.logical, "home")
    assert result["vault_root"] == str(env.vault)
    assert result["storage"] == "smb-ready" and result["binding"] == env.binding
    assert result["status"] == "published" and result["duplicate"] is False
    assert legacy.read_bytes() == original
    current = env.state / "publication" / (env.p._publication_id(
        {k: v for k, v in result.items() if k not in ("published_at", "duplicate")}) + ".json")
    assert current.is_file() and current != legacy
    receipt = json.loads(current.read_bytes())
    assert receipt == {k: v for k, v in result.items() if k != "duplicate"}
    assert set(receipt["artifact_sha256"]) == {env.p.JSON_REL, env.p.NOTE_REL}
    for relative, digest in receipt["artifact_sha256"].items():
        assert digest == hashlib.sha256((env.vault / relative).read_bytes()).hexdigest()
    snapshot = automatic_memory._validate_memory_state(
        json.loads((env.vault / env.p.JSON_REL).read_bytes()))
    assert receipt["snapshot_sha256"] == env.p.automatic_memory_proofs.snapshot_hash(snapshot)
    assert receipt["snapshot_sha256"] != historical["snapshot_sha256"]
    expected_pending = {legacy.name: original} if kind == "staged" else {}
    expected_published = {current.name: current.read_bytes()}
    if kind == "published":
        expected_published[legacy.name] = original
    def assert_receipt_directories():
        # Retain historical staged provenance; retire only the new transaction journal.
        for folder, expected in (("publication-pending", expected_pending),
                                 ("publication", expected_published)):
            assert {path.name: path.read_bytes() for path in
                    (env.state / folder).iterdir()} == expected
    assert_receipt_directories()
    after_publication = {str(path): path.read_bytes() for folder in (env.vault, env.state)
                         for path in folder.rglob("*") if path.is_file()}
    assert env.p.publish(env.state / "memory.json", env.state, env.logical, "home")["duplicate"]
    assert legacy.read_bytes() == original
    assert_receipt_directories()
    assert after_publication == {str(path): path.read_bytes() for folder in (env.vault, env.state)
                                 for path in folder.rglob("*") if path.is_file()}


@pytest.mark.parametrize("failure", ["other-root", "equivalent-path", "tampered", "identity-valid-hash-tamper", "schema", "status", "writer", "artifact-shape", "snapshot", "alias", "mount", "storage", "policy", "non-home", "legacy-mode", "injected-smb", "artifact-edited"])
@pytest.mark.parametrize("kind", ["published", "staged"])
def test_historical_namespace_refuses_invalid_binding_or_ownership_before_writes(namespace_migration, monkeypatch, failure, kind):
    env = namespace_migration
    legacy = _namespace_history(env, kind, monkeypatch)
    outer = json.loads(legacy.read_text())
    prior = outer["publication"] if kind == "staged" else outer
    if failure == "other-root": prior["vault_root"] += "/unrelated"
    if failure == "equivalent-path": prior["vault_root"] = str(env.vault) + "/."
    if failure in ("tampered", "identity-valid-hash-tamper"):
        prior["artifact_sha256"][env.p.NOTE_REL] = "f" * 64
    if failure == "schema": prior["schema_version"] = True
    if failure == "status": prior["status"] = "failed"
    if failure == "writer": prior["writer_role"] = "sjm-source-only"
    if failure == "artifact-shape": prior["artifact_sha256"] = []
    if failure == "snapshot": prior["snapshot_sha256"] = "f" * 64
    if failure in ("other-root", "equivalent-path", "identity-valid-hash-tamper", "schema", "status", "writer", "artifact-shape", "snapshot"):
        renamed = legacy.with_name(env.p._publication_id(
            {k: v for k, v in prior.items() if k != "published_at"}) + ".json")
        legacy.rename(renamed)
        legacy = renamed
    legacy.write_text(json.dumps(outer))
    historical_bytes = legacy.read_bytes()
    identity = env.p._publication_id({k: v for k, v in prior.items() if k != "published_at"})
    assert (legacy.stem != identity) if failure == "tampered" else (legacy.stem == identity)
    if failure == "alias": env.alias_target = env.vault.parent / "wrong-target"
    if failure == "mount":
        assert env.binding["source"] in env.mount_output
        env.mount_output = env.mount_output.replace(env.binding["source"], env.binding["source"] + "-wrong", 1)
    if failure == "storage": env.authority["storage"] = {**env.binding, "source": "wrong"}
    if failure == "policy": env.authority["policy_ledger_sha256"] = "0" * 64
    if failure == "non-home": env.user = "keithcheung"
    if failure == "artifact-edited": (env.vault / env.p.JSON_REL).write_text("invented human edit")
    options = {}
    if failure == "legacy-mode": options["preflight"] = ready
    if failure == "injected-smb": options["preflight"] = lambda v: {
        "policy_ledger_sha256": ready(v)["policy_ledger_sha256"],
        "storage": "smb-ready", "binding": env.binding}
    # Existing coordination locks may be acquired; their bytes are pinned too.
    for name in (".publication.lock", ".reconciliation.lock"):
        assert (env.state / name).read_bytes() == b""
    before = {str(path): path.read_bytes() for folder in (env.vault, env.state)
              for path in folder.rglob("*") if path.is_file()}
    malformed = (ValueError, "publication receipt is malformed or belongs to another vault")
    ownership = (PermissionError, "generated recall was modified by another writer")
    expected_errors = {
        "other-root": malformed,
        "equivalent-path": malformed,
        "tampered": (ValueError, "publication receipt content binding is invalid"),
        "identity-valid-hash-tamper": ownership,
        "schema": malformed,
        "status": malformed,
        "writer": malformed,
        "artifact-shape": malformed,
        "snapshot": malformed,
        "alias": (PermissionError, "approved SMB target is unavailable: wrong vault target"),
        "mount": (PermissionError, "approved SMB mount is absent, replaced or read-only"),
        "storage": (PermissionError, "SMB storage binding is not authorized"),
        "policy": (PermissionError, "policy ledger changed; publication needs a fresh policy review"),
        "non-home": (PermissionError, "shared publication is allowed only on the Home Mac"),
        "legacy-mode": malformed,
        "injected-smb": malformed,
        "artifact-edited": ownership,
    }
    error, message = expected_errors[failure]
    with pytest.raises(error, match=message):
        env.p.publish(env.state / "memory.json", env.state, env.logical, "home", **options)
    assert before == {str(path): path.read_bytes() for folder in (env.vault, env.state)
                      for path in folder.rglob("*") if path.is_file()}
    assert legacy.read_bytes() == historical_bytes


def test_publication_produces_indexed_cited_artifacts_and_idempotent_receipt(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    result = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert result["status"] == "published"
    shared = vault / "System/memory/automatic-memory-current.json"
    assert automatic_memory.recall(shared, "fact.office", read_only=True)["value"] == "The SJM office is in Macau."
    note = (vault / "wiki/context/context_automatic-memory-recall.md").read_text()
    assert "event-user-1" in note and "The SJM office is in Macau." in note
    index = (vault / "wiki/context/index.md").read_text()
    assert "[[context_automatic-memory-recall]]" in index and "Entries: 1" in index
    assert "| Context | 1 |" in (vault / "wiki/index.md").read_text()
    before = {str(f): f.read_bytes() for f in vault.rglob("*") if f.is_file()}
    repeat = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert repeat["duplicate"] is True
    assert before == {str(f): f.read_bytes() for f in vault.rglob("*") if f.is_file()}
    assert result["snapshot_sha256"] == hashlib.sha256(shared.read_bytes()).hexdigest()


@pytest.mark.parametrize("role", ["sjm-source-only", "unknown"])
def test_sjm_cannot_publish_even_with_injected_healthy_preflight(tmp_path, role):
    vault, state = setup(tmp_path)
    with pytest.raises(PermissionError):
        publisher().publish(state / "memory.json", state, vault, role, preflight=ready)
    assert not (vault / "System").exists()


def test_live_publication_without_activation_authority_is_blocked(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    # Refusal fixture must not traverse the installed Home SMB alias.
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **kw: self)
    with pytest.raises(PermissionError):
        publisher().publish(state / "memory.json", state, vault, "home")
    assert not (vault / "System").exists()


def test_unhealthy_preflight_and_concurrent_owner_cannot_publish(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    with pytest.raises(PermissionError):
        p.publish(state / "memory.json", state, vault, "home", preflight=lambda v: {**ready(v), "syncthing": "syncing"})
    with (state / ".publication.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert not (vault / "System").exists()


def test_unreconciled_acceptance_and_tampered_receipt_block_publication(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    receipt = next((state / "reconciliation").glob("*.json"))
    original = receipt.read_bytes()
    receipt.unlink()
    with pytest.raises(ValueError, match="reconcil"):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    receipt.write_bytes(original)
    body = json.loads(original)
    body["content_id"] = "bad"
    receipt.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="reconcil"):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert not (vault / "System").exists()


def test_log_failure_has_no_success_receipt_and_retry_does_not_duplicate(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    p = publisher()
    log = p._append_log
    monkeypatch.setattr(p, "_append_log", lambda *a: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert not list((state / "publication").glob("*.json"))
    monkeypatch.setattr(p, "_append_log", log)
    result = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert result["status"] == "published"
    assert (vault / "wiki/log.md").read_text().count(result["snapshot_sha256"]) == 1


def test_receipt_tampering_and_symlink_escape_fail_closed(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    receipt = next((state / "publication").glob("*.json"))
    receipt.write_text('{}')
    with pytest.raises(ValueError, match="receipt"):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    fresh_vault, fresh_state = setup(tmp_path / "fresh")
    escape = tmp_path / "escape"
    escape.mkdir()
    (fresh_vault / "System").symlink_to(escape, target_is_directory=True)
    with pytest.raises(PermissionError):
        p.publish(fresh_state / "memory.json", fresh_state, fresh_vault, "home", preflight=ready)
    assert list(escape.iterdir()) == []


def test_publisher_preserves_existing_user_edits_and_refuses_shared_local_state(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    note = vault / "wiki/context/context_automatic-memory-recall.md"
    note.write_text("Keith's manual edits must survive.\n")
    with pytest.raises(PermissionError, match="modified"):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert note.read_text() == "Keith's manual edits must survive.\n"
    with pytest.raises(PermissionError, match="local"):
        p.publish(state / "memory.json", vault / "System/state", vault, "home", preflight=ready)
    assert not (vault / "System/state").exists()


def test_note_has_context_frontmatter_and_snapshot_identity(tmp_path):
    vault, state = setup(tmp_path)
    result = publisher().publish(state / "memory.json", state, vault, "home", preflight=ready)
    note = (vault / "wiki/context/context_automatic-memory-recall.md").read_text()
    for field in ('type: context', 'origin: k2b-extract', 'date: 2026-09-09', 'up: "[[index]]"'):
        assert field in note
    assert result["snapshot_sha256"] in note


def test_failure_between_artifacts_leaves_no_receipt_and_replay_repairs(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    p = publisher()
    original = eod_capture._atomic_write_text
    def fail_note(path, text):
        if path.name == "context_automatic-memory-recall.md":
            raise OSError("write interrupted")
        return original(path, text)
    monkeypatch.setattr(eod_capture, "_atomic_write_text", fail_note)
    with pytest.raises(OSError):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert (vault / "System/memory/automatic-memory-current.json").is_file()
    assert not list((state / "publication").glob("*.json"))
    monkeypatch.setattr(eod_capture, "_atomic_write_text", original)
    assert p.publish(state / "memory.json", state, vault, "home", preflight=ready)["status"] == "published"


def test_canonical_vault_ownership_excludes_a_second_state_root(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    unused_vault, second_state = setup(tmp_path / "second")
    p = publisher()
    original = eod_capture._atomic_write_text
    outcomes = []
    def overlap(path, text):
        if path == vault / "System/memory/automatic-memory-current.json" and not outcomes:
            outcomes.append("started")
            try:
                p.publish(second_state / "memory.json", second_state, vault, "home", preflight=ready)
            except BlockingIOError:
                outcomes.append("blocked")
            else:
                outcomes.append("overlapped")
        original(path, text)
    monkeypatch.setattr(eod_capture, "_atomic_write_text", overlap)
    assert p.publish(state / "memory.json", state, vault, "home", preflight=ready)["status"] == "published"
    assert outcomes == ["started", "blocked"]


def test_interrupted_publication_recovers_after_newer_memory_arrives(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    p = publisher()
    original = eod_capture._atomic_write_text
    def fail_note(path, text):
        if path.name == "context_automatic-memory-recall.md":
            raise OSError("interrupted before note")
        original(path, text)
    monkeypatch.setattr(eod_capture, "_atomic_write_text", fail_note)
    with pytest.raises(OSError):
        p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    monkeypatch.setattr(eod_capture, "_atomic_write_text", original)
    newer = automatic_memory.enqueue_bundle(state, _bundle(
        completed_at="2026-09-10T10:00:00+08:00", cursor="turn:0002",
        value="The office is now Green.", quote="The office is now Green."))
    eod_capture.accept_memory_envelope(state, newer, writer_role="home")
    eod_capture.reconcile_memory_delivery(state, newer["delivery_id"], writer_role="home")
    result = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert result["status"] == "published"
    assert "The office is now Green." in (vault / "wiki/context/context_automatic-memory-recall.md").read_text()


def test_additional_duplicate_provenance_advances_watermark_without_failure(tmp_path):
    vault, state = setup(tmp_path)
    p = publisher()
    first = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    duplicate = automatic_memory.enqueue_bundle(state, _bundle(thread_source="desktop"))
    eod_capture.accept_memory_envelope(state, duplicate, writer_role="home")
    assert eod_capture.reconcile_memory_delivery(state, duplicate["delivery_id"], writer_role="home")["duplicates"] == 1
    second = p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert first["snapshot_sha256"] == second["snapshot_sha256"]
    assert len(second["reconciliation_watermark"]) == 2
    assert p.publish(state / "memory.json", state, vault, "home", preflight=ready)["duplicate"] is True
    assert (vault / "wiki/log.md").read_text().count(second["snapshot_sha256"]) == 1


def test_vault_lock_does_not_depend_on_process_temp_directory(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    _, second_state = setup(tmp_path / "second")
    p = publisher()
    original = eod_capture._atomic_write_text
    outcomes = []
    def overlap(path, text):
        if path == vault / "System/memory/automatic-memory-current.json" and not outcomes:
            outcomes.append("started")
            monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "other-temp"))
            try:
                p.publish(second_state / "memory.json", second_state, vault, "home", preflight=ready)
            except BlockingIOError:
                outcomes.append("blocked")
            else:
                outcomes.append("overlapped")
        original(path, text)
    monkeypatch.setattr(eod_capture, "_atomic_write_text", overlap)
    p.publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert outcomes == ["started", "blocked"]


def test_renderer_header_drift_cannot_receive_publication_success(tmp_path, monkeypatch):
    vault, state = setup(tmp_path)
    original = eod_capture.publish_shared_recall
    def drift(*args, **kwargs):
        writer = kwargs['write_func']
        kwargs['write_func'] = lambda rel, text: writer(rel, text.replace('status: generated\n', 'status: changed\n'))
        return original(*args, **kwargs)
    monkeypatch.setattr(eod_capture, 'publish_shared_recall', drift)
    with pytest.raises(ValueError, match='frontmatter'):
        publisher().publish(state/'memory.json', state, vault, 'home', preflight=ready)
    assert not (vault/'System').exists()


def test_index_helper_exit_is_a_catchable_failure_without_success(tmp_path):
    vault, state = setup(tmp_path)
    (vault/'wiki/context/index.md').write_text('# unrecognized index\n')
    with pytest.raises(OSError, match='index'):
        publisher().publish(state/'memory.json', state, vault, 'home', preflight=ready)
    assert not list((state/'publication').glob('*.json'))


def test_completed_pending_journal_is_retired_but_receipt_remains(tmp_path):
    vault, state = setup(tmp_path)
    publisher().publish(state/'memory.json', state, vault, 'home', preflight=ready)
    assert not list((state/'publication-pending').glob('*.json'))
    assert len(list((state/'publication').glob('*.json'))) == 1


def test_malformed_pending_journal_names_exact_failure_without_quarantine(tmp_path):
    vault, state = setup(tmp_path)
    pending = state/'publication-pending'/'bad.json'
    pending.parent.mkdir()
    pending.write_text(json.dumps({'status':'staged','publication':'x'}))
    with pytest.raises(ValueError, match='bad.json'):
        publisher().publish(state/'memory.json', state, vault, 'home', preflight=ready)
    assert pending.exists()
    assert not (vault/'System').exists()


def test_missing_log_is_detected_before_any_shared_artifact_write(tmp_path):
    vault, state = setup(tmp_path)
    (vault/'wiki/log.md').unlink()
    with pytest.raises(OSError, match='log'):
        publisher().publish(state/'memory.json', state, vault, 'home', preflight=ready)
    assert not (vault/'System').exists()


def test_shared_hub_edits_during_staging_are_merged_not_reverted(tmp_path):
    vault, state = setup(tmp_path)
    calls = 0
    def edit_during_staging(v):
        nonlocal calls
        calls += 1
        if calls == 2:
            index = v/'wiki/context/index.md'
            index.write_text(index.read_text()+'\nManual context detail to retain.\n')
            master = v/'wiki/index.md'
            master.write_text(master.read_text()+'\nManual master detail to retain.\n')
            log = v/'wiki/log.md'
            log.write_text(log.read_text()+'Manual log entry to retain.\n')
        return ready(v)
    publisher().publish(state/'memory.json', state, vault, 'home', preflight=edit_during_staging)
    assert 'Manual context detail to retain.' in (vault/'wiki/context/index.md').read_text()
    assert 'Manual master detail to retain.' in (vault/'wiki/index.md').read_text()
    assert 'Manual log entry to retain.' in (vault/'wiki/log.md').read_text()


def test_invalid_policy_bytes_are_an_explicit_gate_refusal(monkeypatch):
    p = publisher()
    # Fake only the read boundaries; this test never reads/writes live files.
    home = Path('/Users/keithmbpm2')
    vault = home/'Projects/K2B-Vault'
    monkeypatch.setattr(Path, 'resolve', lambda self, *a, **kw: self)
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: home))
    monkeypatch.setattr(p.getpass, 'getuser', lambda: 'keithmbpm2')
    authority = {'schema_version':1, 'enabled':True, 'vault_root':str(vault),
                 'exclusive_paths':[p.JSON_REL,p.NOTE_REL], 'policy_ledger_sha256':'a'*64}
    def read_text(path, *args, **kwargs):
        assert path.name == 'publication-authority.json'
        return json.dumps(authority)
    def read_bytes(path):
        assert path.name == 'policy-ledger.jsonl'
        return b'not valid JSON'
    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    with pytest.raises(PermissionError, match='policy ledger is malformed'):
        p.live_preflight(vault)


def test_v2_corrections_are_published_and_recall_from_shared_snapshot(tmp_path):
    """Publisher carries v2 correction metadata, not a lossy v1 downgrade."""
    vault, state = setup(tmp_path)
    old = json.loads((state / "memory.json").read_text())["records"][
        "fact.office"
    ]["versions"][0]
    newer = automatic_memory.enqueue_bundle(
        state,
        _bundle(
            host_id="home",
            session_id="session-home-correction",
            completed_at="2026-09-11T12:00:00+08:00",
            cursor="turn:0010",
            key="decision.office",
            kind="decision",
            value="Move the SJM office to Hengqin.",
            event_id="event-home-correction",
            quote="Move the SJM office to Hengqin.",
        ),
    )
    eod_capture.accept_memory_envelope(state, newer, writer_role="home")
    eod_capture.reconcile_memory_delivery(state, newer["delivery_id"], writer_role="home")
    records = json.loads((state / "memory.json").read_text())["records"]
    new_version = next(
        version
        for version in records["decision.office"]["versions"]
        if version["citation"]["event_id"] == "event-home-correction"
    )
    candidate = {
        "relation": "supersedes",
        "target_item_id": old["item_id"],
        "source_item_id": new_version["item_id"],
        "topic_ref": "wiki/projects/project_atlas.md",
        "field": "office_location",
        "evidence_refs": [old["item_id"], new_version["item_id"]],
    }
    receipt = automatic_memory.create_review_receipt(
        state / "memory.json",
        candidate,
        reviewer="codex_conversation",
        model="gpt-5-codex",
        reviewed_at="2026-09-28T10:00:00+00:00",
    )
    automatic_memory.apply_corrections(
        state / "memory.json",
        [{**candidate, "review_receipt_id": receipt["receipt_id"]}],
        recovery_dir=state / "recovery",
    )

    result = publisher().publish(state / "memory.json", state, vault, "home", preflight=ready)
    assert result["status"] == "published"
    shared = vault / "System/memory/automatic-memory-current.json"
    published = json.loads(shared.read_text())
    assert published["schema_version"] == 2
    assert len(published["corrections"]) == 1
    recalled = automatic_memory.recall(shared, "fact.office", read_only=True)
    assert recalled["status"] == "superseded"
    assert recalled["replacement"]["key"] == "decision.office"
    note = (vault / "wiki/context/context_automatic-memory-recall.md").read_text()
    assert "Status: superseded" in note
    assert "Move the SJM office to Hengqin." in note


def test_unknown_future_schema_fails_closed_without_overwrite(tmp_path):
    vault, state = setup(tmp_path)
    target = state / "memory.json"
    future = {"schema_version": 3, "records": {}, "corrections": []}
    target.write_text(json.dumps(future))
    with pytest.raises(automatic_memory.StateCorruptionError, match="schema_version"):
        publisher().publish(target, state, vault, "home", preflight=ready)
    assert json.loads(target.read_text()) == future
    assert not (vault / "System").exists()
