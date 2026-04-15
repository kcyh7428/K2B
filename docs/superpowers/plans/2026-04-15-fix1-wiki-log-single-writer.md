# K2B Audit Fix #1 — wiki/log.md Single-Writer Helper

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** All appends to `~/Projects/K2B-Vault/wiki/log.md` go through one locked helper script. No skill ever `>>`-appends directly. Kills interleaved-write corruption and makes log format change-in-one-place.

**Architecture:** New helper at `scripts/wiki-log-append.sh` takes `<skill> <action> <summary>`, acquires an exclusive lock (`flock` if present, `mkdir` fallback for macOS), appends one formatted line to `wiki/log.md`. Every skill's current prose "append to wiki/log.md" instruction is replaced with a one-line call to this helper. Fix #8 (a separate plan) will later add a pre-commit hook that grep-rejects any `>>.*wiki/log\.md` in staged diffs, sealing the helper as the only writer.

**Tech stack:** Bash, `flock`/`mkdir` locking, BATS (optional — shell smoke tests with `bash -n` + functional run).

**Spec source:** `docs/superpowers/specs/2026-04-15-k2b-audit-fixes-design.md` Fix #1 (lines 68–132).

**Dependencies:** none (first in Keith's order after shipped Fix #4). Must land before Fix #8's pre-commit hook check-2 is wired.

---

## File Structure

**Create:**
- `scripts/wiki-log-append.sh` — the single-writer helper (executable, ~40 lines)
- `tests/wiki-log-append.test.sh` — smoke + concurrency test script (~60 lines)

**Modify (9 skills, one-line replacement per call-site):**
- `.claude/skills/k2b-ship/SKILL.md:213` — step "9. Append wiki/log.md"
- `.claude/skills/k2b-compile/SKILL.md:132` — step "5e. Append to wiki/log.md"
- `.claude/skills/k2b-review/SKILL.md:85,208` — two call-sites
- `.claude/skills/k2b-weave/SKILL.md:111,132` — two call-sites
- `.claude/skills/k2b-vault-writer/SKILL.md:334` — "Update wiki/log.md"
- `.claude/skills/k2b-linkedin/SKILL.md:18,208` — two call-sites
- `.claude/skills/k2b-insight-extractor/SKILL.md:79,84` — two call-sites
- `.claude/skills/k2b-daily-capture/SKILL.md:98`
- `.claude/skills/k2b-lint/SKILL.md:234,246,253` — three call-sites

**Do NOT modify** (these mention wiki/log.md as prose only, not writer sites):
- `k2b-research/SKILL.md:788` (describes compile's behavior)
- `k2b-autoresearch/SKILL.md:232` (describes ship's behavior)
- `k2b-tldr`, `k2b-youtube-capture`, `k2b-meeting-processor`, `k2b-compile/eval/eval.json` (reader prose, not writer calls)

**File responsibilities:**
- `wiki-log-append.sh` — lock acquire, format line, append, release. Zero business logic.
- `wiki-log-append.test.sh` — verifies lock, format, parallel-safety.
- Each skill edit — replaces prose "append" instruction with concrete script call. Preserves surrounding context.

---

## Task 1: Create the helper script

**Files:**
- Create: `scripts/wiki-log-append.sh`

- [ ] **Step 1.1: Create the script**

```bash
#!/usr/bin/env bash
# Single writer for ~/Projects/K2B-Vault/wiki/log.md
# Usage: wiki-log-append.sh <skill> <action> <summary>
# Example: wiki-log-append.sh /compile raw/research/foo.md "updated 3 wiki pages"
#
# Format written: "YYYY-MM-DD HH:MM  <skill>  <action>  <summary>\n"
# Locking: flock -x if available, mkdir fallback for macOS.

set -euo pipefail

SKILL="${1:?wiki-log-append: skill arg required}"
ACTION="${2:?wiki-log-append: action arg required}"
SUMMARY="${3:?wiki-log-append: summary arg required}"

LOG="${K2B_WIKI_LOG:-$HOME/Projects/K2B-Vault/wiki/log.md}"
LOCK="${K2B_WIKI_LOG_LOCK:-/tmp/k2b-wiki-log.lock}"
TS="$(date '+%Y-%m-%d %H:%M')"
LINE="${TS}  ${SKILL}  ${ACTION}  ${SUMMARY}"

if [ ! -f "$LOG" ]; then
  echo "wiki-log-append: log file not found: $LOG" >&2
  exit 2
fi

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -x 9
  printf '%s\n' "$LINE" >> "$LOG"
else
  # macOS fallback: mkdir is atomic
  LOCK_DIR="${LOCK}.d"
  TRIES=0
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    TRIES=$((TRIES + 1))
    if [ "$TRIES" -gt 200 ]; then
      echo "wiki-log-append: could not acquire $LOCK_DIR after 10s" >&2
      exit 3
    fi
    sleep 0.05
  done
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
  printf '%s\n' "$LINE" >> "$LOG"
fi
```

Write with `chmod 755`:
```bash
chmod 755 scripts/wiki-log-append.sh
```

- [ ] **Step 1.2: Syntax check**

Run: `bash -n scripts/wiki-log-append.sh`
Expected: no output, exit 0.

- [ ] **Step 1.3: Smoke test against a throwaway file**

Run:
```bash
TMP=$(mktemp)
K2B_WIKI_LOG="$TMP" K2B_WIKI_LOG_LOCK=/tmp/k2b-wiki-log-test.lock \
  scripts/wiki-log-append.sh /test test-action "hello world"
cat "$TMP"
rm "$TMP" && rm -rf /tmp/k2b-wiki-log-test.lock.d 2>/dev/null || true
```

Expected: one line matching `YYYY-MM-DD HH:MM  /test  test-action  hello world`.

- [ ] **Step 1.4: Commit**

```bash
git add scripts/wiki-log-append.sh
git commit -m "feat(scripts): add wiki-log-append.sh single-writer helper (audit Fix #1)"
```

---

## Task 2: Write concurrency + format tests

**Files:**
- Create: `tests/wiki-log-append.test.sh`

- [ ] **Step 2.1: Write the test script**

```bash
#!/usr/bin/env bash
# tests/wiki-log-append.test.sh
# Smoke + concurrency test for scripts/wiki-log-append.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/wiki-log-append.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR" /tmp/k2b-wiki-log-itest.lock.d' EXIT

LOG="$TMPDIR/log.md"
LOCK=/tmp/k2b-wiki-log-itest.lock
touch "$LOG"

export K2B_WIKI_LOG="$LOG" K2B_WIKI_LOG_LOCK="$LOCK"

fail() { echo "FAIL: $*" >&2; exit 1; }

# --- Test 1: single append writes one line in the expected format ---
"$HELPER" /ship feature_foo.md "shipped feature_foo"
LINES=$(wc -l < "$LOG" | tr -d ' ')
[ "$LINES" = "1" ] || fail "expected 1 line, got $LINES"
grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}  /ship  feature_foo\.md  shipped feature_foo$' "$LOG" \
  || fail "line format mismatch: $(cat "$LOG")"

# --- Test 2: missing args exit non-zero ---
if "$HELPER" /ship 2>/dev/null; then fail "expected failure on missing args"; fi

# --- Test 3: parallel writers all land, no interleaving, no lock leak ---
: > "$LOG"
for i in $(seq 1 20); do
  "$HELPER" /test "parallel-$i" "payload-$i" &
done
wait
LINES=$(wc -l < "$LOG" | tr -d ' ')
[ "$LINES" = "20" ] || fail "expected 20 parallel lines, got $LINES"
# Every line must start with a valid timestamp
if grep -vE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}  ' "$LOG" >/dev/null; then
  fail "found malformed/interleaved line: $(grep -vE '^[0-9]{4}' "$LOG" | head -1)"
fi
# Lock dir must be gone
[ ! -d "${LOCK}.d" ] || fail "lock dir leaked: ${LOCK}.d"

# --- Test 4: missing log file exits 2 ---
MISSING="$TMPDIR/does-not-exist.md"
if K2B_WIKI_LOG="$MISSING" "$HELPER" /test t "x" 2>/dev/null; then
  fail "expected exit 2 on missing log file"
fi

echo "wiki-log-append.test.sh: all tests passed"
```

Then `chmod 755 tests/wiki-log-append.test.sh`.

- [ ] **Step 2.2: Run the tests**

Run: `bash tests/wiki-log-append.test.sh`
Expected: prints `wiki-log-append.test.sh: all tests passed`, exits 0.

- [ ] **Step 2.3: Commit**

```bash
git add tests/wiki-log-append.test.sh
git commit -m "test(scripts): wiki-log-append concurrency + format tests"
```

---

## Task 3: Migrate k2b-ship (call-site 1 of 9)

**Files:**
- Modify: `.claude/skills/k2b-ship/SKILL.md` (around line 213 — "9. Append wiki/log.md")

- [ ] **Step 3.1: Read the current block**

Run: `sed -n '205,240p' .claude/skills/k2b-ship/SKILL.md`
Record the exact text to be replaced.

- [ ] **Step 3.2: Replace the prose with a script call**

The replacement instruction must read exactly:

```markdown
### 9. Append wiki/log.md

Call the single-writer helper (never append to wiki/log.md directly):

```bash
scripts/wiki-log-append.sh /ship "<feature-slug>" "shipped <feature-slug>: <one-line-summary>"
```

Replace `<feature-slug>` with the feature note basename (e.g. `feature_k2b-ship`) and `<one-line-summary>` with the same text used in the commit message subject. Helper handles locking, timestamp, and format.
```

Preserve any surrounding numbered step context (the "9." header stays).

- [ ] **Step 3.3: Verify the skill still parses as markdown**

Run: `awk '/^#/' .claude/skills/k2b-ship/SKILL.md | head -30`
Expected: heading sequence unchanged (no stray fences from the edit).

- [ ] **Step 3.4: Commit**

```bash
git add .claude/skills/k2b-ship/SKILL.md
git commit -m "refactor(k2b-ship): route wiki/log.md append through helper (Fix #1)"
```

---

## Task 4: Migrate k2b-compile (call-site 2 of 9)

**Files:**
- Modify: `.claude/skills/k2b-compile/SKILL.md:132` — step "5e. Append to wiki/log.md"

- [ ] **Step 4.1: Read current step 5e and 2-3 lines around it**

Run: `sed -n '128,142p' .claude/skills/k2b-compile/SKILL.md`

- [ ] **Step 4.2: Replace step 5e**

New text:

```markdown
- [ ] **5e. Append to wiki/log.md via helper:**

```bash
scripts/wiki-log-append.sh /compile "<raw-source-path>" "updated: <comma-list> | created: <comma-list>"
```

Helper is the only permitted writer for wiki/log.md. Do NOT `>>`-append directly.
```

- [ ] **Step 4.3: Commit**

```bash
git add .claude/skills/k2b-compile/SKILL.md
git commit -m "refactor(k2b-compile): route 5e wiki/log append through helper (Fix #1)"
```

---

## Task 5: Migrate k2b-review (call-sites 3–4 of 9)

**Files:**
- Modify: `.claude/skills/k2b-review/SKILL.md:85` and `:208`

- [ ] **Step 5.1: Read both call-sites with context**

Run:
```bash
sed -n '80,90p' .claude/skills/k2b-review/SKILL.md
sed -n '200,212p' .claude/skills/k2b-review/SKILL.md
```

- [ ] **Step 5.2: Replace line ~85 (promote path)**

Old prose (approx): `- **Append to \`wiki/log.md\`** recording the promote action`
New:

```markdown
- **Append to `wiki/log.md` via helper:** `scripts/wiki-log-append.sh /review <review-file> "promoted: <target-path>"`
```

- [ ] **Step 5.3: Replace line ~208 (video feedback flow)**

Old prose: `6. Release the flock, log to \`wiki/log.md\` ("processed N picks in videos_<slug>.md: X kept, Y dropped, Z neutral, W failed").`
New:

```markdown
6. Release the flock and log via helper:
   `scripts/wiki-log-append.sh /review videos_<slug>.md "processed N picks: X kept, Y dropped, Z neutral, W failed"`
```

Also update the `:204` failure-path sentence "`Log to wiki/log.md`" to point at the helper with wording `"<review-file> (failed: <error>)"`.

- [ ] **Step 5.4: Commit**

```bash
git add .claude/skills/k2b-review/SKILL.md
git commit -m "refactor(k2b-review): route wiki/log appends through helper (Fix #1)"
```

---

## Task 6: Migrate k2b-weave (call-sites 5–6 of 9)

**Files:**
- Modify: `.claude/skills/k2b-weave/SKILL.md:111` and `:132`

- [ ] **Step 6.1: Replace both numbered steps**

Line ~111:
```markdown
13. **Append summary via helper:**
    `scripts/wiki-log-append.sh /weave crosslinks_<slug>_HHMM.md "N proposals"`
```

Line ~132:
```markdown
5. **Append summary via helper:**
   `scripts/wiki-log-append.sh /weave-apply <digest-file> "N applied, M rejected, K deferred"`
```

- [ ] **Step 6.2: Commit**

```bash
git add .claude/skills/k2b-weave/SKILL.md
git commit -m "refactor(k2b-weave): route wiki/log appends through helper (Fix #1)"
```

---

## Task 7: Migrate k2b-vault-writer, k2b-linkedin, k2b-insight-extractor, k2b-daily-capture, k2b-lint (call-sites 7–9 of 9)

**Files:**
- Modify: `.claude/skills/k2b-vault-writer/SKILL.md:334`
- Modify: `.claude/skills/k2b-linkedin/SKILL.md:18,208`
- Modify: `.claude/skills/k2b-insight-extractor/SKILL.md:79,84`
- Modify: `.claude/skills/k2b-daily-capture/SKILL.md:98`
- Modify: `.claude/skills/k2b-lint/SKILL.md:234,246,253`

- [ ] **Step 7.1: k2b-vault-writer line 334**

Replace `3. **Update \`wiki/log.md\`**: Append an entry recording what was created and cross-linked` with:

```markdown
3. **Update `wiki/log.md` via helper:**
   `scripts/wiki-log-append.sh /vault-writer <note-path> "created/updated: <summary>, linked: <targets>"`
```

- [ ] **Step 7.2: k2b-linkedin lines 18 and 208**

Line 18 is a 1-line description — change to:
```markdown
On publish, update `wiki/content-pipeline/index.md` and append to `wiki/log.md` via `scripts/wiki-log-append.sh`.
```

Line ~208:
```markdown
- **Append via helper:** `scripts/wiki-log-append.sh /linkedin "<post-slug>" "published urn=<post-urn>"`
```

- [ ] **Step 7.3: k2b-insight-extractor lines 79 and 84**

Both `3. Append to wiki/log.md` occurrences become:
```markdown
3. Append to `wiki/log.md` via `scripts/wiki-log-append.sh /insight <insight-file> "<one-line-summary>"`
```

(If the two sites describe different flows, tailor the second summary to its context — do not duplicate verbatim.)

- [ ] **Step 7.4: k2b-daily-capture line 98**

Replace `- After saving, append to \`wiki/log.md\` with cross-linked entities` with:
```markdown
- After saving, append via helper:
  `scripts/wiki-log-append.sh /daily <daily-note> "captured: <entities>"`
```

- [ ] **Step 7.5: k2b-lint lines 234, 246, 253**

- 234 (`4. Append lint summary to wiki/log.md`) →
  `4. Append lint summary via \`scripts/wiki-log-append.sh /lint <lint-run-id> "<summary>"\``
- 246 (`6. Append to wiki/log.md`) →
  `6. Append via \`scripts/wiki-log-append.sh /lint <lint-run-id> "<summary>"\``
- 253 (`- Always update wiki/log.md after a lint pass.`) →
  `- Always update wiki/log.md via \`scripts/wiki-log-append.sh\` (never \`>>\`) after a lint pass.`

- [ ] **Step 7.6: Confirm no remaining direct-append prose exists outside the helper**

Run:
```bash
grep -rn '>>.*wiki/log\.md' .claude/skills/ scripts/ || echo "clean"
```

Expected: `clean`.

Run:
```bash
grep -rln 'wiki/log\.md' .claude/skills/ | while read f; do
  grep -q 'wiki-log-append\.sh\|k2b-compile\|describes\|reads' "$f" || echo "UNPATCHED: $f"
done
```

If any `UNPATCHED:` line points to a skill that should be a writer, fix it before continuing.

- [ ] **Step 7.7: Commit**

```bash
git add .claude/skills/k2b-vault-writer/SKILL.md \
        .claude/skills/k2b-linkedin/SKILL.md \
        .claude/skills/k2b-insight-extractor/SKILL.md \
        .claude/skills/k2b-daily-capture/SKILL.md \
        .claude/skills/k2b-lint/SKILL.md
git commit -m "refactor(skills): route remaining wiki/log appends through helper (Fix #1)"
```

---

## Task 8: End-to-end verification

- [ ] **Step 8.1: Re-run the helper tests**

Run: `bash tests/wiki-log-append.test.sh`
Expected: `all tests passed`.

- [ ] **Step 8.2: Dry-run one real append against the live log**

This touches the live vault. Only proceed if Keith approves in-session.

```bash
scripts/wiki-log-append.sh /test-fix1 "meta" "fix #1 single-writer smoke"
tail -1 ~/Projects/K2B-Vault/wiki/log.md
```

Expected: the tail line matches the exact format and contains `/test-fix1`. If Keith wants it removed after the smoke, he edits the vault manually — the helper never deletes.

- [ ] **Step 8.3: Summarize migration state**

Run:
```bash
echo "=== direct >> appenders (must be 0) ==="
grep -rn '>>.*wiki/log\.md' .claude/skills/ scripts/ || echo "0"
echo "=== helper call-sites ==="
grep -rn 'wiki-log-append\.sh' .claude/skills/ | wc -l
```

Expected: `0` direct appenders; helper call-site count ≥ 13 (one per migrated instruction).

- [ ] **Step 8.4: Final commit if any fix-ups were needed**

If step 8.3 surfaced anything missed, patch it and commit:
```bash
git add -u
git commit -m "refactor(skills): final wiki/log helper migration sweep (Fix #1)"
```

---

## Self-review checklist (verify before marking plan complete)

- [ ] Every direct `>>` to `wiki/log.md` removed from `.claude/skills/` and `scripts/`.
- [ ] Helper script is executable (`ls -l scripts/wiki-log-append.sh` shows `x`).
- [ ] Test script passes clean.
- [ ] Every migrated skill still renders as valid markdown (headings + fences balanced).
- [ ] No skill was migrated that is a reader-prose site (k2b-research/autoresearch/tldr/youtube-capture/meeting-processor should remain unchanged).
- [ ] Spec Fix #1 "Design choices" (a)–(d) all reflected: script at `scripts/`, lock at `/tmp/k2b-wiki-log.lock`, 3-arg signature, Fix #8 will enforce.

## Notes for the reviewing agent

- This plan deliberately does not add BATS as a dep; a plain `bash` test runner is sufficient and keeps the tree simple.
- The `K2B_WIKI_LOG` and `K2B_WIKI_LOG_LOCK` env-var overrides exist **only** for the test harness — no skill should use them in production.
- Richer per-skill log entries (old format had multi-field blocks) are intentionally flattened into the `<summary>` arg. This is a breaking format change that the spec accepts: new entries have a uniform 4-column layout.
- The pre-commit enforcement (`>>.*wiki/log\.md` reject) lands in Fix #8. Until then, discipline is carried by this plan's step 7.6 and step 8.3 greps.
