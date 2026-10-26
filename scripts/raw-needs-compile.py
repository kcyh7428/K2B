#!/usr/bin/env python3
"""Inspect immutable raw sources and receipt-backed digestion state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from raw_source_status import classify
import knowledge_integration as integration

FRONTMATTER_READ_LIMIT_BYTES = 256 * 1024
MAX_RAW_BYTES = 2 * 1024 * 1024
NEEDS_COMPILE_VALUES = {"", "false", "no", "0", "null", "none"}


def vault_root() -> Path:
    return Path(os.environ.get("K2B_VAULT_ROOT") or os.environ.get("K2B_VAULT_PATH") or
                "~/Projects/K2B-Vault").expanduser().resolve()


def compiled_value(path: Path) -> str | None:
    try:
        with path.open("rb") as source:
            data = source.read(FRONTMATTER_READ_LIMIT_BYTES)
        return classify(path.as_posix(), data.decode("utf-8"))["compiled"]
    except (OSError, UnicodeError):
        return None


def needs_compile_value(value: str | None) -> bool:
    return value is None or value in NEEDS_COMPILE_VALUES


def read_receipts(state_root: Path) -> list[dict]:
    folder = integration._default_receipt_dir(state_root)
    if folder.is_symlink() or any(p.is_symlink() for p in folder.parents):
        raise RuntimeError("receipt directory crosses a symlink")
    if not folder.exists():
        return []
    receipts, budget = [], 16 * 1024 * 1024
    with os.scandir(folder) as entries:
        for count, entry in enumerate(entries):
            if count >= 1000:
                raise RuntimeError("receipt inventory exceeds the bounded scan")
            if not entry.name.endswith(".json"):
                continue
            if budget <= 0:
                raise RuntimeError("receipt bytes exceed the bounded scan")
            data = integration._safe_read(Path(entry.path), min(budget, 256 * 1024))
            budget -= len(data)
            receipt = json.loads(data)
            if integration._validate_known_receipt(receipt) is None:
                raise RuntimeError(f"receipt binding is invalid: {entry.name!r}")
            if entry.name != receipt["operation_id"] + ".json":
                raise RuntimeError(f"receipt filename binding is invalid: {entry.name!r}")
            receipts.append(receipt)
    return receipts


def iter_raw_notes(root: Path, min_age_hours: float, *, receipts=None, include_all=False):
    raw = root / "raw"
    if raw.is_symlink():
        raise RuntimeError("raw source root is a symlink")
    if not raw.is_dir():
        return []
    cutoff = time.time() - min_age_hours * 3600
    items = []
    for dirpath, dirnames, filenames in os.walk(raw, followlinks=False):
        directory = Path(dirpath)
        dirnames[:] = sorted(name for name in dirnames if not name.startswith(".")
            and not (directory / name).is_symlink())
        for name in sorted(filenames):
            path = directory / name
            if (name.startswith(".") or not name.endswith(".md") or name == "index.md"
                    or ".sync-conflict-" in name or path.is_symlink()):
                continue
            try:
                info = path.stat()
                if info.st_mtime > cutoff:
                    continue
                with path.open("rb") as source:
                    data = source.read(MAX_RAW_BYTES + 1)
            except OSError as exc:
                raise RuntimeError("raw source inventory is unavailable") from exc
            rel = path.relative_to(root).as_posix()
            oversized = len(data) > MAX_RAW_BYTES
            metadata = classify(rel, data[:MAX_RAW_BYTES].decode("utf-8", errors="replace"))
            digest = hashlib.sha256(data).hexdigest() if not oversized else None
            state, reason = "needs_review", metadata["reason"]
            if oversized:
                state, reason = "blocked", "source exceeds the bounded read; integration is unmeasured"
            elif metadata["classification"] == "technical_record":
                state, reason = "blocked", "technical record, outside the human digestion queue"
            elif metadata["metadata_state"] == "unreadable":
                state, reason = "blocked", "unreadable source metadata"
            else:
                descriptor = {"path": rel, "sha256": digest,
                    "classification": metadata["classification"],
                    "legacy_disposition": metadata["legacy_disposition"]}
                row = integration.build_queue({"schema_version": 1, "records": {}},
                    receipts or [], raw_sources=[descriptor])[0]
                state = row["state"]
                reason = row["reason"] or reason
                if state == "needs_review" and not needs_compile_value(metadata["compiled"]):
                    state, reason = "blocked", "legacy compilation state retained; receipt-backed integration unmeasured"
            if not include_all and (metadata["classification"] == "technical_record"
                    or metadata["legacy_disposition"] is not None
                    or not needs_compile_value(metadata["compiled"])
                    or state in {"integrated", "retained_leaf"}):
                continue
            items.append({"path": rel, "compiled": metadata["compiled"] if metadata["compiled"] is not None else "missing",
                "age_hours": round((time.time() - info.st_mtime) / 3600, 1),
                "action": "/compile " + rel if state == "needs_review" else "manual review", "state": state, "reason": reason,
                "classification": metadata["classification"], "metadata_state": metadata["metadata_state"],
                "source_sha256": digest, "integration_scope": "local_receipts",
                "legacy_completed": metadata["legacy_completed"]})
    return sorted(items, key=lambda row: (-row["age_hours"], row["path"]))


def render_markdown(items):
    return "\n".join(f"- `{row['path']}`: {row['state']}; {row['reason']}; `{row['action']}`"
                     for row in items)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--min-age-hours", type=float, default=24.0)
    parser.add_argument("--all", action="store_true", help="include technical and legacy dispositions for inspection")
    parser.add_argument("--state-root", type=Path, default=None)
    args = parser.parse_args()
    if args.max < 0 or args.min_age_hours < 0:
        parser.error("max and minimum age must be nonnegative")
    root = vault_root()
    if not root.is_dir():
        print("raw-needs-compile: configured vault unavailable", file=sys.stderr)
        return 2
    state = args.state_root or Path(os.environ.get("K2B_LOCAL_STATE",
        str(Path.home() / ".local/state/k2b"))) / "digestion"
    try:
        receipts = read_receipts(state)
        items = iter_raw_notes(root, args.min_age_hours, receipts=receipts, include_all=args.all)[:args.max]
    except (OSError, ValueError, RuntimeError, integration.IntegrationError, RecursionError) as exc:
        print("raw-needs-compile: source or receipt inventory unavailable; no completeness claim; " + str(exc), file=sys.stderr)
        return 3
    if args.format == "json":
        print(json.dumps(items, ensure_ascii=False, sort_keys=True))
    elif items:
        print(render_markdown(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
