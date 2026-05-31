#!/usr/bin/env python3
"""pytest unit tests for orchestrator_worker.py"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = tmp_path / "orch.sqlite"
    monkeypatch.setenv("K2B_VAULT_PATH", str(vault))
    monkeypatch.setenv("K2B_ORCH_DB", str(db))
    # Use a temp workspace for k2bi that exists
    ws = tmp_path / "k2bi"
    ws.mkdir()
    monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(ws))
    # Remove any cached orchestrator modules
    to_remove = [k for k in sys.modules if "orchestrator" in k]
    for k in to_remove:
        del sys.modules[k]


@pytest.fixture
def store(temp_env, tmp_path):
    from scripts.lib import orchestrator_store as store

    db = tmp_path / "orch.sqlite"
    vault = tmp_path / "vault"
    store.DB_PATH = str(db)
    store.RESULTS_DIR = str(vault / "raw" / "orchestrator-results")
    store.BOARD_PATH = str(vault / "System" / "orchestrator" / "board.md")
    store.K2B_VAULT = str(vault)
    store.init_db(store.connect())
    os.makedirs(store.RESULTS_DIR, exist_ok=True)
    return store


@pytest.fixture
def telegram_recorder(tmp_path, monkeypatch):
    recorder = tmp_path / "recorder.sh"
    recorder.write_text("#!/bin/bash\necho \"$@\" >> \"$RECORDER_OUT\"\n")
    recorder.chmod(0o755)
    out = tmp_path / "out.txt"
    monkeypatch.setenv("RECORDER_OUT", str(out))
    monkeypatch.setenv("K2B_ORCH_TELEGRAM_CMD", str(recorder))
    return out


def _spawn_worker(task_id):
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "scripts.lib.orchestrator_worker", task_id],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    return proc


class TestWorkerEcho:
    def test_worker_echo_done(self, store, telegram_recorder):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid)
        proc = _spawn_worker(tid)
        stdout, stderr = proc.communicate(timeout=30)
        assert proc.returncode == 0, stderr.decode()

        # Wait for DB to settle
        for _ in range(50):
            t = store.get_task(tid)
            if t["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        assert t["status"] == "done"

        artifact = Path(store.RESULTS_DIR) / f"{tid}-k2bi-smoke.md"
        assert artifact.exists()
        assert "orchestrator-smoke-ok" in artifact.read_text()

        messages = telegram_recorder.read_text().strip().splitlines()
        assert len(messages) == 1
        assert "DONE" in messages[0]
        assert tid in messages[0]

    def test_worker_pid_is_own(self, store, telegram_recorder):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid)
        proc = _spawn_worker(tid)
        proc.wait(timeout=30)
        for _ in range(50):
            t = store.get_task(tid)
            if t["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        # worker_pid should be the subprocess PID, not None
        assert t["worker_pid"] == proc.pid


class TestWorkerFailPaths:
    def test_non_allowlisted_command(self, store, telegram_recorder):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="bad-command",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid)
        proc = _spawn_worker(tid)
        proc.wait(timeout=30)
        for _ in range(50):
            t = store.get_task(tid)
            if t["status"] in ("done", "failed", "blocked"):
                break
            time.sleep(0.1)
        assert t["status"] == "failed"
        messages = telegram_recorder.read_text().strip().splitlines()
        assert len(messages) == 1
        assert "FAILED" in messages[0]

    def test_stale_heartbeat_treated_as_failed(self, store, telegram_recorder, tmp_path, monkeypatch):
        # In-process test with mocked subprocess and time
        import threading as _threading
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid)

        # Mock subprocess.run to return success instantly
        original_run = worker.subprocess.run

        class FakeResult:
            returncode = 0
            stdout = "ok"
            stderr = ""

        def fake_run(*args, **kwargs):
            return FakeResult()

        worker.subprocess.run = fake_run

        # Capture notify messages directly so subprocess.run is only called for the command
        notified = []
        original_notify = store.notify
        store.notify = lambda msg: notified.append(msg)

        # No-op the heartbeat thread so last_beat_ok stays at init value
        original_thread_cls = worker.threading.Thread
        class FakeThread(original_thread_cls):
            def start(self):
                if getattr(self._target, "__name__", None) == "_beat":
                    self._target = lambda: None
                super().start()

        worker.threading.Thread = FakeThread

        # Patch time: init gets base_time, stale check gets base_time+400
        base_time = 1000000.0
        calls = [0]

        def fake_time():
            calls[0] += 1
            if calls[0] == 1:
                return base_time
            return base_time + 400

        real_time = worker.time.time
        worker.time.time = fake_time
        try:
            rc = worker.main(tid)
            assert rc == 0  # worker exits cleanly even when downgrading status
        finally:
            worker.subprocess.run = original_run
            worker.time.time = real_time
            worker.threading.Thread = original_thread_cls
            store.notify = original_notify

        t = store.get_task(tid)
        assert t["status"] == "failed"
        assert "stale" in (t.get("blocker_reason") or "").lower()

        assert len(notified) == 1
        assert "FAILED" in notified[0]

    def test_trusted_workspace_ignores_task_value(self, store, telegram_recorder, tmp_path, monkeypatch):
        # Set up a real temp workspace and point k2bi there
        real_ws = tmp_path / "real_k2bi"
        real_ws.mkdir()
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(real_ws))

        # Reload modules to pick up the env
        to_remove = [k for k in sys.modules if "orchestrator" in k]
        for k in to_remove:
            del sys.modules[k]
        from scripts.lib import orchestrator_store as store2
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        store2.init_db(store2.connect())
        os.makedirs(store2.RESULTS_DIR, exist_ok=True)

        tid = store2.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        # Manually set a different workspace_path in the task row
        conn = store2.connect()
        conn.execute("UPDATE tasks SET workspace_path=? WHERE id=?", ("/tmp/evil", tid))
        conn.commit()
        conn.close()

        store2.mark_running(tid)

        original_run = worker.subprocess.run
        captured = {}

        def fake_run(*args, **kwargs):
            captured["cwd"] = kwargs.get("cwd")
            class R:
                returncode = 0
                stdout = "ok"
                stderr = ""
            return R()

        # Suppress notify so only the command hits our fake_run
        original_notify = store2.notify
        store2.notify = lambda msg: None

        worker.subprocess.run = fake_run
        try:
            worker.main(tid)
        finally:
            worker.subprocess.run = original_run
            store2.notify = original_notify

        assert captured["cwd"] == str(real_ws)
        t = store2.get_task(tid)
        assert t["status"] == "done"

    def test_worker_lock_held_and_released(self, store, telegram_recorder, tmp_path):
        # In-process: verify lock file exists during run and is gone after
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid)

        lock_path = profiles.get_profile("k2bi")["worker_lock"]
        # Ensure lock does not exist
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass

        original_run = worker.subprocess.run
        event = threading.Event()

        def fake_run(*args, **kwargs):
            # Assert lock exists while running
            assert os.path.exists(lock_path), "worker lock should exist during run"
            event.set()
            class R:
                returncode = 0
                stdout = "ok"
                stderr = ""
            return R()

        worker.subprocess.run = fake_run
        try:
            worker.main(tid)
        finally:
            worker.subprocess.run = original_run

        assert event.is_set()
        assert not os.path.exists(lock_path), "worker lock should be gone after run"

    def test_second_worker_blocked_when_lock_held(self, store, telegram_recorder, tmp_path):
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        tid1 = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        tid2 = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid1)
        store.mark_running(tid2)

        lock_path = profiles.get_profile("k2bi")["worker_lock"]
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass

        # Pre-create the lock to simulate another worker holding it
        with open(lock_path, "w") as f:
            f.write("99999")

        original_run = worker.subprocess.run
        ran = [False]

        def fake_run(*args, **kwargs):
            ran[0] = True
            class R:
                returncode = 0
                stdout = "ok"
                stderr = ""
            return R()

        # Suppress notify so only the command hits our fake_run
        original_notify = store.notify
        store.notify = lambda msg: None

        worker.subprocess.run = fake_run
        try:
            rc = worker.main(tid2)
        finally:
            worker.subprocess.run = original_run
            store.notify = original_notify
            try:
                os.unlink(lock_path)
            except FileNotFoundError:
                pass

        assert not ran[0], "command should NOT run when lock is held"
        t = store.get_task(tid2)
        assert t["status"] == "blocked"
        assert "lock already held" in (t.get("blocker_reason") or "").lower()


class TestNarrativeCommand:
    def test_resolve_command_argv_shape(self):
        from scripts.lib import orchestrator_profiles as profiles

        argv = profiles.resolve_command(
            "k2bi",
            "k2bi-narrative",
            payload={"narrative": "-AI capex --help so what"},
        )
        assert argv == [
            "python3",
            "-m",
            "scripts.lib.invest_narrative_pipeline",
            "--narrative=-AI capex --help so what",
        ]

    def test_resolve_command_without_payload(self):
        from scripts.lib import orchestrator_profiles as profiles

        # allowlist check path: returns base argv even without payload
        argv = profiles.resolve_command("k2bi", "k2bi-narrative")
        assert argv == [
            "python3",
            "-m",
            "scripts.lib.invest_narrative_pipeline",
        ]

    def test_worker_sets_k2bi_vault_root_env(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        real_ws = tmp_path / "real_k2bi"
        real_ws.mkdir()
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(real_ws))
        monkeypatch.setenv("K2B_ORCH_CMD_TIMEOUT", "1")

        # Reload to pick up env
        to_remove = [k for k in sys.modules if "orchestrator" in k]
        for k in to_remove:
            del sys.modules[k]
        from scripts.lib import orchestrator_store as store2
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        store2.init_db(store2.connect())
        os.makedirs(store2.RESULTS_DIR, exist_ok=True)

        tid = store2.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-narrative",
            success_criteria="ok",
            permissions="analyst-command",
            payload={"narrative": "AI capex is booming across all sectors globally now"},
        )
        store2.mark_running(tid)

        original_run = worker.subprocess.run
        captured_env = {}

        def fake_run(*args, **kwargs):
            captured_env["env"] = kwargs.get("env")
            class R:
                returncode = 0
                stdout = "/tmp/theme.md"
                stderr = ""
            return R()

        original_notify = store2.notify
        store2.notify = lambda msg: None

        worker.subprocess.run = fake_run
        try:
            worker.main(tid)
        finally:
            worker.subprocess.run = original_run
            store2.notify = original_notify

        assert captured_env.get("env") is not None
        assert captured_env["env"].get("K2BI_VAULT_ROOT") == profiles.k2bi_vault()

    def test_post_run_count_gate_pass(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        real_ws = tmp_path / "real_k2bi"
        real_ws.mkdir()
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(real_ws))

        to_remove = [k for k in sys.modules if "orchestrator" in k]
        for k in to_remove:
            del sys.modules[k]
        from scripts.lib import orchestrator_store as store2
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        store2.init_db(store2.connect())
        os.makedirs(store2.RESULTS_DIR, exist_ok=True)

        tid = store2.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-narrative",
            success_criteria="ok",
            permissions="analyst-command",
            payload={"narrative": "AI capex is booming across all sectors globally now"},
        )
        store2.mark_running(tid)

        # Create a fixture theme file with candidate-count: 6
        theme_path = tmp_path / "theme_ai_capex.md"
        theme_path.write_text(
            "---\n"
            "candidate-count: 6\n"
            "type: macro-theme\n"
            "---\n\n"
            "## Candidates\n\n"
            "- AAPL\n"
        )

        original_run = worker.subprocess.run

        def fake_run(*args, **kwargs):
            class R:
                returncode = 0
                stdout = str(theme_path)
                stderr = ""
            return R()

        original_notify = store2.notify
        store2.notify = lambda msg: None

        worker.subprocess.run = fake_run
        try:
            worker.main(tid)
        finally:
            worker.subprocess.run = original_run
            store2.notify = original_notify

        t = store2.get_task(tid)
        assert t["status"] == "done"

    def test_post_run_count_gate_fail_under_count(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        real_ws = tmp_path / "real_k2bi"
        real_ws.mkdir()
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(real_ws))

        to_remove = [k for k in sys.modules if "orchestrator" in k]
        for k in to_remove:
            del sys.modules[k]
        from scripts.lib import orchestrator_store as store2
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        store2.init_db(store2.connect())
        os.makedirs(store2.RESULTS_DIR, exist_ok=True)

        tid = store2.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-narrative",
            success_criteria="ok",
            permissions="analyst-command",
            payload={"narrative": "AI capex is booming across all sectors globally now"},
        )
        store2.mark_running(tid)

        theme_path = tmp_path / "theme_ai_capex.md"
        theme_path.write_text(
            "---\n"
            "candidate-count: 3\n"
            "type: macro-theme\n"
            "---\n\n"
            "## Candidates\n\n"
            "- AAPL\n"
        )

        original_run = worker.subprocess.run

        def fake_run(*args, **kwargs):
            class R:
                returncode = 0
                stdout = str(theme_path)
                stderr = ""
            return R()

        original_notify = store2.notify
        store2.notify = lambda msg: None

        worker.subprocess.run = fake_run
        try:
            worker.main(tid)
        finally:
            worker.subprocess.run = original_run
            store2.notify = original_notify

        t = store2.get_task(tid)
        assert t["status"] == "failed"
        assert "theme file malformed or under-count" in (t.get("blocker_reason") or "")

    def test_post_run_count_gate_fail_missing_file(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        real_ws = tmp_path / "real_k2bi"
        real_ws.mkdir()
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(real_ws))

        to_remove = [k for k in sys.modules if "orchestrator" in k]
        for k in to_remove:
            del sys.modules[k]
        from scripts.lib import orchestrator_store as store2
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        store2.init_db(store2.connect())
        os.makedirs(store2.RESULTS_DIR, exist_ok=True)

        tid = store2.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-narrative",
            success_criteria="ok",
            permissions="analyst-command",
            payload={"narrative": "AI capex is booming across all sectors globally now"},
        )
        store2.mark_running(tid)

        original_run = worker.subprocess.run

        def fake_run(*args, **kwargs):
            class R:
                returncode = 0
                stdout = "/nonexistent/theme.md"
                stderr = ""
            return R()

        original_notify = store2.notify
        store2.notify = lambda msg: None

        worker.subprocess.run = fake_run
        try:
            worker.main(tid)
        finally:
            worker.subprocess.run = original_run
            store2.notify = original_notify

        t = store2.get_task(tid)
        assert t["status"] == "failed"
        assert "theme file malformed or under-count" in (t.get("blocker_reason") or "")

    def test_post_run_count_gate_fail_unparseable_frontmatter(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        real_ws = tmp_path / "real_k2bi"
        real_ws.mkdir()
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(real_ws))

        to_remove = [k for k in sys.modules if "orchestrator" in k]
        for k in to_remove:
            del sys.modules[k]
        from scripts.lib import orchestrator_store as store2
        from scripts.lib import orchestrator_worker as worker
        from scripts.lib import orchestrator_profiles as profiles

        store2.init_db(store2.connect())
        os.makedirs(store2.RESULTS_DIR, exist_ok=True)

        tid = store2.add_task(
            assignee_profile="k2bi",
            command_key="k2bi-narrative",
            success_criteria="ok",
            permissions="analyst-command",
            payload={"narrative": "AI capex is booming across all sectors globally now"},
        )
        store2.mark_running(tid)

        theme_path = tmp_path / "theme_bad.md"
        theme_path.write_text("not frontmatter at all\n")

        original_run = worker.subprocess.run

        def fake_run(*args, **kwargs):
            class R:
                returncode = 0
                stdout = str(theme_path)
                stderr = ""
            return R()

        original_notify = store2.notify
        store2.notify = lambda msg: None

        worker.subprocess.run = fake_run
        try:
            worker.main(tid)
        finally:
            worker.subprocess.run = original_run
            store2.notify = original_notify

        t = store2.get_task(tid)
        assert t["status"] == "failed"
        assert "theme file malformed or under-count" in (t.get("blocker_reason") or "")


class TestParseCandidateCountFailClosed:
    """_parse_candidate_count must be a fail-closed frontmatter parser, not a
    line scanner. A garbled YAML block that merely contains a candidate-count
    line must NOT yield an int (the old scanner's fail-OPEN bug)."""

    def test_valid_frontmatter_returns_count(self, tmp_path):
        from scripts.lib import orchestrator_worker as worker
        p = tmp_path / "theme_ok.md"
        p.write_text("---\ntype: macro-theme\ncandidate-count: 6\n---\n\nbody\n")
        assert worker._parse_candidate_count(str(p)) == 6

    def test_valid_under_count_returns_low_int(self, tmp_path):
        from scripts.lib import orchestrator_worker as worker
        p = tmp_path / "theme_low.md"
        p.write_text("---\ncandidate-count: 3\n---\n")
        assert worker._parse_candidate_count(str(p)) == 3

    def test_invalid_yaml_with_count_line_fails_closed(self, tmp_path):
        """THE regression: invalid YAML between valid fences, count line present.
        Old line scanner returned 6 (fail-open); parser must return None."""
        from scripts.lib import orchestrator_worker as worker
        p = tmp_path / "theme_garbled.md"
        p.write_text("---\ncandidate-count: 6\nbad: [unterminated flow\n---\n")
        assert worker._parse_candidate_count(str(p)) is None

    def test_no_closing_fence_fails_closed(self, tmp_path):
        from scripts.lib import orchestrator_worker as worker
        p = tmp_path / "theme_open.md"
        p.write_text("---\ncandidate-count: 6\nno closing fence here\n")
        assert worker._parse_candidate_count(str(p)) is None

    def test_non_dict_frontmatter_fails_closed(self, tmp_path):
        from scripts.lib import orchestrator_worker as worker
        p = tmp_path / "theme_scalar.md"
        p.write_text("---\njust a bare string\n---\n")
        assert worker._parse_candidate_count(str(p)) is None

    def test_non_int_count_fails_closed(self, tmp_path):
        from scripts.lib import orchestrator_worker as worker
        p = tmp_path / "theme_str_count.md"
        p.write_text("---\ncandidate-count: six\n---\n")
        assert worker._parse_candidate_count(str(p)) is None

    def test_bool_count_fails_closed(self, tmp_path):
        from scripts.lib import orchestrator_worker as worker
        p = tmp_path / "theme_bool_count.md"
        p.write_text("---\ncandidate-count: true\n---\n")
        assert worker._parse_candidate_count(str(p)) is None
