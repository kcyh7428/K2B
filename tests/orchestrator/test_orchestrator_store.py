#!/usr/bin/env python3
"""pytest unit tests for orchestrator_store.py"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# We must set envs BEFORE importing the module because DB_PATH etc are
# resolved at import time.


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = tmp_path / "orch.sqlite"
    monkeypatch.setenv("K2B_VAULT_PATH", str(vault))
    monkeypatch.setenv("K2B_ORCH_DB", str(db))
    # Ensure a fresh module import for each test
    to_remove = [k for k in sys.modules if "orchestrator" in k]
    for k in to_remove:
        del sys.modules[k]


@pytest.fixture
def store(temp_env, tmp_path):
    from scripts.lib import orchestrator_store as store

    # Ensure this instance uses temp paths even if module was cached
    db = tmp_path / "orch.sqlite"
    vault = tmp_path / "vault"
    store.DB_PATH = str(db)
    store.RESULTS_DIR = str(vault / "raw" / "orchestrator-results")
    store.BOARD_PATH = str(vault / "System" / "orchestrator" / "board.md")
    store.K2B_VAULT = str(vault)
    store.init_db(store.connect())
    os.makedirs(store.RESULTS_DIR, exist_ok=True)
    return store


class TestAddAndGet:
    def test_add_task_basic(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        assert tid.startswith(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        t = store.get_task(tid)
        assert t["status"] == "ready"
        assert t["assignee_profile"] == "k2bi"
        assert t["command_key"] == "test-echo-readonly"
        assert t["success_criteria"] == "ok"
        assert t["permissions"] == "analyst-command"
        # k2bi workspace comes from profile, not caller
        assert t["workspace_path"] is not None

    def test_add_task_id_format(self, store):
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
        # NNN should increment
        suffix1 = int(tid1.split("-")[-1])
        suffix2 = int(tid2.split("-")[-1])
        assert suffix2 == suffix1 + 1


class TestClaimRace:
    def test_sequential_claim(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        assert store.mark_running(tid) is True
        assert store.mark_running(tid) is False
        t = store.get_task(tid)
        assert t["status"] == "running"

    def test_concurrent_claim_race(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        results = []
        errors = []
        barrier = threading.Barrier(8)

        def claim():
            barrier.wait()
            try:
                results.append(store.mark_running(tid))
            except Exception as e:  # noqa: BLE001 - any raise is a failure
                errors.append(repr(e))

        threads = [threading.Thread(target=claim) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # No thread may crash (a starving/split-brain lock would raise here),
        # every thread must have returned, and exactly one claim must win.
        assert errors == [], f"claim threads raised: {errors}"
        assert len(results) == 8, f"expected 8 results, got {len(results)}"
        assert sum(1 for r in results if r) == 1
        t = store.get_task(tid)
        assert t["status"] == "running"


class TestWorkerPid:
    def test_mark_running_leaves_worker_pid_null(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid)
        t = store.get_task(tid)
        assert t["worker_pid"] is None

    def test_set_worker_pid(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid)
        store.set_worker_pid(tid, 12345)
        t = store.get_task(tid)
        assert t["worker_pid"] == 12345

    def test_set_worker_pid_only_while_running(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        # status is ready, not running
        store.set_worker_pid(tid, 12345)
        t = store.get_task(tid)
        # should not update because status != running
        assert t["worker_pid"] is None


class TestTransitionAndHeartbeat:
    def test_transition_sets_fields(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.transition(tid, "failed", blocker_reason="x")
        t = store.get_task(tid)
        assert t["status"] == "failed"
        assert t["blocker_reason"] == "x"
        assert t["finished_at"] is not None

    def test_update_payload_locked_blocker_reason_set_clear_and_unchanged(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.transition(tid, "blocked", blocker_reason="keep me")

        with store._acquire_lock():
            conn = store.connect()
            store.init_db(conn)
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            payload = json.loads(row["payload"])
            payload["step"] = "unchanged"
            assert store._update_payload_locked(conn, tid, payload, status=row["status"])
            conn.commit()
            conn.close()
        assert store.get_task(tid)["blocker_reason"] == "keep me"

        with store._acquire_lock():
            conn = store.connect()
            store.init_db(conn)
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            payload = json.loads(row["payload"])
            payload["step"] = "cleared"
            assert store._update_payload_locked(
                conn,
                tid,
                payload,
                status=row["status"],
                blocker_reason=None,
            )
            conn.commit()
            conn.close()
        assert store.get_task(tid)["blocker_reason"] is None

        with store._acquire_lock():
            conn = store.connect()
            store.init_db(conn)
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            payload = json.loads(row["payload"])
            payload["step"] = "set"
            assert store._update_payload_locked(
                conn,
                tid,
                payload,
                status=row["status"],
                blocker_reason="new reason",
            )
            conn.commit()
            conn.close()
        assert store.get_task(tid)["blocker_reason"] == "new reason"

        with store._acquire_lock():
            conn = store.connect()
            store.init_db(conn)
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            payload = json.loads(row["payload"])
            with pytest.raises(ValueError, match="invalid blocker_reason sentinel"):
                store._update_payload_locked(
                    conn,
                    tid,
                    payload,
                    status=row["status"],
                    blocker_reason=store._BlockerReasonUnchanged(),
                )
            conn.close()

        class FakeBlockerReasonUnchanged(store._BlockerReasonUnchanged):
            pass

        with store._acquire_lock():
            conn = store.connect()
            store.init_db(conn)
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            payload = json.loads(row["payload"])
            with pytest.raises(ValueError, match="invalid blocker_reason sentinel"):
                store._update_payload_locked(
                    conn,
                    tid,
                    payload,
                    status=row["status"],
                    blocker_reason=FakeBlockerReasonUnchanged(),
                )
            conn.close()

    def test_normalize_blocker_reason_is_single_line_and_bounded(self, store):
        raw = " first line\nsecond\tline  " + ("x" * 220)

        normalized = store._normalize_blocker_reason(raw, limit=40)

        assert normalized == "first line second line xxxxxxxxxxxxxx..."
        assert len(normalized) == 40
        assert store._normalize_blocker_reason(raw, limit=0) == ""
        assert store._normalize_blocker_reason(raw, limit=2) == "fi"
        with pytest.raises(TypeError, match="blocker reason must be a string"):
            store._normalize_blocker_reason({"not": "a string"})

    def test_heartbeat_updates(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid)
        before = store.get_task(tid)["heartbeat_at"]
        time.sleep(0.05)
        store.heartbeat(tid)
        after = store.get_task(tid)["heartbeat_at"]
        assert after != before


class TestReclaimZombies:
    def test_reclaim_stale_heartbeat(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid)
        store.set_worker_pid(tid, 99999)
        # Manually age the heartbeat
        old = "2020-01-01T00:00:00+00:00"
        conn = store.connect()
        conn.execute("UPDATE tasks SET heartbeat_at=? WHERE id=?", (old, tid))
        conn.commit()
        conn.close()

        reclaimed = store.reclaim_zombies(timeout_s=300)
        assert tid in reclaimed
        t = store.get_task(tid)
        assert t["status"] == "ready"
        assert "zombie" in t["blocker_reason"]
        assert t["worker_pid"] is None
        assert t["started_at"] is None
        assert t["heartbeat_at"] is None

    def test_reclaim_both_null(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        # Insert directly with both heartbeat_at and started_at NULL
        conn = store.connect()
        conn.execute(
            "UPDATE tasks SET status='running', started_at=NULL, heartbeat_at=NULL, worker_pid=NULL WHERE id=?",
            (tid,),
        )
        conn.commit()
        conn.close()

        reclaimed = store.reclaim_zombies(timeout_s=300)
        assert tid in reclaimed
        t = store.get_task(tid)
        assert t["status"] == "ready"

    def test_no_reclaim_fresh_heartbeat(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid)
        store.heartbeat(tid)
        reclaimed = store.reclaim_zombies(timeout_s=300)
        assert tid not in reclaimed
        t = store.get_task(tid)
        assert t["status"] == "running"

    def test_reclaim_kills_alive_pid(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid)
        # Spawn a short-lived child in its own process group so killpg targets it
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        store.set_worker_pid(tid, child.pid)
        old = "2020-01-01T00:00:00+00:00"
        conn = store.connect()
        conn.execute("UPDATE tasks SET heartbeat_at=? WHERE id=?", (old, tid))
        conn.commit()
        conn.close()

        reclaimed = store.reclaim_zombies(timeout_s=300)
        assert tid in reclaimed
        # Child should be dead before flip
        assert child.poll() is not None


class TestAssigneeLock:
    def test_assignee_lock_held(self, store):
        tid1 = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid1)
        tid2 = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        holder = store.assignee_lock_held("k2bi", exclude_id=tid2)
        assert holder == tid1
        assert store.assignee_lock_held("k2bi") == tid1

    def test_assignee_lock_none(self, store):
        assert store.assignee_lock_held("k2bi") is None


class TestBlockedBy:
    def test_clear_blocked_by(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.set_blocked_by(tid, "holder-123")
        t = store.get_task(tid)
        assert t["blocked_by"] == "holder-123"
        store.clear_blocked_by(tid)
        t = store.get_task(tid)
        assert t["blocked_by"] is None

    def test_poll_once_clears_stale_blocked_by(self, store):
        tid1 = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_running(tid1)
        store.transition(tid1, "done")
        tid2 = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.set_blocked_by(tid2, tid1)
        # poll_once should clear blocked_by since tid1 is no longer running
        store.poll_once()
        t2 = store.get_task(tid2)
        # It may get blocked for other reasons (preflight), but blocked_by should be cleared
        # if the lock is not held. However preflight will likely block since k2bi workspace
        # doesn't exist in temp env. So we just verify blocked_by is None or updated.
        # The spec says clear_blocked_by is called when holder not held.
        # In our temp env, preflight fails so task goes blocked, but blocked_by should be cleared.
        assert t2["blocked_by"] is None


class TestRenderBoard:
    def test_render_board(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.render_board(store.BOARD_PATH)
        assert os.path.exists(store.BOARD_PATH)
        content = Path(store.BOARD_PATH).read_text()
        assert tid in content

    def test_render_board_suppresses_terminal_blocker_reason(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.set_blocked_by(tid, "holder-123")
        store.transition(tid, "terminal_deployed", blocker_reason="stale blocker")

        store.render_board(store.BOARD_PATH)

        content = Path(store.BOARD_PATH).read_text()
        assert "terminal_deployed" in content
        assert "stale blocker" not in content
        assert "blocked by holder-123" not in content


class TestNotify:
    def test_notify_calls_telegram_cmd(self, store, tmp_path):
        recorder = tmp_path / "recorder.sh"
        recorder.write_text("#!/bin/bash\necho \"$@\" >> \"$RECORDER_OUT\"\n")
        recorder.chmod(0o755)
        out = tmp_path / "out.txt"
        os.environ["RECORDER_OUT"] = str(out)
        os.environ["K2B_ORCH_TELEGRAM_CMD"] = str(recorder)
        store.notify("hello-test")
        assert "hello-test" in out.read_text()
        del os.environ["RECORDER_OUT"]


class TestWorkerLockStale:
    """H2: a SIGKILLed worker orphans its lock; preflight must self-heal."""

    def test_dead_pid_is_stale(self, temp_env, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()  # reap -> pid now dead
        lock = tmp_path / "wl.lock"
        lock.write_text(str(p.pid))
        assert profiles._worker_lock_is_stale(str(lock)) is True

    def test_live_pid_not_stale(self, temp_env, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        lock = tmp_path / "wl.lock"
        lock.write_text(str(os.getpid()))
        assert profiles._worker_lock_is_stale(str(lock)) is False

    def test_empty_lock_is_stale(self, temp_env, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        lock = tmp_path / "wl.lock"
        lock.write_text("")
        assert profiles._worker_lock_is_stale(str(lock)) is True

    def test_garbage_lock_is_stale(self, temp_env, tmp_path):
        from scripts.lib import orchestrator_profiles as profiles

        lock = tmp_path / "wl.lock"
        lock.write_text("not-a-pid")
        assert profiles._worker_lock_is_stale(str(lock)) is True


class TestCliReentrancyGuards:
    """H4: unblock/return must not re-ready a RUNNING task (re-entrancy)."""

    def _cli(self, store, *args):
        env = {
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "K2B_ORCH_DB": store.DB_PATH,
            "K2B_VAULT_PATH": store.K2B_VAULT,
        }
        return subprocess.run(
            [sys.executable, "-m", "scripts.lib.orchestrator_store", *args],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        )

    def _add(self, store):
        return store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )

    def test_unblock_refused_on_running(self, store):
        tid = self._add(store)
        store.mark_running(tid)
        r = self._cli(store, "unblock", tid)
        assert r.returncode != 0
        assert store.get_task(tid)["status"] == "running"

    def test_return_refused_on_running(self, store):
        tid = self._add(store)
        store.mark_running(tid)
        r = self._cli(store, "return", tid)
        assert r.returncode != 0
        assert store.get_task(tid)["status"] == "running"

    def test_unblock_allowed_on_blocked(self, store):
        tid = self._add(store)
        store.mark_blocked(tid, "preflight x")
        r = self._cli(store, "unblock", tid)
        assert r.returncode == 0, r.stderr
        assert store.get_task(tid)["status"] == "ready"

    def test_return_refused_on_zombie(self, store):
        # A zombie is parked because death is UNCONFIRMED; returning it would
        # bypass the no-re-ready safety property.
        tid = self._add(store)
        store.transition(tid, "zombie", blocker_reason="unconfirmed death")
        r = self._cli(store, "return", tid)
        assert r.returncode != 0
        assert store.get_task(tid)["status"] == "zombie"

    def test_complete_refused_on_zombie(self, store):
        tid = self._add(store)
        store.transition(tid, "zombie", blocker_reason="unconfirmed death")
        r = self._cli(store, "complete", tid)
        assert r.returncode != 0
        assert store.get_task(tid)["status"] == "zombie"

    def test_complete_refused_on_running(self, store):
        tid = self._add(store)
        store.mark_running(tid)
        r = self._cli(store, "complete", tid)
        assert r.returncode != 0
        assert store.get_task(tid)["status"] == "running"

    def test_cancel_refused_on_null_pid_zombie(self, store):
        # A running/zombie row with NO registered PID is in the spawn window
        # (worker may be alive but unregistered); cancel must refuse, not falsely
        # confirm death and release the lock.
        tid = self._add(store)
        store.transition(tid, "zombie", blocker_reason="unconfirmed death")  # worker_pid NULL
        r = self._cli(store, "cancel", tid)
        assert r.returncode != 0
        assert store.get_task(tid)["status"] == "zombie"

    def test_cancel_zombie_releases_lock_when_death_confirmed(self, store):
        # A dead worker_pid: _kill_group_and_confirm confirms death, so cancel
        # may proceed and release the assignee lock.
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()  # reap -> pid now dead
        tid = self._add(store)
        store.transition(tid, "zombie", worker_pid=p.pid, blocker_reason="x")
        r = self._cli(store, "cancel", tid)
        assert r.returncode == 0, r.stderr
        assert store.get_task(tid)["status"] == "cancelled"
        assert store.assignee_lock_held("k2bi") is None

    def test_set_worker_pid_false_when_not_running(self, store):
        tid = self._add(store)
        store.mark_running(tid)
        store.transition(tid, "cancelled")
        assert store.set_worker_pid(tid, 12345) is False

    def test_set_worker_pid_true_when_running(self, store):
        tid = self._add(store)
        store.mark_running(tid)
        assert store.set_worker_pid(tid, 12345) is True

    def test_cas_cancel_guards_status_and_pid(self, store):
        tid = self._add(store)
        store.transition(tid, "zombie", worker_pid=111)
        assert store.cas_cancel(tid, "zombie", 999) is False   # pid mismatch
        assert store.cas_cancel(tid, "running", 111) is False  # status mismatch
        assert store.cas_cancel(tid, "zombie", 111) is True    # match
        assert store.get_task(tid)["status"] == "cancelled"


class TestZombieHoldsAssigneeLock:
    """A zombie (unconfirmed-dead) task must block same-assignee dispatch."""

    def _add(self, store):
        return store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )

    def test_assignee_lock_held_by_zombie(self, store):
        z = self._add(store)
        store.transition(z, "zombie", blocker_reason="unconfirmed death")
        assert store.assignee_lock_held("k2bi") == z

    def test_poll_once_does_not_spawn_behind_zombie(self, store):
        z = self._add(store)
        store.transition(z, "zombie", blocker_reason="unconfirmed death")
        ready = self._add(store)
        result = store.poll_once()
        assert result["spawned"] is None
        t = store.get_task(ready)
        assert t["status"] == "ready"
        assert t["blocked_by"] == z


class TestWorkerTimeout:
    """A command that runs past the timeout must mark the task failed, never
    leave it stuck 'running' (which would invite a hidden reclaim/retry)."""

    def test_timeout_marks_failed(self, store, tmp_path, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles
        from scripts.lib import orchestrator_worker as worker

        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(ws))
        monkeypatch.setenv("K2B_ORCH_CMD_TIMEOUT", "1")
        worker_lock = "/tmp/k2b-orch-k2bi-worker.lock"
        if os.path.exists(worker_lock):
            os.unlink(worker_lock)
        # Command writes output, then sleeps well past the 1s timeout.
        monkeypatch.setattr(
            profiles, "resolve_command",
            lambda p, k, payload=None: [sys.executable, "-c",
                          "import sys,time; sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(8)"],
        )
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command",
        )
        store.mark_running(tid)
        worker.main(tid)
        t = store.get_task(tid)
        assert t["status"] == "failed", t["status"]
        assert "timed out" in (t["blocker_reason"] or "")
        assert not os.path.exists(worker_lock)  # released even on timeout
