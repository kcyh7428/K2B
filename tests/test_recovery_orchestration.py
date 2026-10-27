"""Tests for bounded recovery orchestration helpers."""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import recovery_orchestration as ro  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *a):  # noqa: D401
        return

    def do_GET(self):  # noqa: N802
        _Handler.seen.append({"path": self.path, "key": self.headers.get("X-API-Key")})
        behaviour = self.server.behaviour  # type: ignore[attr-defined]
        if behaviour == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/rest/db/status")
            self.end_headers()
            return
        if behaviour == "oversize":
            self.send_response(200)
            body = b"{" + b"\x20" * (70 * 1024) + b"}"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if behaviour == "malformed":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not json")
            return
        status = {
            "state": "idle",
            "error": "",
            "needFiles": 0,
            "needBytes": 0,
            "needDirectories": 0,
            "errors": 0,
            "pullErrors": 0,
        }
        if behaviour == "nonzero":
            status["needFiles"] = 3
        if behaviour == "bool":
            status["needFiles"] = True
        if behaviour == "missing":
            del status["errors"]
        if behaviour == "negative":
            status["needBytes"] = -1
        if behaviour == "error_present":
            status["error"] = "boom"
        body = json.dumps(status).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.behaviour = "ok"  # type: ignore[attr-defined]
    _Handler.seen.clear()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _write_config(home: Path, vault: Path, port: int, *, address=None, key="SECRETKEY", extra="") -> Path:
    cfg_dir = home / "Library" / "Application Support" / "Syncthing"
    cfg_dir.mkdir(parents=True)
    addr = address if address is not None else f"127.0.0.1:{port}"
    xml = (
        "<configuration>"
        + extra
        + f"<folder id=\"vault-id\" path=\"{vault}\" />"
        + f"<gui tls=\"false\"><address>{addr}</address>"
        + f"<apikey>{key}</apikey></gui>"
        + "</configuration>"
    )
    path = cfg_dir / "config.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def _lock_specs(tmp: Path) -> list[dict]:
    return [
        {"name": "note_write", "kind": "mkdir", "path": str(tmp / "note.lock.d")},
        {"name": "compile_index", "kind": "mkdir", "path": str(tmp / "compile.lock.d")},
        {"name": "integration", "kind": "flock", "path": str(tmp / "integration.lock")},
        {"name": "wiki_log", "kind": "flock", "path": str(tmp / "wiki.lock")},
        {"name": "publication", "kind": "flock", "path": str(tmp / "publication.lock")},
    ]


def _status_obs_json():
    return {
        "schema_version": 1,
        "actor": "sjm",
        "observed_at_ns": 10_000_000_000,
        "state": "idle",
        "need_files": 0,
        "need_bytes": 0,
        "need_directories": 0,
        "errors": 0,
        "pull_errors": 0,
        "error_present": False,
        "locks": {name: ("not_applicable" if name in ("integration", "publication") else "clear")
                  for name in ro.REQUIRED_LOCKS},
        "lock_scope": ro.LOCK_SCOPE,
        "witness_specs": [{"name": name, "kind": "flock", "path": "/synthetic/" + name}
                          for name in ro.REQUIRED_LOCKS],
    }


def test_status_success_and_no_key_leak(server, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    _write_config(home, vault, server.server_address[1])
    obs = ro.local_status("home", home=home, vault=vault, lock_specs=_lock_specs(tmp_path))
    assert obs["state"] == "idle"
    assert obs["error_present"] is False
    assert obs["lock_scope"] == ro.LOCK_SCOPE
    assert set(obs["locks"]) == set(ro.REQUIRED_LOCKS)
    assert "SECRETKEY" not in json.dumps(obs)
    ro.validate_observation(obs, actor="home", now_ns=obs["observed_at_ns"])


def test_serialized_observer_executes_complete_status_route(server, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    _write_config(home, vault, server.server_address[1])
    namespace = {}
    exec(ro.build_probe_sjm_stdin().rsplit(b"\nprint(", 1)[0], namespace)
    result = namespace["local_status"]("sjm", home=home, vault=vault,
                                       lock_specs=_lock_specs(tmp_path))
    assert result["actor"] == "sjm"
    ro.validate_observation(result, actor="sjm", now_ns=result["observed_at_ns"])


def test_utf8_xml_declaration_passes_complete_status_route(server, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    config = _write_config(home, vault, server.server_address[1])
    config.write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n' + config.read_bytes())
    result = ro.local_status("home", home=home, vault=vault, lock_specs=_lock_specs(tmp_path))
    ro.validate_observation(result, actor="home", now_ns=result["observed_at_ns"])


def test_redirect_refused_no_key_forwarded(server, tmp_path):
    server.behaviour = "redirect"  # type: ignore[attr-defined]
    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    _write_config(home, vault, server.server_address[1])
    with pytest.raises(ro.OrchestrationError) as exc:
        ro.local_status("home", home=home, vault=vault, lock_specs=_lock_specs(tmp_path))
    assert exc.value.reason == "gui_status_failed"
    assert len(_Handler.seen) == 1


def test_proxy_env_ignored(server, tmp_path, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://10.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://10.0.0.1:1")
    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    _write_config(home, vault, server.server_address[1])
    obs = ro.local_status("home", home=home, vault=vault, lock_specs=_lock_specs(tmp_path))
    assert obs["state"] == "idle"
    assert _Handler.seen


def test_nonloopback_endpoint_refused(server, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    _write_config(home, vault, server.server_address[1], address="10.0.0.5:8384")
    with pytest.raises(ro.OrchestrationError) as exc:
        ro.local_status("home", home=home, vault=vault, lock_specs=_lock_specs(tmp_path))
    assert exc.value.reason == "gui_endpoint_refused"


def test_oversize_and_malformed_refused(server, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    _write_config(home, vault, server.server_address[1])
    server.behaviour = "oversize"  # type: ignore[attr-defined]
    with pytest.raises(ro.OrchestrationError) as exc:
        ro.local_status("home", home=home, vault=vault, lock_specs=_lock_specs(tmp_path))
    assert exc.value.reason == "gui_response_too_large"
    server.behaviour = "malformed"  # type: ignore[attr-defined]
    with pytest.raises(ro.OrchestrationError) as exc:
        ro.local_status("home", home=home, vault=vault, lock_specs=_lock_specs(tmp_path))
    assert exc.value.reason == "gui_response_malformed"


@pytest.mark.parametrize("behaviour", ["bool", "missing", "negative"])
def test_structurally_bad_counters_refused(server, tmp_path, behaviour):
    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    _write_config(home, vault, server.server_address[1])
    server.behaviour = behaviour  # type: ignore[attr-defined]
    with pytest.raises(ro.OrchestrationError):
        ro.local_status("home", home=home, vault=vault, lock_specs=_lock_specs(tmp_path))


@pytest.mark.parametrize("behaviour", ["nonzero", "error_present"])
def test_nonzero_status_is_returned_but_not_valid(server, tmp_path, behaviour):
    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    _write_config(home, vault, server.server_address[1])
    server.behaviour = behaviour  # type: ignore[attr-defined]
    obs = ro.local_status("home", home=home, vault=vault, lock_specs=_lock_specs(tmp_path))
    assert obs["state"] == "idle"
    with pytest.raises(ro.OrchestrationError):
        ro.validate_observation(obs, actor="home", now_ns=obs["observed_at_ns"])


def test_doctype_refused(server, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    home = tmp_path / "home"
    _write_config(home, vault, server.server_address[1], extra="<!DOCTYPE x [<!ENTITY e 'v'>]>")
    with pytest.raises(ro.OrchestrationError) as exc:
        ro.local_status("home", home=home, vault=vault, lock_specs=_lock_specs(tmp_path))
    assert exc.value.reason == "config_xml_unsafe"


def _good_obs(now):
    return {
        "schema_version": 1,
        "actor": "home",
        "observed_at_ns": now,
        "state": "idle",
        "need_files": 0,
        "need_bytes": 0,
        "need_directories": 0,
        "errors": 0,
        "pull_errors": 0,
        "error_present": False,
        "locks": {name: "clear" for name in ro.REQUIRED_LOCKS},
        "lock_scope": ro.LOCK_SCOPE,
        "witness_specs": [{"name": name, "kind": "flock", "path": "/synthetic/" + name}
                          for name in ro.REQUIRED_LOCKS],
    }


def test_validate_accepts_fresh():
    now = 10_000_000_000
    ro.validate_observation(_good_obs(now), actor="home", now_ns=now)


def test_validate_rejects_stale_future_wrong_actor():
    now = 10_000_000_000
    with pytest.raises(ro.OrchestrationError) as e:
        ro.validate_observation(_good_obs(now), actor="home", now_ns=now + 5_000_000_000)
    assert e.value.reason == "observation_stale"
    with pytest.raises(ro.OrchestrationError) as e:
        ro.validate_observation(_good_obs(now), actor="home", now_ns=now - 2_000_000_000)
    assert e.value.reason == "observation_future"
    with pytest.raises(ro.OrchestrationError) as e:
        ro.validate_observation(_good_obs(now), actor="sjm", now_ns=now)
    assert e.value.reason == "observation_actor_invalid"


def test_validate_rejects_extra_and_bad_locks():
    now = 10_000_000_000
    obs = _good_obs(now)
    obs["extra"] = "x"
    with pytest.raises(ro.OrchestrationError) as e:
        ro.validate_observation(obs, actor="home", now_ns=now)
    assert e.value.reason == "observation_fields_invalid"
    obs = _good_obs(now)
    obs["locks"]["note_write"] = "busy"
    with pytest.raises(ro.OrchestrationError) as e:
        ro.validate_observation(obs, actor="home", now_ns=now)
    assert e.value.reason == "observation_lock_not_clear"
    obs = _good_obs(now)
    obs["locks"]["unknown_lock"] = "clear"
    with pytest.raises(ro.OrchestrationError) as e:
        ro.validate_observation(obs, actor="home", now_ns=now)
    assert e.value.reason == "observation_locks_incomplete"


def test_validate_rejects_bool_counter():
    now = 10_000_000_000
    obs = _good_obs(now)
    obs["need_files"] = True
    with pytest.raises(ro.OrchestrationError) as e:
        ro.validate_observation(obs, actor="home", now_ns=now)
    assert e.value.reason == "observation_counter_invalid"


def test_flock_held_and_probe_does_not_create(tmp_path):
    import fcntl
    lock = tmp_path / "probe.lock"
    assert not lock.exists()
    result = ro._probe_flock(lock)
    assert result == "clear"
    assert not lock.exists()
    lock.write_text("x")
    fd = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert ro._probe_flock(lock) == "busy"
        fcntl.flock(fd, fcntl.LOCK_UN)
        assert ro._probe_flock(lock) == "clear"
    finally:
        os.close(fd)


def test_mkdir_lock_protocol(tmp_path):
    target = tmp_path / "note.lock.d"
    assert ro._probe_mkdir(target) == "clear"
    assert not target.exists()
    target.mkdir()
    assert ro._probe_mkdir(target) == "busy"
    assert target.exists()
    symlink = tmp_path / "sym.lock.d"
    symlink.symlink_to(target)
    assert ro._probe_mkdir(symlink) == "unknown"


def test_probe_sjm_fixed_ssh_and_no_secret(monkeypatch):
    captured = {}
    good = json.dumps(_status_obs_json()).encode()

    def fake_probe(argv, payload, timeout):
        captured["argv"] = argv
        captured["payload"] = payload
        return subprocess.CompletedProcess(argv, 0, good, b"")

    monkeypatch.setattr(ro, "_run_probe", fake_probe)
    monkeypatch.setattr(ro.time, "time_ns", lambda: 10_000_000_000)
    result = ro.probe_sjm()
    argv = captured["argv"]
    assert argv[0] == "ssh"
    assert "-T" in argv and "BatchMode=yes" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "ConnectTimeout=1" in argv
    assert argv[-2] == "sjm-ai"
    assert argv[-1] == "python3 -"
    payload = captured["payload"]
    assert b"SECRETKEY" not in payload
    assert b"local_status('sjm')" in payload
    assert result["received_at_ns"] == 10_000_000_000
    ro.validate_observation(result["observation"], actor="sjm", now_ns=10_000_000_000)


def test_probe_sjm_bad_actor_refused(monkeypatch):
    obs = _good_obs(10_000_000_000)

    def fake_probe(argv, payload, timeout):
        return subprocess.CompletedProcess(argv, 0, json.dumps(obs).encode(), b"")

    monkeypatch.setattr(ro, "_run_probe", fake_probe)
    monkeypatch.setattr(ro.time, "time_ns", lambda: 10_000_000_000)
    with pytest.raises(ro.OrchestrationError) as e:
        ro.probe_sjm()
    assert e.value.reason == "observation_actor_invalid"


def test_probe_sjm_oversized_and_failure(monkeypatch):
    def big(argv, payload, timeout):
        raise ro.OrchestrationError("probe_output_too_large")

    monkeypatch.setattr(ro, "_run_probe", big)
    with pytest.raises(ro.OrchestrationError) as e:
        ro.probe_sjm()
    assert e.value.reason == "probe_output_too_large"

    def fail(argv, payload, timeout):
        return subprocess.CompletedProcess(argv, 1, b"", b"errdetail")

    monkeypatch.setattr(ro, "_run_probe", fail)
    with pytest.raises(ro.OrchestrationError) as e:
        ro.probe_sjm()
    assert e.value.reason == "probe_failed"
    assert "errdetail" not in e.value.reason


def test_run_probe_stdout_cap(monkeypatch):
    monkeypatch.setattr(ro, "PROBE_SJM_MAX_BYTES", 1024)
    with pytest.raises(ro.OrchestrationError) as e:
        ro._run_probe([sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*8192)"], b"", 3)
    assert e.value.reason == "probe_output_too_large"


def test_run_probe_stderr_cap(monkeypatch):
    monkeypatch.setattr(ro, "PROBE_SJM_MAX_BYTES", 1024)
    with pytest.raises(ro.OrchestrationError) as e:
        ro._run_probe([sys.executable, "-c", "import sys;sys.stderr.buffer.write(b'x'*8192)"], b"", 3)
    assert e.value.reason == "probe_output_too_large"


def test_run_probe_timeout_and_no_orphan():
    with pytest.raises(ro.OrchestrationError) as e:
        ro._run_probe([sys.executable, "-c", "import time;time.sleep(5)"], b"", 0.05)
    assert e.value.reason == "probe_timeout"


def test_run_probe_ok():
    proc = ro._run_probe([sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'hi')"], b"", 5)
    assert proc.returncode == 0
    assert proc.stdout == b"hi"
    assert proc.stderr == b""


def _tree(root: Path) -> None:
    (root / "wiki" / "deep" / "nested" / "zzz").mkdir(parents=True)
    (root / "wiki" / "deep" / "nested" / "aaa").mkdir(parents=True)
    (root / "wiki" / "keep").mkdir(parents=True)
    (root / "wiki" / "deep" / "nested" / "zzz" / "q.md").write_text("zzz")
    (root / "wiki" / "deep" / "nested" / "aaa" / "a.md").write_text("aaa")
    (root / "wiki" / "keep" / "k.md").write_text("keep")
    (root / "unselected.txt").write_text("nope")
    os.chmod(root / "wiki" / "deep" / "nested", 0o750)


APPROVED = ["wiki/keep/k.md", "wiki/deep/nested/zzz/q.md", "wiki/deep/nested/aaa/a.md"]


def test_plan_deterministic_under_permuted_list(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    p1 = ro.archive_plan(root, APPROVED)
    p2 = ro.archive_plan(root, list(reversed(APPROVED)))
    assert p1["entries"] == p2["entries"]
    assert p1["entries_nul_sha256"] == p2["entries_nul_sha256"]
    assert p1["files"] == sorted(p1["files"], key=lambda s: s.encode())
    assert "wiki/deep/nested" in p1["entries"]
    assert "wiki" in p1["entries"]
    assert "unselected.txt" not in p1["entries"]
    assert ro.entries_nul(p1).endswith(b"\x00")


def test_plan_rejects_duplicate_and_tamper(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    with pytest.raises(ro.OrchestrationError) as e:
        ro.archive_plan(root, APPROVED + [APPROVED[0]])
    assert e.value.reason == "archive_duplicate_file"
    plan = ro.archive_plan(root, APPROVED)
    plan["entries_nul_sha256"] = "0" * 64
    plan["plan_sha256"] = ro.plan_digest(plan)
    with pytest.raises(ro.OrchestrationError) as e:
        ro.entries_nul(plan)
    assert e.value.reason == "plan_digest_invalid"


def test_plan_rejects_missing_ancestor_under_recomputed_digest(tmp_path):
    import hashlib
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    plan = ro.archive_plan(root, ["wiki/keep/k.md"])
    plan["directories"] = [d for d in plan["directories"] if d["path"] != "wiki"]
    plan["entries"] = [e for e in plan["entries"] if e != "wiki"]
    plan["entries_nul_sha256"] = hashlib.sha256(b"".join(x.encode() + b"\0" for x in plan["entries"])).hexdigest()
    plan["plan_sha256"] = ro.plan_digest(plan)
    with pytest.raises(ro.OrchestrationError):
        ro.entries_nul(plan)


def test_plan_rejects_rule_tamper(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    plan = ro.archive_plan(root, ["wiki/keep/k.md"])
    plan["rule_sha256"] = "0" * 64
    plan["plan_sha256"] = ro.plan_digest(plan)
    with pytest.raises(ro.OrchestrationError):
        ro.entries_nul(plan)


def test_plan_rejects_case_collision(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    plan = ro.archive_plan(root, ["wiki/keep/k.md"])
    clone = json.loads(json.dumps(plan))
    clone["files"] = clone["files"] + ["Wiki/keep/k.md"]
    clone["plan_sha256"] = ro.plan_digest(clone)
    with pytest.raises(ro.OrchestrationError):
        ro.entries_nul(clone)


def test_plan_rejects_symlink_and_hardlink(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    (root / "wiki" / "keep" / "link.md").symlink_to(root / "wiki" / "keep" / "k.md")
    with pytest.raises(ro.OrchestrationError):
        ro.archive_plan(root, APPROVED + ["wiki/keep/link.md"])
    os.link(root / "wiki" / "keep" / "k.md", root / "wiki" / "keep" / "hard.md")
    with pytest.raises(ro.OrchestrationError) as e:
        ro.archive_plan(root, ["wiki/keep/hard.md"])
    assert e.value.reason == "archive_hardlink_rejected"


def test_plan_rejects_traversal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    with pytest.raises(ro.OrchestrationError):
        ro.archive_plan(root, ["../escape.md"])


def test_tar_round_trip(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _tree(root)
    plan = ro.archive_plan(root, APPROVED)
    listfile = tmp_path / "list.nul"
    listfile.write_bytes(ro.entries_nul(plan))
    archive = tmp_path / "out.tar"
    env = dict(os.environ, COPYFILE_DISABLE="1")
    create = ro.tar_create_argv(root, listfile, archive)
    subprocess.run(create, check=True, env=env, capture_output=True)
    dest = tmp_path / "dest"
    dest.mkdir()
    extract = ro.tar_extract_argv(archive, dest)
    subprocess.run(extract, check=True, capture_output=True)
    restored = sorted(
        str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()
    )
    assert restored == sorted(plan["files"])
    mode = stat.S_IMODE(os.stat(dest / "wiki" / "deep" / "nested").st_mode)
    assert mode == 0o750


def test_tar_builders_require_absolute_and_nested_overlap(tmp_path):
    with pytest.raises(ro.OrchestrationError) as e:
        ro.tar_create_argv(Path("rel"), tmp_path / "l", tmp_path / "a")
    assert e.value.reason == "tar_path_not_absolute"
    with pytest.raises(ro.OrchestrationError) as e:
        ro.tar_extract_argv(Path("relative.tar"), tmp_path / "dest")
    assert e.value.reason == "tar_path_not_absolute"
    nested_archive = tmp_path / "dest" / "inner.tar"
    nested_dest = tmp_path / "dest"
    with pytest.raises(ro.OrchestrationError) as e:
        ro.tar_extract_argv(nested_archive, nested_dest)
    assert e.value.reason == "tar_paths_overlap"
    sibling_archive = tmp_path / "a.tar"
    sibling_dest = tmp_path / "b_dest"
    sibling_dest.mkdir()
    argv = ro.tar_extract_argv(sibling_archive, sibling_dest)
    assert "-xpf" in argv


def test_default_lock_specs_owner_paths(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.delenv("K2B_NOTE_WRITE_LOCK", raising=False)
    monkeypatch.delenv("K2B_COMPILE_INDEX_LOCK", raising=False)
    monkeypatch.delenv("K2B_WIKI_LOG_LOCK", raising=False)
    home = tmp_path / "operator"
    vault = home / "Projects" / "K2B-Vault"
    vault.mkdir(parents=True)
    records = {row["name"]: row for row in ro.default_lock_specs(home, vault=vault)}
    assert Path(records["integration"]["path"]) == home / ".local" / "state" / "k2b" / "digestion" / ".integration.lock"
    expected = "k2b-memory-publication-" + hashlib.sha256(str(vault.resolve()).encode()).hexdigest() + ".lock"
    assert Path(records["publication"]["path"]) == Path("/tmp").resolve() / expected


def test_lock_specs_duplicates_rejected(tmp_path):
    specs = _lock_specs(tmp_path)
    specs.append(dict(specs[0]))
    with pytest.raises(ro.OrchestrationError):
        ro._normalize_lock_specs(specs, tmp_path, tmp_path)


def test_nas_target_and_parent_validation():
    good = ro.nas_command("maint@10.0.0.9", "/volume1/homes/maint", "identity")
    assert good[0] == "ssh"
    assert good[-2] == "maint@10.0.0.9"
    assert "/bin/hostname" in good[-1]
    ro.nas_command("maint@100.64.0.5", "/volume1/homes/maint", "tools")
    for bad in ["maint@8.8.8.8", "maint@127.0.0.1", "maint@0.0.0.0", "maint@224.0.0.1", "maint@::1"]:
        with pytest.raises(ro.OrchestrationError) as e:
            ro.nas_command(bad, "/volume1/homes/user", "identity")
        assert e.value.reason == "nas_target_invalid"
    for bad_parent in ["/volume1/homes/../x", "/volume1/homes/user;rm", "/root", "/volume1/homes/a b"]:
        with pytest.raises(ro.OrchestrationError) as e:
            ro.nas_command("maint@10.0.0.9", bad_parent, "identity")
        assert e.value.reason == "nas_parent_invalid"


def test_nas_run_id_and_actions():
    with pytest.raises(ro.OrchestrationError) as e:
        ro.nas_command("maint@10.0.0.9", "/volume1/homes/maint", "hash_archive")
    assert e.value.reason == "nas_run_id_required"
    with pytest.raises(ro.OrchestrationError) as e:
        ro.nas_command("maint@10.0.0.9", "/volume1/homes/maint", "hash_archive", run_id="bad id")
    assert e.value.reason == "nas_run_id_invalid"
    good = ro.nas_command("maint@10.0.0.9", "/volume1/homes/maint", "hash_archive", run_id="manual-2024-x")
    assert "/volume1/homes/maint/K2B-Recovery/manual-2024-x/knowledge.tar.gpg" in good[-1]
    with pytest.raises(ro.OrchestrationError) as e:
        ro.nas_command("maint@10.0.0.9", "/volume1/homes/maint", "sudo")
    assert e.value.reason == "nas_action_invalid"


def test_tools_and_stat_parsers():
    out = (
        "present:/bin/hostname\n"
        "missing_tool:/usr/bin/stat\n"
        "permission_unavailable:/usr/bin/sha256sum\n"
        "present:/usr/bin/python3\n"
        "permission_unavailable:/usr/syno/bin/synoacltool\n"
    )
    parsed = ro.parse_tools_output(out)
    assert parsed["/usr/bin/stat"] == "missing_tool"
    assert parsed["/usr/bin/sha256sum"] == "permission_unavailable"
    assert parsed["/usr/bin/python3"] == "present"
    st = ro.parse_stat_output("81a4|100|100|0644|5|77|1|42")
    assert st["size"] == 42 and st["mode"] == "0644"
    with pytest.raises(ro.OrchestrationError):
        ro.parse_stat_output("socket|1|1|0644|1|1|1|0")
    with pytest.raises(ro.OrchestrationError):
        ro.parse_stat_output("regular file|1|1|888|1|1|1|1")
    with pytest.raises(ro.OrchestrationError):
        ro.parse_tools_output(out + "present:/bin/hostname\n")


def test_hash_parser():
    digest = "a" * 64
    path = "/volume1/homes/user/K2B-Recovery/manual-1/knowledge.tar.gpg"
    assert ro.parse_hash_output(digest + "  " + path + "\n", path) == digest
    assert ro.parse_hash_output(digest + " *" + path + "\n", path) == digest
    with pytest.raises(ro.OrchestrationError):
        ro.parse_hash_output(digest + "  " + path + "\n" + digest + "  " + path, path)
    with pytest.raises(ro.OrchestrationError):
        ro.parse_hash_output(digest + "  " + path + ".bak", path)
    with pytest.raises(ro.OrchestrationError):
        ro.parse_hash_output("A" * 64 + "  " + path, path)
