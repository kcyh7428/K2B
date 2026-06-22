#!/usr/bin/env python3
import argparse
import contextlib
import fcntl
import json
import os
from datetime import datetime, timezone


@contextlib.contextmanager
def file_lock(lock_path: str):
    """Exclusive flock on a sidecar lock file.

    Blocks (does not skip) when another process holds the lock so concurrent
    apply_actions calls serialize their read-modify-write of the partition
    queue instead of racing. fcntl.flock works on macOS where bash flock(1)
    does not ship by default.
    """
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def human_duration(start: str, end: str) -> str:
    seconds = max(0, int((parse_ts(end) - parse_ts(start)).total_seconds()))
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def read_json(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def append_jsonl(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def truncate_atomic(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8"):
        pass
    os.replace(tmp, path)


def r5c_window(r5c_state_file: str | None, start: str, end: str) -> dict | None:
    if not r5c_state_file:
        return None
    state = read_json(r5c_state_file)
    incident_open = bool(state.get("r5c_incident_open"))
    started = state.get("r5c_incident_started_at") or state.get("r5c_last_incident_started_at")
    if not started:
        return None
    if incident_open:
        return None
    cleared = state.get("r5c_last_cleared_at")
    if not cleared:
        return None
    try:
        r5c_start = parse_ts(str(started))
        r5c_end = parse_ts(str(cleared))
        window_start = parse_ts(start)
        window_end = parse_ts(end)
    except (TypeError, ValueError):
        return None
    if not (r5c_start < window_end and r5c_end > window_start):
        return None
    return {
        "r5c_started_at": isoformat(r5c_start),
        "r5c_cleared_at": isoformat(r5c_end),
    }


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def apply_actions(
    queue_file: str,
    actions_file: str,
    alerts_file: str,
    r5c_state_file: str | None = None,
    suppression_log: str | None = None,
) -> None:
    actions = read_jsonl(actions_file)
    if not actions:
        return

    for action in actions:
        kind = action.get("type")
        if kind == "append_partition":
            existing = read_jsonl(queue_file)
            start = action["start"]
            if any(item.get("type") == "network_partition_started" and item.get("start") == start for item in existing):
                continue
            append_jsonl(queue_file, {
                "type": "network_partition_started",
                "start": start,
                "detected_at": action.get("timestamp", start),
            })
        elif kind == "drain_partition":
            end = action["end"]
            pending = read_jsonl(queue_file)
            if not pending:
                truncate_atomic(queue_file)
                continue
            for item in pending:
                start = item.get("start") or item.get("detected_at") or end
                owned_window = r5c_window(r5c_state_file, start, end)
                if owned_window:
                    if suppression_log:
                        append_jsonl(suppression_log, {
                            "timestamp": end,
                            "type": "network_partition_recovery_suppressed_by_r5c",
                            "check": "network_partition",
                            "status": "suppressed",
                            "partition_start": start,
                            "partition_end": end,
                            **owned_window,
                        })
                    continue
                duration = human_duration(start, end)
                append_jsonl(alerts_file, {
                    "timestamp": end,
                    "type": "network_partition_recovered",
                    "check": "network_partition",
                    "status": "recovered",
                    "message": f"Mac Mini was offline {start} to {end} ({duration}); recovered now.",
                })
            truncate_atomic(queue_file)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--queue-file", required=True)
    apply_parser.add_argument("--actions-file", required=True)
    apply_parser.add_argument("--alerts-file", required=True)
    apply_parser.add_argument("--r5c-state-file", default=os.environ.get("K2B_R5C_AUTORECOVERY_STATE_FILE", ""))
    apply_parser.add_argument("--suppression-log", default=os.environ.get("K2B_ROUTER_WATCHDOG_PARTITION_SUPPRESSION_LOG", ""))
    args = parser.parse_args()

    if args.command == "apply":
        # Lock the queue file so concurrent apply calls serialize their
        # read/append/truncate cycle. The actions file is per-tick (fresh
        # tmpfile each run) so no lock needed there.
        queue_dir = os.path.dirname(args.queue_file) or "."
        queue_lock_path = os.path.join(queue_dir, os.path.basename(args.queue_file) + ".lock")
        with file_lock(queue_lock_path):
            apply_actions(args.queue_file, args.actions_file, args.alerts_file, args.r5c_state_file, args.suppression_log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
