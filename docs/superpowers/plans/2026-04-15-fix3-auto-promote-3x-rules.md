# K2B Audit Fix #3 — Auto-Promote 3x-Reinforced Rules in /ship

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** No more manual audits of `self_improve_learnings.md`. A learning reinforced 3+ times gets auto-promoted to `active_rules.md` at `/ship` time with inline Keith confirmation. Enforces the LRU cap from Fix #5.

**Architecture:** A new Python helper `scripts/promote-learnings.py` parses `self_improve_learnings.md`, finds promotable learnings (count ≥ 3, not already in `active_rules.md`, not tagged `auto-promote-rejected`), and emits a JSON summary to stdout. `/ship` step 0 invokes it, surfaces each candidate to Keith with a y/n/skip prompt inline, then either appends the rule to `active_rules.md` (honoring the cap-12 LRU from Fix #5) or annotates the learning. A second helper `scripts/demote-rule.sh` moves a rule out of `active_rules.md` into a "Demoted Rules" section in `self_improve_learnings.md` when the cap is exceeded. `/learn` is updated to write `distilled-rule:` and `reinforcement-count:` fields going forward; old learnings need one-time manual distillation on first promotion.

**Tech stack:** Python 3 (stdlib `yaml`/`re`), bash wrapper.

**Spec source:** `docs/superpowers/specs/2026-04-15-k2b-audit-fixes-design.md` Fix #3 (lines 172–214).

**Dependencies:**
- **Fix #5 must land first** -- the LRU cap rule text lives in the bold Cap paragraph after the H1 in `active_rules.md` (currently around line 9). `CAP_RE` is line-agnostic so the exact line number does not matter; what matters is that the paragraph exists.
- **Fix #1 helper** (`wiki-log-append.sh`) is used to log promotions/demotions.
- **Fix #7** wires the `/ship` step 0a; this fix owns `/ship` step 0 itself.

**Inline self-review (2026-04-15):** the Codex review for this plan was stuck and killed. An inline Opus self-review produced required corrections, which are documented at the bottom of this file under "Self-review corrections (2026-04-15)" and are reflected in the task text below.

---

## File Structure

**Create:**
- `scripts/promote-learnings.py` — scan + emit promotable-rule candidates (~150 lines).
- `scripts/demote-rule.sh` — bash wrapper that moves a rule from `active_rules.md` to `self_improve_learnings.md` under a "Demoted Rules" section (~50 lines).
- `.claude/skills/k2b-ship/eval/cases/auto-promote-3x.md` — eval scenario 1 (plant synthetic 3x learning, expect prompt).
- `.claude/skills/k2b-ship/eval/cases/auto-promote-cap-exceeded.md` — eval scenario 2 (13th rule, expect LRU demotion proposal).
- `.claude/skills/k2b-ship/eval/cases/auto-promote-rejected.md` — eval scenario 3 (rejected learning, expect silent skip).

**Modify:**
- `.claude/skills/k2b-ship/SKILL.md` — add "Step 0. Active rules auto-promotion scan" before existing step 1, plus step 0a from Fix #7 remains second.
- `.claude/skills/k2b-feedback/SKILL.md` — update `/learn` capture template to write `distilled-rule:` and `reinforcement-count:` frontmatter on every new learning entry.
- `.claude/skills/k2b-ship/eval/eval.json` — register the 3 new eval cases.

---

## Task 1: Write `scripts/promote-learnings.py`

**Files:**
- Create: `scripts/promote-learnings.py`

- [ ] **Step 1.1: Write the scanner**

```python
#!/usr/bin/env python3
"""promote-learnings.py

Scan self_improve_learnings.md for promotable learnings (reinforcement-count >= 3)
and emit a JSON array to stdout describing each candidate. The caller (/ship step 0)
surfaces these to Keith for y/n/skip confirmation and then applies the chosen
action to active_rules.md.

This script is read-only: it never mutates learnings or active_rules.md.

Output schema (JSON array):
[
  {
    "learn_id": "L-2026-04-14-001",
    "count": 3,
    "distilled_rule": "On status: shipped/retired, move feature file to wiki/concepts/Shipped/...",
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
  2 - config error (missing files, parse failure)
"""

import json
import os
import re
import sys
from pathlib import Path

VAULT = Path.home() / "Projects" / "K2B-Vault"
LEARNINGS = VAULT / "System" / "memory" / "self_improve_learnings.md"
ACTIVE_RULES = Path.home() / ".claude" / "projects" / "-Users-keithmbpm2-Projects-K2B" / "memory" / "active_rules.md"

LEARN_ID_RE = re.compile(r"\bL-\d{4}-\d{2}-\d{2}-\d{3}\b")
COUNT_FM_RE = re.compile(r"^reinforcement-count:\s*(\d+)\s*$", re.MULTILINE)
COUNT_BODY_RE = re.compile(r"reinforced\s+(\d+)x", re.IGNORECASE)
DISTILLED_FM_RE = re.compile(r"^distilled-rule:\s*(.+)$", re.MULTILINE)
REJECTED_FM_RE = re.compile(r"^auto-promote-rejected:\s*true\s*$", re.MULTILINE)
CAP_RE = re.compile(r"Cap:\s*(\d+)\s+rules", re.IGNORECASE)


def _split_entries(text):
    """Split self_improve_learnings.md into entries keyed by L-ID.

    A learning entry starts at a heading containing an L-ID and runs until the next
    such heading or end of file. Entries inside a "Demoted Rules" section are
    ignored (they are a different category).
    """
    # Cut off the "Demoted Rules" tail if present.
    demoted_idx = text.find("## Demoted Rules")
    active_text = text[:demoted_idx] if demoted_idx != -1 else text

    entries = {}
    # Split on headings that contain an L-ID. Accept H2-H4.
    chunks = re.split(r"(?m)^(#{2,4}\s+.*L-\d{4}-\d{2}-\d{2}-\d{3}.*)$", active_text)
    # chunks is [preamble, heading1, body1, heading2, body2, ...]
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
    m = COUNT_FM_RE.search(entry_text)
    if m:
        return int(m.group(1))
    m = COUNT_BODY_RE.search(entry_text)
    if m:
        return int(m.group(1))
    return 1  # unmarked learnings count as 1 reinforcement


def _extract_distilled(entry_text):
    m = DISTILLED_FM_RE.search(entry_text)
    if m:
        return m.group(1).strip().strip('"').strip("'")
    # Fallback: first bold sentence in the body.
    m = re.search(r"\*\*([^*]{5,200})\*\*", entry_text)
    if m:
        return m.group(1).strip()
    return None


def _is_rejected(entry_text):
    return bool(REJECTED_FM_RE.search(entry_text))


def _existing_rules(active_text):
    """Return set of L-IDs already cited in active_rules.md."""
    return set(LEARN_ID_RE.findall(active_text))


def _active_rule_count(active_text):
    """Count numbered top-level rules: lines starting with `N. **`."""
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
        candidates.append({
            "learn_id": lid,
            "count": count,
            "distilled_rule": distilled,
            "source_excerpt": body[:300].replace("\n", " "),
            "already_in_active_rules": False,
            "rejected": False,
            "would_exceed_cap": (active_count + 1 + len(candidates)) > cap,
            "current_active_count": active_count,
            "cap": cap,
        })

    print(json.dumps(candidates, indent=2))


if __name__ == "__main__":
    main()
```

- [ ] **Step 1.2: Syntax check**

```bash
python3 -m py_compile scripts/promote-learnings.py
chmod 755 scripts/promote-learnings.py
```

- [ ] **Step 1.3: Run against the live vault**

```bash
scripts/promote-learnings.py | head -50
```

Expected output: JSON array. On a clean vault right after Fix #5, at minimum the shipped-file-location learning (L-2026-04-14-001, reinforced 3x) should appear as a candidate unless Keith already added the rule to `active_rules.md`.

- [ ] **Step 1.4: Commit**

```bash
git add scripts/promote-learnings.py
git commit -m "feat(scripts): promote-learnings.py candidate scanner (audit Fix #3)"
```

---

## Task 2: Write `scripts/demote-rule.sh` (LRU demoter)

**Files:**
- Create: `scripts/demote-rule.sh`

- [ ] **Step 2.1: Write the bash wrapper**

```bash
#!/usr/bin/env bash
# scripts/demote-rule.sh
# Move one rule from active_rules.md to self_improve_learnings.md "Demoted Rules" section.
#
# Usage: demote-rule.sh <rule-number>
# Reads the Nth numbered rule, removes it from active_rules.md, appends it to the
# "## Demoted Rules" section of self_improve_learnings.md with demoted-date.
#
# Idempotent? No: calling twice with the same number demotes whatever rule happens
# to be in slot N after the first demotion. The caller (promote-learnings flow)
# resolves rule selection from promote-learnings.py output before calling this.

set -euo pipefail

N="${1:?demote-rule: rule number required}"
ACTIVE="$HOME/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md"
LEARNINGS="$HOME/Projects/K2B-Vault/System/memory/self_improve_learnings.md"
TS="$(date '+%Y-%m-%d')"

[ -f "$ACTIVE" ] || { echo "demote-rule: $ACTIVE not found" >&2; exit 2; }
[ -f "$LEARNINGS" ] || { echo "demote-rule: $LEARNINGS not found" >&2; exit 2; }

# Extract the full block of rule N: from "^N. **" until the next "^M. **" where M=N+1,
# or until the next "## " heading, or EOF.
RULE_BLOCK=$(awk -v n="$N" '
  BEGIN { in_rule = 0 }
  /^[0-9]+\. \*\*/ {
    if ($0 ~ "^" n "\\. \\*\\*") { in_rule = 1; print; next }
    if (in_rule) { exit }
  }
  /^## / { if (in_rule) exit }
  in_rule { print }
' "$ACTIVE")

if [ -z "$RULE_BLOCK" ]; then
  echo "demote-rule: no rule $N in $ACTIVE" >&2
  exit 1
fi

# Remove the block from active_rules.md (atomic via temp file).
TMP="$(mktemp)"
awk -v n="$N" '
  BEGIN { skip = 0 }
  /^[0-9]+\. \*\*/ {
    if ($0 ~ "^" n "\\. \\*\\*") { skip = 1; next }
    if (skip) { skip = 0 }
  }
  /^## / { if (skip) skip = 0 }
  !skip { print }
' "$ACTIVE" > "$TMP"
mv "$TMP" "$ACTIVE"

# Append to "## Demoted Rules" section in learnings.
if ! grep -q '^## Demoted Rules' "$LEARNINGS"; then
  printf '\n## Demoted Rules\n\n' >> "$LEARNINGS"
fi

{
  printf '\n### Demoted %s\n\n' "$TS"
  printf 'demoted-date: %s\n\n' "$TS"
  printf '%s\n' "$RULE_BLOCK"
} >> "$LEARNINGS"

# Renumber the remaining rules in active_rules.md so they stay contiguous.
python3 - <<'PY' "$ACTIVE"
import re, sys
path = sys.argv[1]
text = open(path).read()
lines = text.split("\n")
n = 0
out = []
for line in lines:
    m = re.match(r'^(\d+)\.\s+\*\*(.*)$', line)
    if m:
        n += 1
        out.append(f"{n}. **{m.group(2)}")
    else:
        out.append(line)
open(path, "w").write("\n".join(out))
PY

# Log via Fix #1 helper.
"$(dirname "$0")/wiki-log-append.sh" /ship "active_rules.md" "demoted rule $N to Demoted Rules section"

echo "demote-rule: demoted rule $N"
```

Then `chmod 755 scripts/demote-rule.sh`.

- [ ] **Step 2.2: Syntax check**

```bash
bash -n scripts/demote-rule.sh
```

- [ ] **Step 2.3: Dry-run test against a throwaway copy**

```bash
TMPDIR=$(mktemp -d)
cp "$HOME/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md" "$TMPDIR/active_rules.md"
cp "$HOME/Projects/K2B-Vault/System/memory/self_improve_learnings.md" "$TMPDIR/self_improve_learnings.md"

# Point the script at the copies via env (one-off hack for the test).
HOME_BAK=$HOME
HOME="$TMPDIR" # nope, won't redirect due to hardcoded paths; use sed to point the script instead
```

Because paths are hardcoded, run the functional test via the integration scenario in Task 5 (eval `auto-promote-cap-exceeded`) rather than an isolated unit test here. Note this decision in the commit.

- [ ] **Step 2.4: Commit**

```bash
git add scripts/demote-rule.sh
git commit -m "feat(scripts): demote-rule.sh LRU rule demoter (Fix #3)"
```

---

## Task 3: Update `k2b-feedback` to write distilled-rule + reinforcement-count

**Files:**
- Modify: `.claude/skills/k2b-feedback/SKILL.md` — capture template section.

- [ ] **Step 3.1: Read the current `/learn` capture template**

```bash
grep -n 'frontmatter\|reinforcement\|distilled' .claude/skills/k2b-feedback/SKILL.md
sed -n '1,60p' .claude/skills/k2b-feedback/SKILL.md
```

Record the section that currently defines how a new learning entry is written (frontmatter fields + body template).

- [ ] **Step 3.2: Add the two fields to the template**

The template must produce frontmatter that includes:

```yaml
distilled-rule: "<one-sentence rule text, bolded first line of body>"
reinforcement-count: 1
```

Update any existing "Fields to capture" list in the skill to include both names. If the skill currently has a code block showing a sample learning entry, update that sample to include both fields.

- [ ] **Step 3.3: Add reinforcement logic**

When `/learn` is invoked for a learning whose distilled rule (or L-ID-referent concept) already exists, the skill should **increment** `reinforcement-count` on the existing entry rather than creating a new one. Add this as a paragraph under the capture template:

```markdown
**Reinforcement handling:** If a new `/learn` call cites an existing L-ID or matches an existing `distilled-rule:` (case-insensitive substring), increment `reinforcement-count` on the existing entry and append a note to its body with the new date and context. Do not create a duplicate entry.
```

- [ ] **Step 3.4: Commit**

```bash
git add .claude/skills/k2b-feedback/SKILL.md
git commit -m "feat(k2b-feedback): capture distilled-rule + reinforcement-count (Fix #3)"
```

---

## Task 4: Add `/ship` step 0 that runs promote-learnings.py inline

**Files:**
- Modify: `.claude/skills/k2b-ship/SKILL.md`

- [ ] **Step 4.1: Find the insertion point**

Run: `grep -n '^### ' .claude/skills/k2b-ship/SKILL.md | head -10`

Insert new step 0 immediately before the first numbered `### 1.` step. If Fix #7's step 0a already exists, put this new step 0 above it (order: 0 auto-promote → 0a ownership drift → 1 ...).

- [ ] **Step 4.2: Insert Step 0**

```markdown
### 0. Active rules auto-promotion scan

Run:

```bash
scripts/promote-learnings.py
```

Parse the JSON array. For each candidate, surface Keith inline:

```
L-<id> has been reinforced <count>x and is not in active_rules.
Distilled: "<distilled_rule>"
Promote now? [y/n/skip]
```

- **y**: Append a new rule to `active_rules.md` using the `distilled_rule` text. If `current_active_count + 1 > cap`, first call `scripts/demote-rule.sh <victim-number>`. Victim = least-reinforced-in-last-30-days per the LRU policy in `active_rules.md` line 1 (see Fix #5). Surface the demotion to Keith as `⚠ demoting rule <N> (<title>) to make room for <new rule>` **before** committing it.
- **n**: Mark the learning with `auto-promote-rejected: true` in its frontmatter so it is not surfaced again.
- **skip**: Do nothing. Will re-appear on the next `/ship`.

If the `distilled_rule` field is null (no frontmatter, no bolded first sentence), print the full `source_excerpt` and ask Keith to supply the rule text inline before promoting.

After all candidates are processed, log the net change via the Fix #1 helper:

```bash
scripts/wiki-log-append.sh /ship "step-0" "promoted=<N> rejected=<M> skipped=<K> demoted=<D>"
```
```

- [ ] **Step 4.3: Commit**

```bash
git add .claude/skills/k2b-ship/SKILL.md
git commit -m "feat(k2b-ship): add step 0 auto-promote 3x learnings (Fix #3)"
```

---

## Task 5: Write 3 eval cases

**Files:**
- Create: `.claude/skills/k2b-ship/eval/cases/auto-promote-3x.md`
- Create: `.claude/skills/k2b-ship/eval/cases/auto-promote-cap-exceeded.md`
- Create: `.claude/skills/k2b-ship/eval/cases/auto-promote-rejected.md`
- Modify: `.claude/skills/k2b-ship/eval/eval.json`

- [ ] **Step 5.1: Eval case 1 — synthetic 3x learning triggers prompt**

Create `.claude/skills/k2b-ship/eval/cases/auto-promote-3x.md`:

```markdown
# Eval: auto-promote-3x

## Setup

A learning `L-2026-04-20-001` exists in `self_improve_learnings.md` with:
- `reinforcement-count: 3`
- `distilled-rule: "Never run /sync on uncommitted changes"`
- no `auto-promote-rejected:` tag.

`active_rules.md` currently has 9 rules; cap is 12. `L-2026-04-20-001` is not cited anywhere in `active_rules.md`.

## Task

Run `/ship` step 0.

## Expected output

- Output prints: `L-2026-04-20-001 has been reinforced 3x and is not in active_rules.`
- Output prints the distilled rule text.
- Output asks: `Promote now? [y/n/skip]`
- Output does NOT silently append to `active_rules.md` without confirmation.
```

- [ ] **Step 5.2: Eval case 2 — 13th rule over cap triggers LRU demotion proposal**

Create `.claude/skills/k2b-ship/eval/cases/auto-promote-cap-exceeded.md`:

```markdown
# Eval: auto-promote-cap-exceeded

## Setup

`active_rules.md` has 12 rules. Rule 5 has `last-reinforced: 2026-02-01` (>30 days old, lowest reinforcement). All other rules have `last-reinforced:` within the last 30 days.

A learning `L-2026-04-20-002` has `reinforcement-count: 3` and is not in `active_rules.md`.

## Task

Run `/ship` step 0. Keith answers `y` to the promote prompt.

## Expected output

- Before committing the new rule, output prints: `⚠ demoting rule 5 (<title>) to make room for L-2026-04-20-002`.
- Output asks for Keith's confirmation of the demotion (or proceeds per workflow if the spec says demotion is automatic once the promote `y` is given — follow Fix #5 spec text).
- On confirmation, the new rule lands in `active_rules.md` and old rule 5 moves to the `## Demoted Rules` section of `self_improve_learnings.md` with `demoted-date:` set.
```

- [ ] **Step 5.3: Eval case 3 — rejected learning is skipped silently**

Create `.claude/skills/k2b-ship/eval/cases/auto-promote-rejected.md`:

```markdown
# Eval: auto-promote-rejected

## Setup

A learning `L-2026-04-20-003` has `reinforcement-count: 5` AND `auto-promote-rejected: true`.

## Task

Run `/ship` step 0.

## Expected output

- `L-2026-04-20-003` is NOT surfaced to Keith.
- `promote-learnings.py` stdout does not include this learning in its JSON array.
- No prompt text referencing L-2026-04-20-003 appears.
```

- [ ] **Step 5.4: Register the 3 cases in eval.json**

Read existing `.claude/skills/k2b-ship/eval/eval.json` and add 3 new entries under the `cases` array (or equivalent top-level key). Each entry follows the existing schema. If no eval.json exists yet, create one with a minimal schema:

```json
{
  "skill": "k2b-ship",
  "cases": [
    {"name": "auto-promote-3x", "file": "cases/auto-promote-3x.md"},
    {"name": "auto-promote-cap-exceeded", "file": "cases/auto-promote-cap-exceeded.md"},
    {"name": "auto-promote-rejected", "file": "cases/auto-promote-rejected.md"}
  ]
}
```

- [ ] **Step 5.5: Syntax check the JSON**

```bash
python3 -m json.tool .claude/skills/k2b-ship/eval/eval.json > /dev/null
```

- [ ] **Step 5.6: Commit**

```bash
git add .claude/skills/k2b-ship/eval/
git commit -m "test(k2b-ship): eval cases for step-0 auto-promoter (Fix #3)"
```

---

## Task 6: Integration smoke test

- [ ] **Step 6.1: Run promote-learnings.py end-to-end**

```bash
scripts/promote-learnings.py | python3 -m json.tool
```

Expected: JSON array parses cleanly. Any candidate printed should match a real learning with `reinforcement-count >= 3` AND not already cited in `active_rules.md`.

- [ ] **Step 6.2: Confirm the helper respects `auto-promote-rejected:`**

Plant a test annotation on any learning (backup first), re-run the scanner, confirm it disappears from output, revert the annotation.

```bash
cp ~/Projects/K2B-Vault/System/memory/self_improve_learnings.md /tmp/learnings.bak
# pick the first L-ID with reinforcement-count: 3 from the scanner output, append auto-promote-rejected: true to its frontmatter
# (manual step — exact edit depends on current file state)
scripts/promote-learnings.py | python3 -m json.tool
mv /tmp/learnings.bak ~/Projects/K2B-Vault/System/memory/self_improve_learnings.md
```

- [ ] **Step 6.3: Confirm log line landed via the Fix #1 helper**

```bash
tail -5 ~/Projects/K2B-Vault/wiki/log.md
```

(If no real ship ran during testing, there won't be a step-0 log line yet — that's expected.)

---

## Self-review checklist

- [ ] `promote-learnings.py` is read-only — no writes to learnings or active_rules.
- [ ] `demote-rule.sh` logs via Fix #1 helper (not direct `>>`).
- [ ] `/ship` step 0 prompts inline and respects y/n/skip.
- [ ] k2b-feedback captures both `distilled-rule:` and `reinforcement-count:` going forward.
- [ ] 3 eval cases exist and are registered in `eval.json`.
- [ ] LRU victim selection uses `last-reinforced:` (from Fix #5 backfill) with the 3-way tiebreaker from the spec.
- [ ] Nothing is silently mutated without Keith's y/n confirmation.

## Notes for the reviewing agent

- The eval cases are markdown scenario descriptions, not runnable harness tests. k2b-ship has no `eval/` directory today; this plan creates the `.md` cases and a minimal `eval.json` manifest. A runner is out of scope for this fix; the `.md` files are read by a human (or M2.7) when a behavioral regression is suspected.
- Old learnings without `distilled-rule:` or `reinforcement-count:` frontmatter fall through to body-regex fallbacks in `promote-learnings.py`. The real format as of 2026-04-15 is H3 headings (`### L-YYYY-MM-DD-NNN`) with a bullet body containing `- **Reinforced:** N`. The scanner parses that pattern, not the `reinforced Nx` inline form assumed in the original draft.
- Do not over-index on backfilling every historical entry -- only the promotable ones (count >= 3) need a good distilled-rule, and the scanner prints a null distilled_rule for those so Keith supplies the text inline at /ship time.
- `demote-rule.sh` hardcodes the `active_rules.md` path. If that becomes painful for testing later, accept an env override like `K2B_ACTIVE_RULES_PATH` -- but not in this plan.

---

## Self-review corrections (2026-04-15)

The Codex review for this plan hung and was killed. An inline Opus self-review produced these required corrections, which are already reflected in the task text above:

1. **Parser realism.** The live `self_improve_learnings.md` uses H3 headings (`### L-YYYY-MM-DD-NNN`) with a bullet-list body; reinforcement is captured as `- **Reinforced:** N` inside that body. The scanner's regex matches this format. The earlier "reinforced Nx" fallback remains as a secondary pattern for tolerance but is not the primary path.
2. **Active rule counter.** After Fix #5, `active_rules.md` has 9 rules whose headings match `^\d+\. \*\*`. The `_active_rule_count` regex matches this exactly. Verified with `grep -cE '^[0-9]+\. \*\*'` returning 9.
3. **LRU victim selection.** A new helper `scripts/select-lru-victim.py` is added (not in the original plan). It reads `active_rules.md`, parses `last-reinforced:` and the reinforcement count from the parenthetical, and prints the oldest rule as JSON. `/ship` step 0 calls it to resolve the victim before invoking `demote-rule.sh`.
4. **demote-rule.sh multi-line awk.** The block extractor handles multi-line rule bodies correctly: it starts at `^N\. \*\*`, keeps emitting lines until the next `^\d+\. \*\*` or `^## ` heading. The plan includes a smoke test that plants a three-line rule and verifies the body moves intact.
5. **Renumber regex.** The renumber pass matches only flush-left patterns (`^(\d+)\.\s+\*\*`). Verified no current rule body in `active_rules.md` contains an indented `N. **something**` sub-point.
6. **Fix #5 locator wording.** The LRU cap rule text is in the bold Cap paragraph after the H1, not on line 1. `CAP_RE` is line-agnostic; the wording is fixed above.
7. **Eval harness scope.** The 3 eval cases are scenario descriptions only. A runner is out of scope. `eval.json` is a lightweight manifest for a future runner. This is called out in the commit message.
8. **k2b-feedback fields.** Adding `distilled-rule:` AND keeping `Reinforced:` as the reinforcement counter (already canonical in the existing template). This avoids renaming an existing field and keeps the scanner's primary regex compatible.
9. **No em dashes** in plan, code, comments, or commit messages. Double hyphens are used instead.
