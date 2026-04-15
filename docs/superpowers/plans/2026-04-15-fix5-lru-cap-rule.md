# K2B Audit Fix #5 — LRU Cap Rule in active_rules.md

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Write the 12-rule LRU cap + demotion policy into line 1 of `active_rules.md` so Fix #3 (`/ship` auto-promoter) can apply it deterministically.

**Architecture:** Pure documentation edit. The policy is currently only in the audit spec — not in the file the code will parse. Also backfill `last-reinforced:` on the 9 existing rules so LRU selection is well-defined from day 1.

**Tech stack:** Markdown edit.

**Spec source:** `docs/superpowers/specs/2026-04-15-k2b-audit-fixes-design.md` Fix #5 (lines 376–406).

**Dependencies:** none. Must land **before** Fix #3 (auto-promoter depends on the written-down policy).

---

## File Structure

**Modify:**
- `/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md`
  - Add LRU cap paragraph immediately after the `# Active Rules` H1 (before `Last promoted:`).
  - Annotate each of the 9 numbered rules with a `last-reinforced: YYYY-MM-DD` field inside the existing parenthetical.

This file is symlinked via the memory-sync setup, so the single edit propagates to both Mac and Mini.

---

## Task 1: Read current file and plan the edits

- [ ] **Step 1.1: Read the full file**

Run: `cat /Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md`

Record:
- Total number of numbered rules (should be 9).
- The exact parenthetical at the end of each rule (e.g. `(L-2026-03-29-002, reinforced 3x)`).
- The current line that follows `# Active Rules` (should be `Last promoted: 2026-04-12`).

---

## Task 2: Insert the LRU policy paragraph

**Files:**
- Modify: `active_rules.md` (lines ~7–9 area, right under H1)

- [ ] **Step 2.1: Insert the policy paragraph**

The paragraph to insert immediately after the `# Active Rules` heading and a blank line:

```markdown
**Cap: 12 rules. LRU demotion policy:** when `/ship` auto-promotes a rule (reinforced 3x, per Fix #3) and this file would exceed 12 entries, the least-reinforced-in-last-30-days rule is demoted to `self_improve_learnings.md` under a "Demoted Rules" section. "Reinforced in last 30 days" means any `/learn` call that cites the rule's L-ID within 30 days of today. Ties are broken by last-reinforced date descending, then total reinforcement count descending, then L-ID alphabetical. Demotion is automatic inside `/ship`, surfaced to Keith as `"⚠ demoting rule N (<title>) to make room for <new rule>"`. No manual override is needed; `/learn` can re-promote by reinforcing again.
```

Result shape:
```markdown
# Active Rules

**Cap: 12 rules. LRU demotion policy:** when `/ship` auto-promotes...

Last promoted: 2026-04-12
Last audited: 2026-04-12 (manual -- pruned 14 -> 7, demoted 3 to MEMORY/CLAUDE.md, merged 2, moved 1 to YouTube agent backlog)
```

- [ ] **Step 2.2: Verify file still parses**

Run:
```bash
head -5 /Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md
```
Expected: frontmatter delimiter, H1, blank, bold "Cap: 12 rules..." paragraph.

---

## Task 3: Backfill `last-reinforced:` on each of the 9 existing rules

- [ ] **Step 3.1: Determine the correct `last-reinforced` date per rule**

For each rule, use the `reinforced Nx` or the L-ID date if no reinforcement is recorded. Default policy: if the parenthetical already says `reinforced 3x`, use the audit date **2026-04-12** (the last manual-audit date from line 9) as the initial `last-reinforced`. Otherwise use the L-ID date.

Concrete mapping (from current file content):
| Rule # | L-ID | reinforced? | last-reinforced (backfill) |
|---|---|---|---|
| 1 | L-2026-03-26-001 | no | 2026-03-26 |
| 2 | L-2026-03-26-003 | no | 2026-03-26 |
| 3 | L-2026-03-26-009 | merged | 2026-03-26 |
| 4 | L-2026-03-29-002 | 3x | 2026-04-08 |
| 5 | L-2026-03-26-010 | no | 2026-03-26 |
| 6 | L-2026-04-04-001 | no | 2026-04-04 |
| 7 | L-2026-04-08-001 | no | 2026-04-08 |
| 8 | (Karpathy 1) | no | 2026-04-12 |
| 9 | (Karpathy 3) | no | 2026-04-12 |

(Rules 8 and 9 have no L-ID; use the audit date since they were captured during the 2026-04-12 prune.)

- [ ] **Step 3.2: Apply the backfill edits**

For each numbered rule, add `, last-reinforced: YYYY-MM-DD` inside the final parenthetical. Example transform for rule 4:

Before:
```markdown
4. **NEVER manual rsync for k2b-remote.** ... (L-2026-03-29-002, reinforced 3x)
```

After:
```markdown
4. **NEVER manual rsync for k2b-remote.** ... (L-2026-03-29-002, reinforced 3x, last-reinforced: 2026-04-08)
```

For rules 8 and 9 (no parenthetical today), append one:
```markdown
8. **Surface ambiguity before coding.** ... (Karpathy principle 1: Think Before Coding, last-reinforced: 2026-04-12)
9. **Every changed line traces to the request.** ... (Karpathy principle 3: Surgical Changes, last-reinforced: 2026-04-12)
```

- [ ] **Step 3.3: Verify all 9 rules now have the field**

Run:
```bash
grep -c 'last-reinforced:' /Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md
```
Expected: `9` (one per numbered rule).

Run:
```bash
grep -nE '^[0-9]+\. ' /Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md | wc -l
```
Expected: `9`.

---

## Task 4: Commit

Note: `active_rules.md` lives under `~/.claude/projects/.../memory/` which is a symlink into `K2B-Vault/System/memory/`. The commit happens in the **vault** repo, not the K2B code repo.

- [ ] **Step 4.1: Check which repo owns the file**

Run:
```bash
readlink /Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory
cd "$(readlink /Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory)"/..
git -C . rev-parse --show-toplevel 2>/dev/null || echo "not a git repo"
```

- [ ] **Step 4.2: Commit in the correct repo**

If the vault is a git repo:
```bash
cd ~/Projects/K2B-Vault
git add System/memory/active_rules.md
git commit -m "docs(memory): write LRU cap policy + backfill last-reinforced (audit Fix #5)"
```

If the vault is NOT a git repo (Syncthing-only), leave the edit to propagate via Syncthing and note this in the plan execution log — no commit needed.

---

## Self-review checklist

- [ ] LRU policy paragraph is the first content after `# Active Rules`.
- [ ] Exactly 9 rules have `last-reinforced:` populated.
- [ ] Tiebreaker order matches the spec: last-reinforced desc → count desc → L-ID alpha.
- [ ] No rule lost its existing L-ID or `reinforced Nx` annotation.
- [ ] No em dashes in the paragraph (repo convention).
- [ ] File still starts with valid YAML frontmatter.

## Notes for the reviewing agent

- This plan is intentionally small — it's the prerequisite for Fix #3. Fix #3's parser will read the `Cap: 12` string and the per-rule `last-reinforced:` fields; keep them machine-readable.
- Do **not** add a "Demoted Rules" section to `self_improve_learnings.md` yet. Fix #3 creates that on first demotion.
- If Keith disagrees with any backfilled date, the defaults above are safe to override on a per-rule basis — but every rule must end up with a field, otherwise Fix #3's LRU selection is undefined.
