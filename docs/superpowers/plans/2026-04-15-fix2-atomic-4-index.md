# K2B Audit Fix #2 -- Atomic 4-Index Helper for /compile (REVISED)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Collapse the "update all 4 indices" advisory into ONE helper call. Stop losing one of the 4 steps under cognitive load.

**Architecture:** `scripts/compile-index-update.py` is the single entry point. It takes the raw source path plus the lists of wiki pages that were updated and created during a compile, computes the deltas into temp files **by parsing the existing in-vault index format in place**, then atomically renames the final files. Step 4 uses the Fix #1 `wiki-log-append.sh` helper to append to `wiki/log.md`. Claude never hand-edits any index file during a compile run -- the skill is edited to forbid it.

**Tech stack:** Python 3, the existing Fix #1 bash helper for the log append sub-call, mkdir lock pattern that matches `scripts/wiki-log-append.sh`.

**Spec source:** `docs/superpowers/specs/2026-04-15-k2b-audit-fixes-design.md` Fix #2 (lines 216-294).

**Dependencies:**
- **Fix #1 must land first** -- DONE (the log-append sub-call uses `scripts/wiki-log-append.sh`).
- No dependency on Fix #3/#5/#7 -- can land in parallel.

---

## Codex-driven rewrite (why this plan differs from the v1 sketch)

The v1 sketch used HTML-comment "marker blocks" (`<!-- compile-index-update:count:start -->`) injected into every index file. Codex review caught four blockers and two majors. Summary of the decisions this revision bakes in:

1. **No markers.** Live indexes at `~/Projects/K2B-Vault/wiki/projects/index.md`, `wiki/people/index.md`, `wiki/index.md`, etc., already use rich Markdown with a consistent header line `Last updated: YYYY-MM-DD | Entries: N` (and optional tail text) plus a 3-column `| Folder | Purpose | Entries |` table in the master index. The helper must parse those in place. Marker injection is abandoned.
2. **Nested-aware subfolder resolution.** `wiki/projects/k2b-investment/foo.md` maps to `projects/k2b-investment`, not `projects`. Resolution walks the path and returns the deepest directory that contains an `index.md`.
3. **Mixed-subfolder compiles.** A single compile run can touch pages in multiple subfolders. The helper groups `updated + created` by resolved subfolder and updates each touched subfolder index exactly once.
4. **Master index shape preservation.** `wiki/index.md` has a 3-column table (`Folder | Purpose | Entries`) and a `**Total wiki pages: N**` summary line. The helper only recomputes the `Entries` column and the `N` summary; it never rewrites the table shape. If the existing shape is unrecognized, exit 1 with a validation error (no rewrite).
5. **Lock pattern matches Fix #1.** mkdir-only lock at `/tmp/k2b-compile-index.lock.d`, same convention as `scripts/wiki-log-append.sh`. `flock` fallback is removed with a comment explaining that compile runs are short and the mkdir path is sufficient.
6. **Log append failure is loud.** If `wiki-log-append.sh` returns non-zero, exit 2 (partial write). Indices are already on disk; the log is the audit trail and silent failure is worse than loud failure.

Format rule: no em dashes anywhere (code, comments, commit messages, docs). Use `--`.

---

## File Structure

**Create:**
- `scripts/compile-index-update.py` -- the helper (~280 lines).
- `tests/compile-index-update.test.sh` -- harness exercising happy path, nested, mixed, malformed master, and log-append failure.
- `tests/fixtures/compile-index-update/` -- fixture vault matching the live format (3-column master, `Last updated: ... | Entries: N` subfolder indexes).

**Modify:**
- `.claude/skills/k2b-compile/SKILL.md` -- replace the 5a-5e procedural block with a single helper call and a forbid-hand-edit note.
- `.claude/skills/k2b-compile/eval/eval.json` -- retarget the 3 assertions that enumerate the 4 indices so they check for the helper call.

**NOT modified:**
- Real vault indexes. There is no backfill step. The helper works against the live format unchanged.

---

## Task 1: Write the Python helper

**File:** `scripts/compile-index-update.py`

### Step 1.1: Implementation

The helper must:
- Validate args.
- Resolve raw source and wiki targets to their deepest containing index.
- Read each target index, parse the `Last updated: ... | Entries: N` line (or the master table), recompute counts from the filesystem, write to a tempfile.
- Stage 2: atomic rename each tempfile into place.
- Stage 3: call `scripts/wiki-log-append.sh`. If it fails, exit 2.
- mkdir lock at `/tmp/k2b-compile-index.lock.d`, same as Fix #1.

Key parsing rules (derived from the live vault):

| Target | Shape | What to update |
|---|---|---|
| `wiki/<sub>/index.md` | Has a line matching `^Last updated: (\S+) \| Entries: (\d+)(.*)$` | Replace the date with today, replace `\d+` with the recomputed count, preserve the optional tail (e.g. `+ 1 subfolder (...)` in `raw/research/index.md`). |
| `raw/<sub>/index.md` | Same shape. | Same. |
| `wiki/index.md` (master) | 3-column table `\| Folder \| Purpose \| Entries \|` under `## Subfolders`, plus optional `**Total wiki pages: N** ...` line. | Recompute the `Entries` column for each existing row by running `_count_pages` against the matching directory. Recompute `N` if the total line is present. Do NOT add or remove rows. |

If any target's shape is unrecognized, exit 1.

### Step 1.2: Write the script

Create `scripts/compile-index-update.py` with the design above. Full code included in Task 2 (single-commit file), so this step is just "write the file".

### Step 1.3: Sanity check

```bash
python3 -m py_compile scripts/compile-index-update.py
chmod 755 scripts/compile-index-update.py
```

---

## Task 2: Build fixture vault and test harness

**Files:**
- `tests/fixtures/compile-index-update/wiki/index.md` (3-column master matching live format)
- `tests/fixtures/compile-index-update/wiki/projects/index.md` (with `Last updated: ... | Entries: N` header)
- `tests/fixtures/compile-index-update/wiki/projects/k2b-investment/index.md` (nested, same header)
- `tests/fixtures/compile-index-update/wiki/people/index.md` (same header, different subfolder)
- `tests/fixtures/compile-index-update/wiki/log.md` (non-empty so the helper can append)
- `tests/fixtures/compile-index-update/raw/research/index.md` (same header)
- `tests/fixtures/compile-index-update/raw/research/k2b-investment/index.md` (nested, same header)
- One or two existing `*.md` page files per subfolder so counts are non-zero.
- `tests/compile-index-update.test.sh`

### Test cases

1. **Happy path, single subfolder:** raw source `raw/research/2026-04-15_sample.md`, updated `wiki/projects/sample.md`. Assert:
   - `wiki/projects/index.md` Entries count updated, Last updated set to today.
   - `wiki/index.md` master Entries column row for `projects/` is updated.
   - `raw/research/index.md` Entries count updated.
   - `wiki/log.md` has a new `/compile` line.

2. **Nested subfolder:** updated `wiki/projects/k2b-investment/architecture.md`. Assert:
   - `wiki/projects/k2b-investment/index.md` (nested) Entries count updated, NOT `wiki/projects/index.md`.
   - Master index row `projects/k2b-investment/` is updated.

3. **Mixed subfolders:** updated `wiki/projects/sample.md` AND `wiki/people/person_X.md` in the same call. Assert:
   - BOTH `wiki/projects/index.md` and `wiki/people/index.md` are touched.
   - Master index reflects both.

4. **Malformed master:** wipe the `| Folder | Purpose | Entries |` header from the fixture master file. Assert exit code 1, nothing written.

5. **Log append failure -> exit 2:** override `K2B_WIKI_LOG` to a non-existent file (the Fix #1 helper exits 2 on missing log). Assert helper exits 2 and the subfolder indexes ARE already written (partial-write scenario is detected loudly).

6. **Bad args:** empty `updated` AND empty `created` exits 1. Missing args exits 1.

### Step 2.1: Write the fixture

Fixture uses the EXACT live format. Example for `wiki/projects/index.md`:

```markdown
---
tags: [index, wiki]
date: 2026-04-15
type: index
origin: k2b-generate
---
# Wiki Projects Index
Fixture projects index.

Last updated: 2026-04-14 | Entries: 1

| Page | Status | Summary | Updated |
|------|--------|---------|---------|
| [[sample]] | on | Sample project | 2026-04-14 |
```

Example for master `wiki/index.md`:

```markdown
---
tags: [index, wiki, master]
date: 2026-04-15
type: index
origin: k2b-generate
---
# K2B Wiki -- Master Index

Last updated: 2026-04-14

## Subfolders

| Folder | Purpose | Entries |
|--------|---------|---------|
| [people/](people/index.md) | Person pages | 1 |
| [projects/](projects/index.md) | Project pages | 1 |
| [projects/k2b-investment/](projects/k2b-investment/index.md) | Nested planning workspace | 1 |

**Total wiki pages: 3**
```

### Step 2.2: Write the test harness

`tests/compile-index-update.test.sh` mirrors `tests/wiki-log-append.test.sh` structure. Full code lives in the implementation commit.

### Step 2.3: Run the harness

```bash
bash tests/compile-index-update.test.sh
```

Expected: `all tests passed`.

---

## Task 3: Migrate k2b-compile SKILL.md to call the helper

**File:** `.claude/skills/k2b-compile/SKILL.md`

### Step 3.1: Replace the 5a-5e block (lines 124-140)

Replace with:

```markdown
### 5. Update indexes (single helper call)

Call the atomic 4-index helper. This is the ONLY permitted way to update any index during a compile run. Do NOT hand-edit `wiki/<sub>/index.md`, `raw/<sub>/index.md`, `wiki/index.md`, or append to `wiki/log.md` directly.

```bash
~/Projects/K2B/scripts/compile-index-update.py \
  "<raw-source-path>" \
  "<comma-separated-updated-pages>" \
  "<comma-separated-created-pages>"
```

The helper:
- Resolves each wiki page to its deepest containing subfolder (nested-aware).
- Groups mixed-subfolder updates and touches every affected subfolder index exactly once.
- Parses the existing `Last updated: ... | Entries: N` header and the master 3-column table in place; never rewrites shape.
- Validates every target index; exits 1 if any shape is unrecognized (nothing mutated).
- Stages all updates into tempfiles, then atomic-renames each into place.
- Calls `scripts/wiki-log-append.sh` (Fix #1) to append the log line. If the log append fails, exits 2 (indices are already written; loud failure is preferred over silent).

Exit codes: 0 ok, 1 validation failure, 2 partial write (indices written, log append failed or mid-rename failure), 3 lock timeout. On non-zero exit, stop the compile run and surface stderr to Keith -- do not retry blindly.
```

Also update the policy ledger note (line 52) from `Must update ALL 4 indexes (subfolder, raw subfolder, master wiki/index.md, wiki/log.md)` to `Must call scripts/compile-index-update.py -- the helper covers all 4 indexes atomically`.

### Step 3.2: Sanity check

```bash
grep -n '^### ' .claude/skills/k2b-compile/SKILL.md | head -15
```

Expected: `### 5. Update indexes (single helper call)` is present; old `5a. Raw subfolder index FIRST` bullets are gone.

---

## Task 4: Update eval.json

**File:** `.claude/skills/k2b-compile/eval/eval.json`

The 3 assertions that enumerate the 4 indices (lines 10, 21, 33) are replaced with helper-call assertions:

- Line 10 `"Does the output append an entry to wiki/log.md with the compile log format?"` becomes `"Does the output call scripts/compile-index-update.py with 3 args (raw source, updated csv, created csv)?"`
- Line 21 `"Does the output append to wiki/log.md with source path, updated pages, created pages, and indexes listed?"` becomes `"Does the output invoke compile-index-update.py exactly once, as an executable command, not as prose?"`
- Line 33 `"Are all 4 index update points (wiki subfolder, raw subfolder, master index, wiki/log.md) explicitly mentioned or executed in the output?"` becomes `"Does the output avoid any direct hand-edit of wiki/<sub>/index.md, raw/<sub>/index.md, wiki/index.md, or wiki/log.md during the compile run?"`

Validate with:

```bash
python3 -m json.tool .claude/skills/k2b-compile/eval/eval.json > /dev/null
```

---

## Task 5 (REMOVED): backfill real vault indexes

The v1 sketch proposed injecting marker comment blocks into every live index file. **This step is deleted.** The helper now parses the existing live format, so no backfill is needed.

---

## Self-review checklist

- [x] No em dashes anywhere in plan, script, tests, or skill edits.
- [ ] Helper exits 0 on happy path, 1 on validation failure (bad args, unrecognized shape), 2 on log-append failure, 3 on lock timeout.
- [ ] `wiki/log.md` append goes through Fix #1 helper only; no direct `>>`.
- [ ] Helper handles nested subfolders (`projects/k2b-investment`).
- [ ] Helper handles mixed-subfolder compiles.
- [ ] Master index shape is preserved (3-column table, `Total wiki pages: N` line).
- [ ] k2b-compile SKILL.md calls the helper instead of enumerating 4 steps.
- [ ] eval.json asserts the helper call, not the 4-point enumeration.
- [ ] Fixture + harness pass: happy path, nested, mixed, malformed master, log-append failure, bad args.

## Commit sequence

1. `docs(plan): rewrite Fix #2 plan per codex review blockers`
2. `feat(scripts): compile-index-update.py atomic 4-index helper (Fix #2, revised)`
3. `test(scripts): fixture + harness matching live vault format (Fix #2)`
4. `refactor(k2b-compile): single helper call replaces 4-index procedural block (Fix #2)`
5. `test(k2b-compile): eval asserts helper call (Fix #2)`

No push. No `/ship`. No `/sync`.

## Notes for the reviewing agent

- The parser is intentionally strict: it recognizes exactly the `Last updated: ... | Entries: N` header and the master 3-column table. Anything else is a validation error that stops the compile run -- the caller must then investigate or hand-fix the live format, which is much safer than guessing.
- The partial-write exit 2 surface is intentional. The helper cannot atomically rename N files across subfolders AND append to a log; if any step after stage-1 fails, the compile run is reported as partial so Keith knows to audit.
- Format: every file, comment, docstring, and commit message in this plan avoids em dashes.
