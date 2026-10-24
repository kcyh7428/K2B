"""Shared startup must be local, source-bound and honest about missing tools."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import shutil
import signal

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/k2b-bootstrap.py"


@pytest.fixture
def local_tree(tmp_path):
    project = tmp_path / "project"
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    for name, text in {
        "AGENTS.md": "# Current authority\nUse local sources.\n",
        "scripts/k2b-context.py": "# context helper\n",
        "scripts/vault-note-write.py": "# ordinary writer\n",
    }.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for name, text in {
        "wiki/index.md": "# Knowledge\nLOCAL INDEX TOKEN\n",
        "wiki/context/context_keith-profile.md": "# Profile\nLOCAL PROFILE TOKEN\n",
        "wiki/context/preference-profile.md": "# Preferences\nLOCAL PREFERENCE TOKEN\n",
        "wiki/context/context_llm-providers.md": "# Providers\nCurrent provider policy.\n",
        "System/memory/active_rules.md": "# Rules\n\nLast audited: RETIRED AUDIT\n\nCURRENT RULE TOKEN\n",
    }.items():
        path = vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return project, vault, state


def run_bootstrap(local_tree, *args, env=None):
    project, vault, state = local_tree
    result = subprocess.run(
        [sys.executable, str(CLI), "--project", str(project), "--vault", str(vault),
         "--state-root", str(state), *args],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})},
        text=True, capture_output=True, timeout=12,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("host,role", [("home", "home"), ("sjm", "sjm-source-only")])
def test_each_host_uses_local_sources_without_contacting_home(local_tree, host, role):
    packet = json.loads(run_bootstrap(local_tree, "--host", host, "--format", "json"))
    assert packet["host"] == host
    assert packet["writer_role"] == role
    assert packet["synchronization"] == "unverified"
    assert packet["home_availability"] == "not_checked_or_required"
    source = next(s for s in packet["sources"] if s["id"] == "authority")
    assert source["sha256"] == hashlib.sha256(b"# Current authority\nUse local sources.\n").hexdigest()
    assert str(local_tree[0]) in source["path"]
    assert "CURRENT RULE TOKEN" in packet["local_status"]
    assert "RETIRED AUDIT" not in packet["local_status"]


def test_text_preserves_rules_index_and_same_profile_for_both_assistants(local_tree):
    text = run_bootstrap(local_tree, "--host", "sjm")
    for token in ["LOCAL INDEX TOKEN", "LOCAL PROFILE TOKEN", "LOCAL PREFERENCE TOKEN", "CURRENT RULE TOKEN"]:
        assert token in text
    assert "saved locally; synchronization unverified" in text
    assert "Kimi Work" in text and "Codex" in text


def test_missing_profile_and_status_are_explicit_not_success(local_tree):
    (local_tree[1] / "wiki/context/context_keith-profile.md").unlink()
    packet = json.loads(run_bootstrap(local_tree, "--format", "json"))
    source = next(s for s in packet["sources"] if s["id"] == "profile")
    assert source["status"] == "missing"
    assert "no local legacy inventory" in packet["local_status"]
    assert "AUTOMATIC MEMORY: unknown local evidence" in packet["local_status"]
    assert packet["synchronization"] == "unverified"


def test_stale_profile_is_labelled_and_source_is_not_rewritten(local_tree):
    profile = local_tree[1] / "wiki/context/context_keith-profile.md"
    os.utime(profile, (1, 1))
    before = profile.read_bytes()
    packet = json.loads(run_bootstrap(local_tree, "--format", "json"))
    source = next(s for s in packet["sources"] if s["id"] == "profile")
    assert source["stale"] is True
    assert profile.read_bytes() == before


def test_unavailable_tools_are_not_claimed_as_session_capabilities(local_tree):
    (local_tree[0] / "scripts/vault-note-write.py").unlink()
    packet = json.loads(run_bootstrap(local_tree, "--format", "json", env={"PATH": "/usr/bin:/bin"}))
    assert packet["capabilities"]["ordinary_save"] == "unavailable"
    assert packet["capabilities"]["browser"] == "session_check_required"
    assert packet["capabilities"]["plugins"] == "session_check_required"


@pytest.mark.parametrize("kind", ["oversize", "symlink", "directory", "nul"])
def test_unsafe_profile_is_not_loaded(local_tree, tmp_path, kind):
    profile = local_tree[1] / "wiki/context/context_keith-profile.md"
    profile.unlink()
    if kind == "oversize":
        profile.write_text("SECRET PROFILE TOKEN" * 5000)
    elif kind == "directory":
        profile.mkdir()
    elif kind == "nul":
        profile.write_text("SECRET PROFILE TOKEN\x00corrupt")
    else:
        outside = tmp_path / "outside.md"
        outside.write_text("SECRET PROFILE TOKEN")
        profile.symlink_to(outside)
    packet = json.loads(run_bootstrap(local_tree, "--format", "json"))
    source = next(s for s in packet["sources"] if s["id"] == "profile")
    assert source["status"] == "unavailable"
    assert "SECRET PROFILE TOKEN" not in json.dumps(packet)


def test_startup_does_not_run_providers_or_remote_commands(local_tree, tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    called = tmp_path / "remote-called"
    for name in ["ssh", "curl", "kimi", "codex", "openai"]:
        path = fake_bin / name
        path.write_text(f'#!/bin/sh\ntouch "{called}"\nexit 90\n')
        path.chmod(0o700)
    run_bootstrap(local_tree, "--host", "sjm", env={"PATH": f"{fake_bin}:/usr/bin:/bin"})
    assert not called.exists()
    assert not local_tree[2].exists(), "read-only startup must not create state"


def load_bootstrap():
    sys.path.insert(0, str(ROOT / "scripts/lib"))
    import k2b_bootstrap
    return k2b_bootstrap


def test_helper_presence_is_unverified_not_a_runtime_probe(local_tree):
    packet = json.loads(run_bootstrap(local_tree, "--format", "json"))
    assert packet["capabilities"]["ordinary_save"] == "present_unverified"
    assert packet["capabilities"]["local_recall"] == "present_unverified"


def test_cross_tree_body_and_native_reader_are_explicitly_source_labelled(local_tree):
    packet = json.loads(run_bootstrap(local_tree, "--format", "json"))
    body = next(s for s in packet["sources"] if s["id"] == "startup_body")
    assert body["path"] == str(ROOT / "scripts/hooks/bootstrap-local.sh")
    assert body["sha256"] == hashlib.sha256((ROOT / "scripts/hooks/bootstrap-local.sh").read_bytes()).hexdigest()
    assert packet["implementation_root"] == str(ROOT)
    reader = next(s for s in packet["sources"] if s["id"] == "native_status_reader")
    assert reader["path"] == str(local_tree[0] / "scripts/lib/native_job_status.py")
    assert reader["status"] == "missing"


def test_composed_reader_budget_does_not_discard_a_completed_body(local_tree, tmp_path, monkeypatch):
    module = load_bootstrap()
    implementation = tmp_path / "implementation"
    body = implementation / "scripts/hooks/bootstrap-local.sh"
    body.parent.mkdir(parents=True)
    body.write_text("#!/bin/bash\nsleep 9.1\necho COMPLETED-LOCAL-STATUS\n")
    monkeypatch.setattr(module, "IMPLEMENTATION_ROOT", implementation)
    packet = module.build_bootstrap(*local_tree, "home")
    assert "COMPLETED-LOCAL-STATUS" in packet["local_status"]


def test_hook_uses_renderer_in_healthy_runtime(local_tree):
    project, vault, state = local_tree
    result = subprocess.run(["/bin/bash", str(ROOT / "scripts/hooks/session-start.sh")],
        env={**os.environ, "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
             "K2B_PROJECT_ROOT": str(project), "K2B_VAULT_PATH": str(vault), "K2B_STATE_ROOT": str(state)},
        text=True, capture_output=True, timeout=12)
    assert result.returncode == 0
    assert "K2B SHARED STARTUP (local read-only evidence)" in result.stdout
    assert "renderer unavailable" not in result.stdout


def test_terminated_conflict_scan_removes_its_private_tempfile(local_tree, tmp_path):
    project, vault, _ = local_tree
    child = project / "scripts/vault-note-write.py"
    child.write_text("import time\ntime.sleep(30)\n")
    temps = tmp_path / "temps"
    temps.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    mktemp = fake_bin / "mktemp"
    mktemp.write_text(f'#!/bin/sh\nexec "{shutil.which("mktemp")}" "{temps}/bootstrap.XXXXXX"\n')
    mktemp.chmod(0o700)
    process = subprocess.Popen(["/bin/bash", str(ROOT / "scripts/hooks/bootstrap-local.sh")],
        env={**os.environ, "K2B_PROJECT_ROOT": str(project), "K2B_VAULT_PATH": str(vault), "TMPDIR": str(temps),
             "PATH": str(fake_bin) + ":" + os.environ["PATH"]},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        deadline = time.monotonic() + 5
        while not list(temps.iterdir()) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert list(temps.iterdir()), "fixture must reach the conflict scan"
        process.terminate()
        process.communicate(timeout=4)
        assert list(temps.iterdir()) == []
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()


def test_stalled_uninstrumented_body_is_bounded_and_children_are_stopped(local_tree, tmp_path, monkeypatch):
    module = load_bootstrap()
    implementation = tmp_path / "implementation"
    body = implementation / "scripts/hooks/bootstrap-local.sh"
    body.parent.mkdir(parents=True)
    body.write_text("#!/bin/bash\ntrap '' TERM\nsleep 30 &\nwait\n")
    monkeypatch.setattr(module, "IMPLEMENTATION_ROOT", implementation)
    monkeypatch.setattr(module, "BODY_TIMEOUT_SECONDS", 0.2, raising=False)
    monkeypatch.setattr(module, "TERMINATION_GRACE_SECONDS", 0.1, raising=False)
    started = time.monotonic()
    packet = module.build_bootstrap(*local_tree, "home")
    assert time.monotonic() - started < 2
    assert "deadline exceeded" in packet["local_status"]


def test_both_internal_deadlines_report_before_registered_hook_timeout(local_tree):
    project, vault, state = local_tree
    reader = project / "scripts/lib/native_job_status.py"
    reader.parent.mkdir()
    reader.write_text("import time\ntime.sleep(30)\n")
    (project / "scripts/vault-note-write.py").write_text("import time\ntime.sleep(30)\n")
    started = time.monotonic()
    result = subprocess.run(["/bin/bash", str(ROOT / "scripts/hooks/session-start.sh")],
        env={**os.environ, "K2B_PROJECT_ROOT": str(project), "K2B_VAULT_PATH": str(vault), "K2B_STATE_ROOT": str(state)},
        capture_output=True, text=True, timeout=18)
    assert result.returncode == 0
    assert time.monotonic() - started < 12
    assert "import deadline" in result.stdout
    assert "conflict check timed out" in result.stdout
    assert "CURRENT RULE TOKEN" in result.stdout


def test_oversize_index_and_rules_are_not_injected_into_startup(local_tree):
    for relative in ["wiki/index.md", "System/memory/active_rules.md"]:
        (local_tree[1] / relative).write_text("OVERSIZE-SOURCE-TOKEN" * 4000)
    text = run_bootstrap(local_tree)
    assert "OVERSIZE-SOURCE-TOKEN" not in text
    assert "source exceeds 65536 byte bound" in text
    assert len(text) < 20000


def test_missing_conflict_checker_reports_unknown(local_tree):
    (local_tree[0] / "scripts/vault-note-write.py").unlink()
    packet = json.loads(run_bootstrap(local_tree, "--format", "json"))
    assert "VAULT CONFLICTS: unknown (conflict checker missing)" in packet["local_status"]


def test_supervisor_preserves_already_read_rules_before_interpreter_stall(local_tree, tmp_path, monkeypatch):
    module = load_bootstrap()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python = fake_bin / "python3"
    python.write_text("#!/bin/sh\nsleep 30\n")
    python.chmod(0o700)
    monkeypatch.setenv("PATH", str(fake_bin) + ":/usr/bin:/bin")
    monkeypatch.setattr(module, "BODY_TIMEOUT_SECONDS", 0.2)
    packet = module.build_bootstrap(*local_tree, "home")
    assert "CURRENT RULE TOKEN" in packet["local_status"]
    assert "LOCAL INDEX TOKEN" in packet["local_status"]
    assert "deadline exceeded" in packet["local_status"]


def test_oversize_legacy_status_is_rejected_before_parsing(local_tree):
    state = local_tree[2]
    state.mkdir()
    (state / "capture-status.json").write_text(json.dumps({"counts": {}, "padding": "x" * 70000}))
    packet = json.loads(run_bootstrap(local_tree, "--format", "json"))
    assert "CAPTURE INVENTORY: unreadable local status (ValueError)" in packet["local_status"]


def test_nul_corruption_is_not_silently_stripped_from_rules(local_tree):
    (local_tree[1] / "System/memory/active_rules.md").write_bytes(b"DO-NOT-INJECT\x00CORRUPT-RULE")
    text = run_bootstrap(local_tree)
    assert "DO-NOT-INJECT" not in text
    assert "non-text or changed source" in text


def test_unreapable_killed_child_returns_an_explicit_cleanup_limit(local_tree, monkeypatch):
    import io
    module = load_bootstrap()

    # An uninterruptible OS child cannot be produced safely in a test. Keep
    # that external process boundary controlled while testing the real caller.
    class UnreapableProcess:
        pid = 123456789

        def __init__(self, *args, **kwargs):
            self.stdout = io.StringIO()
            self.stderr = io.StringIO()

        def communicate(self, timeout=None):
            if timeout is None:
                raise AssertionError("supervisor attempted an unbounded final wait")
            raise subprocess.TimeoutExpired("body", timeout, output=b"PARTIAL-STATUS\n")

    monkeypatch.setattr(module.subprocess, "Popen", UnreapableProcess)
    monkeypatch.setattr(module.os, "killpg", lambda *args: None)
    packet = module.build_bootstrap(*local_tree, "home")
    assert "cleanup incomplete" in packet["local_status"]
    assert "PARTIAL-STATUS" in packet["local_status"]


@pytest.mark.parametrize("relative,survivor", [
    ("wiki/index.md", "CURRENT RULE TOKEN"),
    ("System/memory/active_rules.md", "LOCAL INDEX TOKEN"),
])
def test_invalid_utf8_section_preserves_other_startup_sections(local_tree, relative, survivor):
    (local_tree[1] / relative).write_bytes(b"CORRUPT-UTF8-TOKEN\xff\n")
    (local_tree[0] / "scripts/vault-note-write.py").unlink()
    text = run_bootstrap(local_tree)
    assert survivor in text
    assert "CORRUPT-UTF8-TOKEN" not in text
    assert "invalid UTF-8 source" in text
    assert "VAULT CONFLICTS: unknown (conflict checker missing)" in text
