"""K2B integrated-loop core library.

Parses observer-candidates.md, allocates L-IDs, and atomically rewrites
target files. Consumers: scripts/loop/loop_apply.py, loop_render.py.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class Candidate:
    item_id: str  # 8-hex content hash, stable across reads
    severity: str  # "high" | "medium" | "low"
    area: str  # "workflow" | "preferences" | ...
    rule: str  # the rule text (headline)
    evidence: str  # evidence text, may be multi-line


_CANDIDATE_HEADER = re.compile(
    r"^- \[(?P<sev>high|medium|low)\]\s+(?P<area>[^:]+):\s*(?P<rule>.+)$"
)
_EVIDENCE_LINE = re.compile(r"^\s+Evidence:\s*(?P<ev>.+)$")


def parse_candidates(path: Path) -> List[Candidate]:
    """Parse observer-candidates.md and return the Candidate Learnings.

    Skips the Detected Patterns section. Parse errors raise ValueError
    (per L-2026-04-22-001: parse errors are blocking invariants).
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")

    m = re.search(
        r"^## Candidate Learnings[^\n]*\n(.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        return []
    block = m.group(1)

    items: List[Candidate] = []
    current_header = None
    current_evidence: list[str] = []

    def flush() -> None:
        if current_header is None:
            return
        rule = current_header["rule"].strip()
        evidence = " ".join(current_evidence).strip()
        item_id = hashlib.sha256(rule.encode("utf-8")).hexdigest()[:8]
        items.append(
            Candidate(
                item_id=item_id,
                severity=current_header["sev"],
                area=current_header["area"].strip(),
                rule=rule,
                evidence=evidence,
            )
        )

    for line in block.splitlines():
        header = _CANDIDATE_HEADER.match(line)
        if header:
            flush()
            current_header = header.groupdict()
            current_evidence = []
            continue
        ev = _EVIDENCE_LINE.match(line)
        if ev and current_header is not None:
            current_evidence.append(ev.group("ev"))
            continue

    flush()
    return items


_LID_PATTERN = re.compile(
    r"^### (L-(?P<date>\d{4}-\d{2}-\d{2})-(?P<num>\d{3}))\b", re.MULTILINE
)


def allocate_next_lid(learnings_path: Path, date_str: str) -> str:
    """Return the next unused L-YYYY-MM-DD-NNN for the given date.

    Scans learnings for existing L-IDs matching date_str, returns the
    successor of the max NNN. Missing file -> start at 001.
    """
    max_num = 0
    if learnings_path.exists():
        text = learnings_path.read_text(encoding="utf-8")
        for m in _LID_PATTERN.finditer(text):
            if m.group("date") == date_str:
                max_num = max(max_num, int(m.group("num")))
    return f"L-{date_str}-{max_num + 1:03d}"
