# K2B Integrated Loop Ship 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline with checkpoints) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the session-start dashboard + auto-apply routing + research-delivery-link rule so observer/review/research items stop rotting. Binary MVP: fixture with 5 observer candidates → 3 one-word accepts + 2 one-word rejects → 3 land in `self_improve_learnings.md`, 2 land in `observations.archive/`, 0 remain in `observer-candidates.md`. See `K2B-Vault/wiki/concepts/feature_k2b-integrated-loop.md` for the spec.

**Architecture:** Three primitives, all atomic: (1) a dashboard renderer called from the session-start hook that numbers items with stable content-hash IDs, (2) a routing script `loop-apply.sh` that accepts multiple `--accept/--reject/--defer` actions in one call and rewrites the three target files under flock, (3) a `follow-up-delivery:` frontmatter field added to the research-note template plus a `/lint` check. Python core (`loop_lib.py`) does the parsing, writing, and L-ID allocation; bash wrappers are thin.

**Tech Stack:** Python 3 (parsing/writing), bash (session-start hook, test harness), flock for locking (with mkdir fallback for macOS), existing K2B conventions from `scripts/observer-mark-processed.sh` and `scripts/ship-detect-tier.py`.

**Scope B applies.** Design decisions that come up mid-implementation: propose-and-proceed unless Keith vetoes. Do not stop to ask "should we X?" — decide, act, document the decision inline.

---

## File Structure

### Create

- `scripts/loop/__init__.py` — empty, makes the dir importable for tests
- `scripts/loop/loop_lib.py` — parsing + L-ID allocation + atomic writes (~300 LOC)
- `scripts/loop/loop-apply.sh` — bash entry point for routing, composes `--accept N --reject N --defer N`
- `scripts/loop/loop-render-dashboard.sh` — bash, calls python to render dashboard for session-start
- `scripts/loop/loop_render.py` — python renderer; emits dashboard text with stable item IDs
- `scripts/loop/loop_apply.py` — python entry point; invoked by `loop-apply.sh`
- `tests/fixtures/loop-mvp/observer-candidates.md` — frozen 5-candidate fixture
- `tests/fixtures/loop-mvp/self_improve_learnings.md` — baseline learnings file (empty-ish, with header + one existing L-ID to test allocation)
- `tests/loop/test_loop_lib.py` — python unit tests
- `tests/loop/loop-apply.test.sh` — bash integration test
- `tests/loop/loop-mvp.test.sh` — the binary MVP test

### Modify

- `scripts/hooks/session-start.sh` — replace "Check observer candidates" block (lines 58-63) with call to `loop-render-dashboard.sh` that renders numbered items; drop the bare-dump
- `scripts/lint-memory.sh` — add new check: research notes > 30 days with `follow-up-delivery: null` or absent
- `K2B-Vault/Templates/research-note.md` (if exists) OR a fresh template — add `follow-up-delivery:` field
- `K2B-Vault/wiki/concepts/feature_k2b-integrated-loop.md` — append "Updates" section when shipped
- `.claude/skills/k2b-research/SKILL.md` — update the research-note frontmatter template to include `follow-up-delivery:`

---

## Task 0: Create fixture files

**Files:**
- Create: `tests/fixtures/loop-mvp/observer-candidates.md`
- Create: `tests/fixtures/loop-mvp/self_improve_learnings.md`
- Create: `tests/fixtures/loop-mvp/observations.archive/.gitkeep`
- Create: `tests/fixtures/loop-mvp/expected-after-accept.md` (test expectation)
- Create: `tests/fixtures/loop-mvp/expected-after-reject.jsonl` (test expectation)

- [ ] **Step 1: Write the 5-candidate fixture**

The fixture mirrors the real observer-candidates.md format (seen in `K2B-Vault/wiki/context/observer-candidates.md`). We need 5 candidates the test can accept/reject deterministically. Use the 5 that were applied on 2026-04-22 per the TLDR as the fixture content — they are load-bearing, they match what the observer actually produced, and they are already in the learnings file (so the test asserting dedupe/collision behavior has a reference point).

Write to `tests/fixtures/loop-mvp/observer-candidates.md`:

```markdown
# Observer Candidates

Last analysis: 2026-04-22 21:44
Observations analyzed: 13

## Summary
Fixture for loop-mvp test. Five candidates from the 2026-04-22 21:44 observer run, frozen for deterministic testing. Do NOT edit — the loop-mvp test asserts exact content.

## Candidate Learnings (confirm with Keith)
- [high] workflow: Treat parse errors and silent failures as blocking invariants, not advisories -- fold before ship, freeze the shelf on parse error, refuse the status transition
  Evidence: WMM Commit 2 Codex pass 1 caught malformed-bullet silent delete; folded as "any parse error freezes the shelf" invariant
- [high] workflow: When an offline deadline looms, write a durable handoff note rather than commit partial state -- log-and-resume beats half-shipped
  Evidence: Session summary favoured durable handoff note over half-shipped state when given 1 min offline window
- [high] workflow: Shipping order is /ship first (wiki + admin lanes), /sync second (deploy lane) -- admin must complete before deploy
  Evidence: Session summary ship first, sync second when asked the ordering
- [medium] writing-style: When a data format choice affects retrieval quality, measure signal and switch formats empirically -- do not defend the canonical form if measurement says otherwise
  Evidence: On WMM Commit 2, canonical pipe-delimited shelf-row format drowned embedding signal when fed to sentence-transformers
- [medium] workflow: Accept multiple adversarial review passes when each pass produces a real finding -- gate quality beats shipping speed
  Evidence: On WMM Commit 2, four Codex adversarial review passes fired (pass 4 was approve); each of the first three passes produced a new HIGH that was real
```

- [ ] **Step 2: Write the baseline learnings fixture**

Write to `tests/fixtures/loop-mvp/self_improve_learnings.md`:

```markdown
---
name: K2B Learnings Log (Fixture)
description: Baseline learnings for loop-mvp test. Starts with one 2026-04-22 L-ID so the allocator has to skip to -002.
type: self-improve
---
# K2B Learnings (Fixture)

> Fixture for loop-mvp test. Starts with one existing 2026-04-22 L-ID to validate L-ID allocation.

### L-2026-04-22-000
distilled-rule: "Placeholder existing learning for L-ID allocation test."
- **Area:** test
- **Distilled rule:** Placeholder existing learning for L-ID allocation test.
- **Learning:** Fixture-only. Not used.
- **Context:** Fixture baseline.
- **Reinforced:** 1
- **Confidence:** low
- **Date:** 2026-04-22
```

- [ ] **Step 3: Ensure archive dir exists**

```bash
mkdir -p /Users/keithmbpm2/Projects/K2B/tests/fixtures/loop-mvp/observations.archive
touch /Users/keithmbpm2/Projects/K2B/tests/fixtures/loop-mvp/observations.archive/.gitkeep
```

- [ ] **Step 4: Commit fixtures**

```bash
git add tests/fixtures/loop-mvp/
git commit -m "test(loop): add loop-mvp fixtures -- 5-candidate observer + baseline learnings"
```

---

## Task 1: Python core — parse candidates file

**Files:**
- Create: `scripts/loop/__init__.py` (empty)
- Create: `scripts/loop/loop_lib.py`
- Create: `tests/loop/__init__.py` (empty)
- Create: `tests/loop/test_loop_lib.py`

- [ ] **Step 1: Write failing test for `parse_candidates`**

Create `tests/loop/test_loop_lib.py`:

```python
"""Unit tests for scripts/loop/loop_lib.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from loop import loop_lib  # noqa: E402


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "loop-mvp"


def test_parse_candidates_returns_five_items():
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    assert len(items) == 5
    assert items[0].severity == "high"
    assert items[0].area == "workflow"
    assert "parse errors" in items[0].rule.lower()
    assert items[0].evidence  # non-empty


def test_parse_candidates_assigns_stable_ids():
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    ids = [it.item_id for it in items]
    assert len(set(ids)) == 5  # all unique
    assert all(len(i) == 8 and all(c in "0123456789abcdef" for c in i) for i in ids)

    # Re-parsing yields same IDs (deterministic hash)
    items2 = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    assert [it.item_id for it in items2] == ids
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/keithmbpm2/Projects/K2B
python3 -m pytest tests/loop/test_loop_lib.py::test_parse_candidates_returns_five_items -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'loop'`.

- [ ] **Step 3: Write minimal `loop_lib.py`**

Create `scripts/loop/__init__.py` (empty) and `scripts/loop/loop_lib.py`:

```python
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

    # Find the Candidate Learnings section
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
        # Blank line or end of section; do nothing (flush on next header or end)

    flush()
    return items
```

- [ ] **Step 4: Run test to verify pass**

```bash
python3 -m pytest tests/loop/test_loop_lib.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/loop/__init__.py scripts/loop/loop_lib.py tests/loop/__init__.py tests/loop/test_loop_lib.py
git commit -m "feat(loop): parse observer-candidates.md into stable-id Candidate records"
```

---

## Task 2: Python core — L-ID allocation

**Files:**
- Modify: `scripts/loop/loop_lib.py`
- Modify: `tests/loop/test_loop_lib.py`

- [ ] **Step 1: Write failing test for `allocate_next_lid`**

Append to `tests/loop/test_loop_lib.py`:

```python
def test_allocate_next_lid_empty_for_date(tmp_path):
    # Learnings file with no entries for 2026-04-23 -> first allocation is -001
    learnings = tmp_path / "learnings.md"
    learnings.write_text("# empty\n\n### L-2026-04-22-007\n", encoding="utf-8")
    assert loop_lib.allocate_next_lid(learnings, "2026-04-23") == "L-2026-04-23-001"


def test_allocate_next_lid_skips_existing(tmp_path):
    # Fixture has L-2026-04-22-000; next should be -001
    learnings = tmp_path / "learnings.md"
    learnings.write_text(
        "### L-2026-04-22-000\n\n### L-2026-04-22-002\n", encoding="utf-8"
    )
    assert loop_lib.allocate_next_lid(learnings, "2026-04-22") == "L-2026-04-22-003"


def test_allocate_next_lid_handles_missing_file(tmp_path):
    assert loop_lib.allocate_next_lid(tmp_path / "nope.md", "2026-04-22") == "L-2026-04-22-001"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/loop/test_loop_lib.py::test_allocate_next_lid_empty_for_date -v
```

Expected: FAIL with `AttributeError: module ... has no attribute 'allocate_next_lid'`.

- [ ] **Step 3: Implement `allocate_next_lid`**

Append to `scripts/loop/loop_lib.py`:

```python
_LID_PATTERN = re.compile(
    r"^### (L-(?P<date>\d{4}-\d{2}-\d{2})-(?P<num>\d{3}))\b", re.MULTILINE
)


def allocate_next_lid(learnings_path: Path, date_str: str) -> str:
    """Return the next unused L-YYYY-MM-DD-NNN for the given date.

    Scans the learnings file for existing L-IDs matching date_str and
    returns the successor of the max NNN. Missing file -> start at 001.
    """
    max_num = 0
    if learnings_path.exists():
        text = learnings_path.read_text(encoding="utf-8")
        for m in _LID_PATTERN.finditer(text):
            if m.group("date") == date_str:
                max_num = max(max_num, int(m.group("num")))
    return f"L-{date_str}-{max_num + 1:03d}"
```

- [ ] **Step 4: Run test to verify pass**

```bash
python3 -m pytest tests/loop/test_loop_lib.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/loop/loop_lib.py tests/loop/test_loop_lib.py
git commit -m "feat(loop): allocate_next_lid scans learnings for per-date max and returns successor"
```

---

## Task 3: Python core — rewrite candidates (remove by id)

**Files:**
- Modify: `scripts/loop/loop_lib.py`
- Modify: `tests/loop/test_loop_lib.py`

- [ ] **Step 1: Write failing test for `rewrite_candidates_without`**

Append to `tests/loop/test_loop_lib.py`:

```python
def test_rewrite_candidates_removes_specified_ids(tmp_path):
    src = FIXTURE_DIR / "observer-candidates.md"
    dst = tmp_path / "observer-candidates.md"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    before = loop_lib.parse_candidates(dst)
    assert len(before) == 5
    remove_ids = {before[0].item_id, before[2].item_id}

    loop_lib.rewrite_candidates_without(dst, remove_ids)

    after = loop_lib.parse_candidates(dst)
    assert len(after) == 3
    assert {c.item_id for c in after}.isdisjoint(remove_ids)
    # Kept items preserve original order
    kept_rules = [c.rule for c in after]
    assert kept_rules == [before[1].rule, before[3].rule, before[4].rule]


def test_rewrite_candidates_noop_when_nothing_to_remove(tmp_path):
    src = FIXTURE_DIR / "observer-candidates.md"
    dst = tmp_path / "observer-candidates.md"
    original = src.read_text(encoding="utf-8")
    dst.write_text(original, encoding="utf-8")
    loop_lib.rewrite_candidates_without(dst, set())
    assert dst.read_text(encoding="utf-8") == original


def test_rewrite_candidates_empties_section_when_all_removed(tmp_path):
    src = FIXTURE_DIR / "observer-candidates.md"
    dst = tmp_path / "observer-candidates.md"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    items = loop_lib.parse_candidates(dst)
    loop_lib.rewrite_candidates_without(dst, {c.item_id for c in items})
    assert loop_lib.parse_candidates(dst) == []
    # Section header preserved; detected patterns preserved
    remaining = dst.read_text(encoding="utf-8")
    assert "## Candidate Learnings" in remaining
    assert "## Detected Patterns" in remaining or "## Summary" in remaining
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/loop/test_loop_lib.py::test_rewrite_candidates_removes_specified_ids -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `rewrite_candidates_without`**

Append to `scripts/loop/loop_lib.py`:

```python
import os
import tempfile


def rewrite_candidates_without(path: Path, remove_ids: set[str]) -> None:
    """Rewrite observer-candidates.md omitting items whose id is in remove_ids.

    Atomic: writes to a sibling tempfile then os.replace()s onto path.
    Preserves non-candidate sections (Summary, Detected Patterns) verbatim.
    """
    if not remove_ids:
        return
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    items = parse_candidates(path)
    kept = [it for it in items if it.item_id not in remove_ids]

    # Rebuild the Candidate Learnings section. Everything outside the section
    # stays byte-identical.
    m = re.search(
        r"(^## Candidate Learnings[^\n]*\n)(.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        return
    header, _body = m.group(1), m.group(2)

    lines = [header]
    for it in kept:
        lines.append(f"- [{it.severity}] {it.area}: {it.rule}\n")
        if it.evidence:
            lines.append(f"  Evidence: {it.evidence}\n")
    new_section = "".join(lines)
    # Preserve the trailing structure (blank line before next `## ` or EOF)
    if m.end() < len(text) and text[m.end():].startswith("\n"):
        pass  # next section starts cleanly
    else:
        new_section = new_section.rstrip("\n") + "\n\n"

    new_text = text[: m.start()] + new_section + text[m.end():]

    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".tmp_{path.name}_", suffix=".md"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
```

- [ ] **Step 4: Run test to verify pass**

```bash
python3 -m pytest tests/loop/test_loop_lib.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/loop/loop_lib.py tests/loop/test_loop_lib.py
git commit -m "feat(loop): rewrite observer-candidates.md atomically, removing processed items"
```

---

## Task 4: Python core — append learning and archive reject

**Files:**
- Modify: `scripts/loop/loop_lib.py`
- Modify: `tests/loop/test_loop_lib.py`

- [ ] **Step 1: Write failing tests for `append_learning` and `archive_reject`**

Append to `tests/loop/test_loop_lib.py`:

```python
def test_append_learning_writes_expected_entry(tmp_path):
    learnings = tmp_path / "learnings.md"
    learnings.write_text("# K2B Learnings\n\n", encoding="utf-8")
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    cand = items[0]
    lid = loop_lib.append_learning(
        learnings, cand, date_str="2026-04-23", observer_run="2026-04-22 21:44"
    )
    assert lid == "L-2026-04-23-001"
    text = learnings.read_text(encoding="utf-8")
    assert "### L-2026-04-23-001" in text
    assert f'distilled-rule: "{cand.rule}"' in text
    assert f"- **Area:** {cand.area}" in text
    assert f"- **Distilled rule:** {cand.rule}" in text
    assert f"- **Confidence:** {cand.severity}" in text
    assert "- **Reinforced:** 1" in text
    assert "- **Date:** 2026-04-23" in text
    assert "- **Source:** observer-candidates (auto-applied 2026-04-23 via session-start dashboard)" in text
    assert cand.evidence in text


def test_append_learning_increments_for_same_day(tmp_path):
    learnings = tmp_path / "learnings.md"
    learnings.write_text("# empty\n", encoding="utf-8")
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    lid1 = loop_lib.append_learning(learnings, items[0], date_str="2026-04-23", observer_run="run")
    lid2 = loop_lib.append_learning(learnings, items[1], date_str="2026-04-23", observer_run="run")
    assert lid1 == "L-2026-04-23-001"
    assert lid2 == "L-2026-04-23-002"


def test_archive_reject_writes_jsonl_line(tmp_path):
    archive_dir = tmp_path / "observations.archive"
    archive_dir.mkdir()
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    cand = items[3]
    loop_lib.archive_reject(archive_dir, cand, date_str="2026-04-23", actor="keith")
    target = archive_dir / "rejected-2026-04-23.jsonl"
    assert target.exists()
    line = target.read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["item_id"] == cand.item_id
    assert record["severity"] == cand.severity
    assert record["area"] == cand.area
    assert record["rule"] == cand.rule
    assert record["evidence"] == cand.evidence
    assert record["rejected"] == "keith 2026-04-23"


def test_archive_reject_appends_without_clobber(tmp_path):
    archive_dir = tmp_path / "observations.archive"
    archive_dir.mkdir()
    items = loop_lib.parse_candidates(FIXTURE_DIR / "observer-candidates.md")
    loop_lib.archive_reject(archive_dir, items[3], date_str="2026-04-23", actor="keith")
    loop_lib.archive_reject(archive_dir, items[4], date_str="2026-04-23", actor="keith")
    target = archive_dir / "rejected-2026-04-23.jsonl"
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/loop/test_loop_lib.py::test_append_learning_writes_expected_entry -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `append_learning` and `archive_reject`**

Append to `scripts/loop/loop_lib.py`:

```python
import json


_LEARNING_TEMPLATE = """
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
    """Append a new L-ID entry for the candidate. Returns the allocated L-ID.

    Uses `allocate_next_lid` for NNN. Appends atomically (read-modify-write
    under an OS-level lock is done by the bash wrapper; this helper assumes
    single-writer semantics for the duration of the call).
    """
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
    new_text = existing + block.lstrip("\n")
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


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".tmp_{path.name}_", suffix=".md"
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
```

Note: factor the existing `rewrite_candidates_without` to use `_atomic_write` in a follow-up mini-refactor inline.

- [ ] **Step 4: Refactor `rewrite_candidates_without` to use `_atomic_write`**

Replace the tempfile block in `rewrite_candidates_without` with `_atomic_write(path, new_text)`.

- [ ] **Step 5: Run tests to verify pass**

```bash
python3 -m pytest tests/loop/test_loop_lib.py -v
```

Expected: 12 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/loop/loop_lib.py tests/loop/test_loop_lib.py
git commit -m "feat(loop): append_learning + archive_reject with atomic writes"
```

---

## Task 5: Bash wrapper `loop-apply.sh` + python entry point

**Files:**
- Create: `scripts/loop/loop_apply.py`
- Create: `scripts/loop/loop-apply.sh`
- Create: `tests/loop/loop-apply.test.sh`

- [ ] **Step 1: Write the failing bash integration test**

Create `tests/loop/loop-apply.test.sh`:

```bash
#!/usr/bin/env bash
# Integration test for scripts/loop/loop-apply.sh.
# Copies the fixture to a tmp dir, invokes loop-apply, verifies mutations.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/loop-mvp"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Stage sandbox
cp "$FIXTURE/observer-candidates.md" "$TMP/observer-candidates.md"
cp "$FIXTURE/self_improve_learnings.md" "$TMP/self_improve_learnings.md"
mkdir -p "$TMP/observations.archive"

export K2B_LOOP_CANDIDATES="$TMP/observer-candidates.md"
export K2B_LOOP_LEARNINGS="$TMP/self_improve_learnings.md"
export K2B_LOOP_ARCHIVE_DIR="$TMP/observations.archive"
export K2B_LOOP_DATE="2026-04-23"
export K2B_LOOP_ACTOR="keith"
export K2B_LOOP_OBSERVER_RUN="2026-04-22 21:44"

# Accept first three, reject last two (MVP scenario)
"$ROOT/scripts/loop/loop-apply.sh" --accept 1 --accept 2 --accept 3 --reject 4 --reject 5

# Verify learnings got 3 entries (plus baseline L-2026-04-22-000 already in fixture)
count=$(grep -cE '^### L-2026-04-23-00[123]$' "$TMP/self_improve_learnings.md")
if [ "$count" != "3" ]; then
  echo "FAIL: expected 3 new L-2026-04-23-00X entries, got $count" >&2
  exit 1
fi

# Verify archive has 2 rejects
archive="$TMP/observations.archive/rejected-2026-04-23.jsonl"
if [ ! -f "$archive" ]; then
  echo "FAIL: archive file missing: $archive" >&2
  exit 1
fi
rejects=$(wc -l < "$archive" | tr -d ' ')
if [ "$rejects" != "2" ]; then
  echo "FAIL: expected 2 reject lines, got $rejects" >&2
  exit 1
fi

# Verify candidates file has zero remaining Candidate Learnings items
remaining=$(awk '
  /^## Candidate Learnings/ { inside=1; next }
  /^## / && inside { exit }
  inside && /^- \[/ { n++ }
  END { print n+0 }
' "$TMP/observer-candidates.md")
if [ "$remaining" != "0" ]; then
  echo "FAIL: expected 0 remaining candidates, got $remaining" >&2
  exit 1
fi

echo "PASS: loop-apply.test.sh"
```

Make it executable:

```bash
chmod +x tests/loop/loop-apply.test.sh
```

- [ ] **Step 2: Run test to verify it fails**

```bash
bash tests/loop/loop-apply.test.sh
```

Expected: FAIL (script doesn't exist).

- [ ] **Step 3: Write `loop_apply.py`**

Create `scripts/loop/loop_apply.py`:

```python
"""CLI entry point for loop routing. Invoked by scripts/loop/loop-apply.sh.

Reads actions from argv, mutates:
  - $K2B_LOOP_LEARNINGS  (append L-IDs on accept)
  - $K2B_LOOP_ARCHIVE_DIR/rejected-YYYY-MM-DD.jsonl  (append on reject)
  - $K2B_LOOP_CANDIDATES (remove both accepted and rejected)

Defers are no-ops at the routing level; the dashboard tracks defer
counters in a separate Task 7 once the primitive is proven.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop import loop_lib


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(f"loop-apply: missing env var {name}\n")
        sys.exit(2)
    return v


def main() -> int:
    parser = argparse.ArgumentParser(description="K2B loop routing.")
    parser.add_argument("--accept", type=int, action="append", default=[])
    parser.add_argument("--reject", type=int, action="append", default=[])
    parser.add_argument("--defer", type=int, action="append", default=[])
    args = parser.parse_args()

    candidates_path = Path(_env("K2B_LOOP_CANDIDATES"))
    learnings_path = Path(_env("K2B_LOOP_LEARNINGS"))
    archive_dir = Path(_env("K2B_LOOP_ARCHIVE_DIR"))
    date_str = _env("K2B_LOOP_DATE")
    actor = _env("K2B_LOOP_ACTOR")
    observer_run = _env("K2B_LOOP_OBSERVER_RUN")

    items = loop_lib.parse_candidates(candidates_path)
    if not items:
        print("loop-apply: no candidates to route", file=sys.stderr)
        return 0

    total = len(items)
    def _pick(idx: int) -> loop_lib.Candidate:
        if idx < 1 or idx > total:
            raise SystemExit(f"loop-apply: index {idx} out of range 1..{total}")
        return items[idx - 1]

    accepted = [_pick(i) for i in args.accept]
    rejected = [_pick(i) for i in args.reject]
    # defer is currently a no-op; exercised by the binary MVP via separate test

    for cand in accepted:
        lid = loop_lib.append_learning(
            learnings_path, cand, date_str=date_str, observer_run=observer_run
        )
        print(f"accepted {cand.item_id} -> {lid}")

    for cand in rejected:
        loop_lib.archive_reject(
            archive_dir, cand, date_str=date_str, actor=actor
        )
        print(f"rejected {cand.item_id}")

    remove_ids = {c.item_id for c in accepted} | {c.item_id for c in rejected}
    if remove_ids:
        loop_lib.rewrite_candidates_without(candidates_path, remove_ids)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write `loop-apply.sh` with flock**

Create `scripts/loop/loop-apply.sh`:

```bash
#!/usr/bin/env bash
# K2B integrated-loop routing entry point.
# Applies accept/reject/defer actions to observer candidates under flock.
# Env vars (consumed by loop_apply.py):
#   K2B_LOOP_CANDIDATES, K2B_LOOP_LEARNINGS, K2B_LOOP_ARCHIVE_DIR,
#   K2B_LOOP_DATE, K2B_LOOP_ACTOR, K2B_LOOP_OBSERVER_RUN
# Defaults are set below when the env var is unset, so day-to-day
# invocation doesn't need a wrapper.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

VAULT_DEFAULT="$HOME/Projects/K2B-Vault"
MEM_DEFAULT="$HOME/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory"

export K2B_LOOP_CANDIDATES="${K2B_LOOP_CANDIDATES:-$VAULT_DEFAULT/wiki/context/observer-candidates.md}"
export K2B_LOOP_LEARNINGS="${K2B_LOOP_LEARNINGS:-$MEM_DEFAULT/self_improve_learnings.md}"
export K2B_LOOP_ARCHIVE_DIR="${K2B_LOOP_ARCHIVE_DIR:-$VAULT_DEFAULT/wiki/context/observations.archive}"
export K2B_LOOP_DATE="${K2B_LOOP_DATE:-$(date '+%Y-%m-%d')}"
export K2B_LOOP_ACTOR="${K2B_LOOP_ACTOR:-keith}"
export K2B_LOOP_OBSERVER_RUN="${K2B_LOOP_OBSERVER_RUN:-unknown}"

LOCK="${K2B_LOOP_LOCK:-/tmp/k2b-loop-apply.lock}"

run_apply() {
  python3 "$SCRIPT_DIR/loop_apply.py" "$@"
}

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -x 9
  run_apply "$@"
else
  LOCK_DIR="${LOCK}.d"
  TRIES=0
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    TRIES=$((TRIES + 1))
    if [ "$TRIES" -gt 200 ]; then
      echo "loop-apply: could not acquire $LOCK_DIR after 10s" >&2
      exit 4
    fi
    sleep 0.05
  done
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
  run_apply "$@"
fi
```

Make executable:

```bash
chmod +x scripts/loop/loop-apply.sh
```

- [ ] **Step 5: Run integration test**

```bash
bash tests/loop/loop-apply.test.sh
```

Expected: `PASS: loop-apply.test.sh`.

- [ ] **Step 6: Commit**

```bash
git add scripts/loop/loop_apply.py scripts/loop/loop-apply.sh tests/loop/loop-apply.test.sh
git commit -m "feat(loop): loop-apply.sh routes accept/reject/defer under flock"
```

---

## Task 6: Dashboard renderer

**Files:**
- Create: `scripts/loop/loop_render.py`
- Create: `scripts/loop/loop-render-dashboard.sh`
- Create: `tests/loop/loop-render.test.sh`

- [ ] **Step 1: Write failing render test**

Create `tests/loop/loop-render.test.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/loop-mvp"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp "$FIXTURE/observer-candidates.md" "$TMP/observer-candidates.md"
mkdir -p "$TMP/review" "$TMP/raw/research"

export K2B_LOOP_CANDIDATES="$TMP/observer-candidates.md"
export K2B_LOOP_REVIEW_DIR="$TMP/review"
export K2B_LOOP_RESEARCH_DIR="$TMP/raw/research"

out="$("$ROOT/scripts/loop/loop-render-dashboard.sh")"

# Must render a header
echo "$out" | grep -q "K2B LOOP DASHBOARD" || { echo "FAIL: missing header"; exit 1; }
# Must show 5 numbered candidates
echo "$out" | grep -Eq "^\s*\[1\] \[high\]" || { echo "FAIL: missing [1] high"; exit 1; }
echo "$out" | grep -Eq "^\s*\[5\] \[medium\]" || { echo "FAIL: missing [5] medium"; exit 1; }
# Must emit routing grammar
echo "$out" | grep -q "a N / r N / d N" || { echo "FAIL: missing grammar hint"; exit 1; }
# Must include section counts
echo "$out" | grep -qE "Observer candidates \(5\)" || { echo "FAIL: bad candidate count"; exit 1; }
echo "PASS: loop-render.test.sh"
```

```bash
chmod +x tests/loop/loop-render.test.sh
```

- [ ] **Step 2: Run test to verify it fails**

```bash
bash tests/loop/loop-render.test.sh
```

Expected: FAIL.

- [ ] **Step 3: Implement renderer**

Create `scripts/loop/loop_render.py`:

```python
"""Render the session-start loop dashboard.

Reads:
  - $K2B_LOOP_CANDIDATES      observer-candidates.md
  - $K2B_LOOP_REVIEW_DIR      review/ directory (flat .md files)
  - $K2B_LOOP_RESEARCH_DIR    raw/research/ directory

Emits a compact dashboard to stdout. Empty sections collapse.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from loop import loop_lib


def _find_research_without_delivery(research_dir: Path, today: date) -> list[tuple[Path, int]]:
    """Return [(path, age_days), ...] for research notes older than 30 days
    with `follow-up-delivery:` null/absent in frontmatter."""
    if not research_dir.is_dir():
        return []
    out: list[tuple[Path, int]] = []
    for p in sorted(research_dir.glob("*.md")):
        try:
            head = p.read_text(encoding="utf-8").splitlines()[:40]
        except OSError:
            continue
        fm_end = None
        in_fm = False
        has_delivery = False
        for i, line in enumerate(head):
            if i == 0 and line.strip() == "---":
                in_fm = True
                continue
            if in_fm and line.strip() == "---":
                fm_end = i
                break
            if in_fm and re.match(r"^follow-up-delivery:\s*\S", line):
                # Non-empty value means committed (including "none")
                if "null" not in line.lower():
                    has_delivery = True
        if fm_end is None:
            continue  # not a proper frontmatter note
        if has_delivery:
            continue
        # Age by filename date prefix YYYY-MM-DD_*
        m = re.match(r"(\d{4}-\d{2}-\d{2})_", p.name)
        if not m:
            continue
        try:
            when = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        age_days = (today - when).days
        if age_days > 30:
            out.append((p, age_days))
    # Oldest first, cap at 3
    out.sort(key=lambda t: -t[1])
    return out[:3]


def _list_review_items(review_dir: Path) -> list[Path]:
    if not review_dir.is_dir():
        return []
    return sorted(
        p
        for p in review_dir.glob("*.md")
        if p.name not in {"index.md"}
    )


def main() -> int:
    candidates_path = Path(os.environ["K2B_LOOP_CANDIDATES"])
    review_dir = Path(os.environ.get("K2B_LOOP_REVIEW_DIR", ""))
    research_dir = Path(os.environ.get("K2B_LOOP_RESEARCH_DIR", ""))
    today = date.today()

    candidates = loop_lib.parse_candidates(candidates_path) if candidates_path.exists() else []
    reviews = _list_review_items(review_dir) if review_dir else []
    researches = _find_research_without_delivery(research_dir, today) if research_dir else []

    if not candidates and not reviews and not researches:
        return 0

    lines: list[str] = []
    lines.append(f"## K2B LOOP DASHBOARD -- {today.isoformat()}")
    lines.append("")
    lines.append("Routing grammar: respond with one token per item, e.g. `a1 a2 r3 d4`.")
    lines.append("  a N = ACCEPT item N (apply routing)")
    lines.append("  r N = REJECT item N (archive)")
    lines.append("  d N = DEFER item N (leave for next session)")
    lines.append("Claude will call `scripts/loop/loop-apply.sh` with your choices before the next prompt.")
    lines.append("Routing grammar summary: a N / r N / d N")
    lines.append("")

    idx = 0
    if candidates:
        lines.append(f"### Observer candidates ({len(candidates)})")
        for cand in candidates:
            idx += 1
            lines.append(f"  [{idx}] [{cand.severity}] {cand.item_id} · {cand.area} · {cand.rule}")
        lines.append("")

    if reviews:
        lines.append(f"### Review queue ({len(reviews)})")
        for p in reviews:
            idx += 1
            lines.append(f"  [{idx}] {p.name}")
        lines.append("")

    if researches:
        lines.append(f"### Research without delivery link ({len(researches)})")
        for p, age in researches:
            idx += 1
            lines.append(f"  [{idx}] {p.name} (age {age} days)")
        lines.append("")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `scripts/loop/loop-render-dashboard.sh`:

```bash
#!/usr/bin/env bash
# Render the K2B loop dashboard. Called from scripts/hooks/session-start.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VAULT_DEFAULT="$HOME/Projects/K2B-Vault"
export K2B_LOOP_CANDIDATES="${K2B_LOOP_CANDIDATES:-$VAULT_DEFAULT/wiki/context/observer-candidates.md}"
export K2B_LOOP_REVIEW_DIR="${K2B_LOOP_REVIEW_DIR:-$VAULT_DEFAULT/review}"
export K2B_LOOP_RESEARCH_DIR="${K2B_LOOP_RESEARCH_DIR:-$VAULT_DEFAULT/raw/research}"

python3 "$SCRIPT_DIR/loop_render.py"
```

```bash
chmod +x scripts/loop/loop-render-dashboard.sh
```

- [ ] **Step 4: Run render test**

```bash
bash tests/loop/loop-render.test.sh
```

Expected: `PASS: loop-render.test.sh`.

- [ ] **Step 5: Commit**

```bash
git add scripts/loop/loop_render.py scripts/loop/loop-render-dashboard.sh tests/loop/loop-render.test.sh
git commit -m "feat(loop): render dashboard with numbered items + routing grammar"
```

---

## Task 7: Wire into session-start.sh

**Files:**
- Modify: `scripts/hooks/session-start.sh`

- [ ] **Step 1: Replace the observer-dump block**

In `scripts/hooks/session-start.sh`, lines 58-63 currently read:

```bash
# --- 3. Check observer candidates ---
candidates="$CONTEXT_DIR/observer-candidates.md"
if [ -f "$candidates" ] && [ -s "$candidates" ]; then
  output+="OBSERVER FINDINGS (review HIGH confidence items -- confirm or reject inline):"$'\n'
  output+="$(cat "$candidates")"$'\n\n'
fi
```

Replace with:

```bash
# --- 3. K2B loop dashboard (observer candidates + review + research-delivery) ---
# Replaces bare observer-candidates dump. The dashboard numbers items with
# stable content-hash IDs so Claude can route one-word accept/reject/defer
# tokens back to scripts/loop/loop-apply.sh.
dashboard_output="$("$K2B/scripts/loop/loop-render-dashboard.sh" 2>/dev/null || true)"
if [ -n "$dashboard_output" ]; then
  output+="$dashboard_output"$'\n\n'
  output+="LOOP ROUTING INSTRUCTION: If Keith's first message contains one-word tokens matching"$'\n'
  output+="the grammar above (a N / r N / d N), call scripts/loop/loop-apply.sh with the"$'\n'
  output+="translated --accept N / --reject N / --defer N flags before doing anything else."$'\n\n'
fi
```

- [ ] **Step 2: Smoke-test the hook against live data**

```bash
CLAUDE_PROJECT_DIR=/Users/keithmbpm2/Projects/K2B bash /Users/keithmbpm2/Projects/K2B/scripts/hooks/session-start.sh | head -60
```

Expected: output includes a `## K2B LOOP DASHBOARD` header and numbered candidates IF the live `wiki/context/observer-candidates.md` has candidates. If it's empty, dashboard section is absent.

- [ ] **Step 3: Commit**

```bash
git add scripts/hooks/session-start.sh
git commit -m "feat(loop): session-start hook renders loop dashboard (replaces observer dump)"
```

---

## Task 8: Research delivery-link rule

**Files:**
- Modify: `.claude/skills/k2b-research/SKILL.md` (if the research note template lives there)
- Modify: `scripts/lint-memory.sh`
- Create: `tests/loop/lint-research-delivery.test.sh`

- [ ] **Step 1: Locate the research-note frontmatter template**

```bash
grep -rn "type: research" /Users/keithmbpm2/Projects/K2B/.claude/skills/ /Users/keithmbpm2/Projects/K2B-Vault/Templates/ 2>/dev/null | head
```

Expected: finds 1-2 locations. Update whichever is the canonical template.

- [ ] **Step 2: Add `follow-up-delivery:` to the template**

In the frontmatter block of the template, insert (alphabetical-ish with existing fields):

```yaml
follow-up-delivery: null  # <feature-slug> when this research commits to a feature, "none" when purely informational, null when pending
```

- [ ] **Step 3: Add lint check — failing test first**

Create `tests/loop/lint-research-delivery.test.sh`:

```bash
#!/usr/bin/env bash
# Smoke test for the research-without-delivery-link lint check.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/raw/research"

# Stale note (> 30 days, no delivery link)
old_date=$(date -v-60d '+%Y-%m-%d')
cat > "$TMP/raw/research/${old_date}_research_old.md" <<EOF
---
type: research
date: ${old_date}
follow-up-delivery: null
---
# old
EOF

# Fresh note (< 30 days, no delivery link) -- should NOT be flagged
new_date=$(date -v-3d '+%Y-%m-%d')
cat > "$TMP/raw/research/${new_date}_research_fresh.md" <<EOF
---
type: research
date: ${new_date}
follow-up-delivery: null
---
# fresh
EOF

# Stale note WITH delivery link -- should NOT be flagged
cat > "$TMP/raw/research/${old_date}_research_linked.md" <<EOF
---
type: research
date: ${old_date}
follow-up-delivery: feature_foo
---
# linked
EOF

export K2B_LINT_RESEARCH_DIR="$TMP/raw/research"
out="$("$ROOT/scripts/loop/lint-research-delivery.sh")"

if ! echo "$out" | grep -q "${old_date}_research_old.md"; then
  echo "FAIL: old stale note not flagged"
  exit 1
fi
if echo "$out" | grep -q "${new_date}_research_fresh.md"; then
  echo "FAIL: fresh note was flagged"
  exit 1
fi
if echo "$out" | grep -q "${old_date}_research_linked.md"; then
  echo "FAIL: linked note was flagged"
  exit 1
fi
echo "PASS: lint-research-delivery.test.sh"
```

```bash
chmod +x tests/loop/lint-research-delivery.test.sh
```

- [ ] **Step 4: Implement the lint check script**

Create `scripts/loop/lint-research-delivery.sh`:

```bash
#!/usr/bin/env bash
# Flag raw/research/*.md notes > 30 days old with `follow-up-delivery: null`
# or absent. Called from /lint. Stdout = list of flagged paths (one per line).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VAULT_DEFAULT="$HOME/Projects/K2B-Vault"
RESEARCH_DIR="${K2B_LINT_RESEARCH_DIR:-$VAULT_DEFAULT/raw/research}"

python3 - <<'PYEOF'
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

research_dir = Path(os.environ.get("K2B_LINT_RESEARCH_DIR") or Path.home() / "Projects" / "K2B-Vault" / "raw" / "research")
today = date.today()
if not research_dir.is_dir():
    sys.exit(0)

for p in sorted(research_dir.glob("*.md")):
    m = re.match(r"(\d{4}-\d{2}-\d{2})_", p.name)
    if not m:
        continue
    try:
        when = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        continue
    age_days = (today - when).days
    if age_days <= 30:
        continue
    try:
        head = p.read_text(encoding="utf-8").splitlines()[:40]
    except OSError:
        continue
    in_fm = False
    fm_closed = False
    has_delivery = False
    for i, line in enumerate(head):
        if i == 0 and line.strip() == "---":
            in_fm = True
            continue
        if in_fm and line.strip() == "---":
            fm_closed = True
            break
        if in_fm and re.match(r"^follow-up-delivery:\s*\S", line):
            if "null" not in line.lower():
                has_delivery = True
    if not fm_closed:
        continue
    if has_delivery:
        continue
    print(f"{p.name} (age {age_days} days, follow-up-delivery missing/null)")
PYEOF
```

```bash
chmod +x scripts/loop/lint-research-delivery.sh
```

- [ ] **Step 5: Run the lint test**

```bash
bash tests/loop/lint-research-delivery.test.sh
```

Expected: `PASS`.

- [ ] **Step 6: Wire into lint-memory.sh (optional integration)**

In `scripts/lint-memory.sh`, find the end of the last check block and append:

```bash
# --- Research without delivery commitment ---
stale_research="$("$K2B"/scripts/loop/lint-research-delivery.sh 2>/dev/null || true)"
if [ -n "$stale_research" ]; then
  echo ""
  echo "## Research without delivery commitment (follow-up-delivery null/absent, > 30 days):"
  echo "$stale_research"
fi
```

Adapt the `$K2B` variable reference to whatever the surrounding script uses (it may be `$REPO_ROOT` or similar).

- [ ] **Step 7: Commit**

```bash
git add scripts/loop/lint-research-delivery.sh tests/loop/lint-research-delivery.test.sh scripts/lint-memory.sh .claude/skills/k2b-research/SKILL.md
git commit -m "feat(loop): research-requires-delivery-link rule + /lint flag for stale research"
```

---

## Task 9: Binary MVP end-to-end test

**Files:**
- Create: `tests/loop/loop-mvp.test.sh`

- [ ] **Step 1: Write the MVP test (the binary gate)**

Create `tests/loop/loop-mvp.test.sh`:

```bash
#!/usr/bin/env bash
# BINARY MVP TEST for feature_k2b-integrated-loop.
# Scenario: 5 fixture candidates -> 3 accepts + 2 rejects via loop-apply.sh.
# Pass conditions (all must hold):
#   1. self_improve_learnings.md has 3 new L-2026-04-23-NNN entries with
#      Source: observer-candidates field.
#   2. observations.archive/rejected-2026-04-23.jsonl has 2 lines with
#      `rejected: keith 2026-04-23` marker.
#   3. observer-candidates.md has 0 remaining candidates.
#   4. Learnings dedupe invariant: no duplicate L-ID.
#   5. Dashboard renders the fixture with 5 numbered items BEFORE routing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURE="$ROOT/tests/fixtures/loop-mvp"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cp "$FIXTURE/observer-candidates.md" "$TMP/observer-candidates.md"
cp "$FIXTURE/self_improve_learnings.md" "$TMP/self_improve_learnings.md"
mkdir -p "$TMP/observations.archive" "$TMP/review" "$TMP/raw/research"

export K2B_LOOP_CANDIDATES="$TMP/observer-candidates.md"
export K2B_LOOP_LEARNINGS="$TMP/self_improve_learnings.md"
export K2B_LOOP_ARCHIVE_DIR="$TMP/observations.archive"
export K2B_LOOP_REVIEW_DIR="$TMP/review"
export K2B_LOOP_RESEARCH_DIR="$TMP/raw/research"
export K2B_LOOP_DATE="2026-04-23"
export K2B_LOOP_ACTOR="keith"
export K2B_LOOP_OBSERVER_RUN="2026-04-22 21:44"

# --- Gate 5: dashboard renders with 5 numbered items ---
dashboard="$("$ROOT/scripts/loop/loop-render-dashboard.sh")"
for n in 1 2 3 4 5; do
  echo "$dashboard" | grep -Eq "^\s*\[${n}\] " || { echo "FAIL gate 5: dashboard missing [${n}]"; echo "$dashboard"; exit 1; }
done
echo "$dashboard" | grep -q "Observer candidates (5)" || { echo "FAIL gate 5: wrong count"; exit 1; }
echo "  gate 5 PASS: dashboard renders 5 numbered items"

# --- Invoke routing (Keith types `a1 a2 a3 r4 r5`) ---
"$ROOT/scripts/loop/loop-apply.sh" --accept 1 --accept 2 --accept 3 --reject 4 --reject 5

# --- Gate 1: learnings file has 3 new L-2026-04-23-NNN entries ---
new_lids=$(grep -cE '^### L-2026-04-23-00[123]$' "$TMP/self_improve_learnings.md" || true)
if [ "$new_lids" != "3" ]; then
  echo "FAIL gate 1: expected 3 L-2026-04-23-00[123] entries, got $new_lids"
  exit 1
fi
source_tags=$(grep -c '^- \*\*Source:\*\* observer-candidates (auto-applied 2026-04-23 via session-start dashboard)' "$TMP/self_improve_learnings.md" || true)
if [ "$source_tags" != "3" ]; then
  echo "FAIL gate 1: expected 3 Source: observer-candidates tags, got $source_tags"
  exit 1
fi
echo "  gate 1 PASS: 3 new L-IDs with Source tag"

# --- Gate 2: archive has 2 reject lines ---
archive="$TMP/observations.archive/rejected-2026-04-23.jsonl"
if [ ! -f "$archive" ]; then echo "FAIL gate 2: archive missing"; exit 1; fi
rej=$(wc -l < "$archive" | tr -d ' ')
if [ "$rej" != "2" ]; then echo "FAIL gate 2: expected 2 rejects, got $rej"; exit 1; fi
if ! grep -q '"rejected": "keith 2026-04-23"' "$archive"; then
  echo "FAIL gate 2: rejected marker missing"
  exit 1
fi
echo "  gate 2 PASS: 2 rejects archived with keith marker"

# --- Gate 3: observer-candidates has 0 remaining items ---
remaining=$(awk '
  /^## Candidate Learnings/ { inside=1; next }
  /^## / && inside { exit }
  inside && /^- \[/ { n++ }
  END { print n+0 }
' "$TMP/observer-candidates.md")
if [ "$remaining" != "0" ]; then
  echo "FAIL gate 3: expected 0 remaining candidates, got $remaining"
  exit 1
fi
echo "  gate 3 PASS: 0 remaining candidates"

# --- Gate 4: no duplicate L-IDs ---
dupes=$(grep -oE '^### L-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{3}' "$TMP/self_improve_learnings.md" | sort | uniq -d | wc -l | tr -d ' ')
if [ "$dupes" != "0" ]; then
  echo "FAIL gate 4: duplicate L-IDs detected"
  grep -oE '^### L-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{3}' "$TMP/self_improve_learnings.md" | sort | uniq -d
  exit 1
fi
echo "  gate 4 PASS: no duplicate L-IDs"

echo ""
echo "BINARY MVP: SHIP (5/5 gates passed)"
```

```bash
chmod +x tests/loop/loop-mvp.test.sh
```

- [ ] **Step 2: Run MVP test**

```bash
bash tests/loop/loop-mvp.test.sh
```

Expected: All 5 gates PASS. Final line: `BINARY MVP: SHIP (5/5 gates passed)`.

- [ ] **Step 3: Commit**

```bash
git add tests/loop/loop-mvp.test.sh
git commit -m "test(loop): binary MVP test -- 5 candidates -> 3a+2r, 5/5 gates"
```

---

## Task 10: Feature spec Updates + shipped transition

**Files:**
- Modify: `K2B-Vault/wiki/concepts/feature_k2b-integrated-loop.md`

This is the /ship admin work. Do NOT edit status manually — use /ship per CLAUDE.md "Roadmap & Feature Notes".

- [ ] **Step 1: Run /ship once all tests pass**

Invoke `/ship` from Claude Code. It handles:
- MVP gate check (references `tests/loop/loop-mvp.test.sh` pass output as evidence)
- Codex adversarial review (Checkpoint 2)
- Commit any remaining unstaged work
- Move feature spec to `Shipped/` folder (Ship 1 banner in Updates)
- DEVLOG + wiki/log append
- Push + ask sync now-or-defer

- [ ] **Step 2: After /ship lands, verify the hook works on live data**

```bash
ls /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/observer-candidates.md
CLAUDE_PROJECT_DIR=/Users/keithmbpm2/Projects/K2B bash /Users/keithmbpm2/Projects/K2B/scripts/hooks/session-start.sh | grep -A 40 "K2B LOOP DASHBOARD" | head -50
```

Expected: dashboard renders the live candidates (currently 7 per the 2026-04-22 22:44 snapshot), numbered, with grammar.

---

## Self-Review

**Spec coverage:**
- Piece 1 Session-start dashboard → Tasks 6 + 7 ✓
- Piece 2 Auto-apply on accept → Tasks 4 + 5 (learnings append on accept, archive on reject) ✓
- Piece 3 Research-requires-delivery-link → Task 8 ✓
- Binary MVP test → Task 9 ✓
- Fixture-based test → Task 0 ✓
- Atomic write + flock → Task 5 (bash flock) + Task 4 (_atomic_write) ✓
- Stable item IDs → Task 1 (sha256 hash) ✓
- Crosslinks + research-file-move routing deferred — NOT in Ship 1 scope, documented in spec "Out of scope"
- Defer counter increments → NOT implemented in Ship 1 (spec lists `(deferred 1x, 2x, 3x)` — gap acknowledged; the defer action is a no-op today, to be added in Ship 2 if the loop runs long enough to see defers)

**Placeholders scan:** none — every step has complete code or a specific command.

**Type consistency:** `Candidate` dataclass has `item_id`, `severity`, `area`, `rule`, `evidence`. Used identically in `parse_candidates`, `append_learning`, `archive_reject`, `rewrite_candidates_without`, `loop_apply.py`, and `loop_render.py`. ✓

**Known deferral:** the "defer counter" (spec line 129 `(deferred 1x, 2x, 3x)`) is out of Ship 1. Current behavior: `--defer` is a no-op; the item stays in observer-candidates.md and re-surfaces next session identically. Counter tracking is a Ship 2 enhancement and is NOT required for the binary MVP test. Declared here so the Shipped spec can note it.

**Scope B decisions I'm making without asking:**

1. **Fixture content is the 5 learnings already applied 2026-04-22.** They match what the real observer produced at that exact time; using them keeps the test anchored to the historical run the spec names. The fact that they're already in the live learnings file is irrelevant — the test uses a sandboxed fixture learnings file.

2. **Item IDs are 8-hex sha256 of rule text.** No dependency on observer changes; deterministic; collision-free at this scale.

3. **Routing grammar: `a N`, `r N`, `d N` where N is 1-based display index.** Indices are stable within a single session-start → route cycle because the dashboard and the routing script parse the same file in the same order.

4. **Rejected items go to `observations.archive/rejected-YYYY-MM-DD.jsonl`** (one file per day, append-only). Matches existing pattern (`observations-*.jsonl` files in the same dir).

5. **Defer is a no-op in Ship 1.** Documented above. Ship 2 adds the counter.

6. **The learnings `- **Learning:**` body reuses the rule text.** Observer candidates don't give richer expanded learning text — just the rule + evidence. Duplication is honest about the source. When Keith wants to elaborate, he can edit the L-ID entry or write a richer `/learn`.

---

## Execution Handoff

Plan saved to `plans/2026-04-22_integrated-loop-ship-1.md`.

Executing inline in this session (auto mode, Scope B). Will checkpoint after Task 5 (routing primitives green) and Task 9 (binary MVP green) before the /ship run.
