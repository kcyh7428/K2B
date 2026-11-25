import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "vault_connect", Path(__file__).resolve().parents[1] / "scripts/k2b-vault-connect.py")
connect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(connect)


@pytest.mark.parametrize(("source", "flags", "expected"), [
    (connect.SOURCE, "smbfs, nodev", True),
    (connect.SOURCE, "smbfs, read-only", None),
    ("//cheung@192.168.50.67/Keith-Work", "smbfs, nodev", None),
    ("/dev/disk", "apfs, local", None),
    ("", "", False),
])
def test_exact_mount(monkeypatch, source, flags, expected):
    mount = Path("/Users/keithmbpm2/Projects/K2B-NAS")
    listing = f"{source} on {mount} ({flags})" if source else ""
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: listing)
    if expected is None:
        with pytest.raises(ValueError, match="occupied"):
            connect.mounted(mount)
    else:
        assert connect.mounted(mount) is expected


@pytest.mark.parametrize("case", ["missing", "lost", "wrong-alias", "good"])
def test_check_only_never_reconnects(monkeypatch, capsys, case):
    actor = "keithmbpm2"
    mount = connect.MOUNT_POINTS[actor]
    monkeypatch.setattr(connect.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name=actor))
    monkeypatch.setattr(Path, "home", lambda: Path("/Users") / actor)
    monkeypatch.setattr(Path, "is_symlink", lambda path: True)
    monkeypatch.setattr(Path, "readlink",
                        lambda path: mount / ("other" if case == "wrong-alias" else "K2B-Vault"))
    values = iter([case != "missing", case != "lost"])
    monkeypatch.setattr(connect, "mounted", lambda path: next(values))
    monkeypatch.setattr(connect.sys, "argv", ["k2b-vault-connect", "--check-only"])
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    monkeypatch.setattr(connect, "reconnect", lambda *args: pytest.fail("mount requested"))
    assert connect.main() == (0 if case == "good" else 1)
    assert ("K2B vault readable" in capsys.readouterr().out) == (case == "good")


def test_offline_attempt_does_not_compile_or_mount(monkeypatch, tmp_path):
    def unavailable(*args, **kwargs):
        raise TimeoutError("NAS unavailable")

    monkeypatch.setattr(connect.socket, "create_connection", unavailable)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("mount requested"))
    with pytest.raises(ValueError, match="over Tailscale"):
        connect.reconnect(tmp_path, "keithmbpm2")


def test_timed_out_netfs_rechecks_mount(monkeypatch, tmp_path):
    from contextlib import nullcontext

    def run(command, **kwargs):
        if command[0] != "/usr/bin/clang":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(connect.socket, "create_connection", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(subprocess, "run", run)
    mount_checks = iter([False, True])
    monkeypatch.setattr(connect, "mounted", lambda mount: next(mount_checks))
    connect.reconnect(tmp_path, "keithmbpm2")


def test_sjm_numbered_mount_is_refused(monkeypatch):
    mount = connect.MOUNT_POINTS["keithcheung"]
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw:
                        f"{connect.SOURCE} on {mount}-1 (smbfs, nodev)")
    with pytest.raises(ValueError, match="numbered"):
        connect.mounted(mount)


def test_sjm_refuses_to_cover_existing_directory(tmp_path):
    with pytest.raises(ValueError, match="occupied"):
        connect.reconnect(tmp_path, "keithcheung")


def test_home_refuses_files_created_while_preparing_mount(monkeypatch, tmp_path):
    from contextlib import nullcontext

    def compile_helper(command, **kwargs):
        assert command[0] == "/usr/bin/clang", "unexpected mount"
        (tmp_path / "local-file").touch()

    monkeypatch.setattr(connect.socket, "create_connection", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(subprocess, "run", compile_helper)
    monkeypatch.setattr(connect, "mounted", lambda mount: False)
    with pytest.raises(ValueError, match="changed while preparing"):
        connect.reconnect(tmp_path, "keithmbpm2")
