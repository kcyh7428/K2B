#!/usr/bin/env python3
"""pytest unit tests for orchestrator return gate, one-flight lock, and TTL sweep."""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
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


class TestAddTaskStatusAndLock:
    def test_add_task_default_status_ready(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        t = store.get_task(tid)
        assert t["status"] == "ready"

    def test_add_task_with_status(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="open-source AI",
        )
        t = store.get_task(tid)
        assert t["status"] == "waiting_for_kimi_output"
        assert t["entity_key"] == "open-source AI"

    def test_flight_lock_blocks_duplicate_entity_key(self, store):
        store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="open-source AI",
        )
        with pytest.raises(store.FlightLockError) as exc_info:
            store.add_task(
                assignee_profile="k2bi",
                command_key="test-echo-readonly",
                success_criteria="ok",
                permissions="analyst-command",
                status="waiting_for_kimi_output",
                entity_key="open-source AI",
            )
        assert "flight already active for 'open-source AI'" in str(exc_info.value)

    def test_flight_lock_case_insensitive(self, store):
        store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="Open-Source AI",
        )
        with pytest.raises(store.FlightLockError):
            store.add_task(
                assignee_profile="k2bi",
                command_key="test-echo-readonly",
                success_criteria="ok",
                permissions="analyst-command",
                status="waiting_for_kimi_output",
                entity_key="  open-source ai ",
            )

    def test_flight_lock_allows_after_terminal(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="open-source AI",
        )
        store.transition(tid, "cancelled")
        tid2 = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="open-source AI",
        )
        assert store.get_task(tid2)["status"] == "waiting_for_kimi_output"

    def test_flight_lock_exempts_empty_entity_key(self, store):
        store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="",
        )
        store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="",
        )
        # No exception means both were created
        tasks = store.list_tasks()
        assert len(tasks) == 2

    def test_poll_once_does_not_spawn_parked(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="open-source AI",
        )
        result = store.poll_once()
        assert result["spawned"] is None
        t = store.get_task(tid)
        assert t["status"] == "waiting_for_kimi_output"


class TestReturnAcceptanceGate:
    def _good_content(self, tid):
        # Must end with the task-bound completion sentinel (the gate's primary
        # non-truncation + paste-to-flight proof).
        lines = [
            "This is a comprehensive research report on open-source AI. It contains detailed analysis.",
            "Here is a second substantive line with plenty of content to meet the threshold requirements.",
            "Third line: the landscape of open-source AI has shifted dramatically in recent months.",
            "Fourth line: companies like Meta and Google have released powerful models under permissive licenses.",
            "Fifth line: the implications for enterprise adoption are significant and far-reaching.",
        ]
        urls = [
            "https://example.com/source1",
            "https://example.org/source2",
            "http://example.net/source3",
        ]
        body = "\n".join(lines + urls) + "\nThe conclusion wraps up the analysis with a firm statement."
        return body + f"\n=== END OF KIMI RESEARCH: {tid} ==="

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

    def test_return_blocked_to_ready(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
        )
        store.mark_blocked(tid, "preflight x")
        r = self._cli(store, "return", tid, "--text", "some text")
        assert r.returncode == 0, r.stderr
        assert store.get_task(tid)["status"] == "ready"

    def test_return_waiting_for_kimi_output_pass(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        content = self._good_content(tid)
        r = self._cli(store, "return", tid, "--text", content)
        assert r.returncode == 0, r.stderr
        t = store.get_task(tid)
        assert t["status"] == "returned"
        payload = json.loads(t["payload"] or "{}")
        assert payload["return_bytes"] == len(content.encode("utf-8"))
        assert "return_sha256" in payload
        assert payload["return_path"].endswith(f"{tid}-kimi-raw.md")
        assert "returned_at" in payload
        assert os.path.exists(payload["return_path"])

    def test_return_waiting_for_kimi_output_too_small(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        r = self._cli(store, "return", tid, "--text", "x" * 10)
        assert r.returncode == 1
        assert "rejected: size 10 bytes outside [500, 2000000]" in r.stderr

    def test_return_waiting_for_kimi_output_too_large(self, store, tmp_path):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        big_path = tmp_path / "big.md"
        big_path.write_text("x" * 2_000_001, encoding="utf-8")
        r = self._cli(store, "return", tid, "--path", str(big_path))
        assert r.returncode == 1
        assert "rejected: size 2000001 bytes outside [500, 2000000]" in r.stderr

    def test_return_waiting_for_kimi_output_few_urls(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        content = "A" * 600 + "\nhttps://example.com\n"
        r = self._cli(store, "return", tid, "--text", content)
        assert r.returncode == 1
        assert "rejected: fewer than 3 source URLs (found 1)" in r.stderr

    def test_return_waiting_for_kimi_output_few_lines(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        lines = [
            "Short line one with enough characters to be considered substantive indeed.",
            "Short line two with enough characters to be considered substantive indeed.",
            "Short line three with enough characters to be considered substantive indeed.",
        ]
        urls = [
            "https://a.com/1",
            "https://b.com/2",
            "https://c.com/3",
        ]
        # Pad to meet size requirement with short lines (<=40 chars) so they don't count
        padding = "\n".join(["xx"] * 250)
        content = "\n".join(lines + urls) + "\n" + padding + "."
        r = self._cli(store, "return", tid, "--text", content)
        assert r.returncode == 1
        assert "rejected: fewer than 5 substantive lines (found 3)" in r.stderr

    def test_return_waiting_for_kimi_output_truncated_last_line(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        lines = [
            "This is a comprehensive research report on open-source AI with detailed analysis.",
            "Here is a second substantive line with plenty of content to meet the threshold.",
            "Third line: the landscape of open-source AI has shifted dramatically recently.",
            "Fourth line: companies like Meta and Google have released powerful models.",
            "Fifth line: the implications for enterprise adoption are significant",
        ]
        urls = [
            "https://example.com/source1",
            "https://example.org/source2",
            "http://example.net/source3",
        ]
        # Add padding to meet size requirement; last line still truncated
        content = "\n".join(lines + urls) + "\n" + "X" * 500
        r = self._cli(store, "return", tid, "--text", content)
        assert r.returncode == 1
        # No completion sentinel -> rejected as truncated/not-from-this-flight.
        assert "missing completion sentinel" in r.stderr

    def test_return_waiting_for_kimi_output_truncated_dangling_http(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        lines = [
            "This is a comprehensive research report on open-source AI with detailed analysis.",
            "Here is a second substantive line with plenty of content to meet the threshold.",
            "Third line: the landscape of open-source AI has shifted dramatically recently.",
            "Fourth line: companies like Meta and Google have released powerful models.",
            "Fifth line: the implications for enterprise adoption are significant and far-reaching.",
        ]
        urls = [
            "https://example.com/source1",
            "https://example.org/source2",
            "http://example.net/source3",
        ]
        # Append a dangling incomplete URL at the very end with no trailing whitespace
        content = "\n".join(lines + urls) + "\nhttps://incomplete"
        r = self._cli(store, "return", tid, "--text", content)
        assert r.returncode == 1
        # No completion sentinel -> rejected as truncated/not-from-this-flight.
        assert "missing completion sentinel" in r.stderr

    def test_return_waiting_for_kimi_output_truncated_unclosed_bracket(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        lines = [
            "This is a comprehensive research report on open-source AI with detailed analysis.",
            "Here is a second substantive line with plenty of content to meet the threshold.",
            "Third line: the landscape of open-source AI has shifted dramatically recently.",
            "Fourth line: companies like Meta and Google have released powerful models.",
            "Fifth line: the implications for enterprise adoption are significant and far-reaching.",
        ]
        urls = [
            "https://example.com/source1",
            "https://example.org/source2",
            "http://example.net/source3",
        ]
        content = "\n".join(lines + urls) + "\n[unclosed link"
        r = self._cli(store, "return", tid, "--text", content)
        assert r.returncode == 1
        # No completion sentinel -> rejected as truncated/not-from-this-flight.
        assert "missing completion sentinel" in r.stderr

    def test_return_already_returned(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        content = self._good_content(tid)
        r1 = self._cli(store, "return", tid, "--text", content)
        assert r1.returncode == 0
        r2 = self._cli(store, "return", tid, "--text", content)
        assert r2.returncode == 1
        assert f"rejected: already returned (task {tid})" in r2.stderr

    def test_return_needs_human_to_ready(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="needs_human",
            entity_key="AI",
        )
        r = self._cli(store, "return", tid, "--text", "answer")
        assert r.returncode == 0
        assert store.get_task(tid)["status"] == "ready"

    def test_return_complete_from_returned(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        content = self._good_content(tid)
        r = self._cli(store, "return", tid, "--text", content)
        assert r.returncode == 0
        r2 = self._cli(store, "complete", tid)
        assert r2.returncode == 0
        assert store.get_task(tid)["status"] == "done"

    def test_return_stores_file(self, store, tmp_path):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        path = tmp_path / "kimi_output.md"
        content = self._good_content(tid)
        path.write_text(content, encoding="utf-8")
        r = self._cli(store, "return", tid, "--path", str(path))
        assert r.returncode == 0
        t = store.get_task(tid)
        payload = json.loads(t["payload"] or "{}")
        assert os.path.exists(payload["return_path"])
        assert Path(payload["return_path"]).read_text(encoding="utf-8") == content

    # --- Codex round-1 hardening: sentinel, CAS, complete-bypass, status validation ---

    def test_return_wrong_task_sentinel_rejected(self, store):
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command",
            status="waiting_for_kimi_output", entity_key="AI",
        )
        # Otherwise-valid content, but the sentinel names a DIFFERENT task id ->
        # paste-into-wrong-flight must be rejected.
        content = self._good_content("SOME-OTHER-TASK-999")
        r = self._cli(store, "return", tid, "--text", content)
        assert r.returncode == 1
        assert "missing completion sentinel" in r.stderr
        assert store.get_task(tid)["status"] == "waiting_for_kimi_output"

    def test_return_unbalanced_brackets_in_tail_rejected(self, store):
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command",
            status="waiting_for_kimi_output", entity_key="AI",
        )
        lines = [
            "This is a comprehensive research report on open-source AI with detailed analysis here.",
            "Here is a second substantive line with plenty of content to meet the threshold easily.",
            "Third line: the landscape of open-source AI has shifted dramatically over recent months.",
            "Fourth line: companies like Meta and Google have released powerful permissive models now.",
            "Fifth line: the implications for enterprise adoption are significant and quite far-reaching.",
        ]
        urls = ["https://a.com/1", "https://b.com/2", "https://c.com/3"]
        # Valid sentinel as last line, but an unclosed '[' within the last 200 chars.
        content = (
            "\n".join(lines + urls)
            + "\n[an unclosed reference near the very end of the report body\n"
            + f"=== END OF KIMI RESEARCH: {tid} ==="
        )
        r = self._cli(store, "return", tid, "--text", content)
        assert r.returncode == 1
        assert "unbalanced brackets" in r.stderr

    def test_complete_rejects_waiting_for_kimi_output(self, store):
        # complete must NOT bypass the return gate / release the lock.
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command",
            status="waiting_for_kimi_output", entity_key="AI",
        )
        r = self._cli(store, "complete", tid)
        assert r.returncode == 1
        assert "refusing to complete a 'waiting_for_kimi_output' task" in r.stderr
        assert store.get_task(tid)["status"] == "waiting_for_kimi_output"

    def test_complete_rejects_needs_human(self, store):
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command",
            status="needs_human", entity_key="AI",
        )
        r = self._cli(store, "complete", tid)
        assert r.returncode == 1
        assert "refusing to complete a 'needs_human' task" in r.stderr

    def test_add_task_invalid_status_raises(self, store):
        with pytest.raises(ValueError):
            store.add_task(
                assignee_profile="k2bi", command_key="test-echo-readonly",
                success_criteria="ok", permissions="analyst-command",
                status="waiting_for_kimi_ouput",  # typo
            )

    def test_add_task_terminal_initial_status_raises(self, store):
        with pytest.raises(ValueError):
            store.add_task(
                assignee_profile="k2bi", command_key="test-echo-readonly",
                success_criteria="ok", permissions="analyst-command",
                status="done",
            )

    def test_cli_add_invalid_status_exit1(self, store):
        r = self._cli(
            store, "add", "--profile", "k2bi", "--command-key", "test-echo-readonly",
            "--success", "ok", "--status", "running",
        )
        assert r.returncode == 1
        assert "invalid initial status" in r.stderr

    def test_finalize_return_lose_writes_nothing(self, store):
        # When the flight is gone (cancelled) before the claim, the locked recheck
        # makes _finalize_return write NOTHING -- no orphan file, no overwrite --
        # and the DB stays consistent (Codex round-2 + round-3).
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command",
            status="waiting_for_kimi_output", entity_key="AI",
        )
        store.transition(tid, "cancelled")
        return_path = os.path.join(store.RESULTS_DIR, f"{tid}-kimi-raw.md")
        assert store._finalize_return(tid, "raw evidence body", "{}", return_path) is False
        assert not os.path.exists(return_path)  # nothing written
        assert store.get_task(tid)["status"] == "cancelled"

    def test_finalize_return_loser_does_not_overwrite_winner(self, store):
        # Codex round-3: a duplicate/concurrent return loser must not overwrite the
        # winner's raw evidence. The lock serializes concurrent returns into exactly
        # this order; the second finds the flight no longer waiting and writes nothing,
        # so the on-disk content still matches the winner's recorded sha256.
        import hashlib
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command",
            status="waiting_for_kimi_output", entity_key="AI",
        )
        return_path = os.path.join(store.RESULTS_DIR, f"{tid}-kimi-raw.md")
        win = "WINNER content A"
        win_sha = hashlib.sha256(win.encode()).hexdigest()
        assert store._finalize_return(tid, win, json.dumps({"return_sha256": win_sha}), return_path) is True
        lose = "LOSER content B (different)"
        lose_sha = hashlib.sha256(lose.encode()).hexdigest()
        assert store._finalize_return(tid, lose, json.dumps({"return_sha256": lose_sha}), return_path) is False
        on_disk = Path(return_path).read_text(encoding="utf-8")
        assert on_disk == win  # winner's file untouched by the loser
        t = store.get_task(tid)
        assert t["status"] == "returned"
        assert json.loads(t["payload"])["return_sha256"] == win_sha  # sha matches file

    def test_finalize_return_success_publishes_and_claims(self, store):
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command",
            status="waiting_for_kimi_output", entity_key="AI",
        )
        return_path = os.path.join(store.RESULTS_DIR, f"{tid}-kimi-raw.md")
        claimed = store._finalize_return(tid, "raw evidence body", json.dumps({"k": "v"}), return_path)
        assert claimed is True
        assert os.path.exists(return_path)
        t = store.get_task(tid)
        assert t["status"] == "returned"
        assert json.loads(t["payload"])["k"] == "v"

    def test_cas_to_ready_loses_when_not_in_expected_status(self, store):
        # Codex round-4: needs_human/blocked -> ready must not resurrect a flight
        # cancelled/TTL-expired after a stale read. cas_to_ready only flips a row
        # STILL in the expected status.
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command",
            status="needs_human", entity_key="AI",
        )
        store.transition(tid, "cancelled")
        assert store.cas_to_ready(tid, "needs_human", "{}") is False
        assert store.get_task(tid)["status"] == "cancelled"

    def test_cas_to_ready_wins_when_in_expected_status(self, store):
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command",
            status="needs_human", entity_key="AI",
        )
        assert store.cas_to_ready(tid, "needs_human", json.dumps({"a": 1})) is True
        t = store.get_task(tid)
        assert t["status"] == "ready"
        assert json.loads(t["payload"])["a"] == 1

    def test_unblock_cas_loses_when_cancelled(self, store):
        # Codex round-5: unblock (blocked -> ready) must not resurrect a
        # concurrently cancelled task.
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command", entity_key="AI",
        )
        store.transition(tid, "blocked", blocker_reason="x")
        store.transition(tid, "cancelled")
        assert store.cas_to_ready(tid, "blocked") is False
        assert store.get_task(tid)["status"] == "cancelled"

    def test_unblock_cas_clears_blocker_reason(self, store):
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command", entity_key="AI",
        )
        store.transition(tid, "blocked", blocker_reason="some reason")
        assert store.cas_to_ready(tid, "blocked") is True
        t = store.get_task(tid)
        assert t["status"] == "ready"
        assert t["blocker_reason"] is None

    def test_complete_rejects_cancelled_task(self, store):
        # Codex round-5 class: complete must not resurrect a terminal task to 'done'.
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command", entity_key="AI",
        )
        store.transition(tid, "cancelled")
        r = self._cli(store, "complete", tid)
        assert r.returncode == 1
        assert "no longer in a completable state" in r.stderr
        assert store.get_task(tid)["status"] == "cancelled"


class TestTtlSweep:
    def test_poll_once_cancels_old_waiting_for_kimi_output(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        # Age the task to 15 days old
        old = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=15)).isoformat()
        conn = store.connect()
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, tid))
        conn.commit()
        conn.close()

        result = store.poll_once()
        assert tid in result["ttl_expired"]
        t = store.get_task(tid)
        assert t["status"] == "cancelled"
        assert t["blocker_reason"] == "ttl-expired"

    def test_poll_once_does_not_cancel_fresh_waiting_for_kimi_output(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        # Age to 13 days (just under default 14)
        old = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=13)).isoformat()
        conn = store.connect()
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, tid))
        conn.commit()
        conn.close()

        result = store.poll_once()
        assert tid not in result["ttl_expired"]
        t = store.get_task(tid)
        assert t["status"] == "waiting_for_kimi_output"

    def test_poll_once_cancels_old_needs_human(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="needs_human",
            entity_key="AI",
        )
        old = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=8)).isoformat()
        conn = store.connect()
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, tid))
        conn.commit()
        conn.close()

        result = store.poll_once()
        assert tid in result["ttl_expired"]
        t = store.get_task(tid)
        assert t["status"] == "cancelled"
        assert t["blocker_reason"] == "ttl-expired"

    def test_poll_once_does_not_cancel_fresh_needs_human(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="needs_human",
            entity_key="AI",
        )
        old = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=6)).isoformat()
        conn = store.connect()
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, tid))
        conn.commit()
        conn.close()

        result = store.poll_once()
        assert tid not in result["ttl_expired"]
        t = store.get_task(tid)
        assert t["status"] == "needs_human"

    def test_poll_once_does_not_touch_returned(self, store):
        tid = store.add_task(
            assignee_profile="k2bi",
            command_key="test-echo-readonly",
            success_criteria="ok",
            permissions="analyst-command",
            status="waiting_for_kimi_output",
            entity_key="AI",
        )
        content = "A" * 600 + "\n" + "\n".join([
            "https://a.com/1", "https://b.com/2", "https://c.com/3",
        ]) + "\n" + "\n".join([
            "Line one with more than forty characters for substance requirements.",
            "Line two with more than forty characters for substance requirements.",
            "Line three with more than forty characters for substance requirements.",
            "Line four with more than forty characters for substance requirements.",
            "Line five with more than forty characters for substance requirements.",
        ]) + f"\n=== END OF KIMI RESEARCH: {tid} ==="
        env = {
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "K2B_ORCH_DB": store.DB_PATH,
            "K2B_VAULT_PATH": store.K2B_VAULT,
        }
        r = subprocess.run(
            [sys.executable, "-m", "scripts.lib.orchestrator_store", "return", tid, "--text", content],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr

        old = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=20)).isoformat()
        conn = store.connect()
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (old, tid))
        conn.commit()
        conn.close()

        result = store.poll_once()
        assert tid not in result["ttl_expired"]
        t = store.get_task(tid)
        assert t["status"] == "returned"

    def test_ttl_sweep_naive_timestamp_no_crash(self, store):
        # A NAIVE (offset-less) updated_at must not raise TypeError and abort the
        # whole poll cycle; it is normalized to UTC and swept if old enough.
        tid = store.add_task(
            assignee_profile="k2bi", command_key="test-echo-readonly",
            success_criteria="ok", permissions="analyst-command",
            status="waiting_for_kimi_output", entity_key="AI",
        )
        naive_old = (
            datetime.now(timezone.utc).replace(tzinfo=None)
            - __import__("datetime").timedelta(days=20)
        ).isoformat()
        assert "+" not in naive_old and not naive_old.endswith("Z")  # confirm naive
        conn = store.connect()
        conn.execute("UPDATE tasks SET updated_at=? WHERE id=?", (naive_old, tid))
        conn.commit()
        conn.close()
        result = store.poll_once()  # must not raise
        assert tid in result["ttl_expired"]
        assert store.get_task(tid)["status"] == "cancelled"
