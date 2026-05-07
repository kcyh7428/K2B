#!/usr/bin/env python3
import argparse
import atexit
import copy
import datetime as dt
import fcntl
import json
import os
import re
import signal
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from uuid import uuid4


DEFAULT_TARGETS = [
    "https://chatgpt.com/",
    "https://claude.ai/",
    "https://aistudio.google.com/",
    "https://notebooklm.google.com/",
    "https://generativelanguage.googleapis.com/",
]

SELECTOR_TYPES = {"Selector", "URLTest", "Fallback", "LoadBalance", "Relay"}
MANUAL_SELECTOR_PREFIX = "♻️ 手动切换"
HK_RE = re.compile(r"(🇭🇰|香港|hong[\s_-]*kong|(?:^|[^a-z0-9])hk(?:[^a-z0-9]|$)|hk-\d+)", re.IGNORECASE)
STATE_VERSION = 2
RESPONSIVENESS_HALF_LIFE_MS = 1000.0


class RunTimedOut(RuntimeError):
    pass


def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def raise_timeout(_signum, _frame) -> None:
    raise RunTimedOut("leaf optimizer exceeded overall timeout")


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def proxy_path(name: str) -> str:
    return "/proxies/" + urllib.parse.quote(name, safe="")


def request_json(method: str, base: str, path: str, secret: str, body: dict | None = None, timeout: float = 5.0) -> tuple[int, dict]:
    data = None
    headers = {"Authorization": "Bearer " + secret}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            return resp.status, json.loads(raw or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            payload = {"message": raw}
        return e.code, payload
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        return 0, {"message": f"{type(e).__name__}: {e}"}


def append_jsonl(path: str, row: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def try_lock(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    return handle


def close_lock(handle) -> None:
    try:
        handle.close()
    except OSError:
        pass


def cleanup_stale_temps(path: str) -> None:
    directory = os.path.dirname(path)
    prefix = os.path.basename(path) + "."
    if not os.path.isdir(directory):
        return
    for name in os.listdir(directory):
        if name.startswith(prefix) and name.endswith(".tmp"):
            try:
                os.unlink(os.path.join(directory, name))
            except OSError:
                pass


def atomic_write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_json(path: str, default: dict) -> dict:
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def empty_state() -> dict:
    return {"version": STATE_VERSION, "last_change_at": {}, "consecutive_wins": {}, "score_history": {}}


def load_state(path: str) -> tuple[dict, bool, str]:
    cleanup_stale_temps(path)
    if not os.path.exists(path):
        return empty_state(), True, "state_missing"
    try:
        state = load_json(path, {})
    except (json.JSONDecodeError, OSError):
        return empty_state(), True, "state_invalid"
    if not isinstance(state, dict):
        return empty_state(), True, "state_invalid"
    if state.get("version") != STATE_VERSION:
        print(
            f"leaf-optimizer: state version mismatch "
            f"(file={state.get('version')!r}, expected={STATE_VERSION}); "
            f"discarding rolling-score history and consecutive-wins, "
            f"rebuilding from scratch. This is expected when the score "
            f"formula changes (e.g. v1 -> v2 on 2026-05-07).",
            file=sys.stderr,
            flush=True,
        )
        return empty_state(), True, "state_version_migrated"
    for key in ("last_change_at", "consecutive_wins", "score_history"):
        if not isinstance(state.get(key), dict):
            return empty_state(), True, "state_invalid"
    return state, False, ""


def is_hk_leaf(name: str) -> bool:
    return bool(HK_RE.search(name))


def is_manual_selector_name(name: str) -> bool:
    return name.startswith(MANUAL_SELECTOR_PREFIX)


def is_leaf_proxy(name: str, payload: dict) -> bool:
    if name in {"DIRECT", "REJECT", "GLOBAL"}:
        return False
    if is_manual_selector_name(name):
        return False
    proxy_type = payload.get("type") or ""
    return proxy_type not in SELECTOR_TYPES


def leaf_score(success_rate: float, avg_delay: float | None) -> float:
    if avg_delay is None:
        return success_rate
    delay = max(0.0, avg_delay)
    return success_rate * RESPONSIVENESS_HALF_LIFE_MS / (RESPONSIVENESS_HALF_LIFE_MS + delay)


def leaf_rank_key(row: dict) -> tuple[float, float, float]:
    try:
        avg_delay = float(row["avg_delay_ms"]) if row["avg_delay_ms"] is not None else 999999.0
    except (TypeError, ValueError):
        avg_delay = 999999.0
    return (
        float(row["score"]),
        float(row["success_rate"]),
        -avg_delay,
    )


def score_leaf(base: str, secret: str, leaf: str, targets: Iterable[str], timeout_ms: int) -> dict:
    target_results: dict[str, dict] = {}
    ok_count = 0
    delays: list[int] = []
    for target in targets:
        delay_path = (
            proxy_path(leaf)
            + "/delay?"
            + urllib.parse.urlencode({"timeout": str(timeout_ms), "url": target})
        )
        code, payload = request_json("GET", base, delay_path, secret, timeout=(timeout_ms / 1000) + 2)
        delay = payload.get("delay") if isinstance(payload, dict) else None
        ok = code == 200 and isinstance(delay, int) and delay >= 0
        if ok:
            ok_count += 1
            delays.append(delay)
        target_results[target] = {
            "ok": ok,
            "http_code": code,
            "delay_ms": delay,
            "message": payload.get("message") if isinstance(payload, dict) else None,
        }
    targets_list = list(targets)
    success_rate = ok_count / len(targets_list) if targets_list else 0.0
    avg_delay = sum(delays) / len(delays) if delays else None
    score = leaf_score(success_rate, avg_delay)
    return {
        "leaf": leaf,
        "targets": target_results,
        "success_rate": round(success_rate, 4),
        "avg_delay_ms": round(avg_delay, 2) if avg_delay is not None else None,
        "score": round(score, 4),
        "excluded_reason": "hk_leaf" if is_hk_leaf(leaf) else None,
    }


def latest_selector_payload(base: str, secret: str, selector: str) -> dict | None:
    status, payload = request_json("GET", base, proxy_path(selector), secret, timeout=3)
    if status <= 0 or status >= 400:
        return None
    return payload


def put_selector(base: str, secret: str, selector: str, leaf: str, attempts: int = 2) -> int:
    status = 0
    for attempt in range(attempts):
        status, _ = request_json("PUT", base, proxy_path(selector), secret, {"name": leaf}, timeout=5)
        if 200 <= status < 300:
            return status
        if attempt + 1 < attempts:
            time.sleep(1.0 * (attempt + 1))
    return status


def average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def append_score(history: dict, selector: str, leaf: str, score: float, limit: int) -> list[float]:
    by_selector = history.setdefault(selector, {})
    values = by_selector.setdefault(leaf, [])
    values.append(float(score))
    del values[:-limit]
    return values


def prune_state(state: dict, selectors: list[str], selector_leaves: dict[str, set[str]]) -> None:
    for selector, leaves in selector_leaves.items():
        score_bucket = state.setdefault("score_history", {}).setdefault(selector, {})
        win_bucket = state.setdefault("consecutive_wins", {}).setdefault(selector, {})
        for leaf in list(score_bucket):
            if leaf not in leaves:
                del score_bucket[leaf]
        for leaf in list(win_bucket):
            if leaf not in leaves:
                del win_bucket[leaf]


def scope_violation(selector: str, payload: dict | None, target: str | None) -> str | None:
    if not is_manual_selector_name(selector):
        return "selector_not_manual"
    if not payload:
        return "selector_unavailable"
    if payload.get("type") != "Selector":
        return "selector_not_selector_type"
    if target is not None and target not in (payload.get("all") or []):
        return "target_not_member"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision-log", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/leaf-optimizer.jsonl"))
    parser.add_argument("--state-file", default=os.path.expanduser("~/Library/Application Support/k2b-router-watchdog/leaf-optimizer-state.json"))
    parser.add_argument("--lock-file", default=os.path.expanduser("~/Library/Application Support/k2b-router-watchdog/leaf-optimizer.lock"))
    parser.add_argument("--mutation-lock-file", default=os.path.expanduser("~/Library/Application Support/k2b-router-watchdog/mihomo-mutation.lock"))
    parser.add_argument("--sentinel", default=os.path.expanduser("~/.k2b-router-leafopt-enabled"))
    parser.add_argument("--now", default=None)
    parser.add_argument("--timeout-ms", type=int, default=2500)
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    parser.add_argument("--candidate-regex", default=os.environ.get("K2B_LEAF_OPTIMIZER_CANDIDATE_REGEX", r"^♻️ 手动切换"))
    parser.add_argument("--min-success-rate", type=float, default=0.8)
    parser.add_argument("--min-score-improvement", type=float, default=float(os.environ.get("K2B_LEAF_OPTIMIZER_MIN_SCORE_IMPROVEMENT", "0.05")))
    parser.add_argument("--max-workers", type=int, default=int(os.environ.get("K2B_LEAF_OPTIMIZER_MAX_WORKERS", "8")))
    parser.add_argument("--min-dwell-hours", type=float, default=float(os.environ.get("K2B_LEAF_OPTIMIZER_MIN_DWELL_HOURS", "12")))
    parser.add_argument("--invalid-min-dwell-minutes", type=float, default=float(os.environ.get("K2B_LEAF_OPTIMIZER_INVALID_MIN_DWELL_MINUTES", "60")))
    parser.add_argument("--min-consecutive-wins", type=int, default=int(os.environ.get("K2B_LEAF_OPTIMIZER_MIN_CONSECUTIVE_WINS", "2")))
    parser.add_argument("--rolling-runs", type=int, default=int(os.environ.get("K2B_LEAF_OPTIMIZER_ROLLING_RUNS", "3")))
    parser.add_argument("--overall-timeout-seconds", type=int, default=int(os.environ.get("K2B_LEAF_OPTIMIZER_OVERALL_TIMEOUT_SECONDS", "180")))
    parser.add_argument("--scoring-timeout-seconds", type=int, default=int(os.environ.get("K2B_LEAF_OPTIMIZER_SCORING_TIMEOUT_SECONDS", "120")))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base = os.environ["MIHOMO_API_BASE"]
    secret = os.environ["MIHOMO_API_SECRET"]
    group = os.environ["MIHOMO_OPENAI_GROUP"]
    now = parse_ts(args.now) if args.now else utc_now()
    targets = [target.strip() for target in args.targets.split(",") if target.strip()]
    candidate_re = re.compile(args.candidate_regex)
    run_id = str(uuid4())
    enabled = os.path.exists(args.sentinel)
    effective_dry_run = args.dry_run or not enabled
    if args.overall_timeout_seconds > 0:
        signal.signal(signal.SIGALRM, raise_timeout)
        signal.alarm(args.overall_timeout_seconds)
    lock_handle = try_lock(args.lock_file)
    if lock_handle is None:
        summary = {
            "timestamp": iso(now),
            "run_id": run_id,
            "group": group,
            "enabled": enabled,
            "dry_run": effective_dry_run,
            "reason": "lock_busy",
            "changed": 0,
            "assignments": [],
        }
        append_jsonl(args.decision_log, summary)
        print(json.dumps(summary, ensure_ascii=False))
        signal.alarm(0)
        return 0
    atexit.register(close_lock, lock_handle)
    mutation_lock_handle = None

    def finish(summary: dict, rc: int, state_to_write: dict | None = None) -> int:
        try:
            if state_to_write is not None and not effective_dry_run:
                signal.alarm(0)
                atomic_write_json(args.state_file, state_to_write)
        except OSError as exc:
            summary = {
                **summary,
                "reason": "state_write_failed",
                "state_write_error": f"{type(exc).__name__}: {exc}",
            }
            rc = 2
        append_jsonl(args.decision_log, summary)
        print(json.dumps(summary, ensure_ascii=False))
        signal.alarm(0)
        if mutation_lock_handle is not None:
            close_lock(mutation_lock_handle)
        close_lock(lock_handle)
        try:
            atexit.unregister(close_lock)
        except ValueError:
            pass
        return rc

    if candidate_re.search(group):
        summary = {
            "timestamp": iso(now),
            "run_id": run_id,
            "group": group,
            "enabled": enabled,
            "dry_run": effective_dry_run,
            "reason": "scope_violation",
            "scope_violation": "openai_group_matches_candidate_regex",
            "changed": 0,
            "assignments": [],
        }
        return finish(summary, 2)

    if not enabled and not args.dry_run:
        summary = {
            "timestamp": iso(now),
            "run_id": run_id,
            "group": group,
            "enabled": False,
            "dry_run": True,
            "reason": "sentinel_missing",
            "message": f"skipped: sentinel missing ({args.sentinel})",
            "changed": 0,
            "assignments": [],
        }
        return finish(summary, 0)

    status, group_payload = request_json("GET", base, proxy_path(group), secret, timeout=3)
    if status <= 0 or status >= 400:
        summary = {
            "timestamp": iso(now),
            "run_id": run_id,
            "group": group,
            "enabled": enabled,
            "dry_run": effective_dry_run,
            "reason": "api_unreachable",
            "http_status": status,
            "message": group_payload.get("message") if isinstance(group_payload, dict) else None,
            "changed": 0,
            "assignments": [],
        }
        return finish(summary, 2)
    selectors = [
        item for item in (group_payload.get("all") or [])
        if candidate_re.search(item or "")
    ]

    status, proxies_payload = request_json("GET", base, "/proxies", secret, timeout=5)
    if status <= 0 or status >= 400:
        summary = {
            "timestamp": iso(now),
            "run_id": run_id,
            "group": group,
            "enabled": enabled,
            "dry_run": effective_dry_run,
            "reason": "api_unreachable",
            "http_status": status,
            "message": proxies_payload.get("message") if isinstance(proxies_payload, dict) else None,
            "changed": 0,
            "assignments": [],
        }
        return finish(summary, 2)
    proxies = proxies_payload.get("proxies") if isinstance(proxies_payload, dict) else {}

    selector_payloads: dict[str, dict] = {}
    leaf_pool: set[str] = set()
    for selector in selectors:
        payload = latest_selector_payload(base, secret, selector)
        if not payload:
            continue
        initial_scope_problem = scope_violation(selector, payload, None)
        if initial_scope_problem:
            selector_payloads[selector] = {"scope_problem": initial_scope_problem}
            continue
        selector_payloads[selector] = payload
        for name in payload.get("all") or []:
            proxy_payload = proxies.get(name) or {}
            if is_leaf_proxy(name, proxy_payload):
                leaf_pool.add(name)

    leaf_scores: dict[str, dict] = {}
    scoring_timed_out = False
    executor = ThreadPoolExecutor(max_workers=max(1, args.max_workers))
    try:
        futures = {
            executor.submit(score_leaf, base, secret, leaf, targets, args.timeout_ms): leaf
            for leaf in sorted(leaf_pool)
        }
        try:
            for future in as_completed(futures, timeout=max(1, args.scoring_timeout_seconds)):
                leaf = futures[future]
                leaf_scores[leaf] = future.result()
        except FuturesTimeout:
            scoring_timed_out = True
            for future in futures:
                future.cancel()
    finally:
        executor.shutdown(wait=not scoring_timed_out, cancel_futures=True)
    if scoring_timed_out:
        summary = {
            "timestamp": iso(now),
            "run_id": run_id,
            "group": group,
            "enabled": enabled,
            "dry_run": effective_dry_run,
            "reason": "scoring_timeout",
            "scored_leafs": len(leaf_scores),
            "leaf_pool": len(leaf_pool),
            "changed": 0,
            "assignments": [],
        }
        return finish(summary, 2)
    eligible = {
        leaf: row for leaf, row in leaf_scores.items()
        if not row["excluded_reason"] and row["success_rate"] >= args.min_success_rate
    }
    used: set[str] = set()
    assignments: list[dict] = []
    changed = 0
    fatal_scope_violation = any(bool(payload.get("scope_problem")) for payload in selector_payloads.values())
    loaded_state, state_safe_mode, state_safe_reason = load_state(args.state_file)
    state = copy.deepcopy(loaded_state)
    last_change_at = state.setdefault("last_change_at", {})
    consecutive_wins = state.setdefault("consecutive_wins", {})
    score_history = state.setdefault("score_history", {})
    selector_leaves: dict[str, set[str]] = {}
    mutation_lock_busy = False
    if not effective_dry_run:
        mutation_lock_handle = try_lock(args.mutation_lock_file)
        if mutation_lock_handle is None:
            mutation_lock_busy = True
        else:
            atexit.register(close_lock, mutation_lock_handle)

    for selector in selectors:
        payload = selector_payloads.get(selector)
        if payload and payload.get("scope_problem"):
            fatal_scope_violation = True
            assignments.append({
                "selector": selector,
                "current_leaf": None,
                "target_leaf": None,
                "changed": False,
                "reason": "scope_violation",
                "scope_violation": payload["scope_problem"],
            })
            continue
        if not payload:
            assignments.append({
                "selector": selector,
                "current_leaf": None,
                "target_leaf": None,
                "changed": False,
                "reason": "selector_unavailable",
            })
            continue

        current = payload.get("now") or ""
        available = [leaf for leaf in (payload.get("all") or []) if leaf in eligible]
        selector_leaves[selector] = set(payload.get("all") or [])
        ranked = sorted(
            available,
            key=lambda leaf: leaf_rank_key(eligible[leaf]),
            reverse=True,
        )
        target = next((leaf for leaf in ranked if leaf not in used), ranked[0] if ranked else None)
        if target:
            used.add(target)

        current_score = leaf_scores.get(current)
        target_score = leaf_scores.get(target) if target else None
        selector_history = score_history.setdefault(selector, {})
        for leaf in available:
            append_score(score_history, selector, leaf, eligible[leaf]["score"], max(1, args.rolling_runs))
        current_values = selector_history.get(current, [])
        target_values = selector_history.get(target, []) if target else []
        current_rolling_score = average([float(item) for item in current_values])
        target_rolling_score = average([float(item) for item in target_values])
        current_invalid = (
            not current
            or current not in leaf_scores
            or is_hk_leaf(current)
            or not current_score
            or current_score["success_rate"] < args.min_success_rate
            or bool(current_score.get("excluded_reason"))
        )
        score_delta = None
        if target and target_rolling_score is not None:
            current_compare = current_rolling_score
            if current_compare is None and current_score and current_score.get("score") is not None:
                current_compare = float(current_score["score"])
            if current_compare is not None:
                score_delta = round(target_rolling_score - current_compare, 4)
        success_rate_delta = None
        if target_score is not None and current_score is not None:
            success_rate_delta = round(float(target_score["success_rate"]) - float(current_score["success_rate"]), 4)

        wins_for_selector = consecutive_wins.setdefault(selector, {})
        for candidate in list(wins_for_selector):
            if candidate != target:
                wins_for_selector[candidate] = 0
        if target:
            wins_for_selector[target] = int(wins_for_selector.get(target, 0)) + 1
        consecutive_win_count = int(wins_for_selector.get(target or "", 0))

        last_change_raw = last_change_at.get(selector)
        dwell_blocked = False
        invalid_dwell_blocked = False
        if last_change_raw and not current_invalid:
            try:
                dwell_until = parse_ts(last_change_raw) + dt.timedelta(hours=args.min_dwell_hours)
                dwell_blocked = now < dwell_until
            except ValueError:
                dwell_blocked = False
        if last_change_raw and current_invalid:
            try:
                invalid_dwell_until = parse_ts(last_change_raw) + dt.timedelta(minutes=args.invalid_min_dwell_minutes)
                invalid_dwell_blocked = now < invalid_dwell_until
            except ValueError:
                invalid_dwell_blocked = False

        stable_enough = consecutive_win_count >= args.min_consecutive_wins
        clear_better = score_delta is None or score_delta >= args.min_score_improvement
        should_change = bool(
            target
            and target != current
            and not state_safe_mode
            and (
                (current_invalid and not invalid_dwell_blocked)
                or (clear_better and stable_enough and not dwell_blocked)
            )
        )
        reason = "unchanged"
        http_status = None
        verified_after_put = None
        if not target:
            reason = "no_eligible_leaf"
        elif state_safe_mode:
            reason = state_safe_reason
        elif current_invalid:
            reason = "invalid_dwell_active" if invalid_dwell_blocked else "current_invalid"
        elif dwell_blocked:
            reason = "dwell_active"
        elif target and not stable_enough:
            reason = "waiting_for_consecutive_win"
        elif should_change:
            reason = "better_leaf"

        if should_change and fatal_scope_violation:
            should_change = False
            reason = "scope_violation_present"
        elif should_change and not effective_dry_run:
            if mutation_lock_busy:
                should_change = False
                reason = "mutation_lock_busy"
            else:
                fresh_payload = latest_selector_payload(base, secret, selector)
                scope_problem = scope_violation(selector, fresh_payload, target)
                if scope_problem:
                    fatal_scope_violation = True
                    should_change = False
                    reason = "scope_violation"
                elif fresh_payload and fresh_payload.get("now") != current:
                    should_change = False
                    reason = "stale_selector"
                else:
                    http_status = put_selector(base, secret, selector, target)
                    if 200 <= http_status < 300:
                        verify_payload = latest_selector_payload(base, secret, selector)
                        verified_after_put = bool(verify_payload and verify_payload.get("now") == target)
        if should_change and not effective_dry_run and http_status is not None:
            should_change = 200 <= http_status < 300
            if should_change:
                if verified_after_put:
                    changed += 1
                    last_change_at[selector] = iso(now)
                    wins_for_selector[target] = 0
                else:
                    should_change = False
                    reason = "put_verify_failed"
            else:
                reason = f"put_http_{http_status}"
        elif should_change:
            changed += 1

        assignments.append({
            "selector": selector,
            "current_leaf": current,
            "target_leaf": target,
            "changed": should_change,
            "reason": reason,
            "score_delta": score_delta,
            "success_rate_delta": success_rate_delta,
            "current_rolling_score": round(current_rolling_score, 4) if current_rolling_score is not None else None,
            "target_rolling_score": round(target_rolling_score, 4) if target_rolling_score is not None else None,
            "consecutive_wins": consecutive_win_count,
            "dwell_blocked": dwell_blocked,
            "invalid_dwell_blocked": invalid_dwell_blocked,
            "http_status": http_status,
            "mutation_guard": "shared_lock_fresh_get_then_verify",
        })

    prune_state(state, selectors, selector_leaves)
    summary = {
        "timestamp": iso(now),
        "run_id": run_id,
        "group": group,
        "enabled": enabled,
        "dry_run": effective_dry_run,
        "reason": "scope_violation" if fatal_scope_violation else "completed",
        "selectors": selectors,
        "scored_leafs": len(leaf_scores),
        "eligible_leafs": len(eligible),
        "changed": changed,
        "assignments": assignments,
    }
    return finish(summary, 2 if fatal_scope_violation else 0, state)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        signal.alarm(0)
