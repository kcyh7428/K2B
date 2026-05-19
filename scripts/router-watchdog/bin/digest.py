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
                try:
                    rows.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    # Malformed line. Skip silently so one bad row doesn't kill
                    # the whole digest. Callers that need unreadable counts use
                    # load_jsonl_with_errors below.
                    continue
    return rows


def load_jsonl_with_errors(path: str) -> tuple[list[dict], int]:
    """Load JSONL returning (good_rows, unreadable_count).

    Used for drift filtering where a single malformed row must not silently
    eat the operator's signal -- the digest needs to surface "N rows were
    unparseable" alongside the parseable count. Guards against non-dict JSON
    values (e.g. `null`, `[1,2,3]`) which parse successfully but break
    downstream `.get(...)` calls.
    """
    if not path or not os.path.exists(path):
        return [], 0
    rows: list[dict] = []
    unreadable = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                unreadable += 1
                continue
            if not isinstance(parsed, dict):
                unreadable += 1
                continue
            rows.append(parsed)
    return rows, unreadable


def filter_log_by_recent_timestamp(rows: list[dict], cutoff: dt.datetime) -> tuple[list[dict], int]:
    """Tolerant timestamp filter shared by health/score/drift logs.

    Returns (kept_within_window, skipped_due_to_bad_or_missing_ts).
    Non-dicts already filtered upstream by load_jsonl_with_errors when used
    via that path; this function additionally guards against bad ISO strings
    so a single corrupted timestamp can't crash the digest.
    """
    kept: list[dict] = []
    skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue
        ts = row.get("timestamp")
        if not ts:
            skipped += 1
            continue
        try:
            parsed = parse_ts(ts)
        except (ValueError, AttributeError, TypeError):
            skipped += 1
            continue
        if parsed >= cutoff:
            kept.append(row)
    return kept, skipped


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


def drift_summary(rows: list[dict]) -> tuple[int, str | None]:
    kinds: Counter[str] = Counter()
    for row in rows:
        kind = row.get("kind") or row.get("type") or "unknown"
        kinds[str(kind)] += 1
    if not kinds:
        return 0, None
    summary = ", ".join(f"{kind} x{count}" for kind, count in kinds.most_common(4))
    return sum(kinds.values()), summary


def filter_drift_rows(rows: list[dict], cutoff: dt.datetime) -> tuple[list[dict], int]:
    """Backwards-compatible alias for filter_log_by_recent_timestamp."""
    return filter_log_by_recent_timestamp(rows, cutoff)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health-log", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/health.jsonl"))
    parser.add_argument("--score-log", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/node-score.jsonl"))
    parser.add_argument("--confirm-failures-log", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/confirm-failures.jsonl"))
    parser.add_argument("--now", default=None)
    parser.add_argument("--candidate-regex", default=os.environ.get("K2B_AUTOSWITCH_CANDIDATE_REGEX", r"^♻️ 手动切换"))
    args = parser.parse_args()

    now = parse_ts(args.now) if args.now else dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    cutoff = now - dt.timedelta(hours=24)

    # Use the tolerant loader for all three logs so a single bad row in any
    # log can't crash the digest. health and score logs are pre-existing
    # paths; the defensive treatment here is the same as the new drift path.
    health_raw, _ = load_jsonl_with_errors(args.health_log)
    health, _health_skipped = filter_log_by_recent_timestamp(health_raw, cutoff)
    scores_raw, _ = load_jsonl_with_errors(args.score_log)
    scores, _scores_skipped = filter_log_by_recent_timestamp(scores_raw, cutoff)
    drift_raw_rows, drift_unreadable_lines = load_jsonl_with_errors(args.confirm_failures_log)
    drift_rows, drift_skipped_ts = filter_log_by_recent_timestamp(drift_raw_rows, cutoff)
    drift_unreadable_total = drift_unreadable_lines + drift_skipped_ts
    drift_count, drift_types = drift_summary(drift_rows)

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
    lines.append(f"Drift events (last 24h): {drift_count}.")
    if drift_types:
        lines.append(f"Drift types: {drift_types}.")
    if drift_unreadable_total:
        lines.append(f"Drift rows unreadable (last 24h): {drift_unreadable_total}.")

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
