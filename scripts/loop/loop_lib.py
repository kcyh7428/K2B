"""K2B integrated-loop core library.

Parses observer-candidates.md, allocates L-IDs, and atomically rewrites
target files. Consumers: scripts/loop/loop_apply.py, loop_render.py.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import fcntl
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
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
        # `(none)` is the documented empty-section sentinel (exact-match
        # lowercase, symmetric with how `## Detected Patterns` uses it).
        # Tolerated ONLY between candidates (current_header is None) so the
        # strict-mode guard still fires on a stray `(none)` inside a
        # candidate's evidence block, where it could mask a malformed entry.
        if stripped == "(none)" and current_header is None:
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
        target_fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
        _fsync_dir(path.parent)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _file_lock(path: Path):
    timeout = float(os.environ.get("K2B_LOOP_LOCK_TIMEOUT_SECONDS", "30"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as f:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() - start >= timeout:
                    raise TimeoutError(f"could not acquire lock {path} after {timeout:g}s")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@contextmanager
def _shell_compatible_lock(path: Path):
    """Mirror shell scripts' flock-if-available, mkdir-fallback lock behavior."""
    if shutil.which("flock"):
        with _file_lock(path):
            yield
        return
    lock_dir = Path(f"{path}.d")
    tries = 0
    while True:
        try:
            lock_dir.mkdir(parents=True)
            break
        except FileExistsError:
            tries += 1
            if tries > 200:
                raise TimeoutError(f"could not acquire lock {lock_dir} after 10s")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except OSError:
            pass


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


def _read_defers_unlocked(path: Path) -> Dict[Tuple[str, str], int]:
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


def _defer_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def read_defers(path: Path) -> Dict[Tuple[str, str], int]:
    """Return {(item_id, kind): count} from observer-defers.jsonl.

    One line = one defer event. Count is number of matching lines. Missing
    file or non-JSON lines are tolerated (audit log, not execution state).
    """
    if not path.exists():
        return {}
    with _file_lock(_defer_lock_path(path)):
        return _read_defers_unlocked(path)


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
    with _file_lock(_defer_lock_path(path)):
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        _fsync_dir(path.parent)
        return _read_defers_unlocked(path).get((item_id, kind), 0)


def reset_defers(path: Path, *, item_id: str, kind: str) -> None:
    """Atomic rewrite of defers file with all (item_id, kind) entries removed."""
    if not path.exists():
        return
    with _file_lock(_defer_lock_path(path)):
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


@dataclass(frozen=True)
class ConflictItem:
    item_id: str  # 8-hex of conflict id; stable across sessions
    path: Path
    conflict_id: str
    subject: str
    predicate: str
    existing_value: str
    new_value: str
    source_session_path: str
    existing_source: str
    dedupe_key: str
    surfaced_count: int
    existing_line_hash: str = ""


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
    lock_paths = {
        (src.parent / ".review-move.lock").resolve(strict=False),
        (dst.parent / ".review-move.lock").resolve(strict=False),
    }
    with ExitStack() as stack:
        for lock_path in sorted(lock_paths, key=str):
            stack.enter_context(_file_lock(lock_path))
        text = src.read_text(encoding="utf-8")
        new_text = _set_review_action(text, action)

        dst.parent.mkdir(parents=True, exist_ok=True)
        final_dst = dst
        if final_dst.exists():
            # Suffix with an integer until unique.
            n = 1
            while n <= 1000:
                candidate = dst.with_name(f"{dst.stem}.{n}{dst.suffix}")
                if not candidate.exists():
                    final_dst = candidate
                    break
                n += 1
            else:
                raise RuntimeError(f"too many destination collisions for {dst}")

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


# --- EOD capture: conflict-item surface --------------------------------------


def _conflict_item_id(conflict_id: str, filename: str) -> str:
    payload = conflict_id or filename
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def list_conflicts(conflict_dir: Path) -> List[ConflictItem]:
    """Return sorted pending EOD capture conflicts from *.json files."""
    if not conflict_dir.is_dir():
        return []
    items: List[ConflictItem] = []
    for p in sorted(conflict_dir.glob("*.json")):
        if p.name.startswith(".tmp"):
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"loop-lib: warning: cannot read conflict {p}: {exc}", file=sys.stderr)
            continue
        except json.JSONDecodeError as exc:
            print(f"loop-lib: warning: malformed conflict JSON {p}: {exc}", file=sys.stderr)
            continue
        if not isinstance(raw, dict):
            continue
        conflict_id = str(raw.get("conflict_id") or p.stem)
        items.append(
            ConflictItem(
                item_id=_conflict_item_id(conflict_id, p.name),
                path=p,
                conflict_id=conflict_id,
                subject=str(raw.get("subject", "")),
                predicate=str(raw.get("predicate", "")),
                existing_value=str(raw.get("existing_value", "")),
                new_value=str(raw.get("new_value", "")),
                source_session_path=str(raw.get("source_session_path", "")),
                existing_source=str(raw.get("existing_source", "")),
                dedupe_key=str(raw.get("dedupe_key", "")),
                surfaced_count=int(raw.get("surfaced_count") or 0),
                existing_line_hash=str(raw.get("existing_line_hash", "")),
            )
        )
    return items


def _resolve_conflict_source(conflict: ConflictItem, vault_root: Path) -> tuple[Path, int | None]:
    source = conflict.existing_source
    if ":" in source:
        path_part, line_part = source.rsplit(":", 1)
        try:
            line_no = int(line_part)
        except ValueError:
            path_part = source
            line_no = None
    else:
        path_part = source
        line_no = None
    path = Path(path_part)
    if ".." in path.parts:
        raise ValueError(f"conflict {conflict.conflict_id}: invalid existing_source path")
    if not path.is_absolute():
        path = vault_root / path
    allowed_path = vault_root / "wiki" / "context" / "shelves"
    current = vault_root
    for part in ("wiki", "context", "shelves"):
        current = current / part
        if current.is_symlink():
            raise ValueError(
                f"conflict {conflict.conflict_id}: existing_source uses symlinked shelves path"
            )
    resolved = path.resolve(strict=False)
    allowed_root = allowed_path.resolve(strict=False)
    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise ValueError(
            f"conflict {conflict.conflict_id}: existing_source outside shelves: {path}"
        )
    return resolved, line_no


def _replace_pipe_field_value(
    line: str,
    *,
    predicate: str,
    existing_value: str,
    new_value: str,
) -> tuple[str, str]:
    """Replace one pipe-delimited predicate value.

    Returns (new_line, status), where status is "replaced", "already", or
    "missing". This deliberately avoids free substring replacement.
    """
    target_key = predicate.strip().lower()
    if not target_key:
        return line, "missing"
    if _has_unescaped_pipe(existing_value) or _has_unescaped_pipe(new_value):
        raise ValueError("conflict values containing pipe delimiters are not supported")
    if any(ch in existing_value or ch in new_value for ch in ("\n", "\r", "\t")):
        raise ValueError("conflict values containing control whitespace are not supported")

    parts = _split_pipe_fields(line)
    for i, part in enumerate(parts):
        m = re.match(r"(\s*([^:]+):\s*)(.*?)(\s*)$", part)
        if not m:
            continue
        key = m.group(2).strip().lower()
        if key != target_key:
            continue
        value = m.group(3).strip()
        if _same_pipe_value(value, new_value):
            return line, "already"
        if not _same_pipe_value(value, existing_value):
            return line, "missing"
        parts[i] = f"{m.group(1)}{new_value}{m.group(4)}"
        return "|".join(parts), "replaced"
    return line, "missing"


def _extract_pipe_field(line: str, key: str) -> str | None:
    target_key = key.strip().lower()
    for part in _split_pipe_fields(line):
        if ":" not in part:
            continue
        raw_key, raw_value = part.split(":", 1)
        if raw_key.strip().lower() == target_key:
            return raw_value.strip()
    return None


def _split_pipe_fields(line: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for ch in line:
        if ch == "|" and not escaped:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        if ch == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    parts.append("".join(current))
    return parts


def _has_unescaped_pipe(value: str) -> bool:
    escaped = False
    for ch in value:
        if ch == "|" and not escaped:
            return True
        if ch == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    return False


def _same_pipe_value(a: str, b: str) -> bool:
    return re.sub(r"\s+", " ", a.strip()).casefold() == re.sub(
        r"\s+", " ", b.strip()
    ).casefold()


def _line_hash(line: str) -> str:
    return hashlib.sha256(line.rstrip("\r\n").encode("utf-8")).hexdigest()


def _target_lock_path(target: Path) -> Path:
    """Use shelf-writer's lock for shelf files so appends and rewrites serialize.

    Mirrors scripts/washing-machine/shelf-writer.sh:
    K2B_SHELF_LOCK_DIR/k2b-shelf-<shelf>.lock, with flock or .d fallback.
    """
    if target.parent.name == "shelves" and target.suffix == ".md":
        lock_root = Path(os.environ.get("K2B_SHELF_LOCK_DIR", "/tmp"))
        return lock_root / f"k2b-shelf-{target.stem}.lock"
    return target.with_name(f".{target.name}.lock")


def _conflict_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def _conflict_operation_lock_path(path: Path) -> Path:
    return path.parent / ".pending-conflicts.lock"


def _verify_conflict_file_current(conflict: ConflictItem) -> None:
    try:
        raw = json.loads(conflict.path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"conflict disappeared before accept: {conflict.path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed conflict JSON: {conflict.path}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"malformed conflict JSON: {conflict.path}")
    fields = (
        "conflict_id",
        "subject",
        "predicate",
        "existing_value",
        "new_value",
        "dedupe_key",
    )
    for field in fields:
        current = str(raw.get(field) or "")
        expected = str(getattr(conflict, field))
        if current != expected:
            raise ValueError(
                f"conflict {conflict.conflict_id}: pending file changed before accept"
            )


def _verify_existing_source_line(
    conflict: ConflictItem, lines: Sequence[str], line_no: int | None
) -> None:
    if line_no is None:
        return
    if line_no < 1 or line_no > len(lines):
        raise ValueError(
            f"conflict {conflict.conflict_id}: existing_source line {line_no} "
            "is outside target file"
        )
    source_line = lines[line_no - 1]
    source_dedupe = _extract_pipe_field(source_line, "dedupe_key")
    if source_dedupe != conflict.dedupe_key:
        raise ValueError(
            f"conflict {conflict.conflict_id}: existing_source line {line_no} "
            f"does not match dedupe_key {conflict.dedupe_key}"
        )


def _verify_archive_identity(conflict: ConflictItem, archived: dict, target: Path) -> None:
    fields = ("conflict_id", "subject", "predicate", "dedupe_key")
    for field in fields:
        current = str(archived.get(field) or "")
        expected = str(getattr(conflict, field))
        if current != expected:
            raise ValueError(
                f"conflict {conflict.conflict_id}: archive identity mismatch in {target}"
            )


def _remove_conflict_file_after_apply(conflict: ConflictItem) -> None:
    try:
        conflict.path.unlink(missing_ok=True)
        _fsync_dir(conflict.path.parent)
    except OSError as exc:
        print(
            f"loop-lib: warning: canonical value applied but conflict file remains "
            f"{conflict.path}: {exc}",
            file=sys.stderr,
        )


def accept_conflict(conflict: ConflictItem, *, vault_root: Path) -> Path:
    """Accept the new value, update canonical home, and delete conflict file."""
    target, line_no = _resolve_conflict_source(conflict, vault_root)
    if not conflict.dedupe_key:
        raise ValueError(
            f"conflict {conflict.conflict_id}: dedupe_key is required to accept"
        )
    lock_path = _target_lock_path(target)
    # All pending-conflict operations take this directory-level lock before any
    # shelf lock. shelf-writer only takes the shelf lock, so there is no
    # conflict/shelf lock inversion with semantic appends.
    with _file_lock(_conflict_operation_lock_path(conflict.path)):
        with _shell_compatible_lock(lock_path):
            _verify_conflict_file_current(conflict)
            with target.open("r", encoding="utf-8", newline="") as f:
                text = f.read()
            lines = text.splitlines(keepends=True)
            _verify_existing_source_line(conflict, lines, line_no)
            dedupe_matches = [
                i
                for i, line in enumerate(lines)
                if _extract_pipe_field(line, "dedupe_key") == conflict.dedupe_key
            ]
            if len(dedupe_matches) != 1:
                raise ValueError(
                    f"conflict {conflict.conflict_id}: dedupe_key "
                    f"{conflict.dedupe_key} matched {len(dedupe_matches)} rows in {target}"
                )
            candidate_index = dedupe_matches[0]

            current_line = lines[candidate_index]
            current_value = _extract_pipe_field(current_line, conflict.predicate)
            if current_value is None:
                raise ValueError(
                    f"conflict {conflict.conflict_id}: predicate {conflict.predicate} "
                    f"not found in {target}"
                )

            if _same_pipe_value(current_value, conflict.new_value):
                _remove_conflict_file_after_apply(conflict)
                return target

            if (
                conflict.existing_line_hash
                and _line_hash(current_line) != conflict.existing_line_hash
            ):
                raise ValueError(
                    f"conflict {conflict.conflict_id}: stale target row in {target}"
                )

            if not _same_pipe_value(current_value, conflict.existing_value):
                raise ValueError(
                    f"conflict {conflict.conflict_id}: stale existing value in {target}; "
                    f"expected {conflict.existing_value!r}, found {current_value!r}"
                )

            new_line, status = _replace_pipe_field_value(
                current_line,
                predicate=conflict.predicate,
                existing_value=conflict.existing_value,
                new_value=conflict.new_value,
            )
            if status != "replaced":
                raise ValueError(
                    f"conflict {conflict.conflict_id}: expected {conflict.predicate}:"
                    f"{conflict.existing_value} not found in {target}"
                )
            lines[candidate_index] = new_line
            expected_text = "".join(lines)
            expected_hash = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
            _atomic_write(target, expected_text)
            with target.open("r", encoding="utf-8", newline="") as f:
                written_text = f.read()
            if hashlib.sha256(written_text.encode("utf-8")).hexdigest() != expected_hash:
                raise RuntimeError(
                    f"conflict {conflict.conflict_id}: target read-back failed in {target}"
                )
            written_lines = written_text.splitlines(keepends=True)
            written_matches = [
                line
                for line in written_lines
                if _extract_pipe_field(line, "dedupe_key") == conflict.dedupe_key
            ]
            if len(written_matches) != 1 or not _same_pipe_value(
                _extract_pipe_field(written_matches[0], conflict.predicate) or "",
                conflict.new_value,
            ):
                raise RuntimeError(
                    f"conflict {conflict.conflict_id}: target read-back failed in {target}"
                )
            _remove_conflict_file_after_apply(conflict)
    return target


def archive_conflict_reject(
    conflict: ConflictItem,
    *,
    archive_dir: Path,
    date_str: str,
    actor: str,
) -> Path:
    """Archive a rejected conflict before removing the pending JSON."""
    try:
        raw = json.loads(conflict.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {
            "conflict_id": conflict.conflict_id,
            "subject": conflict.subject,
            "predicate": conflict.predicate,
            "existing_value": conflict.existing_value,
            "new_value": conflict.new_value,
            "source_session_path": conflict.source_session_path,
        }
    raw["rejected_at"] = date_str
    raw["rejected_by"] = actor
    raw["archive_reason"] = "rejected"
    target = archive_dir / date_str / conflict.path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return target


def reject_conflict(conflict: ConflictItem) -> None:
    """Reject the new value and remove the pending conflict."""
    conflict.path.unlink(missing_ok=True)
    _fsync_dir(conflict.path.parent)


def reject_conflict_to_archive(
    conflict: ConflictItem,
    *,
    archive_dir: Path,
    date_str: str,
    actor: str,
) -> Path:
    """Archive and remove one rejected conflict while holding its file lock."""
    with _file_lock(_conflict_operation_lock_path(conflict.path)):
        archived = archive_conflict_reject(
            conflict,
            archive_dir=archive_dir,
            date_str=date_str,
            actor=actor,
        )
        reject_conflict(conflict)
        return archived


def defer_conflict(
    conflict: ConflictItem,
    *,
    archive_dir: Path,
    date_str: str,
    threshold: int = 3,
) -> tuple[int, Path | None]:
    """Increment surfaced_count. Auto-archive after threshold defers."""
    with _file_lock(_conflict_operation_lock_path(conflict.path)):
        target = archive_dir / date_str / conflict.path.name
        try:
            raw = json.loads(conflict.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = None
        except json.JSONDecodeError as exc:
            raise ValueError(f"malformed conflict JSON: {conflict.path}") from exc
        archived = {}
        try:
            archived = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except json.JSONDecodeError as exc:
            if raw is None:
                raise ValueError(f"malformed conflict archive JSON: {target}") from exc
        except OSError:
            pass
        if archived.get("archive_reason") == "deferred_threshold":
            archived_conflict_id = str(archived.get("conflict_id") or "")
            if archived_conflict_id and archived_conflict_id != conflict.conflict_id:
                raise ValueError(
                    f"conflict {conflict.conflict_id}: archive conflict_id mismatch in {target}"
                )
            _verify_archive_identity(conflict, archived, target)
            archived_count = int(archived.get("surfaced_count") or threshold)
            if archived_count < threshold:
                raise ValueError(
                    f"conflict {conflict.conflict_id}: archive count below threshold in {target}"
                )
            if not str(archived.get("auto_archived_at") or "").strip():
                raise ValueError(
                    f"conflict {conflict.conflict_id}: archive missing auto_archived_at in {target}"
                )
            if raw is None:
                return archived_count, target
            pending_count = int(raw.get("surfaced_count") or 0)
            expected_archive_count = (
                pending_count if pending_count >= threshold else pending_count + 1
            )
            if expected_archive_count < threshold:
                raise ValueError(
                    f"conflict {conflict.conflict_id}: premature archive in {target}"
                )
            if archived_count != expected_archive_count:
                raise ValueError(
                    f"conflict {conflict.conflict_id}: stale archive count in {target}"
                )
            conflict.path.unlink(missing_ok=True)
            _fsync_dir(conflict.path.parent)
            return archived_count, target
        if raw is None:
            raise ValueError(f"conflict disappeared before defer: {conflict.path}")
        current_count = int(raw.get("surfaced_count") or 0)
        if current_count >= threshold:
            new_count = current_count
        else:
            new_count = current_count + 1
        raw["surfaced_count"] = new_count
        if new_count >= threshold:
            raw["archive_reason"] = "deferred_threshold"
            raw["auto_archived_at"] = date_str
            payload = json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            archive_dir.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, payload)
            try:
                archived = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"conflict archive read-back failed: {target}") from exc
            if archived.get("archive_reason") != "deferred_threshold":
                raise RuntimeError(f"conflict archive missing defer reason: {target}")
            conflict.path.unlink(missing_ok=True)
            _fsync_dir(conflict.path.parent)
            return new_count, target
        payload = json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        _atomic_write(conflict.path, payload)
        return new_count, None


# --- Ship 2: unified numbering across observer + review + conflicts ----------


def resolve_index(
    idx: int,
    candidates: Sequence[Candidate],
    reviews: Sequence[ReviewItem],
    conflicts: Sequence[ConflictItem] | None = None,
) -> Tuple[str, object]:
    """Map a 1-based dashboard index to (kind, object).

    kind is "observer", "review", or "conflict". Raises IndexError for
    out-of-range.
    """
    conflicts = conflicts if conflicts is not None else []
    if idx < 1:
        raise IndexError(f"index {idx} below 1")
    n_obs = len(candidates)
    if idx <= n_obs:
        return ("observer", candidates[idx - 1])
    rel = idx - n_obs
    if rel <= len(reviews):
        return ("review", reviews[rel - 1])
    rel_conflict = rel - len(reviews)
    if rel_conflict <= len(conflicts):
        return ("conflict", conflicts[rel_conflict - 1])
    raise IndexError(
        f"index {idx} out of range (observer 1..{n_obs}, review "
        f"{n_obs + 1}..{n_obs + len(reviews)}, conflict "
        f"{n_obs + len(reviews) + 1}..{n_obs + len(reviews) + len(conflicts)})"
    )
