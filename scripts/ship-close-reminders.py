#!/usr/bin/env python3
"""ship-close-reminders.py -- auto-close open reminders that name a just-shipped feature.

Code-enforced implementation of /ship step 14 category 5
(L-2026-03-25-002 "vault-note sweep", reinforced L-2026-05-30-001).

When a feature ships, any OPEN reminder line in reminders.md whose text contains the
feature slug is:
  1. flipped `[open]` -> `[closed]`
  2. annotated with a `(closed YYYY-MM-DD via /ship <matched-slug>)` marker
  3. moved from the `## Open` section to the top of the `## Closed` section

Why a script and not a checklist step: this exact close was skipped on the
2026-05-30 orchestrator Ship 1a (commit a439d5d), so the stale reminder reached
`/plate` and Keith had to point it out. A grep-and-flip checklist line in a learning
file is not enforcement; a script invoked by /ship is.

Matching semantics (precise by design -- this is what kills the false-positive class):
  - Each slug is matched as a BOUNDED token: a literal case-insensitive occurrence
    that is NOT flanked by other slug characters (letters, digits, `_`, `-`).
    No auto-stripping of `feature_`, no auto-prefixing. The caller controls specificity.
  - Bounded matching kills two false-positive classes at once: loose-word overlap
    (a reminder that only says "orchestrator") and prefix collision (shipping
    feature_foo must NOT close feature_foo-v2 or feature_foo10).
  - /ship Step 14 always passes the full `feature_<slug>` form (the feature-note
    basename from Step 2).
  - With multiple slugs, each closed line is annotated with the SPECIFIC slug that
    matched it, so the audit trail in reminders.md stays accurate.

Concurrency: the read -> transform -> atomic-replace is wrapped in an advisory
`fcntl.flock` on a sibling lock file, so two concurrent /ship runs (or any other
writer that honours the same lock) cannot clobber each other's closes. Manual Obsidian
edits do not take this lock -- this guards the controllable risk (concurrent script
invocations), which is the silent-drop class to worry about here.

Atomic: writes a temp file and os.replace()s it over the original.
Idempotent: a slug with no matching open line is a no-op.

Usage:
  ship-close-reminders.py <feature-slug> [<feature-slug> ...]
      [--date YYYY-MM-DD] [--dry-run] [--file PATH]

Exit codes:
  0  success (including no-match no-op, and dry-run)
  2  reminders.md not found
  3  bad usage
"""

import argparse
import datetime
import fcntl
import os
import re
import sys

# Characters that make up a feature slug. A slug only "matches" a reminder when it is
# NOT flanked by these on either side, so feature_foo does not close feature_foo-v2.
SLUG_CHARS = r"A-Za-z0-9_-"

DEFAULT_FILE = os.path.expanduser(
    os.path.expanduser(os.environ.get("K2B_VAULT_PATH", "~/Projects/K2B-Vault"))
    + "/wiki/context/reminders.md"
)


def first_matching_slug(line, slugs):
    """Return the first slug that appears in the line as a BOUNDED token, else None.

    Bounded = the slug is not immediately preceded or followed by another slug
    character (letters, digits, `_`, `-`). Case-insensitive. Order follows argv so
    multi-slug attribution is deterministic.
    """
    for s in slugs:
        if not s:
            continue
        pat = r"(?<![{c}]){s}(?![{c}])".format(c=SLUG_CHARS, s=re.escape(s))
        if re.search(pat, line, re.IGNORECASE):
            return s
    return None


def transform_open_to_closed(line, slug_label, date_str):
    """Flip the [open] marker to [closed] and append a closed-date annotation.

    Preserves everything after the marker verbatim and appends the closed marker at
    the end of the line so the existing `(added ...)` trailer stays intact.
    """
    flipped = line.replace("[open]", "[closed]", 1).rstrip("\n")
    return flipped + " (closed {} via /ship {})".format(date_str, slug_label)


def build_output(lines, ordered, date_str):
    """Return (out_lines, closed_report). out_lines is None when nothing matched."""
    closed_payload = []   # transformed lines to insert into ## Closed
    closed_report = []    # (original_line_text, matched_slug) for reporting
    kept = []             # lines that survive in place

    for ln in lines:
        stripped = ln.lstrip()
        if stripped.startswith("- [open]"):
            matched = first_matching_slug(ln, ordered)
            if matched is not None:
                closed_payload.append(transform_open_to_closed(ln, matched, date_str))
                closed_report.append((stripped.rstrip("\n"), matched))
                continue
        kept.append(ln)

    if not closed_payload:
        return None, []

    out = []
    inserted = False
    i = 0
    n = len(kept)
    while i < n:
        line = kept[i]
        out.append(line)
        if not inserted and line.strip() == "## Closed":
            # Preserve a single following blank line, then insert the closed lines.
            if i + 1 < n and kept[i + 1].strip() == "":
                out.append(kept[i + 1])
                i += 1
            for payload in closed_payload:
                out.append(payload + "\n")
            inserted = True
        i += 1

    if not inserted:
        # No ## Closed header found: append one at EOF.
        if out and out[-1].strip() != "":
            out.append("\n")
        out.append("## Closed\n")
        out.append("\n")
        for payload in closed_payload:
            out.append(payload + "\n")

    return out, closed_report


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("slugs", nargs="*", help="feature slug(s) just shipped")
    ap.add_argument("--date", default=None, help="closed date (default: today)")
    ap.add_argument("--dry-run", action="store_true", help="print actions, write nothing")
    ap.add_argument("--file", default=DEFAULT_FILE, help="path to reminders.md")
    args = ap.parse_args()

    # Drop empty/whitespace slugs so an unset "$SLUG" expanding to "" is a clean no-op
    # rather than a crash (defence-in-depth; Step 14 also guards with [ -n "$SLUG" ]).
    raw_slugs = [s.strip() for s in args.slugs if s and s.strip()]
    if not raw_slugs:
        print("ship-close-reminders: no (non-empty) feature slug given (no-op)")
        return 0

    path = os.path.expanduser(args.file)
    if not os.path.isfile(path):
        sys.stderr.write("ship-close-reminders: reminders file not found: {}\n".format(path))
        return 2

    date_str = args.date or datetime.date.today().isoformat()

    # Unique original-case slugs, argv order preserved (case-insensitive de-dupe).
    seen = set()
    ordered = []
    for s in raw_slugs:
        sl = s.lower()
        if sl in seen:
            continue
        seen.add(sl)
        ordered.append(s)

    # Lock across read -> transform -> replace. Shared lock for dry-run (consistent
    # snapshot, no contention), exclusive for the real write.
    lock_path = path + ".lock"
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_SH if args.dry_run else fcntl.LOCK_EX)

        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        out, closed_report = build_output(lines, ordered, date_str)

        if out is None:
            print("ship-close-reminders: no open reminder matched {} (no-op)".format(
                ", ".join(raw_slugs)))
            return 0

        if args.dry_run:
            print("ship-close-reminders: DRY RUN -- would close {} reminder(s):".format(
                len(closed_report)))
            for orig, label in closed_report:
                print("  - [{}] {}".format(label, orig))
            return 0

        tmp = path + ".tmp_ship_close"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(out)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

        print("ship-close-reminders: closed {} reminder(s):".format(len(closed_report)))
        for orig, label in closed_report:
            print("  - [{}] {}".format(label, orig))
        return 0
    finally:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


if __name__ == "__main__":
    sys.exit(main())
