---
name: k2b-lint
description: Vault health maintenance -- find and fix structural issues, keep indexes current, detect orphans and stale content.
---

# k2b-lint -- Vault Health Maintenance

> **Host boundary:** On SJM, lint is report-only and must not repair or rewrite the synchronized vault. All fix modes run on Home.

## Live K2B Authority

- `AGENTS.md` is the instruction authority and `.agents/skills` is the only live skill root.
- Codex is the interactive commander; Kimi K2.7 is the background text worker.
- OpenAI-built diffs use Kimi review with no fallback; Kimi-built diffs use Codex review.
- Background work is disabled unless Keith explicitly authorizes a named job with an observable receipt.
- Capture enters through the dashboard or a vault drop, never Telegram.
- Canonical memory is `K2B-Vault/System/memory`; read Codex sessions only when explicitly required and never read Claude state.

Subsumes the backlogged `feature_vault-housekeeping-agent`. In Stage 1, run it only on demand via `/lint`.

## Trigger

When Keith says `/lint`, "check vault health", or "run lint".

## Lint Checks

Run all checks in order. Report findings grouped by severity.

### 1. Index Drift

For each `wiki/*/index.md` and `raw/*/index.md`:
- Glob the folder for all .md files (excluding index.md itself)
- Compare against index.md entries
- **Missing from index**: page exists but no index entry --> auto-fix (add entry)
- **Ghost in index**: index entry but page doesn't exist --> auto-fix (remove entry)
- **Stale summary**: page title changed but index summary is outdated --> flag for review

### 2. Orphan Pages

Grep all vault .md files for wikilinks. A page is orphan if:
- Not linked from any other note (zero inbound links)
- Not listed in any index.md
- Exceptions: index.md files, Home.md, MOC_*.md, Daily/*.md, templates

Report orphans. Suggest which index or note should link to them.

### 3. Broken Wikilinks

Grep all vault .md files for `[[...]]` patterns. For each wikilink:
- Check if target file exists (glob for `**/target-name.md`)
- If not found: report as broken
- If close match exists (fuzzy): suggest correction

Auto-fix: create stubs for missing person/project pages using templates.

### 4. Missing Stubs

Scan recent notes (last 30 days) for mentions of people names or project names that don't have dedicated pages.
- People mentioned in meeting notes without a person page --> create stub
- Projects mentioned without a project page --> create stub
- After stub creation, update the relevant index.md

### 5. Stale Content

Flag pages not updated in 90+ days that have `status: on` or `status: active`:
- These may need status change to `simmering` or `archived`
- Report count and list

### 6. Unprocessed Inbox

Count items in `review/` older than 7 days:
- Report count and age of oldest item
- After Plan A ships: Inbox should only have content ideas. Flag anything else as misrouted.
- Check `review/` for stale review items (contradictions, suggestions) older than 7 days

### 7. log.md Health

Check `wiki/log.md`:
- Verify they exist and are parseable
- Report last 5 entries for Keith's awareness
- Flag if no entries in last 7 days (suggests captures aren't logging)

### 8. Orphan Sources (Cole's check #3)

Check raw/ folders for files where `compiled:` is missing or false, and the file is older than 24 hours:
- Glob `raw/**/*.md` (excluding index.md files)
- Read frontmatter of each file
- If `compiled:` is missing, false, or empty AND file date is >24h ago: flag as uncompiled
- Report: "N raw sources pending compilation"
- Suggest: run `/compile batch` to process them

### 9. Sparse Articles (Cole's check #6)

Check wiki/ pages for content under 200 words:
- Glob `wiki/**/*.md` (excluding index.md files)
- Count words in each file (exclude frontmatter)
- If <200 words: flag as sparse
- **Exemptions**: index.md files, files with `> Stub` callout, files in wiki/context/ (operational notes are often short)
- Report: "N wiki pages are sparse (<200 words)"
- Suggest: enrich from related raw sources or mark as intentionally brief

### 10. Backlink Warnings (Cole's check #5, soft)

Check wiki/ pages for inbound link count:
- For each wiki page, count how many other wiki pages link to it via `[[filename]]`
- If a page has <2 inbound links: flag as weakly connected
- **Exemptions**: index.md files, newly created pages (<7 days old)
- Report: "N wiki pages have fewer than 2 inbound links"
- This is a SOFT warning, not enforcement. Don't auto-fix.

### 11. Active Rules Staleness

Catches the failure mode where `active_rules.md` drifts out of sync with the vault after refactors (e.g. the 2026-04-11 audit found rules 2, 3, 6, 7 referencing dead paths from the pre-wiki migration).

Steps:
1. Read `K2B-Vault/System/memory/active_rules.md`.
2. Parse the `Last promoted:` date from the header.
3. Extract all vault-relative path references from rule bodies:
   - Backtick-wrapped paths (`` `wiki/insights/` ``, `` `raw/tldrs/` ``)
   - Bare folder references in prose (e.g. `Notes/Projects/`, `wiki/content-pipeline/`)
4. For each extracted path, check if it resolves in `K2B-Vault/`.
5. Flag:
   - **Dead path**: rule references a folder that does not exist (hard error)
   - **Legacy folder**: rule references `Notes/`, `Inbox/`, `Content-Ideas/`, or `Insights/` at vault root (these were retired in the raw/wiki/review migration)
   - **Stale promotion**: `Last promoted:` date is older than 30 days (soft warning)
6. **Promotion candidates**: Read `~/Projects/K2B-Vault/System/memory/self_improve_learnings.md`. Surface any learnings with a date newer than `Last promoted:` AND `Reinforced >= 2`. These are candidates for promotion to active rules.
7. Report format:
   ```
   [rules] Rule N references dead path `wiki/foo/` -- does not exist
   [rules] Rule N references legacy folder `Notes/Projects/` -- use `wiki/projects/`
   [rules] Last promoted 45 days ago -- review learnings for promotion candidates
   [rules] 3 promotion candidates: L-2026-04-02-001, L-2026-04-04-001, L-2026-04-07-003
   ```
8. Never auto-fix. Active rules are Keith's voice; he decides what to rewrite or retire.

### 12. Contradiction Detection (Cole's check #7, semantic)

Kimi K2.7 Code-powered semantic check -- only runs when explicitly requested (`/lint deep`):

```bash
~/Projects/K2B/scripts/minimax-lint-deep.sh [domain]
```

- Runs on Kimi K2.7 Code, not the interactive Codex commander
- Script reads wiki pages, sends to Kimi, returns JSON with contradiction pairs
- Codex parses JSON and presents findings to Keith
- Add confirmed contradictions to review/ queue for Keith's judgment
- If domain is specified, only scans pages with matching `domain:` frontmatter
- If omitted, scans all wiki pages (excluding context/)
- Note: only run on-demand, not weekly.

### 13. Memory Integrity (Paterson consistency, Item 3 of 2026-04-19 memory plan)

Audits `MEMORY.md` and `active_rules.md` in the symlinked memory dir (`K2B-Vault/System/memory/`). Catches two silent failure modes:

- An `.md` pointer in `MEMORY.md` resolving to a missing file (common after renaming or deleting a memory file without updating the index).
- `MEMORY.md` or `active_rules.md` growing past K2B's configured 200-line
  visibility budget. Content past that point is outside the intended memory
  surface.

Run as part of the manual `/lint` flow:

```bash
~/Projects/K2B/scripts/lint-memory.sh
```

The helper is read-only, exits 0 regardless, and prints one `[memory]` line per finding. No auto-fix -- Keith decides whether a missing pointer is a typo, a deletion that should propagate, or a file waiting to be created; he also decides whether to consolidate or prune when line counts approach the cap.

Emit findings into:
- the inline report's `## Needs Review` section
- the structured artifact's `## Needs Review` aggregator
- the structured artifact's new `## Memory Integrity (Check #13)` section

Add two counters to the structured artifact frontmatter:
- `memory-missing-pointers: N` (count of unresolved `[text](path)` links)
- `memory-line-cap-warnings: N` (0, 1, or 2 depending on which files overflowed)

### 14. Research Without Delivery Commitment (feature_k2b-integrated-loop Ship 1)

Flags any `raw/research/*.md` note older than 30 days whose frontmatter has `follow-up-delivery: null` or no `follow-up-delivery:` field. Prevents the research-as-delivery failure mode (R1 in the 2026-04-22 root-cause diagnosis): a research note landed but never named the feature it commits to.

Run:

```bash
~/Projects/K2B/scripts/loop/lint-research-delivery.sh
```

Read-only, exits 0. Prints one line per flagged note: `filename (age N days, follow-up-delivery missing/null)`.

Emit findings into the inline report's `## Content Pipeline` section and the structured artifact's `## Research Without Delivery (Check #14)` section.

Fix pathways Keith picks from when the lint flags a note:
- Link feature -> edit the note's frontmatter `follow-up-delivery:` to the feature slug after Keith chooses that fix.
- Mark purely informational -> set `follow-up-delivery: none`.
- Parked indefinitely -> move to an archive folder.

## Output Format

Every lint run produces two artifacts:
1. **Inline report** shown to Keith (for manual runs)
2. **Structured artifact** at `~/Projects/K2B-Vault/wiki/context/lint-report.md` -- overwritten each run, consumed by `/improve` and other skills

### Inline Report Format

```
# Vault Lint Report -- YYYY-MM-DD

## Summary
- Checks run: 13
- Auto-fixed: N issues
- Needs review: N items
- Clean: N checks passed

## Auto-Fixed
- [index] Added 3 missing entries to People/index.md
- [index] Removed 1 ghost entry from Reference/index.md
- [stub] Created person_New-Name.md stub

## Needs Review
- [orphan] insight_old-topic.md has zero inbound links
- [stale] work_galaxy-fm-mapping.md last updated 2026-03-22, status: simmering
- [broken] [[nonexistent-page]] referenced in project_k2b.md

## All Clear
- log.md: healthy, last entry 2 days ago
- Inbox: 1 content idea (normal)
```

### Artifact Format (`wiki/context/lint-report.md`)

Frontmatter carries the summary counts and per-check roll-up. Body groups findings by check so downstream skills can extract specific sections:

```yaml
---
type: lint-report
date: 2026-04-11
run-mode: manual  # or deep
checks-run: 13
auto-fixed: 3
needs-review: 5
clean: 4
hard-errors: 0
rules-dead-paths: 0
rules-legacy-folders: 0
rules-last-promoted: 2026-04-11
rules-promotion-candidates: 0
vault-orphans: 2
vault-broken-links: 1
review-stale-items: 4
uncompiled-raw: 7
sparse-wiki-pages: 3
memory-missing-pointers: 0
memory-line-cap-warnings: 0
up: "[[index]]"
---

# Vault Lint Report -- 2026-04-11

## Needs Review

Aggregator across all checks, ordered by severity: hard errors first (dead paths, broken wikilinks targeting nonexistent files), then flagged items (orphans, stale review items, uncompiled raw, sparse wiki, weak backlinks), then soft warnings (stale promotion, legacy folder references). Each line prefixed with the check tag (e.g. `[rules]`, `[orphan]`, `[broken]`, `[stale]`, `[uncompiled]`).

This section is the canonical entry point for downstream consumers like `/improve` Section 3 -- they read this list rather than walking the per-check sections below.

## Active Rules (Check #11)
... findings ...

## Vault Structure (Checks #1-5)
... findings ...

## Content Pipeline (Checks #6-9)
... findings ...

## Link Graph (Checks #3, #10)
... findings ...
```

This structured file is the source of truth for `/improve` Sections 1b and 3 -- they read this file rather than re-running the queries. Section 3 reads `## Needs Review`; Section 1b reads `## Active Rules`.

## Stage 1 Scheduling State

There is no recurring lint job in Stage 1. Do not register or revive one from this skill. Scheduling lint would be a separate explicitly authorized change and must run on the home Mac, never SJM. Check 12 (contradiction detection) runs only when Keith says `/lint deep`; it is expensive and must not run automatically.

When run manually (`/lint`):
1. Run all checks
2. Show report inline
3. Ask Keith which auto-fixes to apply
4. Apply approved fixes
5. Write structured report to `wiki/context/lint-report.md` (overwrite)
6. Append via `scripts/wiki-log-append.sh /lint <lint-run-id> "<summary>"`

On SJM, stop after the inline report. Do not apply fixes, overwrite `lint-report.md`, or append to `wiki/log.md` from the read-only synchronized vault.

## Rules

- Never delete notes. Only flag for Keith's decision.
- Auto-fix is limited to: adding missing index entries, removing ghost index entries, creating stubs from templates.
- All other fixes require Keith's approval.
- Always update `wiki/log.md` via `scripts/wiki-log-append.sh` (never `>>`) after a lint pass.
- If lint finds 0 issues, still log it (proves the check ran).

## Usage Logging

After completing the main task:
```bash
python3 "$HOME/Projects/K2B/scripts/k2b-shared-append.py" usage --skill k2b-lint --summary "lint: MODE SUMMARY"
```
