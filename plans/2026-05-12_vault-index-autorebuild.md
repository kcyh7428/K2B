# Plan: feature_vault-index-autorebuild

**Status:** plan-review pending (Codex Checkpoint 1 before any code).
**Feature spec:** [[wiki/concepts/feature_vault-index-autorebuild]].
**Drafted:** 2026-05-12.

## 1. Background

Two raw subfolders are drifting from their indexes:

| Folder | Files on disk | Index rows | Missing rows |
|---|---:|---:|---:|
| `raw/sessions/` | 26 | 11 | 15 |
| `raw/tldrs/` | 10 | 4 | 6 |

Drift exists because `/ship` step 13.5 (session-summary capture) and `/tldr` raw save both write the .md file but do **not** add a row to the matching subfolder index. The existing helper `scripts/compile-index-update.py` updates indexes only on `/compile` runs, which neither path triggers.

Existing infrastructure to respect:

- **Single canonical writer.** Active Rule 2: "Single-writer helpers for wiki/log.md (`scripts/wiki-log-append.sh`) and wiki index updates (`scripts/compile-index-update.py`). Never `>>`-append directly from skill bodies." `compile-index-update.py` already owns subfolder index writes (including `raw/`). Adding a second writer (e.g. `raw-index-add.py`) violates the rule.
- **Index file format** is structured but non-uniform: YAML frontmatter, H1, optional description, then `Last updated: YYYY-MM-DD | Entries: N` line, then a markdown table whose **column shape differs per subfolder**: `raw/sessions/` uses `| File | Date | Feature |`, `raw/tldrs/` uses `| Page | Source | Date | Compiled |`, `raw/research/` uses `| Page | Source | Date | Compiled |`. The "Feature" / "Source" / "Compiled" columns are editorial content that cannot be re-derived from filesystem state.
- **Atomic write discipline** for vault mutations: tmp file then `os.replace()`. `compile-index-update.py` already does this.

## 2. Approach summary

Three coordinated changes in one commit, ordered red-then-green per TDD:

1. **Extend `scripts/compile-index-update.py`** with a new `--mode=add-row` invocation that takes a single new file path and adds exactly one row to its parent subfolder's `index.md`, idempotently (no-op if a row for that file already exists). Atomic write, locked the same way the existing modes are.
2. **Wire `/ship` session-summary capture** (skill body line ~843-880) and **`/tldr` raw save** (skill body line ~21-37) to call the new mode after the file write succeeds. Both wires fail soft: if the index update fails, log a warning, don't fail the ship/tldr.
3. **Add a `/lint` check** (`Check #15: raw subfolder drift`) that compares each `raw/*/index.md` table contents against `ls` of the folder and reports missing rows. Read-only; surfaces the bug but does not auto-clean.

The MVP test then runs the existing 21 backfill rows (15 sessions + 6 tldrs) as the "first repair run" by calling the new mode once per missing file via a one-shot backfill helper script. Second run produces zero diff (idempotency).

## 3. Detailed design

### 3.1 New mode in `compile-index-update.py`

Add a fourth invocation shape alongside the existing `<raw-source-path> <updated-csv> <created-csv>`:

```
compile-index-update.py --mode=add-row <new-file-path>
```

Behavior:

1. Resolve `<new-file-path>` to its deepest containing `index.md` (same nested-aware logic the existing code already has).
2. Acquire the existing `mkdir`-only lock (`/tmp/k2b-compile-index.lock.d`).
3. Read the index file.
4. Parse the table header to detect column shape. Use a small lookup keyed by table-header text (the strings `| File | Date | Feature |`, `| Page | Source | Date | Compiled |`, etc.) → column-fill strategy.
5. If a wikilink already exists in the table matching `[[<basename-without-extension>]]` OR `[[<basename-without-extension>|...]]`, no-op return success (idempotency).
6. Else build one row using the column-fill strategy for that shape:
   - **`raw/sessions/`** (`| File | Date | Feature |`): File = `[[<basename>]]`, Date = read from new file's `date:` frontmatter or filename prefix, Feature = read from new file's `feature:` frontmatter if present else empty string.
   - **`raw/tldrs/`** (`| Page | Source | Date | Compiled |`): Page = `[[<basename>]]`, Source = `claude-code-session`, Date = filename prefix or frontmatter `date:`, Compiled = empty.
   - **`raw/research/`**: same columns as tldrs. Source from frontmatter `source:` if present, else empty.
   - **Unknown shape:** exit 1, log the offending index path. Loud failure so a new index format isn't silently corrupted.
7. Insert the row sorted by Date descending (newest first), matching the existing convention.
8. Update the `Last updated:` and `Entries: N` line.
9. Atomic write via tmp file + `os.replace()`.
10. Release lock. Exit 0.

Exit codes match existing contract: 0 ok, 1 validation failure, 2 partial write, 3 lock timeout.

### 3.2 Backfill script `scripts/raw-index-backfill.sh`

Simple bash wrapper for the one-time 21-row migration. Walks `raw/sessions/` and `raw/tldrs/` and calls `compile-index-update.py --mode=add-row` per file. Idempotent because the helper is idempotent. Not committed long-term; deleted after migration succeeds OR kept as `scripts/maintenance/raw-index-backfill.sh` for future raw subfolders. **Decision needed:** keep or delete after migration.

### 3.3 `/ship` skill body wire (k2b-ship/SKILL.md ~step 13.5)

After the existing `mv "$TMPFILE" "$SESSIONS_DIR/$FILENAME"` block:

```bash
# Add row to raw/sessions/index.md (idempotent, fail-soft)
if ! python3 ~/Projects/K2B/scripts/compile-index-update.py \
    --mode=add-row "$SESSIONS_DIR/$FILENAME" 2>>/tmp/k2b-ship-index-add.log; then
  echo "[warn] failed to update raw/sessions/index.md for $FILENAME; see /tmp/k2b-ship-index-add.log" >&2
fi
```

Fail-soft because: the session-summary file is already on disk, observer loop picks it up regardless of the index, and a failed index write should not block a successful ship.

### 3.4 `/tldr` skill body wire (k2b-tldr/SKILL.md ~step 3 "Save the TLDR")

After the existing save step that writes to `raw/tldrs/YYYY-MM-DD_tldr-topic.md`:

```bash
# Add row to raw/tldrs/index.md (idempotent, fail-soft)
if ! python3 ~/Projects/K2B/scripts/compile-index-update.py \
    --mode=add-row "$TLDR_PATH" 2>>/tmp/k2b-tldr-index-add.log; then
  echo "[warn] failed to update raw/tldrs/index.md; see /tmp/k2b-tldr-index-add.log" >&2
fi
```

### 3.5 `/lint` Check #15

Add to existing `/lint` script. For each `raw/*/index.md` and `wiki/*/index.md`:

1. Walk the folder, build a set of basenames-without-extension (excluding `index.md`).
2. Parse the index table and extract all `[[wikilink]]` targets.
3. Set difference: `files_on_disk - linked_in_index` = missing rows. Report count + first 3 examples.
4. Also report the reverse: `linked_in_index - files_on_disk` = stale rows (file deleted but row remains).

Read-only. Output formatted like the other `/lint` checks. Exits non-zero only if Keith's `--strict` flag is set; default behavior is informational.

## 4. Test plan (TDD)

Five binary MVP conditions, written as tests BEFORE implementation. All must pass before commit.

### Test 1: pre-fix reproduction (RED first)

```bash
tests/lint-raw-index-drift.test.sh
```

- Asserts `compile-index-update.py --mode=add-row` exists OR `/lint` Check #15 runs OR a fresh `scripts/raw-index-drift-check.py` reports 15 missing for sessions and 6 for tldrs.
- Captures current state as a snapshot for verification of the green pass.

### Test 2: first repair reduces drift to zero (GREEN)

```bash
tests/raw-index-backfill.test.sh
```

- Runs `scripts/raw-index-backfill.sh`.
- Re-runs the drift check.
- Asserts missing count for both subfolders is 0.

### Test 3: idempotent second run

- Snapshot `raw/sessions/index.md` and `raw/tldrs/index.md` bytes after first run.
- Runs `scripts/raw-index-backfill.sh` again.
- Asserts no byte difference (full diff is empty).

### Test 4: /ship session-summary wire

```bash
tests/ship-session-summary-index.test.sh
```

- Sandbox vault dir.
- Invokes the relevant slice of /ship step 13.5 with a synthesized session file.
- Asserts: (a) the file lands at `raw/sessions/YYYY-MM-DD_HHMMSS_session-summary.md`, (b) the index has exactly one new row for that file, (c) running the slice again with the same input does NOT add a duplicate row.

### Test 5: /tldr raw capture wire

```bash
tests/tldr-raw-index.test.sh
```

- Sandbox vault dir.
- Invokes the relevant slice of /tldr with a synthesized TLDR body.
- Asserts: (a) file lands at `raw/tldrs/YYYY-MM-DD_tldr-test.md`, (b) the index has exactly one new row, (c) re-invocation no-ops.

## 5. Migration concerns

- **Existing 21 missing rows** are not destructive to add — they only fill gaps. Hand-curated rows are not touched (idempotency guard skips files whose wikilinks already exist).
- **Existing `Entries: N` line is updated** by the new mode. If a subfolder index has a quirky / hand-written header section above the table, the helper preserves everything outside the `## Files`-style section it parses.
- **No vault file is moved.** Folder layout is unchanged.

## 6. Risk list + mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| New mode corrupts an index with unrecognized table shape | low | Exit 1 on unknown shape, never write. Test 1 covers the known 3 shapes; new shapes fail loudly. |
| /ship or /tldr fail because of index update failure | low | Fail-soft wires log warning, do not block. Index drift is recoverable; ship/tldr failures are not. |
| Race between two concurrent /ship or /tldr calls | very low (single user, no concurrency) | Existing `mkdir` lock in `compile-index-update.py` serializes. |
| `--mode=add-row` invocation diverges from existing `<raw-source-path>` 3-arg invocation | low | Use `argparse` subcommand-style parsing; clear error if neither matches. |
| One-time backfill adds rows with empty Feature/Compiled columns that look messier than hand-curated rows | medium | Acceptable: empty editorial cells beat 21 missing rows. Operator can edit cells after the fact. |
| The 21-row backfill creates a single huge diff in vault that disturbs the Syncthing-watching workflow | low | Run backfill on MacBook only; Mac Mini receives via Syncthing; observer ignores raw subfolder index files. |

## 7. Out of scope

- No vault folder migration (per `feature_vault-three-zones` audit).
- No review TTL sweeper (lives in [[feature_review-ttl-sweeper]]).
- No wiki/*/index.md changes (those use `compile-index-update.py` already via `/compile`).
- No retroactive Feature/Compiled column backfill — those columns get empty values on backfilled rows. Operator may edit after the fact.

## 8. Shipping plan

One commit, Tier 3 review (scripts/ change to hub helper triggers tier-3 allowlist).

### Commit message draft

```
feat(vault-index-autorebuild): add idempotent add-row mode + writer-side wires

scripts/compile-index-update.py grows a --mode=add-row invocation that
adds exactly one row to a raw subfolder's index.md, atomic + locked,
no-op when the wikilink is already present.

/ship session-summary and /tldr raw save now call add-row after writing
their raw .md file. Both wires fail-soft (log + warn, do not block).

/lint Check #15 reports raw/ + wiki/ subfolder index drift, read-only.

One-time backfill of 21 missing rows: raw/sessions/ 15 missing,
raw/tldrs/ 6 missing. Backfill script kept for future raw subfolders.

MVP test gate: 5 binary conditions per feature_vault-index-autorebuild
all pass. Pre-fix drift reproduced, first repair reduces missing to 0,
second repair idempotent (zero diff), sandbox /ship and /tldr both
update index exactly once per file.
```

### Order of operations inside the session

1. Write failing tests (1-5).
2. Implement `--mode=add-row` in `compile-index-update.py`.
3. Implement `scripts/raw-index-backfill.sh`.
4. Run Tests 1-3, get to green.
5. Implement `/ship` wire.
6. Implement `/tldr` wire.
7. Run Tests 4-5, get to green.
8. Implement `/lint` Check #15.
9. Run the actual one-time backfill (21 rows added).
10. `/ship` — Tier 3, Codex Checkpoint 2 pre-commit review.

### /sync impact

`scripts/compile-index-update.py` and the two SKILL.md files all roll up into the `scripts` and `skills` /sync categories. `/sync` after the ship.

## 9. Open questions for Codex plan-review

1. **Single-writer rule.** Is extending `compile-index-update.py` the right call, or should the add-row mode live in a separate helper? Argument for extending: rule says one writer per hub file. Argument for new helper: add-row is structurally different from the compile-mode multi-index update.

2. **Fail-soft on /ship wire.** Should an index-update failure block the ship, or just warn? Current plan: warn-only because the file is already on disk and observer doesn't depend on the index. Alternative: fail the ship to force the bug to surface fast.

3. **Backfill column values for the 21 existing rows.** Empty Feature/Source/Compiled columns vs heuristic-extracted values (e.g. parse session-summary frontmatter for `feature:`). Heuristic is more work but produces a cleaner backfill; empty cells are simpler but uglier.

4. **`/lint` Check #15 default exit code.** Strict by default (drift = non-zero exit, fails CI-ish runs) or informational (just report, never fail)? Existing Check #14 ("stale research > 30 days") is informational. Match precedent → informational.

5. **TDD red phase.** Test 1 expects `compile-index-update.py --mode=add-row` to exist OR a drift-check script. Should Test 1's red form be the drift-check version (purer red), then add-row test starts at Test 2? Or fold the drift check into Test 1's assertion list?

6. **Backfill script lifecycle.** Keep at `scripts/raw-index-backfill.sh` for future use (new raw subfolders), or delete after migration? Lean: keep, with comment header explaining it's safe to re-run.

7. **Wikilink-match idempotency.** Plan compares basenames without extension. Edge case: a file named `2026-05-12_session-summary.md` and an index row `[[2026-05-12_session-summary|short-label]]`. The pipe-aliased form needs to be handled. Plan calls it out but the regex needs to be exact: `\[\[<basename>(?:\|[^\]]+)?\]\]`. Codex check this regex.

8. **Frontmatter date vs filename date.** Plan reads `date:` from frontmatter as the row's Date cell, falling back to filename prefix. If they disagree, which wins? Lean: frontmatter wins (it's the authoritative metadata; filename is just a slug).

## 10. Estimate

- Plan-review (Codex Checkpoint 1): waiting on this doc.
- Implementation: ~1.5-2 hours (Python + 2 skill edits + 5 tests).
- Adversarial review (Codex Checkpoint 2): ~10-15 min for Tier 3 single pass.
- Total: 2-3 hours of focused work.
