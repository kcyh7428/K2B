"""K2B integrated-loop core library.

Parses observer-candidates.md, allocates L-IDs, and atomically rewrites
target files. Consumers: scripts/loop/loop_apply.py, loop_render.py.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Set


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


def _atomic_write(path: Path, content: str) -> None:
    """Atomic write via tempfile + os.replace. Caller holds any needed lock."""
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".tmp_{path.name}_", suffix=path.suffix or ".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def rewrite_candidates_without(path: Path, remove_ids: Iterable[str]) -> None:
    """Rewrite observer-candidates.md omitting items whose id is in remove_ids.

    Atomic. Preserves non-candidate sections (Summary, Detected Patterns).
    """
    remove_set: Set[str] = set(remove_ids)
    if not remove_set:
        return
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    items = parse_candidates(path)
    kept = [it for it in items if it.item_id not in remove_set]

    m = re.search(
        r"(^## Candidate Learnings[^\n]*\n)(.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        return
    header = m.group(1)

    lines = [header]
    for it in kept:
        lines.append(f"- [{it.severity}] {it.area}: {it.rule}\n")
        if it.evidence:
            lines.append(f"  Evidence: {it.evidence}\n")
    new_section = "".join(lines)
    if not (m.end() < len(text) and text[m.end():].startswith("\n")):
        new_section = new_section.rstrip("\n") + "\n\n"

    new_text = text[: m.start()] + new_section + text[m.end():]
    _atomic_write(path, new_text)
