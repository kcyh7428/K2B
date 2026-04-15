#!/usr/bin/env python3
"""promote-learnings.py

Scan self_improve_learnings.md for promotable learnings (reinforcement count >= 3)
and emit a JSON array to stdout describing each candidate. The caller (/ship step 0)
surfaces these to Keith for y/n/skip confirmation and then applies the chosen
action to active_rules.md.

This script is read-only: it never mutates learnings or active_rules.md.

Live format (verified 2026-04-15):
  ### L-2026-04-14-001
  - **Area:** vault
  - **Learning:** <body text>
  - **Context:** <context text>
  - **Reinforced:** 3
  - **Confidence:** medium
  - **Date:** 2026-04-14

Reinforcement count is extracted from the bullet-style `- **Reinforced:** N`
pattern first (canonical), with frontmatter `reinforcement-count: N` and
inline `reinforced Nx` as secondary fallbacks for tolerance.

Output schema (JSON array):
[
  {
    "learn_id": "L-2026-04-14-001",
    "count": 3,
    "distilled_rule": "<extracted rule text or null>",
    "source_excerpt": "<first 300 chars of the learning body>",
    "already_in_active_rules": false,
    "rejected": false,
    "would_exceed_cap": false,
    "current_active_count": 9,
    "cap": 12
  },
  ...
]

Exit codes:
  0 - scan ran, output on stdout (may be empty array)
  2 - config error (missing files)
"""

import json
import re
import sys
from pathlib import Path

VAULT = Path.home() / "Projects" / "K2B-Vault"
LEARNINGS = VAULT / "System" / "memory" / "self_improve_learnings.md"
ACTIVE_RULES = (
    Path.home()
    / ".claude"
    / "projects"
    / "-Users-keithmbpm2-Projects-K2B"
    / "memory"
    / "active_rules.md"
)

LEARN_ID_RE = re.compile(r"\bL-\d{4}-\d{2}-\d{2}-\d{3}\b")

# Primary: `- **Reinforced:** N` (canonical bullet body format)
REINFORCED_BULLET_RE = re.compile(
    r"^\s*-\s*\*\*Reinforced:\*\*\s*(\d+)\s*$", re.MULTILINE
)
# Secondary: `reinforcement-count: N` (future frontmatter or inline)
COUNT_FM_RE = re.compile(r"^reinforcement-count:\s*(\d+)\s*$", re.MULTILINE)
# Tertiary: `reinforced Nx` inline
COUNT_BODY_RE = re.compile(r"reinforced\s+(\d+)x", re.IGNORECASE)

# Distilled rule:
# Primary: `distilled-rule: "..."` frontmatter-style line
DISTILLED_FM_RE = re.compile(r"^\s*-?\s*\*?\*?distilled-rule:?\*?\*?\s*[:\"]?(.+?)[\"]?\s*$", re.MULTILINE | re.IGNORECASE)
# Fallback: `- **Learning:** <text>` bullet body
LEARNING_BULLET_RE = re.compile(
    r"^\s*-\s*\*\*Learning:\*\*\s*(.+?)(?=\n\s*-\s*\*\*|\Z)",
    re.MULTILINE | re.DOTALL,
)

REJECTED_FM_RE = re.compile(r"^auto-promote-rejected:\s*true\s*$", re.MULTILINE | re.IGNORECASE)
CAP_RE = re.compile(r"Cap:\s*(\d+)\s+rules", re.IGNORECASE)


def _split_entries(text):
    """Split self_improve_learnings.md into entries keyed by L-ID.

    A learning entry starts at an H3 heading (`### L-...`) and runs until the next
    H3 L-heading or end-of-file. Entries inside a "## Demoted Rules" section are
    ignored (they are a different category).
    """
    demoted_idx = text.find("## Demoted Rules")
    active_text = text[:demoted_idx] if demoted_idx != -1 else text

    # Split on H3 L-ID headings. re.split returns [preamble, heading1, body1, heading2, body2, ...]
    chunks = re.split(
        r"(?m)^(###\s+L-\d{4}-\d{2}-\d{2}-\d{3}\b.*)$", active_text
    )
    entries = {}
    i = 1
    while i < len(chunks):
        heading = chunks[i]
        body = chunks[i + 1] if i + 1 < len(chunks) else ""
        m = LEARN_ID_RE.search(heading)
        if m:
            entries[m.group(0)] = heading + body
        i += 2
    return entries


def _extract_count(entry_text):
    """Return the reinforcement count for an entry. Default 1 if unmarked."""
    m = REINFORCED_BULLET_RE.search(entry_text)
    if m:
        return int(m.group(1))
    m = COUNT_FM_RE.search(entry_text)
    if m:
        return int(m.group(1))
    m = COUNT_BODY_RE.search(entry_text)
    if m:
        return int(m.group(1))
    return 1


def _extract_distilled(entry_text):
    """Return a one-sentence distilled rule for promotion, or None."""
    m = DISTILLED_FM_RE.search(entry_text)
    if m:
        candidate = m.group(1).strip().strip('"').strip("'")
        if candidate and not candidate.lower().startswith("distilled"):
            return candidate
    # Fallback: first sentence of the `- **Learning:**` bullet body.
    m = LEARNING_BULLET_RE.search(entry_text)
    if m:
        learning = m.group(1).strip()
        # Take first sentence (up to first `. ` that isn't inside brackets).
        first_sentence = re.split(r"(?<=[.!?])\s+", learning, maxsplit=1)[0]
        return first_sentence[:300].strip()
    return None


def _is_rejected(entry_text):
    return bool(REJECTED_FM_RE.search(entry_text))


def _existing_rules(active_text):
    """Return set of L-IDs already cited in active_rules.md."""
    return set(LEARN_ID_RE.findall(active_text))


def _active_rule_count(active_text):
    """Count numbered top-level rules: flush-left lines starting with `N. **`."""
    return len(re.findall(r"(?m)^\d+\.\s+\*\*", active_text))


def _cap(active_text):
    m = CAP_RE.search(active_text)
    return int(m.group(1)) if m else 12


def main():
    if not LEARNINGS.exists():
        print(f"promote-learnings: {LEARNINGS} not found", file=sys.stderr)
        sys.exit(2)
    if not ACTIVE_RULES.exists():
        print(f"promote-learnings: {ACTIVE_RULES} not found", file=sys.stderr)
        sys.exit(2)

    learnings_text = LEARNINGS.read_text()
    active_text = ACTIVE_RULES.read_text()

    entries = _split_entries(learnings_text)
    existing = _existing_rules(active_text)
    active_count = _active_rule_count(active_text)
    cap = _cap(active_text)

    candidates = []
    for lid, body in entries.items():
        count = _extract_count(body)
        if count < 3:
            continue
        if lid in existing:
            continue
        if _is_rejected(body):
            continue
        distilled = _extract_distilled(body)
        candidates.append(
            {
                "learn_id": lid,
                "count": count,
                "distilled_rule": distilled,
                "source_excerpt": body[:300].replace("\n", " ").strip(),
                "already_in_active_rules": False,
                "rejected": False,
                "would_exceed_cap": (active_count + 1 + len(candidates)) > cap,
                "current_active_count": active_count,
                "cap": cap,
            }
        )

    print(json.dumps(candidates, indent=2))


if __name__ == "__main__":
    main()
