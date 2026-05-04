#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import re
from uuid import uuid4


DEFAULT_TARGETS = [
    "https://chatgpt.com/",
    "https://claude.com/",
    "https://api.telegram.org/",
]


def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def proxy_path(name: str) -> str:
    return "/proxies/" + urllib.parse.quote(name, safe="")


def resolve_chain(base: str, secret: str, group: str, candidate: str) -> tuple[list[str], str, str]:
    chain = [group, candidate]
    seen = {group, candidate}
    leaf = candidate
    candidate_type = "unknown"
    while leaf:
        status, payload = request_json("GET", base, proxy_path(leaf), secret, timeout=3)
        if status >= 400:
            break
        candidate_type = payload.get("type") or candidate_type
        nxt = payload.get("now") or ""
        if not nxt or nxt in seen:
            break
        chain.append(nxt)
        seen.add(nxt)
        leaf = nxt
    return chain, leaf, candidate_type


def active_quarantine(path: str, now: dt.datetime) -> dict[str, str]:
    active: dict[str, str] = {}
    if not os.path.exists(path):
        return active
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            candidate = row.get("candidate")
            until = row.get("quarantine_until")
            if candidate and until and parse_ts(until) > now:
                active[candidate] = until
    return active


def atomic_write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--score-log", default=os.path.expanduser("~/Library/Logs/k2b-router-watchdog/node-score.jsonl"))
    parser.add_argument("--top3-file", default=os.path.expanduser("~/Library/Application Support/k2b-router-watchdog/node-top3.json"))
    parser.add_argument("--now", default=None)
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument("--targets", default=",".join(DEFAULT_TARGETS))
    parser.add_argument("--candidate-regex", default=os.environ.get("K2B_AUTOSWITCH_CANDIDATE_REGEX", r"^♻️ 手动切换"))
    args = parser.parse_args()

    base = os.environ["MIHOMO_API_BASE"]
    secret = os.environ["MIHOMO_API_SECRET"]
    group = os.environ["MIHOMO_OPENAI_GROUP"]
    now = parse_ts(args.now) if args.now else utc_now()
    run_id = str(uuid4())
    targets = [target.strip() for target in args.targets.split(",") if target.strip()]
    quarantine_until = iso(now + dt.timedelta(hours=24))
    active = active_quarantine(args.score_log, now)

    status, group_payload = request_json("GET", base, proxy_path(group), secret, timeout=3)
    if status >= 400:
        raise SystemExit(f"could not fetch OpenAI group {group}: HTTP {status}")
    candidates = group_payload.get("all") or []
    if not candidates and group_payload.get("now"):
        candidates = [group_payload["now"]]

    os.makedirs(os.path.dirname(args.score_log), exist_ok=True)
    rows: list[dict] = []
    with open(args.score_log, "a", encoding="utf-8") as out:
        for candidate in candidates:
            chain, leaf, candidate_type = resolve_chain(base, secret, group, candidate)
            target_results: dict[str, dict] = {}
            ok_count = 0
            delays: list[int] = []
            for target in targets:
                delay_path = (
                    proxy_path(candidate)
                    + "/delay?"
                    + urllib.parse.urlencode({"timeout": str(args.timeout_ms), "url": target})
                )
                code, payload = request_json("GET", base, delay_path, secret, timeout=(args.timeout_ms / 1000) + 2)
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
            success_rate = ok_count / len(targets) if targets else 0.0
            avg_delay = sum(delays) / len(delays) if delays else None
            score = success_rate
            if avg_delay is not None:
                score = max(0.0, success_rate - min(avg_delay / 10000.0, 0.25))
            failed_half_or_more = (1.0 - success_rate) >= 0.5
            q_until = active.get(candidate)
            if failed_half_or_more:
                q_until = quarantine_until
            quarantined = bool(q_until and parse_ts(q_until) > now)
            row = {
                "timestamp": iso(now),
                "run_id": run_id,
                "group": group,
                "candidate": candidate,
                "candidate_type": candidate_type,
                "resolved_leaf": leaf,
                "selector_chain": chain,
                "targets": target_results,
                "success_rate": round(success_rate, 4),
                "score": round(score, 4),
                "quarantined": quarantined,
                "quarantine_until": q_until,
            }
            rows.append(row)
            out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    candidate_re = re.compile(args.candidate_regex)
    top3 = sorted(
        [
            row for row in rows
            if not row["quarantined"]
            and row["success_rate"] >= 0.8
            and candidate_re.search(row["candidate"])
        ],
        key=lambda row: (row["score"], row["success_rate"]),
        reverse=True,
    )[:3]
    atomic_write_json(args.top3_file, {"timestamp": iso(now), "group": group, "top3": top3})
    print(json.dumps({"timestamp": iso(now), "scored": len(rows), "top3": [row["candidate"] for row in top3]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
