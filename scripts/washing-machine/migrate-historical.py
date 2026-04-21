#!/usr/bin/env python3
"""Washing Machine Ship 1 Commit 1b -- one-time historical backfill.

Writes the 2026-04-01 Dr. Lo Hak Keung business-card record to
`wiki/context/shelves/semantic.md` using the Commit 1 shelf writer.

Sources:
  <vault>/Daily/2025-04-11.md                  (mis-dated Daily note,
                                                 authoritative for Tel/WhatsApp/address)
  <vault>/wiki/context/memories/telegram-*.jsonl (Apr 1 image-capture turns,
                                                 sanity cross-reference)

Strategy:
  Hardcoded extractor. The April 1 incident has a single, inspected shape
  ("Dr. Lo Hak Keung" + "Tel: 2830 3709" + "WhatsApp: 9861 9017" + address).
  Running a general classifier here would invert the bootstrap order; the
  classifier does not exist until Commit 3 and the migration is a one-time
  fix for a known record, not a general-purpose tool.

Idempotency:
  Each row stores `source_hash:<sha256[:16]>` where the hash covers
  (daily_note_path + marker + tel). Re-running on the same state sees the
  existing hash in the shelf and emits an idempotent-skip log entry.

Exit codes:
  0 -- wrote the row, idempotent skip, missing source, or present-but-no-Dr.Lo
  1 -- unexpected runtime failure (shelf-writer crash, lock timeout, etc.)
  2 -- authoritative source present but unreadable (bad UTF-8, I/O error).
        Treated as hard failure on the principle that a corrupt migration
        target is a data-loss signal, not graceful degradation -- callers
        that key off exit status must see it.

Plan: plans/2026-04-21_washing-machine-ship-1.md Commit 1b.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import errno
import glob
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
SHELF_WRITER = SCRIPT_DIR / "shelf-writer.sh"

# Dr. Lo record constants -- the migration is hardcoded to this one known
# incident, so these live here, not in a config. Do not reuse for other
# backfills; write a new migration script.
MARKER = "Dr. Lo Hak Keung"
NAME_EN = "Dr. Lo Hak Keung"
NAME_ZH = "羅克強醫生"
ROLE = "Urology"
ORGANIZATION = "St. Paul's Hospital"
CAPTURE_DATE = "2026-04-01"  # real capture date, not the 2025-04-11 mis-date
SHELF = "semantic"
ROW_TYPE = "contact"
SLUG = "person_Dr-Lo-Hak-Keung"

TEL_RE = re.compile(r"Tel:\s*(\d{4}\s*\d{4})", re.IGNORECASE)
WHATSAPP_RE = re.compile(r"WhatsApp:\s*(\d{4}\s*\d{4})", re.IGNORECASE)
ADDRESS_RE = re.compile(r"Address:\s*(.+)", re.IGNORECASE)

# Upper bound (in lines) between the Dr. Lo marker and each extracted
# field. Prevents an unrelated Tel/WhatsApp/Address elsewhere in a
# multi-contact daily note from silently contaminating the record.
MARKER_PROXIMITY_LINES = 15


def log_entry(log_path: Path, message: str) -> None:
    """Append a timestamped markdown bullet to the migration log. Raises
    OSError on filesystem failures -- callers that must not let logging
    flip a primary outcome should invoke via `safe_log()` instead."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header_needed = not log_path.exists()
    with log_path.open("a", encoding="utf-8") as f:
        if header_needed:
            f.write(
                "---\ntags: [context, log, washing-machine, migration]\n"
                "type: log\norigin: k2b-migrate-historical\n"
                'up: "[[index]]"\n---\n\n'
                "# Washing Machine Historical Migration Log\n\n"
                "Append-only record of `scripts/washing-machine/migrate-historical.py` "
                "runs. Line format: `- <ISO-UTC-ts>  <outcome>  <detail>`.\n\n"
                "## Log\n\n"
            )
        f.write(f"- {ts}  {message}\n")


def safe_log(log_path: Path, message: str) -> None:
    """Best-effort `log_entry`. Swallows OSError so that a log-write
    failure cannot flip the primary process outcome. Any failure is
    surfaced via stderr so it is still observable in pipelines and
    post-mortem. The primary outcome signal is always the exit code
    plus (for hard failures) the explicit stderr marker -- the log is
    a post-mortem aid, not a contract."""
    try:
        log_entry(log_path, message)
    except OSError as e:
        print(
            f"migrate-historical: failed to append to log {log_path}: {e}",
            file=sys.stderr,
        )


class MarkerNotFound(Exception):
    """No Dr. Lo marker in the source -- the note is not our concern."""


class IncompleteBlock(Exception):
    """Marker present but required fields missing from the forward proximity
    window. This is a hard-failure signal: the authoritative source was
    found but the record cannot be recovered, and silent success would
    mask data loss."""


def extract_dr_lo(daily_text: str) -> dict:
    """Return extracted fields. Raises:
      MarkerNotFound -- no Dr. Lo marker in the text (clean-skip case).
      IncompleteBlock -- marker found, but Tel/WhatsApp/Address are not
                         all present within MARKER_PROXIMITY_LINES *below*
                         the marker. Forward-only window so an unrelated
                         contact block ABOVE the marker cannot contaminate
                         Dr. Lo's record.
    """
    lines = daily_text.splitlines()
    marker_indices = [i for i, line in enumerate(lines) if MARKER in line]
    if not marker_indices:
        raise MarkerNotFound()

    # Try every marker occurrence in document order. The first one whose
    # forward window contains all three required fields wins. This
    # tolerates a "Focus Today" mention above a "Key Activities" block
    # where the first mention has no contact info but the second one does.
    last_incomplete_detail = None
    for marker_line_idx in marker_indices:
        end = min(len(lines), marker_line_idx + MARKER_PROXIMITY_LINES + 1)
        block = "\n".join(lines[marker_line_idx:end])

        tel_m = TEL_RE.search(block)
        whatsapp_m = WHATSAPP_RE.search(block)
        address_m = ADDRESS_RE.search(block)
        if tel_m and whatsapp_m and address_m:
            tel = re.sub(r"\s+", " ", tel_m.group(1).strip())
            whatsapp = re.sub(r"\s+", " ", whatsapp_m.group(1).strip())
            address = address_m.group(1).strip().rstrip(".,")
            return {
                "tel": tel,
                "whatsapp": whatsapp,
                "address": address,
            }
        last_incomplete_detail = (
            f"marker at line {marker_line_idx + 1}: "
            f"tel={bool(tel_m)}, whatsapp={bool(whatsapp_m)}, address={bool(address_m)}"
        )

    raise IncompleteBlock(
        f"all {len(marker_indices)} marker occurrence(s) lacked required fields "
        f"within {MARKER_PROXIMITY_LINES} lines below; last was {last_incomplete_detail}"
    )


def compute_source_hash(extracted: dict) -> str:
    """Short sha256 over stable record content -- NOT the filesystem path.

    Content-based so the hash matches after a vault move, a machine-to-
    machine re-run, or any other change that rewrites the absolute path
    but not the extracted Dr. Lo data. Fields participating in the hash
    are exactly those that define the Dr. Lo incident record.
    """
    payload = (
        f"{MARKER}|{CAPTURE_DATE}|"
        f"{extracted['tel']}|{extracted['whatsapp']}|{extracted['address']}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@contextlib.contextmanager
def migration_lock(lock_path: Path, timeout_s: float = 10.0) -> Iterator[None]:
    """Mkdir-based advisory lock serializing migration runs so the
    hash-check / shelf-write pair is atomic. Mirrors the flock-with-mkdir-
    fallback pattern in scripts/motivations-helper.sh / shelf-writer.sh,
    but mkdir-only is sufficient here because the migration is called from
    Python and is a one-time bootstrap -- no cross-runtime contention.
    """
    lock_dir = lock_path.with_suffix(lock_path.suffix + ".d")
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            lock_dir.mkdir(parents=False, exist_ok=False)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"migrate-historical: could not acquire {lock_dir} after {timeout_s}s"
                )
            time.sleep(0.05)
        except OSError as e:
            if e.errno == errno.ENOENT:
                lock_dir.parent.mkdir(parents=True, exist_ok=True)
                continue
            raise
    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except OSError:
            pass


def shelf_already_has_hash(shelves_dir: Path, shelf: str, source_hash: str) -> bool:
    shelf_file = shelves_dir / f"{shelf}.md"
    if not shelf_file.exists():
        return False
    needle = f"source_hash:{source_hash}"
    with shelf_file.open("r", encoding="utf-8") as f:
        for line in f:
            if needle in line:
                return True
    return False


def shelf_already_has_record(shelves_dir: Path, shelf: str) -> bool:
    """Stable-identity dedupe: slug + CAPTURE_DATE. Detects existing rows
    whose content has drifted (e.g. Daily note was corrected so the
    source_hash moved), preventing a second append under the same logical
    identity. The migration is a one-time fix for a specific incident;
    a second run must never duplicate the Dr. Lo row even if the source
    text changed."""
    shelf_file = shelves_dir / f"{shelf}.md"
    if not shelf_file.exists():
        return False
    date_marker = f"- {CAPTURE_DATE} |"
    slug_marker = f"| {SLUG} |"
    with shelf_file.open("r", encoding="utf-8") as f:
        for line in f:
            if line.startswith(date_marker) and slug_marker in line:
                return True
    return False


def count_jsonl_matches(glob_pattern: str) -> int:
    """Return the number of JSONL entries across matched files that mention
    the Dr. Lo marker or the Chinese name. Zero is acceptable (Daily note
    is the authoritative source); we just log the count for traceability."""
    total = 0
    for path in glob.glob(glob_pattern):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if MARKER in line or NAME_ZH in line:
                        total += 1
        except (OSError, UnicodeDecodeError):
            continue
    return total


def call_shelf_writer(
    shelves_dir: Path,
    lock_dir: Optional[str],
    attrs: dict,
) -> None:
    """Invoke the Commit 1 shelf writer. Raises CalledProcessError on failure."""
    cmd = [
        str(SHELF_WRITER),
        "--shelf", SHELF,
        "--date", CAPTURE_DATE,
        "--type", ROW_TYPE,
        "--slug", SLUG,
    ]
    for k, v in attrs.items():
        cmd.extend(["--attr", f"{k}:{v}"])
    env = os.environ.copy()
    env["K2B_SHELVES_DIR"] = str(shelves_dir)
    if lock_dir:
        env["K2B_SHELF_LOCK_DIR"] = lock_dir
    subprocess.run(cmd, env=env, check=True)


def run_migration(
    daily_note: Path,
    jsonl_glob: str,
    shelves_dir: Path,
    log_path: Path,
    lock_dir: Optional[str],
    dry_run: bool,
) -> int:
    # Every log_entry call below goes through safe_log so that a
    # filesystem failure during append never flips the primary outcome.
    # The outcome signal is always (exit code + explicit stderr for hard
    # errors); the log is a post-mortem aid.
    if not daily_note.exists():
        safe_log(log_path, f"skip  daily note missing at {daily_note}")
        return 0

    try:
        daily_text = daily_note.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        # Present-but-unreadable is a hard failure. Missing is a clean
        # skip above; malformed is different -- the target exists and is
        # corrupt, which means the Dr. Lo record is effectively
        # unrecoverable. Surface it via exit 2 so `/ship`-style automation
        # can alert instead of treating silent-no-op as success. The
        # stderr marker "is unreadable" is contract -- tests assert it.
        print(
            f"migrate-historical: {daily_note} is unreadable ({type(e).__name__}); "
            f"refusing to report success",
            file=sys.stderr,
        )
        safe_log(
            log_path,
            f"error  malformed daily note {daily_note}: {type(e).__name__}",
        )
        return 2

    try:
        extracted = extract_dr_lo(daily_text)
    except MarkerNotFound:
        safe_log(log_path, f"skip  Dr. Lo marker not found in {daily_note}")
        return 0
    except IncompleteBlock as e:
        # Marker present but record cannot be reconstructed. For a
        # one-time authoritative backfill, this is a hard failure --
        # identical semantic to unreadable-source above. Silent exit 0
        # would mask real data loss.
        print(
            f"migrate-historical: {daily_note} has the Dr. Lo marker but the record "
            f"is incomplete; refusing to report success. Detail: {e}",
            file=sys.stderr,
        )
        safe_log(log_path, f"error  incomplete Dr. Lo block in {daily_note}: {e}")
        return 2

    jsonl_hits = count_jsonl_matches(jsonl_glob)
    source_hash = compute_source_hash(extracted)

    # Serialize the hash-check + write through a migration-level lock so
    # parallel invocations cannot both observe "hash absent" and each
    # append a duplicate row. shelf-writer.sh has its own per-shelf lock
    # that handles concurrency between arbitrary shelf writers, but that
    # lock is released between our check and our call; the migration
    # lock closes that gap.
    lock_root = Path(lock_dir) if lock_dir else Path("/tmp")
    mig_lock = lock_root / "k2b-migrate-historical.lock"
    with migration_lock(mig_lock):
        # Two-layer dedupe, stricter first:
        #   1. slug + CAPTURE_DATE identity -- catches the case where
        #      the Daily note is corrected later and produces a
        #      different content hash; we still must not duplicate.
        #   2. source_hash content -- catches the common case where
        #      nothing has changed (fast path, matches prior runs).
        if shelf_already_has_record(shelves_dir, SHELF):
            safe_log(
                log_path,
                f"skip  idempotent (slug={SLUG}+date={CAPTURE_DATE} already in {SHELF}.md)",
            )
            return 0
        if shelf_already_has_hash(shelves_dir, SHELF, source_hash):
            safe_log(
                log_path,
                f"skip  idempotent (source_hash={source_hash} already in {SHELF}.md)",
            )
            return 0

        attrs = {
            "name": NAME_EN,
            "name_zh": NAME_ZH,
            "tel": extracted["tel"],
            "whatsapp": extracted["whatsapp"],
            "role": ROLE,
            "organization": ORGANIZATION,
            "address": extracted["address"],
            "source": f"daily-{daily_note.stem}",
            "jsonl_refs": str(jsonl_hits),
            "source_hash": source_hash,
        }

        if dry_run:
            safe_log(
                log_path,
                f"dry-run  would write source_hash={source_hash} "
                f"(jsonl_refs={jsonl_hits})",
            )
            return 0

        try:
            call_shelf_writer(shelves_dir, lock_dir, attrs)
        except subprocess.CalledProcessError as e:
            safe_log(
                log_path,
                f"error  shelf-writer exited {e.returncode} for source_hash={source_hash}",
            )
            return 1

        safe_log(
            log_path,
            f"wrote  source_hash={source_hash} (jsonl_refs={jsonl_hits}, date={CAPTURE_DATE})",
        )
        return 0


def main(argv: Optional[list[str]] = None) -> int:
    vault_default = os.environ.get(
        "K2B_VAULT", str(Path.home() / "Projects" / "K2B-Vault")
    )
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--vault",
        default=vault_default,
        help="K2B vault root (default: $K2B_VAULT or ~/Projects/K2B-Vault)",
    )
    p.add_argument(
        "--daily-note",
        default=None,
        help="Path to the mis-dated Daily note (default: <vault>/Daily/2025-04-11.md)",
    )
    p.add_argument(
        "--jsonl-glob",
        default=None,
        help="Glob for telegram JSONL files (default: "
        "<vault>/wiki/context/memories/telegram-*.jsonl)",
    )
    p.add_argument(
        "--shelves-dir",
        default=None,
        help="Override shelves directory (default: $K2B_SHELVES_DIR "
        "or <vault>/wiki/context/shelves)",
    )
    p.add_argument(
        "--log-path",
        default=None,
        help="Migration log path (default: <vault>/wiki/context/"
        "washing-machine-migration.log.md)",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    vault = Path(args.vault)
    daily_note = Path(args.daily_note) if args.daily_note else vault / "Daily" / "2025-04-11.md"
    jsonl_glob = (
        args.jsonl_glob
        if args.jsonl_glob
        else str(vault / "wiki" / "context" / "memories" / "telegram-*.jsonl")
    )
    shelves_dir = (
        Path(args.shelves_dir)
        if args.shelves_dir
        else Path(os.environ.get("K2B_SHELVES_DIR", vault / "wiki" / "context" / "shelves"))
    )
    log_path = (
        Path(args.log_path)
        if args.log_path
        else vault / "wiki" / "context" / "washing-machine-migration.log.md"
    )
    lock_dir = os.environ.get("K2B_SHELF_LOCK_DIR")

    return run_migration(
        daily_note=daily_note,
        jsonl_glob=jsonl_glob,
        shelves_dir=shelves_dir,
        log_path=log_path,
        lock_dir=lock_dir,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
