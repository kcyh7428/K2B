# K2B Audit Fix #7: Memory Layer Ownership Enforcement

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Every fact has exactly one home. Write the ownership matrix into `CLAUDE.md`, build an audit script that flags drift, wire it into `/ship` as an advisory warning, and strip known duplicates from `self_improve_learnings.md` and `self_improve_errors.md`.

**Architecture:** Three moving parts: (1) a ~30-line "Memory Ownership" section added to `CLAUDE.md` right before the "Rules" section. Identity-level, always loaded. (2) A `scripts/audit-ownership.sh` + `scripts/ownership-watchlist.yml` pair that greps for drift and exits non-zero. (3) A step in `/ship` (step 0a, after the Fix #3 auto-promoter which owns step 0 proper) that runs the audit advisory-only, printing warnings without blocking.

**Tech stack:** Bash + `grep` + `python3` with `pyyaml` (required, not optional).

**Spec source:** `docs/superpowers/specs/2026-04-15-k2b-audit-fixes-design.md` Fix #7 (lines 134-170).

**Dependencies:**
- **Fix #4 must be shipped** (it is, commit `972665f`). The ownership rule is meaningful only after procedural duplicates are stripped from `CLAUDE.md`.
- Lands **before** Fix #3 so `/ship` step 0 has the audit call to wire into.
- **Runtime prereq:** `python3` with `pyyaml` installed. Verified: `python3 -c 'import yaml; print(yaml.__version__)'` returns `6.0.2`. No awk fallback.

---

## File Structure

**Create:**
- `scripts/audit-ownership.sh`. Drift detector (~110 lines).
- `scripts/ownership-watchlist.yml`. Known rule phrases + canonical home (~45 lines).

**Modify:**
- `CLAUDE.md`. Add "Memory Layer Ownership" section before "Rules" (~33 lines).
- `~/Projects/K2B-Vault/System/memory/self_improve_learnings.md`. Add top-of-file banner + update L-2026-04-08-001 entry with pointer.
- `~/Projects/K2B-Vault/System/memory/self_improve_errors.md`. Add top-of-file banner + update E-2026-04-12-001 entry with pointer.
- `.claude/skills/k2b-ship/SKILL.md`. Add "step 0a: ownership drift check" calling `audit-ownership.sh` (advisory-only).

---

## Task 1: Write the ownership matrix into CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`. Insert a new section `## Memory Layer Ownership` immediately before `## Rules`.

- [ ] **Step 1.1: Confirm insert location**

Run: `grep -n '^## Rules' CLAUDE.md`
Expected: a single matching line. Insert happens at that line (new section becomes line N, existing `## Rules` shifts down).

- [ ] **Step 1.2: Insert the section**

Exact content to insert:

```markdown
## Memory Layer Ownership

Every fact has exactly one home. When a rule or procedure lives in more than one place, the second copy rots first.

| Fact type | Single home | Loaded at session start? |
|---|---|---|
| Soft rules (tone, no em dashes, no AI cliches) | `CLAUDE.md` top-level prose | yes |
| Hard rules (rsync, feature-status edits) | Code -- pre-sync script + pre-commit hook | enforced, not loaded |
| Domain conventions (file naming, frontmatter, taxonomy) | `CLAUDE.md` File Conventions section | yes |
| Skill how-tos (flock patterns, atomic rename, multi-step procedures) | The skill's `SKILL.md` body | yes (on skill invoke) |
| Auto-promoted learned preferences | `active_rules.md` (cap 12, LRU) | yes |
| Raw learnings history | `self_improve_learnings.md` | no -- reference only |
| Raw errors history | `self_improve_errors.md` | no -- reference only |
| Memory index (pointers only) | `MEMORY.md` | yes |
| Index/log mutations | Single helper function (one flock holder each) | enforced |

Day-one consequences:

1. **No procedural content in CLAUDE.md.** "How to do X" lives in the skill that does X. CLAUDE.md points to the skill.
2. **Hard rules ship as code, not prose.** If a rule cannot be violated without human override, it belongs in a pre-commit hook or a wrapper script, not in a markdown bullet.
3. **Single-writer hubs.** `wiki/log.md` and the 4 compile indexes have exactly one writer script each; no skill `>>`-appends directly.

Ownership drift is checked advisory-only by `/ship` via `scripts/audit-ownership.sh`. Repeated drift is a promotion signal: fold it into one of the homes above or make it enforceable code.
```

- [ ] **Step 1.3: Verify insertion**

Run:
```bash
grep -n '^## ' CLAUDE.md | head -20
```
Expected: `## Memory Layer Ownership` appears immediately before `## Rules`.

- [ ] **Step 1.4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE.md): add Memory Layer Ownership matrix (audit Fix #7)"
```

---

## Task 2: Build the ownership watchlist YAML

**Files:**
- Create: `scripts/ownership-watchlist.yml`

- [ ] **Step 2.1: Write the watchlist**

Each entry names a rule phrase, where it canonically lives, and where it would be drift. The audit script flags any match outside `canonical_home`.

```yaml
# scripts/ownership-watchlist.yml
# Format: list of rule entries checked by audit-ownership.sh
#
# Each entry:
#   id:               unique identifier
#   phrase:           case-sensitive fixed-string the audit greps for (literal)
#   phrase_regex:     alternative to phrase; extended regex (grep -rE)
#   canonical_home:   path(s) where this phrase is allowed to appear
#   severity:         warn | error (advisory uses warn; error is reserved)
#
# NOTE: the `flock` rule was dropped after codex review. Legitimate mentions
# are scattered across CLAUDE.md, k2b-review/SKILL.md, and
# project_mac_mini_clash_required.md. Noise-to-signal ratio is fatal.

rules:
  - id: compile-all-indexes
    phrase: "Compile must update ALL indexes"
    canonical_home:
      - "/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md"
      - ".claude/skills/k2b-compile/SKILL.md"
    severity: warn

  - id: compile-4-index-taxonomy
    phrase: "subfolder, raw subfolder, master wiki/index.md, wiki/log.md"
    canonical_home:
      - ".claude/skills/k2b-compile/SKILL.md"
      - "scripts/compile-index-update.py"
    severity: warn

  - id: rsync-hard-rule
    phrase: "NEVER manual rsync"
    canonical_home:
      - "/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md"
    severity: warn

  - id: shipped-file-location
    phrase: "wiki/concepts/Shipped/"
    canonical_home:
      - ".claude/skills/k2b-ship/SKILL.md"
      - "/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md"
      - "CLAUDE.md"
      - "/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/feedback_shipped_feature_file_location.md"
    severity: warn

  - id: wiki-log-direct-append
    # Regex to catch both ">> wiki/log.md" and ">>  /abs/path/wiki/log.md".
    phrase_regex: '>>[[:space:]]+\S*wiki/log\.md'
    canonical_home: []  # zero allowed copies: this is a Fix #1 violation
    severity: warn
```

- [ ] **Step 2.2: Syntax-check the YAML**

Run:
```bash
python3 -c 'import yaml,sys; yaml.safe_load(open("scripts/ownership-watchlist.yml"))'
```
Expected: no output, exit 0. `pyyaml` is a hard requirement for this audit (installed on both MacBook and Mac Mini, version 6.0.2). There is no awk fallback.

- [ ] **Step 2.3: Commit**

```bash
git add scripts/ownership-watchlist.yml
git commit -m "feat(scripts): ownership watchlist for audit-ownership.sh (Fix #7)"
```

---

## Task 3: Build `audit-ownership.sh`

**Files:**
- Create: `scripts/audit-ownership.sh`

- [ ] **Step 3.1: Write the script**

```bash
#!/usr/bin/env bash
# scripts/audit-ownership.sh
# Reads scripts/ownership-watchlist.yml and flags any known rule phrase that appears
# outside its canonical home.
#
# Supports two rule entry styles:
#   phrase:       literal fixed-string (grep -rIFln)
#   phrase_regex: extended regex       (grep -rIEln)
#
# Exit codes:
#   0 - no drift
#   1 - drift detected (the caller decides whether to block)
#   2 - usage / config error
#
# Usage: scripts/audit-ownership.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WATCHLIST="$REPO_ROOT/scripts/ownership-watchlist.yml"
VAULT="$HOME/Projects/K2B-Vault"

[ -f "$WATCHLIST" ] || { echo "audit-ownership: watchlist not found: $WATCHLIST" >&2; exit 2; }

# Search scope: K2B repo + vault memory + vault wiki/.
SEARCH_PATHS=(
  "$REPO_ROOT/CLAUDE.md"
  "$REPO_ROOT/.claude/skills"
  "$REPO_ROOT/scripts"
  "$VAULT/System/memory"
  "$VAULT/wiki"
)

EXISTING_PATHS=()
for p in "${SEARCH_PATHS[@]}"; do
  [ -e "$p" ] && EXISTING_PATHS+=("$p")
done

# Run the audit in python for YAML parsing + regex/fixed dispatch.
REPO_ROOT="$REPO_ROOT" VAULT="$VAULT" WATCHLIST="$WATCHLIST" \
  python3 - "${EXISTING_PATHS[@]}" <<'PY'
import os, subprocess, sys, yaml

watchlist = os.environ["WATCHLIST"]
repo_root = os.environ["REPO_ROOT"]
paths = sys.argv[1:]

with open(watchlist) as f:
    data = yaml.safe_load(f)
rules = data.get("rules", [])

def canonicalize(h):
    h = os.path.expanduser(h)
    if not os.path.isabs(h):
        h = os.path.join(repo_root, h)
    return os.path.abspath(h)

# The watchlist itself quotes every phrase it looks for, so exclude it from
# the scan. It is the one file where these phrases are legitimate metadata.
WATCHLIST_ABS = os.path.abspath(watchlist)

drift_rules = 0
total_offenders = 0

for rule in rules:
    rid = rule["id"]
    home = [canonicalize(h) for h in rule.get("canonical_home", [])]

    if "phrase" in rule:
        needle = rule["phrase"]
        cmd = ["grep", "-rIFln", "--", needle, *paths]
        display = needle
    elif "phrase_regex" in rule:
        needle = rule["phrase_regex"]
        cmd = ["grep", "-rIEln", "--", needle, *paths]
        display = f"/{needle}/"
    else:
        print(f"audit-ownership: rule {rid} missing phrase/phrase_regex", file=sys.stderr)
        sys.exit(2)

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("audit-ownership: grep not available", file=sys.stderr)
        sys.exit(2)

    matches = [l for l in out.stdout.splitlines() if l]

    offenders = []
    for m in matches:
        m_abs = os.path.abspath(m)
        if m_abs == WATCHLIST_ABS:
            continue
        if not any(
            m_abs == h or m_abs.startswith(h + os.sep)
            for h in home
        ):
            offenders.append(m_abs)

    if offenders:
        drift_rules += 1
        total_offenders += len(offenders)
        print(f"drift: rule={rid} phrase={display!r}")
        for o in sorted(offenders):
            print(f"   offender: {o}")

if drift_rules:
    print(f"audit-ownership: {drift_rules} rule(s) with drift, {total_offenders} offender file(s)")
    sys.exit(1)

print("audit-ownership: no drift")
sys.exit(0)
PY
```

Then `chmod 755 scripts/audit-ownership.sh`. The script uses fixed-string `grep -rIFln` for `phrase` rules and extended regex `grep -rIEln` for `phrase_regex` rules. `pyyaml` is required (hard dep, no fallback).

- [ ] **Step 3.2: Syntax check**

```bash
bash -n scripts/audit-ownership.sh
```

- [ ] **Step 3.3: First real run (expect some drift to surface)**

```bash
scripts/audit-ownership.sh || true
```

Document any drift found. Expected on first run: at least 1 drift hit (the `>> wiki/log.md` rule will catch anything Fix #1 missed; `compile-4-index` may fire from old notes).

- [ ] **Step 3.4: Commit**

```bash
git add scripts/audit-ownership.sh
git commit -m "feat(scripts): audit-ownership.sh drift detector (Fix #7)"
```

---

## Task 4: Strip known duplicates from learnings + errors

**Files:**
- Modify: `~/Projects/K2B-Vault/System/memory/self_improve_learnings.md`
- Modify: `~/Projects/K2B-Vault/System/memory/self_improve_errors.md`

- [ ] **Step 4.1: Add banner to self_improve_learnings.md**

At the top of the file (after any existing YAML frontmatter), insert:

```markdown
> **This file is a historical record of individual learnings and errors. Live behavior rules live in [active_rules.md](active_rules.md) after promotion via `/ship` step 0 (Fix #3). `k2b-feedback` still reads this file to dedup and increment reinforcement counts. That is read-for-bookkeeping, not a behavior source.**
```

Note: the banner is deliberate about the read path. `scripts/hooks/session-start.sh` (lines 72-101) loads reinforced learnings, and `k2b-feedback/SKILL.md` (lines 39-50, 67-80) reads this file for reinforcement dedup. The banner reflects that reality: the file is a historical record, AND `k2b-feedback` still reads it for bookkeeping.

- [ ] **Step 4.2: Update L-2026-04-08-001 with pointer**

Locate the L-2026-04-08-001 entry (the compile 4-index learning). Append a pointer line at the end of that entry:

```markdown
> **See `active_rules.md` rule 7 for the live rule.** This historical entry is not a behavior source.
```

Do not delete the historical body. Just add the pointer so audit-ownership.sh counts it as a legitimate reference, not drift.

- [ ] **Step 4.3: Same treatment for self_improve_errors.md and E-2026-04-12-001**

```markdown
> **This file is a historical record of individual learnings and errors. Live behavior rules live in [active_rules.md](active_rules.md) after promotion via `/ship` step 0 (Fix #3). `k2b-feedback` still reads this file to dedup and increment reinforcement counts. That is read-for-bookkeeping, not a behavior source.**
```

Append pointer after E-2026-04-12-001:
```markdown
> **See `active_rules.md` rule 7 for the live rule.** This historical entry is not a behavior source.
```

- [ ] **Step 4.4: Re-run the audit**

```bash
scripts/audit-ownership.sh || true
```
Drift count should decrease or match. If new drift appeared, investigate before continuing.

- [ ] **Step 4.5: Commit (in vault repo if versioned)**

If the vault is a git repo:
```bash
cd ~/Projects/K2B-Vault
git add System/memory/self_improve_learnings.md System/memory/self_improve_errors.md
git commit -m "docs(memory): banner + pointers on historical records (Fix #7)"
```

If not versioned: Syncthing propagates; note in execution log.

---

## Task 5: Wire `/ship` to call the audit (advisory-only)

**Files:**
- Modify: `.claude/skills/k2b-ship/SKILL.md`. Insert step 0a per the ordering rule below.

- [ ] **Step 5.1: Determine insertion point**

Run: `grep -n '^### ' .claude/skills/k2b-ship/SKILL.md | head -15`

**Ordering rule:** insert step 0a immediately before `### 1.` unless a `### 0. ` header already exists, in which case insert it immediately after that. This keeps the ordering stable whether or not Fix #3 has already added its step 0 (the auto-promoter).

- [ ] **Step 5.2: Insert the step**

````markdown
### 0a. Ownership drift check (advisory)

Run:

```bash
scripts/audit-ownership.sh || true
```

The script exits non-zero when it finds known rule phrases outside their canonical home (see `scripts/ownership-watchlist.yml`). This step is **advisory**. Drift does not block `/ship`. Surface the offenders to Keith inline:

```
[warn] ownership drift: rule=<id> phrase=<phrase>
  offender: <path>
```

Keith decides fix-inline or defer. When he defers, append the drift summary to the ship commit body under a "Deferred:" trailer so the next session sees it.
````

- [ ] **Step 5.3: Commit**

```bash
git add .claude/skills/k2b-ship/SKILL.md
git commit -m "feat(k2b-ship): add advisory ownership drift check step (Fix #7)"
```

---

## Task 6: End-to-end verification

- [ ] **Step 6.1: Re-run audit once more and confirm status**

```bash
scripts/audit-ownership.sh; echo "exit=$?"
```
Record the exit code and list of offenders. An exit code of 0 means zero drift; 1 means Keith gets warnings on next `/ship`.

- [ ] **Step 6.2: Confirm CLAUDE.md still parses**

```bash
wc -l CLAUDE.md
grep -c '^## ' CLAUDE.md
```

- [ ] **Step 6.3: Confirm helper is idempotent (second run matches first)**

```bash
scripts/audit-ownership.sh > /tmp/ownership.1 2>&1 || true
scripts/audit-ownership.sh > /tmp/ownership.2 2>&1 || true
diff /tmp/ownership.1 /tmp/ownership.2 || echo "NON-IDEMPOTENT, investigate"
rm /tmp/ownership.1 /tmp/ownership.2
```
Expected: no diff output.

---

## Self-review checklist

- [ ] CLAUDE.md has `## Memory Layer Ownership` directly before `## Rules`.
- [ ] Watchlist YAML is syntactically valid, has 5+ entries.
- [ ] `audit-ownership.sh` exits 0 on a clean repo, 1 on drift, 2 on config error.
- [ ] Both learnings + errors files have the historical-record banner at line 1.
- [ ] `/ship` step 0a is advisory (never blocks).
- [ ] No procedural "how to do X" content was added to CLAUDE.md. Only the matrix and consequences.
- [ ] No em dashes (spec rule).

## Notes for the reviewing agent

- The watchlist phrases are case-insensitive substring matches. If a legitimate page uses the phrase in quotation marks for teaching purposes, either (a) add it to `canonical_home`, or (b) tighten the phrase so it matches only the rule formulation.
- Fix #3's `/ship` step 0 is separate from this step 0a. Order: 0 (auto-promote) → 0a (ownership drift). Both run before step 1.
- The `grep -rIF` is intentional: binary-file skip, fixed-string, faster than regex.
