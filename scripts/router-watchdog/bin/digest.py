#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
from collections import Counter


def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_jsonl(path: str) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def latest_scores(rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        candidate = row.get("candidate")
        if not candidate:
            continue
        old = latest.get(candidate)
        if old is None or row.get("timestamp", "") >= old.get("timestamp", ""):
            latest[candidate] = row
    return latest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health-log", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/health.jsonl"))
    parser.add_argument("--score-log", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/node-score.jsonl"))
    parser.add_argument("--now", default=None)
    parser.add_argument("--candidate-regex", default=os.environ.get("K2B_AUTOSWITCH_CANDIDATE_REGEX", r"^♻️ 手动切换"))
    args = parser.parse_args()

    now = parse_ts(args.now) if args.now else dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    cutoff = now - dt.timedelta(hours=24)

    health = [row for row in load_jsonl(args.health_log) if row.get("timestamp") and parse_ts(row["timestamp"]) >= cutoff]
    scores = [row for row in load_jsonl(args.score_log) if row.get("timestamp") and parse_ts(row["timestamp"]) >= cutoff]

    fail_counts: Counter[str] = Counter()
    node_fail_counts: Counter[str] = Counter()
    latest_node = "unknown"
    latest_status = "no recent ticks"
    latest_ts = "unknown"

    for row in health:
        checks = row.get("checks", {})
        openai = checks.get("openai_node", {})
        details = openai.get("details") or {}
        node = details.get("selected_node") or details.get("openai_group_selection")
        if node:
            latest_node = node
        for name, check in checks.items():
            if not check.get("ok", True):
                fail_counts[name] += 1
                if node:
                    node_fail_counts[node] += 1

    if health:
        last = health[-1]
        latest_ts = last.get("timestamp", "unknown")
        latest_status = "OK" if last.get("overall_ok") else "FAILING"

    latest = latest_scores(scores)
    candidate_re = re.compile(args.candidate_regex)
    good = sorted(
        [
            row for row in latest.values()
            if not row.get("quarantined")
            and row.get("success_rate", 0) >= 0.8
            and candidate_re.search(row.get("candidate") or "")
        ],
        key=lambda row: (row.get("score", 0), row.get("success_rate", 0)),
        reverse=True,
    )
    quarantined = sorted(
        [row for row in latest.values() if row.get("quarantined")],
        key=lambda row: row.get("quarantine_until", ""),
        reverse=True,
    )

    lines = [
        f"Router watchdog digest: {latest_status} at {latest_ts}; current node: {latest_node}.",
    ]
    if fail_counts:
        top_failures = ", ".join(f"{name} x{count}" for name, count in fail_counts.most_common(4))
        lines.append(f"Last 24h failures: {top_failures}.")
    else:
        lines.append("Last 24h failures: none.")

    if good:
        best = good[0]
        leaf = best.get("resolved_leaf") or best.get("candidate")
        lines.append(
            "Recommendation: prefer "
            f"{leaf} via {best.get('candidate')} "
            f"(score {best.get('score', 0):.2f}, success {best.get('success_rate', 0):.0%})."
        )
    else:
        lines.append("Recommendation: no scored known-good node yet.")

    if quarantined:
        bad = quarantined[0]
        leaf = bad.get("resolved_leaf") or bad.get("candidate")
        until = bad.get("quarantine_until", "unknown")
        lines.append(f"Quarantine: avoid {leaf} via {bad.get('candidate')} until {until}.")
    elif node_fail_counts:
        node, count = node_fail_counts.most_common(1)[0]
        lines.append(f"Watch: {node} had {count} failed check observations.")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
