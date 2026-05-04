#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="${K2B_ROUTER_WATCHDOG_LOG_DIR:-$HOME/Library/Logs/k2b-router-watchdog}"
VAULT_PATH="${K2B_VAULT_PATH:-$HOME/Projects/K2B-Vault}"
HEALTH_LOG="${K2B_ROUTER_WATCHDOG_HEALTH_LOG:-$LOG_DIR/health.jsonl}"
OUTPUT="${K2B_ROUTER_WATCHDOG_ROLLUP_OUTPUT:-$VAULT_PATH/wiki/context/context_router-health.md}"
NOW="${K2B_ROUTER_WATCHDOG_NOW:-$(date -u '+%Y-%m-%dT%H:%M:%SZ')}"

python3 - "$HEALTH_LOG" "$OUTPUT" "$NOW" <<'PY'
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

health_log, output, now_raw = sys.argv[1:]
now = datetime.fromisoformat(now_raw.replace("Z", "+00:00")).astimezone(timezone.utc)
cutoff = now - timedelta(hours=24)

def parse_ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

events = []
if os.path.exists(health_log):
    with open(health_log, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if parse_ts(event["timestamp"]) >= cutoff:
                    events.append(event)
            except Exception:
                continue

check_totals = Counter()
check_fails = Counter()
messages = defaultdict(Counter)
for event in events:
    for name, check in (event.get("checks") or {}).items():
        check_totals[name] += 1
        if not check.get("ok", False):
            check_fails[name] += 1
            msg = check.get("message") or "failed"
            messages[name][msg] += 1

lines = [
    "---",
    "tags: [context, infrastructure, monitoring, router-watchdog]",
    f"date: {now.date().isoformat()}",
    "type: context",
    "origin: k2b-router-watchdog",
    'up: "[[index]]"',
    "---",
    "",
    "# Router Health",
    "",
    f"Last updated: {now_raw}",
    f"Window: last 24 hours",
    f"Ticks observed: {len(events)}",
    "",
    "## Summary",
    "",
]

if not events:
    lines.append("No watchdog ticks recorded in the last 24 hours.")
else:
    bad_ticks = sum(1 for event in events if not event.get("overall_ok", False))
    lines.append(f"- Overall: {len(events) - bad_ticks}/{len(events)} ticks fully ok")
    if check_fails:
        worst = ", ".join(f"{name} {count}/{check_totals[name]}" for name, count in check_fails.most_common())
        lines.append(f"- Failing checks: {worst}")
    else:
        lines.append("- Failing checks: none")

lines.extend(["", "## Checks", ""])
if check_totals:
    lines.append("| Check | OK ticks | Failed ticks | Most common failure |")
    lines.append("|---|---:|---:|---|")
    for name in sorted(check_totals):
        total = check_totals[name]
        fails = check_fails[name]
        ok = total - fails
        common = messages[name].most_common(1)[0][0] if messages[name] else ""
        lines.append(f"| {name} | {ok} | {fails} | {common} |")
else:
    lines.append("No check data.")

lines.append("")
os.makedirs(os.path.dirname(output), exist_ok=True)
tmp = output + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
    f.write("\n")
os.replace(tmp, output)
PY
