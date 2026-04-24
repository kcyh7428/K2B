"""K2B integrated-loop core library.

Parses observer-candidates.md, allocates L-IDs, and atomically rewrites
target files. Consumers: scripts/loop/loop_apply.py, loop_render.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple


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
        severity = current_header["sev"]
        area = current_header["area"].strip()
        evidence = " ".join(current_evidence).strip()
        # Hash the full candidate payload so two items with identical rule
        # text but different severity/area/evidence still get distinct IDs.
        payload = f"{severity}|{area}|{rule}|{evidence}"
        item_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
        items.append(
            Candidate(
                item_id=item_id,
                severity=severity,
                area=area,
                rule=rule,
                evidence=evidence,
            )
        )

    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
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
        # Any non-blank line that is neither a valid candidate header nor an
        # evidence continuation for a pending candidate is malformed. Freeze
        # the pipeline loudly rather than silently dropping data.
        raise ValueError(
            f"observer-candidates parse error in {path}: "
            f"unexpected line under '## Candidate Learnings' -- {line!r}"
        )

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


_LEARNING_TEMPLATE = """\
### {lid}
distilled-rule: "{rule}"
- **Area:** {area}
- **Distilled rule:** {rule}
- **Learning:** {rule}
- **Context:** Observer run {observer_run}, {severity}-confidence candidate learning auto-applied via session-start dashboard. Evidence: {evidence}
- **Reinforced:** 1
- **Confidence:** {severity}
- **Date:** {date_str}
- **Source:** observer-candidates (auto-applied {date_str} via session-start dashboard)
"""


def append_learning(
    learnings_path: Path,
    cand: Candidate,
    *,
    date_str: str,
    observer_run: str,
) -> str:
    """Append a new L-ID entry for the candidate. Returns allocated L-ID."""
    lid = allocate_next_lid(learnings_path, date_str)
    block = _LEARNING_TEMPLATE.format(
        lid=lid,
        rule=cand.rule,
        area=cand.area,
        severity=cand.severity,
        evidence=cand.evidence or "(no evidence recorded)",
        date_str=date_str,
        observer_run=observer_run,
    )
    existing = learnings_path.read_text(encoding="utf-8") if learnings_path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing and not existing.endswith("\n\n"):
        existing += "\n"
    new_text = existing + block
    _atomic_write(learnings_path, new_text)
    return lid


def archive_reject(
    archive_dir: Path,
    cand: Candidate,
    *,
    date_str: str,
    actor: str,
) -> None:
    """Append a reject record to observations.archive/rejected-YYYY-MM-DD.jsonl."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"rejected-{date_str}.jsonl"
    record = {
        "item_id": cand.item_id,
        "severity": cand.severity,
        "area": cand.area,
        "rule": cand.rule,
        "evidence": cand.evidence,
        "rejected": f"{actor} {date_str}",
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with target.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


# --- Ship 2: defer counter (observer + review share one JSONL) ---------------


def read_defers(path: Path) -> Dict[Tuple[str, str], int]:
    """Return {(item_id, kind): count} from observer-defers.jsonl.

    One line = one defer event. Count is number of matching lines. Missing
    file or non-JSON lines are tolerated (audit log, not execution state).
    """
    out: Dict[Tuple[str, str], int] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        item_id = rec.get("item_id")
        kind = rec.get("kind")
        if not item_id or not kind:
            continue
        key = (str(item_id), str(kind))
        out[key] = out.get(key, 0) + 1
    return out


def increment_defer(
    path: Path, *, item_id: str, kind: str, date_str: str
) -> int:
    """Append one defer event and return the new count for (item_id, kind)."""
    record = {
        "item_id": item_id,
        "kind": kind,
        "deferred_at": date_str,
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    return read_defers(path).get((item_id, kind), 0)


def reset_defers(path: Path, *, item_id: str, kind: str) -> None:
    """Atomic rewrite of defers file with all (item_id, kind) entries removed."""
    if not path.exists():
        return
    kept_lines: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rec = json.loads(stripped)
        except json.JSONDecodeError:
            # Preserve malformed lines so we don't silently drop data.
            kept_lines.append(line)
            continue
        if rec.get("item_id") == item_id and rec.get("kind") == kind:
            continue
        kept_lines.append(line)
    new_text = "\n".join(kept_lines)
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    _atomic_write(path, new_text)


def archive_observer_auto_deferred(
    archive_dir: Path,
    cand: "Candidate",
    *,
    date_str: str,
    defer_count: int,
) -> None:
    """Append a record to observations.archive/auto-archived-deferred-YYYY-MM-DD.jsonl."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"auto-archived-deferred-{date_str}.jsonl"
    record = {
        "item_id": cand.item_id,
        "severity": cand.severity,
        "area": cand.area,
        "rule": cand.rule,
        "evidence": cand.evidence,
        "defer_count": defer_count,
        "auto_archived": date_str,
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with target.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


# --- Ship 2: review-item surface ---------------------------------------------


@dataclass(frozen=True)
class ReviewItem:
    item_id: str  # 8-hex of filename stem; stable across sessions
    path: Path
    filename: str  # basename for display


_FRONTMATTER_BOUNDARY = "---"
_REVIEW_ACTION = re.compile(r"^review-action:\s*.*$", re.MULTILINE)


def _review_item_id(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return hashlib.sha256(stem.encode("utf-8")).hexdigest()[:8]


def list_reviews(review_dir: Path) -> List[ReviewItem]:
    """Return sorted ReviewItem objects for *.md files under review_dir.

    Excludes index.md and any files that live in a subdirectory (Ready/,
    Archive/). Observer and review kinds each own their own numbering when
    merged by resolve_index.
    """
    if not review_dir.is_dir():
        return []
    items: List[ReviewItem] = []
    for p in sorted(review_dir.glob("*.md")):
        if p.name == "index.md":
            continue
        items.append(
            ReviewItem(
                item_id=_review_item_id(p.name),
                path=p,
                filename=p.name,
            )
        )
    return items


def _set_review_action(text: str, action: str) -> str:
    """Return file text with review-action replaced or inserted in frontmatter."""
    lines = text.splitlines(keepends=False)
    if not lines or lines[0].strip() != _FRONTMATTER_BOUNDARY:
        # No frontmatter -- prepend one so the field is always present.
        new_fm = [_FRONTMATTER_BOUNDARY, f"review-action: {action}", _FRONTMATTER_BOUNDARY, ""]
        return "\n".join(new_fm + lines) + ("\n" if not text.endswith("\n") else "")

    # Walk the frontmatter block, replace review-action if found; else append
    # before the closing boundary.
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_BOUNDARY:
            end_idx = i
            break
    if end_idx is None:
        # Malformed frontmatter -- leave the file alone except for a safe append.
        return text + f"\nreview-action: {action}\n"

    replaced = False
    new_fm_lines: List[str] = []
    for i in range(1, end_idx):
        if re.match(r"^review-action:\s*", lines[i]):
            new_fm_lines.append(f"review-action: {action}")
            replaced = True
        else:
            new_fm_lines.append(lines[i])
    if not replaced:
        new_fm_lines.append(f"review-action: {action}")

    rebuilt: List[str] = [_FRONTMATTER_BOUNDARY] + new_fm_lines + [_FRONTMATTER_BOUNDARY]
    rebuilt.extend(lines[end_idx + 1 :])
    result = "\n".join(rebuilt)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def _move_review_file(src: Path, dst: Path, *, action: str) -> Path:
    """Write mutated content directly to dst via tempfile + os.replace, then
    delete the source. Creates dst.parent if missing.

    This one-step move avoids the modified-but-not-moved window that occurs
    when writing to the source first -- if the destination write or the
    source unlink fails, the source keeps its original content intact
    (Codex MEDIUM-3 regression).
    """
    text = src.read_text(encoding="utf-8")
    new_text = _set_review_action(text, action)

    dst.parent.mkdir(parents=True, exist_ok=True)
    final_dst = dst
    if final_dst.exists():
        # Suffix with an integer until unique.
        n = 1
        while True:
            candidate = dst.with_name(f"{dst.stem}.{n}{dst.suffix}")
            if not candidate.exists():
                final_dst = candidate
                break
            n += 1

    # Write mutated content straight to destination.
    _atomic_write(final_dst, new_text)
    # Only remove source after destination is durably on disk.
    try:
        src.unlink()
    except OSError:
        # If unlink fails, the destination is already correct; source is
        # stale but recoverable. Surface loudly rather than silently.
        raise
    return final_dst


def accept_review(review: ReviewItem, *, date_str: str, ready_dir: Path) -> Path:
    """Flip review-action: accepted and move review.path into ready_dir."""
    return _move_review_file(
        review.path, ready_dir / review.filename, action="accepted"
    )


def reject_review(
    review: ReviewItem, *, date_str: str, archive_root: Path
) -> Path:
    """Flip review-action: rejected and move into archive_root/YYYY-MM-DD/."""
    return _move_review_file(
        review.path, archive_root / date_str / review.filename, action="rejected"
    )


def archive_review_auto_deferred(
    review: ReviewItem,
    *,
    date_str: str,
    archive_root: Path,
    defer_count: int,  # kept for future use + parity with observer side
) -> Path:
    """Move the review file to archive with action auto-archived-deferred."""
    del defer_count  # unused today; kept for symmetry + audit
    return _move_review_file(
        review.path,
        archive_root / date_str / review.filename,
        action="auto-archived-deferred",
    )


# --- Ship 2: unified numbering across observer + review ---------------------


def resolve_index(
    idx: int,
    candidates: Sequence[Candidate],
    reviews: Sequence[ReviewItem],
) -> Tuple[str, object]:
    """Map a 1-based dashboard index to (kind, object).

    kind is "observer" or "review". Raises IndexError for out-of-range.
    """
    if idx < 1:
        raise IndexError(f"index {idx} below 1")
    n_obs = len(candidates)
    if idx <= n_obs:
        return ("observer", candidates[idx - 1])
    rel = idx - n_obs
    if rel <= len(reviews):
        return ("review", reviews[rel - 1])
    raise IndexError(
        f"index {idx} out of range (observer 1..{n_obs}, review "
        f"{n_obs + 1}..{n_obs + len(reviews)})"
    )
