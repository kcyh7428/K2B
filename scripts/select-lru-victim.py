#!/usr/bin/env python3
"""select-lru-victim.py

LRU victim picker for the active_rules.md cap. Reads
active_rules.md, parses each rule's `last-reinforced: YYYY-MM-DD` field
and its reinforcement count from the trailing parenthetical
(e.g. `reinforced 3x`), and prints the rule that should be demoted to
make room for a new promotion.

Tiebreakers (in order):
  1. `last-reinforced` ascending (oldest first)
  2. reinforcement count ascending (least-reinforced first)
  3. L-ID alphabetical ascending

Output on stdout (single JSON object, no array):
  {
    "rule_number": 5,
    "title": "Poll before acting",
    "last_reinforced": "2026-03-26",
    "reinforcement_count": 1,
    "learn_id": "L-2026-03-26-010"
  }

Exit codes:
  0 - victim found and printed
  1 - no rules parsed (empty active_rules.md or unexpected format)
  2 - config error (missing file)
"""

import json
import re
import sys
from pathlib import Path

ACTIVE_RULES = (
    Path.home()
    / ".claude"
    / "projects"
    / "-Users-keithmbpm2-Projects-K2B"
    / "memory"
    / "active_rules.md"
)

# Match flush-left rule line: "N. **Title.** body text (L-ID, ..., last-reinforced: YYYY-MM-DD)"
RULE_LINE_RE = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*\s*(.*)$", re.MULTILINE)
LAST_REINFORCED_RE = re.compile(r"last-reinforced:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
REINFORCED_COUNT_RE = re.compile(r"reinforced\s+(\d+)x", re.IGNORECASE)
LEARN_ID_RE = re.compile(r"\bL-\d{4}-\d{2}-\d{2}-\d{3}\b")


def _parse_rules(text):
    """Return list of dicts describing each numbered rule in active_rules.md.

    A rule "block" runs from its numbered header line to the next numbered
    header line, the next `## ` heading, or EOF. We collect the full block
    so we can pick up `last-reinforced:` even if it appears on a continuation
    line below the header.
    """
    lines = text.split("\n")
    rules = []
    current = None
    for line in lines:
        m = RULE_LINE_RE.match(line)
        if m:
            if current is not None:
                rules.append(current)
            current = {
                "rule_number": int(m.group(1)),
                "title": m.group(2).rstrip("."),
                "body_lines": [line],
            }
            continue
        if current is None:
            continue
        # Stop collecting when we hit a top-level heading.
        if line.startswith("## "):
            rules.append(current)
            current = None
            continue
        current["body_lines"].append(line)
    if current is not None:
        rules.append(current)

    for r in rules:
        block = "\n".join(r["body_lines"])
        lr = LAST_REINFORCED_RE.search(block)
        r["last_reinforced"] = lr.group(1) if lr else "0000-00-00"
        rc = REINFORCED_COUNT_RE.search(block)
        r["reinforcement_count"] = int(rc.group(1)) if rc else 1
        lid = LEARN_ID_RE.search(block)
        r["learn_id"] = lid.group(0) if lid else ""
    return rules


def main():
    if not ACTIVE_RULES.exists():
        print(f"select-lru-victim: {ACTIVE_RULES} not found", file=sys.stderr)
        sys.exit(2)

    text = ACTIVE_RULES.read_text()
    rules = _parse_rules(text)
    if not rules:
        print("select-lru-victim: no rules parsed", file=sys.stderr)
        sys.exit(1)

    # Sort by (last_reinforced asc, reinforcement_count asc, learn_id asc).
    rules.sort(
        key=lambda r: (
            r["last_reinforced"],
            r["reinforcement_count"],
            r["learn_id"],
        )
    )
    victim = rules[0]
    print(
        json.dumps(
            {
                "rule_number": victim["rule_number"],
                "title": victim["title"],
                "last_reinforced": victim["last_reinforced"],
                "reinforcement_count": victim["reinforcement_count"],
                "learn_id": victim["learn_id"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
