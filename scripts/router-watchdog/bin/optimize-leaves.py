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

DEFAULT_DECISION_LOG = "~/Library/Logs/k2b-router-watchdog/leaf-optimizer.jsonl"
DEFAULT_STATE_FILE = "~/Library/Application Support/k2b-router-watchdog/leaf-optimizer-state.json"
DEFAULT_LOCK_FILE = "~/Library/Application Support/k2b-router-watchdog/leaf-optimizer.lock"
DEFAULT_MUTATION_LOCK_FILE = "~/Library/Application Support/k2b-router-watchdog/mihomo-mutation.lock"
DEFAULT_SENTINEL = "~/.k2b-router-leafopt-enabled"
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


def expand_path(value: str) -> str:
    return os.path.expanduser(os.path.expandvars(value))


def targets_to_string(value) -> str:
    if value is None:
        return ",".join(DEFAULT_TARGETS)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    raise ValueError("targets must be a string or list")


def load_profile_defs(path: str) -> dict[str, dict]:
    payload = load_json(path, {})
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if isinstance(profiles, dict):
        return {str(name): dict(profile or {}) for name, profile in profiles.items()}
    if isinstance(profiles, list):
        result: dict[str, dict] = {}
        for profile in profiles:
            if not isinstance(profile, dict) or not profile.get("name"):
                raise ValueError("profile list entries must be objects with a name")
            result[str(profile["name"])] = dict(profile)
        return result
    raise ValueError("profiles file must contain a profiles object or list")


def profile_runtime(args: argparse.Namespace, name: str | None, raw: dict | None = None) -> argparse.Namespace:
    raw = raw or {}
    runtime = argparse.Namespace(**vars(args))
    runtime.profile = name
    runtime.group_env_var = str(raw.get("group_env_var", args.group_env_var))
    runtime.decision_log = expand_path(str(raw.get("decision_log", args.decision_log)))
    runtime.state_file = expand_path(str(raw.get("state_file", args.state_file)))
    runtime.lock_file = expand_path(str(raw.get("lock_file", args.lock_file)))
    runtime.mutation_lock_file = expand_path(str(raw.get("mutation_lock_file", args.mutation_lock_file)))
    runtime.sentinel = expand_path(str(raw.get("sentinel", args.sentinel)))
    runtime.targets = targets_to_string(raw.get("targets", args.targets))
    runtime.candidate_regex = str(raw.get("selector_regex", raw.get("candidate_regex", args.candidate_regex)))
    runtime.exclude_hk = bool(raw.get("exclude_hk", args.exclude_hk))
    runtime.exclude_leaf_regex = str(raw.get("exclude_leaf_regex", args.exclude_leaf_regex or ""))
    return runtime


def profile_error_summary(args: argparse.Namespace, now: dt.datetime, run_id: str, reason: str, extra: dict | None = None) -> dict:
    summary = {
        "timestamp": iso(now),
        "run_id": run_id,
        "profile": getattr(args, "profile", None),
        "group": None,
        "enabled": False,
        "dry_run": True,
        "reason": reason,
        "changed": 0,
        "assignments": [],
    }
    if extra:
        summary.update(extra)
    return summary


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


def score_leaf(
    base: str,
    secret: str,
    leaf: str,
    targets: Iterable[str],
    timeout_ms: int,
    exclude_hk: bool = True,
    exclude_leaf_re: re.Pattern | None = None,
) -> dict:
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
    excluded_reason = None
    if exclude_hk and is_hk_leaf(leaf):
        excluded_reason = "hk_leaf"
    elif exclude_leaf_re and exclude_leaf_re.search(leaf):
        excluded_reason = "excluded_leaf_regex"
    return {
        "leaf": leaf,
        "targets": target_results,
        "success_rate": round(success_rate, 4),
        "avg_delay_ms": round(avg_delay, 2) if avg_delay is not None else None,
        "score": round(score, 4),
        "excluded_reason": excluded_reason,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles-file", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--all-enabled-profiles", action="store_true")
    parser.add_argument("--decision-log", default=expand_path(DEFAULT_DECISION_LOG))
    parser.add_argument("--state-file", default=expand_path(DEFAULT_STATE_FILE))
    parser.add_argument("--lock-file", default=expand_path(DEFAULT_LOCK_FILE))
    parser.add_argument("--mutation-lock-file", default=expand_path(DEFAULT_MUTATION_LOCK_FILE))
    parser.add_argument("--sentinel", default=expand_path(DEFAULT_SENTINEL))
    parser.add_argument("--now", default=None)
    parser.add_argument("--timeout-ms", type=int, default=2500)
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    parser.add_argument("--candidate-regex", default=os.environ.get("K2B_LEAF_OPTIMIZER_CANDIDATE_REGEX", r"^♻️ 手动切换"))
    parser.add_argument("--group-env-var", default="MIHOMO_OPENAI_GROUP")
    parser.add_argument("--exclude-hk", dest="exclude_hk", action="store_true", default=True)
    parser.add_argument("--no-exclude-hk", dest="exclude_hk", action="store_false")
    parser.add_argument("--exclude-leaf-regex", default="")
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
    return parser.parse_args()


def run_profile(args: argparse.Namespace, now: dt.datetime | None = None, run_id: str | None = None) -> tuple[int, dict]:
    now = now or (parse_ts(args.now) if args.now else utc_now())
    run_id = run_id or str(uuid4())

    missing = next((name for name in ("MIHOMO_API_BASE", "MIHOMO_API_SECRET", args.group_env_var) if not os.environ.get(name)), None)
    if missing:
        summary = profile_error_summary(args, now, run_id, "env_missing", {"missing_env_var": missing})
        append_jsonl(args.decision_log, summary)
        print(json.dumps(summary, ensure_ascii=False))
        return 2, summary

    base = os.environ["MIHOMO_API_BASE"]
    secret = os.environ["MIHOMO_API_SECRET"]
    group = os.environ[args.group_env_var]
    targets = [target.strip() for target in args.targets.split(",") if target.strip()]
    candidate_re = re.compile(args.candidate_regex)
    exclude_leaf_re = re.compile(args.exclude_leaf_regex) if args.exclude_leaf_regex else None
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
            "profile": getattr(args, "profile", None),
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
        return 0, summary
    atexit.register(close_lock, lock_handle)
    mutation_lock_handle = None

    def finish(summary: dict, rc: int, state_to_write: dict | None = None) -> tuple[int, dict]:
        if getattr(args, "profile", None) is not None:
            summary.setdefault("profile", args.profile)
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
        return rc, summary

    if candidate_re.search(group):
        summary = {
            "timestamp": iso(now),
            "run_id": run_id,
            "group": group,
            "enabled": enabled,
            "dry_run": effective_dry_run,
            "reason": "scope_violation",
            "scope_violation": "group_matches_candidate_regex",
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
    if not selectors:
        summary = {
            "timestamp": iso(now),
            "run_id": run_id,
            "group": group,
            "enabled": enabled,
            "dry_run": effective_dry_run,
            "reason": "no_matching_selectors",
            "selectors": [],
            "changed": 0,
            "assignments": [],
        }
        return finish(summary, 0)

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
    pre_excluded_leafs: dict[str, str] = {}
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
                if exclude_leaf_re and exclude_leaf_re.search(name):
                    pre_excluded_leafs[name] = "excluded_leaf_regex"
                    continue
                leaf_pool.add(name)

    leaf_scores: dict[str, dict] = {}
    scoring_timed_out = False
    executor = ThreadPoolExecutor(max_workers=max(1, args.max_workers))
    try:
        futures = {
            executor.submit(score_leaf, base, secret, leaf, targets, args.timeout_ms, args.exclude_hk, exclude_leaf_re): leaf
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
    excluded_leafs = {
        leaf: row["excluded_reason"]
        for leaf, row in leaf_scores.items()
        if row.get("excluded_reason")
    }
    excluded_leafs.update(pre_excluded_leafs)
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
            or (args.exclude_hk and is_hk_leaf(current))
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
        change_blockers: list[str] = []
        if not target:
            change_blockers.append("no_eligible_leaf")
        if target == current:
            change_blockers.append("already_best")
        if state_safe_mode:
            change_blockers.append(state_safe_reason)
        if current_invalid and invalid_dwell_blocked:
            change_blockers.append("invalid_dwell_active")
        if not current_invalid and dwell_blocked:
            change_blockers.append("dwell_active")
        if not current_invalid and target and target != current and not clear_better:
            change_blockers.append("below_min_score_improvement")
        if not current_invalid and target and target != current and clear_better and not stable_enough:
            change_blockers.append("waiting_for_consecutive_win")

        would_change = bool(
            target
            and target != current
            and not state_safe_mode
            and (
                (current_invalid and not invalid_dwell_blocked)
                or (clear_better and stable_enough and not dwell_blocked)
            )
        )
        should_change = would_change
        reason = "unchanged"
        http_status = None
        verified_after_put = None
        if not target:
            reason = "no_eligible_leaf"
        elif state_safe_mode:
            reason = state_safe_reason
        elif target == current:
            reason = "already_best"
        elif current_invalid:
            reason = "invalid_dwell_active" if invalid_dwell_blocked else "current_invalid"
        elif dwell_blocked:
            reason = "dwell_active"
        elif not clear_better:
            reason = "below_min_score_improvement"
        elif target and not stable_enough:
            reason = "waiting_for_consecutive_win"
        elif should_change:
            reason = "better_leaf"

        if should_change and fatal_scope_violation:
            should_change = False
            reason = "scope_violation_present"
            if "scope_violation_present" not in change_blockers:
                change_blockers.append("scope_violation_present")
        elif should_change and not effective_dry_run:
            if mutation_lock_busy:
                should_change = False
                reason = "mutation_lock_busy"
                if "mutation_lock_busy" not in change_blockers:
                    change_blockers.append("mutation_lock_busy")
            else:
                fresh_payload = latest_selector_payload(base, secret, selector)
                scope_problem = scope_violation(selector, fresh_payload, target)
                if scope_problem:
                    fatal_scope_violation = True
                    should_change = False
                    reason = "scope_violation"
                    if "scope_violation" not in change_blockers:
                        change_blockers.append("scope_violation")
                elif fresh_payload and fresh_payload.get("now") != current:
                    should_change = False
                    reason = "stale_selector"
                    if "stale_selector" not in change_blockers:
                        change_blockers.append("stale_selector")
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
                    if "put_verify_failed" not in change_blockers:
                        change_blockers.append("put_verify_failed")
            else:
                reason = f"put_http_{http_status}"
                change_blockers.append(reason)
        elif should_change:
            changed += 1

        assignments.append({
            "selector": selector,
            "current_leaf": current,
            "target_leaf": target,
            "changed": should_change,
            "would_change": would_change,
            "change_blockers": change_blockers,
            "reason": reason,
            "score_delta": score_delta,
            "success_rate_delta": success_rate_delta,
            "current_rolling_score": round(current_rolling_score, 4) if current_rolling_score is not None else None,
            "target_rolling_score": round(target_rolling_score, 4) if target_rolling_score is not None else None,
            "current_invalid": current_invalid,
            "consecutive_wins": consecutive_win_count,
            "dwell_blocked": dwell_blocked,
            "invalid_dwell_blocked": invalid_dwell_blocked,
            "effective_min_score_improvement": args.min_score_improvement,
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
        "eligible_leaf_names": sorted(eligible),
        "excluded_leafs": excluded_leafs,
        "changed": changed,
        "assignments": assignments,
    }
    return finish(summary, 2 if fatal_scope_violation else 0, state)


def resolve_profiles(args: argparse.Namespace) -> list[argparse.Namespace]:
    if not args.profiles_file:
        return [profile_runtime(args, None, {})]

    profile_defs = load_profile_defs(args.profiles_file)
    if args.profile:
        if args.profile not in profile_defs:
            raise ValueError(f"profile not found: {args.profile}")
        return [profile_runtime(args, args.profile, profile_defs[args.profile])]
    if args.all_enabled_profiles:
        profiles = [
            profile_runtime(args, name, raw)
            for name, raw in profile_defs.items()
            if bool(raw.get("enabled", False))
        ]
        if not profiles:
            raise ValueError("no enabled profiles found")
        return profiles
    raise ValueError("profiles-file requires --profile NAME or --all-enabled-profiles")


def discover_selectors_for_profile(args: argparse.Namespace, now: dt.datetime, run_id: str) -> tuple[set[str], dict | None]:
    missing = next((name for name in ("MIHOMO_API_BASE", "MIHOMO_API_SECRET", args.group_env_var) if not os.environ.get(name)), None)
    if missing:
        return set(), profile_error_summary(args, now, run_id, "env_missing", {"missing_env_var": missing})
    base = os.environ["MIHOMO_API_BASE"]
    secret = os.environ["MIHOMO_API_SECRET"]
    group = os.environ[args.group_env_var]
    candidate_re = re.compile(args.candidate_regex)
    if candidate_re.search(group):
        return set(), {
            "timestamp": iso(now),
            "run_id": run_id,
            "profile": args.profile,
            "group": group,
            "enabled": os.path.exists(args.sentinel),
            "dry_run": True,
            "reason": "scope_violation",
            "scope_violation": "group_matches_candidate_regex",
            "changed": 0,
            "assignments": [],
        }
    status, group_payload = request_json("GET", base, proxy_path(group), secret, timeout=3)
    if status <= 0 or status >= 400:
        return set(), {
            "timestamp": iso(now),
            "run_id": run_id,
            "profile": args.profile,
            "group": group,
            "enabled": os.path.exists(args.sentinel),
            "dry_run": True,
            "reason": "api_unreachable",
            "http_status": status,
            "message": group_payload.get("message") if isinstance(group_payload, dict) else None,
            "changed": 0,
            "assignments": [],
        }
    return {
        item for item in (group_payload.get("all") or [])
        if candidate_re.search(item or "")
    }, None


def profile_scope_conflict(args_list: list[argparse.Namespace], now: dt.datetime, run_id: str) -> dict | None:
    seen: dict[str, str] = {}
    conflicts: list[dict] = []
    for args in args_list:
        selectors, error = discover_selectors_for_profile(args, now, run_id)
        if error:
            return error
        for selector in selectors:
            if selector in seen:
                conflicts.append({"selector": selector, "profiles": sorted({seen[selector], args.profile or "default"})})
            else:
                seen[selector] = args.profile or "default"
    if not conflicts:
        return None
    return {
        "timestamp": iso(now),
        "run_id": run_id,
        "profiles": [args.profile for args in args_list],
        "reason": "profile_scope_conflict",
        "changed": 0,
        "conflicts": conflicts,
        "assignments": [],
    }


def main() -> int:
    try:
        args = parse_args()
        profiles = resolve_profiles(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"leaf-optimizer: {exc}", file=sys.stderr)
        return 2

    now = parse_ts(args.now) if args.now else utc_now()
    run_id = str(uuid4())
    if len(profiles) > 1:
        conflict = profile_scope_conflict(profiles, now, run_id)
        if conflict:
            print(json.dumps(conflict, ensure_ascii=False))
            return 2

    summaries: list[dict] = []
    rc = 0
    for profile_args in profiles:
        profile_rc, summary = run_profile(profile_args, now, run_id if len(profiles) == 1 else str(uuid4()))
        summaries.append(summary)
        rc = max(rc, profile_rc)
    if len(summaries) == 1:
        return rc
    print(json.dumps({
        "timestamp": iso(now),
        "run_id": run_id,
        "reason": "completed" if rc == 0 else "profile_error",
        "changed": sum(int(item.get("changed", 0)) for item in summaries),
        "profile_summaries": summaries,
    }, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        signal.alarm(0)
