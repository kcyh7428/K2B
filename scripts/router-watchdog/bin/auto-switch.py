#!/usr/bin/env python3
"""Retired router auto-switch stub.

Auto Switch was retired on 2026-06-22 after the house network moved to
R5C-owned Mihomo/private-VPN operation and the DoggyGo selector assumptions
no longer applied. This file remains only as a forensic audit stub; it never
mutates Mihomo selectors and has no runtime bypass to reactivate the old
selector-mutation logic.
"""
import argparse
import datetime as dt
import json
import os
import sys


RETIRED_EXIT_CODE = 0
RETIRED_AUDIT_ERROR_EXIT_CODE = 2
RETIRED_AUDIT_LOG = "~/Library/Logs/k2b-router-watchdog/retired-mutations.jsonl"
RETIRED_AUDIT_MAX_BYTES = 1024 * 1024


def retired_audit_max_bytes() -> int:
    raw = os.environ.get("K2B_ROUTER_RETIRED_AUDIT_MAX_BYTES", str(RETIRED_AUDIT_MAX_BYTES))
    try:
        value = int(raw)
    except ValueError:
        return RETIRED_AUDIT_MAX_BYTES
    return max(value, 0)


def append_retired_audit(script: str) -> None:
    path = os.path.expanduser(os.environ.get("K2B_ROUTER_RETIRED_AUDIT_LOG", RETIRED_AUDIT_LOG))
    max_bytes = retired_audit_max_bytes()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if max_bytes > 0 and os.path.exists(path) and os.path.getsize(path) >= max_bytes:
        os.replace(path, path + ".1")
    row = {
        "timestamp": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "script": script,
        "action": "retired_noop",
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-file", default=None)
    parser.add_argument("--state-file", default=None)
    parser.add_argument("--score-log", default=None)
    parser.add_argument("--decision-log", default=None)
    parser.add_argument("--alerts-file", default=None)
    parser.add_argument("--sentinel", default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument("--min-consecutive-fails", type=int, default=3)
    parser.add_argument("--min-success-rate", type=float, default=0.8)
    parser.add_argument("--min-score-improvement", type=float, default=0.05)
    parser.add_argument("--max-score-age-hours", type=int, default=24)
    parser.add_argument("--switch-cooldown-minutes", type=int, default=30)
    parser.add_argument("--candidate-regex", default=None)
    parser.add_argument("--score-command", default=None)
    parser.add_argument("--score-command-timeout-seconds", type=int, default=180)
    parser.add_argument("--mutation-lock-file", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.parse_known_args()

    print(
        "auto-switch.py is retired and performed no mutation for the current R5C/private-VPN topology.",
        file=sys.stderr,
    )
    try:
        append_retired_audit("auto-switch.py")
    except OSError as exc:
        print(f"WARNING: failed to write retired mutation audit log: {exc}", file=sys.stderr)
        return RETIRED_AUDIT_ERROR_EXIT_CODE
    return RETIRED_EXIT_CODE


if __name__ == "__main__":
    raise SystemExit(main())
