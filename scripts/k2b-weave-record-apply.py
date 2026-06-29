#!/usr/bin/env python3
"""Record an auto-apply / apply outcome for one crosslink pair in the ledger.

Pair-level UPSERT with retry accumulation. The rest of k2b-weave mutates ledger
rows in place (status changes), and the old append helper reset retry_count to 0
on every new row -- so a naive "append a fresh apply-failed row each run" never
reaches the permanent-failure ceiling (Codex plan-review finding R2). This helper
reads prior retry state ACROSS all rows for the pair, applies the increment rule,
and updates the most-recent matching row in place (or appends one if the pair is
new). Exactly one evolving row per pair under auto-apply.

Outcomes:
  applied             -> status "applied"                  (retry unchanged)
  stale               -> status "stale-renamed"            (retry unchanged)
  held                -> status "held-low-confidence"      (retry unchanged)
  failed-concurrency  -> status "apply-failed"             (retry unchanged; transient)
  failed-hard         -> retry += 1; "apply-failed-permanent" if retry >= max-retry
                         else "apply-failed"

Writes atomically (tmp + rename + fsync). Prints the FINAL status to stdout so the
caller can detect a permanent failure and raise an alert.

Exit codes: 0 success, 1 error (bad args / unreadable ledger).
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone


def read_rows(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # Tolerate a torn trailing line; recover_ledger in the shell owns
                # the canonical repair. Skip here rather than abort.
                continue
    return rows


def atomic_write_rows(path, rows):
    dirname = os.path.dirname(path) or "."
    os.makedirs(dirname, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".weave-ledger.", dir=dirname)
    try:
        with os.fdopen(fd, "w") as f:
            for r in rows:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, path)
        dir_fd = os.open(dirname, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def compute(outcome, prior_retry, max_retry):
    """Return (status, retry_count) for the given outcome."""
    if outcome == "applied":
        return "applied", prior_retry
    if outcome == "stale":
        return "stale-renamed", prior_retry
    if outcome == "held":
        return "held-low-confidence", prior_retry
    if outcome == "failed-concurrency":
        return "apply-failed", prior_retry
    if outcome == "failed-hard":
        retry = prior_retry + 1
        if retry >= max_retry:
            return "apply-failed-permanent", retry
        return "apply-failed", retry
    if outcome == "deferred":
        return "deferred", prior_retry
    if outcome == "rejected":
        retry = prior_retry + 1
        if retry >= max_retry:
            return "permanently-rejected", retry
        return "rejected", retry
    raise ValueError(f"unknown outcome: {outcome}")


def main(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--ledger", required=True)
    p.add_argument("--from", dest="from_slug", required=True)
    p.add_argument("--to", dest="to_slug", required=True)
    p.add_argument("--outcome", required=True)
    p.add_argument("--max-retry", type=int, default=3)
    p.add_argument("--run-id", default="")
    p.add_argument("--date", default="")
    p.add_argument("--from-path", default="")
    p.add_argument("--to-path", default="")
    p.add_argument("--confidence", default="0")
    p.add_argument("--rationale", default="")
    p.add_argument("--evidence", default="")
    args = p.parse_args(argv)

    try:
        confidence = float(args.confidence)
    except ValueError:
        confidence = 0.0

    rows = read_rows(args.ledger)

    # Find rows for this pair; accumulate prior retry across all of them.
    pair_idx = [
        i for i, r in enumerate(rows)
        if r.get("from_slug") == args.from_slug and r.get("to_slug") == args.to_slug
    ]
    prior_retry = 0
    for i in pair_idx:
        try:
            prior_retry = max(prior_retry, int(rows[i].get("retry_count", 0) or 0))
        except (TypeError, ValueError):
            pass

    try:
        status, retry = compute(args.outcome, prior_retry, args.max_retry)
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 1

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # rejected outcomes stamp rejected_at (drives the 30-day re-proposal TTL).
    rejected_at = now if args.outcome == "rejected" else None

    if pair_idx:
        # Upsert + COLLAPSE: update the most-recent matching row and drop any older
        # duplicate rows for the same pair, so there is exactly one authoritative row
        # per pair. Otherwise a stale historical row (pending/deferred/rejected-in-TTL)
        # could keep suppressing the pair from re-proposal and block an apply-failed
        # retry from ever reaching apply-failed-permanent (Codex review finding).
        last = pair_idx[-1]
        row = rows[last]
        row["status"] = status
        row["retry_count"] = retry
        row["updated_at"] = now
        if rejected_at is not None:
            row["rejected_at"] = rejected_at
        if args.run_id:
            row["run_id"] = args.run_id
        drop = set(pair_idx) - {last}
        rows = [r for j, r in enumerate(rows) if j not in drop]
    else:
        # New pair: append a full row.
        rows.append({
            "date": args.date or now[:10],
            "run_id": args.run_id,
            "from_path": args.from_path,
            "to_path": args.to_path,
            "from_slug": args.from_slug,
            "to_slug": args.to_slug,
            "tier": "MEDIUM",
            "confidence": confidence,
            "rationale": args.rationale,
            "evidence_span": args.evidence,
            "status": status,
            "retry_count": retry,
            "rejected_at": rejected_at,
            "updated_at": now,
        })

    atomic_write_rows(args.ledger, rows)
    sys.stdout.write(status + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
