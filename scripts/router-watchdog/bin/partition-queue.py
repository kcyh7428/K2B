#!/usr/bin/env python3
import argparse
import json
import os
from datetime import datetime, timezone


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


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


def apply_actions(queue_file: str, actions_file: str, alerts_file: str) -> None:
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
    args = parser.parse_args()

    if args.command == "apply":
        apply_actions(args.queue_file, args.actions_file, args.alerts_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
