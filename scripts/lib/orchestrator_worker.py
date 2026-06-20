#!/usr/bin/env python3
"""Worker runner: heartbeat, allowlisted-command run, artifact, Telegram."""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHIP_COMMAND_KEY = "k2bi-run-full-ship"
# Capital commands BEYOND the ship key that share the longer ship timeout. The
# ship key is intentionally NOT listed here: it is matched directly against the
# (runtime-patchable) SHIP_COMMAND_KEY constant in the timeout branch, so a
# rename/patch of the ship key still routes correctly. Keeping it out of this
# frozenset avoids the redundant double-listing.
CAPITAL_COMMAND_KEYS = frozenset({"k2bi-apply-limits", "k2bi-deploy-to-vps"})
DEFAULT_CMD_TIMEOUT_S = 540
DEFAULT_SHIP_CMD_TIMEOUT_S = 1200

# Import after PYTHONPATH is set by dispatcher
from scripts.lib import orchestrator_profiles as profiles
from scripts.lib import orchestrator_store as store


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_theme_path(stdout: str) -> str | None:
    """Extract the theme file path from pipeline stdout."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line:
            return line
    return None


def _parse_candidate_count(path: str) -> int | None:
    """Parse candidate-count from theme frontmatter, FAILING CLOSED.

    Returns an int ONLY when the file opens with a well-formed YAML
    frontmatter block (--- ... ---) that parses to a dict carrying an integer
    candidate-count. Any malformation -- bad/absent delimiters, invalid YAML,
    non-dict frontmatter, or a missing/non-int count -- returns None so the
    caller marks the run failed rather than done. A naive line scan would
    return an int from a garbled file that merely contains a candidate-count:
    line, which is exactly the fail-OPEN bug this guards against.
    """
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None
    lines = text.splitlines()
    # Opening fence must be the very first line.
    if not lines or lines[0].strip() != "---":
        return None
    # Find the closing fence.
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        return None
    fm_text = "\n".join(lines[1:close_idx])
    try:
        import yaml
    except ImportError:
        return None  # cannot validate -> fail closed
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    count = data.get("candidate-count")
    # bool is a subclass of int; exclude it explicitly.
    if isinstance(count, bool) or not isinstance(count, int):
        return None
    return count


def _parse_timeout_env(env_name: str, default_s: int) -> int:
    raw = os.environ.get(env_name)
    if raw is None:
        return default_s
    try:
        timeout_s = int(raw)
    except (TypeError, ValueError):
        print(
            f"Worker: invalid {env_name}={raw!r}; using default {default_s}s",
            file=sys.stderr,
        )
        return default_s
    if timeout_s <= 0:
        print(
            f"Worker: invalid {env_name}={raw!r}; timeout must be > 0; "
            f"using default {default_s}s",
            file=sys.stderr,
        )
        return default_s
    return timeout_s


def main(task_id):
    # 1. Validate task state
    task = store.get_task(task_id)
    if task is None or task.get("status") != "running":
        print(f"Worker: task {task_id} not found or not running; exiting", file=sys.stderr)
        return 0

    command_key = task.get("command_key", "")

    # 2. Resolve command (with payload for parameterized keys)
    payload = None
    raw_payload = task.get("payload")
    if raw_payload:
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            payload = None
    argv = profiles.resolve_command(task["assignee_profile"], command_key, payload)
    if argv is None:
        reason = f"command_key not allowlisted: {task.get('command_key', '')}"
        store.transition(task_id, "failed", blocker_reason=reason)
        store.notify(f"[orchestrator] Task {task_id} FAILED. {reason}")
        return 1
    if command_key == "k2bi-deploy-to-vps":
        ok, reason = profiles.validate_a5_deploy_payload(
            payload if isinstance(payload, dict) else {}
        )
        if not ok:
            reason = f"A5 deploy worker token recheck failed: {reason}"
            store.transition(task_id, "failed", blocker_reason=reason)
            store.notify(f"[orchestrator] Task {task_id} FAILED. {reason}")
            return 1

    # 3. Resolve trusted workspace and record own PID. If the task is no longer
    # 'running' at registration time (cancelled or reclaimed out from under us
    # in the spawn window), BAIL before running the command -- another dispatch
    # may own this entity now.
    workspace = profiles.resolve_workspace(task["assignee_profile"])
    if not store.set_worker_pid(task_id, os.getpid()):
        print(
            f"Worker: task {task_id} no longer 'running' at PID registration; "
            f"exiting before command",
            file=sys.stderr,
        )
        return 0
    store.heartbeat(task_id)

    # 4. Acquire worker lock for the whole run
    profile = profiles.get_profile(task["assignee_profile"])
    worker_lock = profile.get("worker_lock") if profile else None
    lock_fd = None
    if worker_lock:
        try:
            lock_fd = os.open(worker_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(lock_fd, str(os.getpid()).encode())
        except FileExistsError:
            reason = "K2Bi worker lock already held"
            store.transition(task_id, "blocked", blocker_reason=reason)
            store.notify(f"[orchestrator] Task {task_id} BLOCKED. {reason}")
            return 1

    try:
        # 5. Start heartbeat daemon thread (crash-resilient)
        stop_event = threading.Event()
        last_beat_ok = [time.time()]

        def _beat():
            while not stop_event.wait(60):
                try:
                    store.heartbeat(task_id)
                    last_beat_ok[0] = time.time()
                except Exception as e:
                    sys.stderr.write(f"heartbeat failed for {task_id}: {e}\n")

        beat_thread = threading.Thread(target=_beat, daemon=True)
        beat_thread.start()

        # 6. Assert trusted cwd and run command
        if os.path.realpath(workspace) != os.path.realpath(
            profiles.resolve_workspace(task["assignee_profile"])
        ):
            reason = "workspace trust assertion failed"
            store.transition(task_id, "failed", blocker_reason=reason)
            store.notify(f"[orchestrator] Task {task_id} FAILED. {reason}")
            return 1
        if command_key == "k2bi-deploy-to-vps":
            ok, reason = profiles.validate_a5_deploy_script_ready()
            if not ok:
                reason = f"A5 deploy worker script recheck failed: {reason}"
                store.transition(task_id, "failed", blocker_reason=reason)
                store.notify(f"[orchestrator] Task {task_id} FAILED. {reason}")
                return 1

        # The command runs in THIS worker's process group (no start_new_session),
        # which is deliberate: the dispatcher's zombie reclaim kills the worker's
        # whole group, so a hung worker's command AND any descendants die in one
        # killpg. Giving the command its own session would let it survive reclaim
        # and cause the double-dispatch we are guarding against.
        #
        # On a worker-local timeout, subprocess.run SIGKILLs and reaps the direct
        # child. Ship 1a's command allowlist is leaf-only (invest_screen --enrich,
        # /bin/echo) -- neither forks a descendant -- so killing the direct child
        # is complete. When Ship 1b adds commands that spawn children, the command
        # must move to a tracked command-group that both this timeout path and
        # reclaim kill explicitly (tracked as a Ship 1b hardening item).
        # `== SHIP_COMMAND_KEY` re-reads the module constant at runtime so a
        # rename/patch of the ship key still routes to the capital timeout
        # (refactor-safety asserted by test_ship_timeout_branch_uses_module_ship_key_constant);
        # CAPITAL_COMMAND_KEYS carries the additional A4 apply-limits capital key.
        if command_key == SHIP_COMMAND_KEY or command_key in CAPITAL_COMMAND_KEYS:
            timeout_s = _parse_timeout_env(
                "K2B_ORCH_SHIP_CMD_TIMEOUT",
                DEFAULT_SHIP_CMD_TIMEOUT_S,
            )
        else:
            timeout_s = _parse_timeout_env("K2B_ORCH_CMD_TIMEOUT", DEFAULT_CMD_TIMEOUT_S)

        # A1 -- force K2BI_VAULT_ROOT alignment for narrative lane
        child_env = {**os.environ}
        if command_key == "k2bi-narrative":
            child_env["K2BI_VAULT_ROOT"] = profiles.k2bi_vault()

        try:
            result = subprocess.run(
                argv,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                env=child_env,
                shell=False,
            )
            returncode = result.returncode
            stdout = result.stdout or ""
            stderr = result.stderr or ""
        except subprocess.TimeoutExpired as te:
            returncode = -1
            stdout = te.stdout or ""
            stderr = te.stderr or ""
            if not stderr:
                stderr = f"command timed out (>{timeout_s}s); direct child killed"

        # On TimeoutExpired, captured output can come back as BYTES even though
        # text=True; normalize to str so the artifact's `.encode('utf-8')` calls
        # below cannot raise AttributeError and skip the failed-transition,
        # which would leave the task stuck 'running'.
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")

        # 7. Write result artifact
        result_slug = profile.get("result_slug", "result") if profile else "result"
        if command_key == "k2bi-narrative":
            result_slug = "k2bi-narrative"
        artifact_dir = store.RESULTS_DIR
        os.makedirs(artifact_dir, exist_ok=True)
        artifact_path = f"{artifact_dir}/{task_id}-{result_slug}.md"
        started = task.get("started_at") or _now_iso()
        finished = _now_iso()
        artifact_body = (
            f"---\n"
            f"task: {task_id}\n"
            f"assignee: {task['assignee_profile']}\n"
            f"command_key: {task['command_key']}\n"
            f"cwd: {workspace}\n"
            f"exit_code: {returncode}\n"
            f"started: {started}\n"
            f"finished: {finished}\n"
            f"stdout_bytes: {len(stdout.encode('utf-8'))}\n"
            f"stderr_bytes: {len(stderr.encode('utf-8'))}\n"
            f"type: orchestrator-result\n"
            f"---\n\n"
            f"## stdout\n\n"
            f"```\n{stdout}\n```\n\n"
            f"## stderr\n\n"
            f"```\n{stderr}\n```"
        )
        tmp = f"{artifact_path}.tmp.{os.getpid()}"
        with open(tmp, "w") as f:
            f.write(artifact_body)
        os.replace(tmp, artifact_path)

        # 8. Stop heartbeat
        stop_event.set()
        beat_thread.join(timeout=2)

        # 9. Decide terminal status
        current = store.get_task(task_id)
        if current is None or current.get("status") != "running":
            final_status = current.get("status") if current else "failed"
        else:
            if returncode == 0:
                if time.time() - last_beat_ok[0] < 300:
                    final_status = "done"
                    final_reason = None
                else:
                    final_status = "failed"
                    final_reason = (
                        "completed but heartbeat went stale; treated as failed to avoid double-dispatch"
                    )

                # Narrative post-run: verify theme file candidate-count >= 5.
                # The pipeline self-enforces >=4 sub-themes, >=5 candidates, and
                # >=1 2nd/3rd-order beneficiary on its successful-exit path; we
                # trust the 2nd/3rd rail to K2Bi and gate only on candidate-count.
                if command_key == "k2bi-narrative" and final_status == "done":
                    theme_path = _extract_theme_path(stdout)
                    count = None
                    if theme_path and os.path.exists(theme_path):
                        count = _parse_candidate_count(theme_path)
                    if not isinstance(count, int) or count < 5:
                        final_status = "failed"
                        final_reason = "theme file malformed or under-count"
            else:
                final_status = "failed"
                final_reason = stderr[:200] if stderr else f"exit code {returncode}"
            store.transition(task_id, final_status, blocker_reason=final_reason, result_url=artifact_path)

        # 10. Render board
        store.render_board()

        # 11. Notify exactly once, label from final_status
        store.notify(
            f"[orchestrator] Task {task_id} {final_status.upper()}. Result: {artifact_path}"
        )

    finally:
        # 12. Release worker lock
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
            try:
                os.unlink(worker_lock)
            except OSError:
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
