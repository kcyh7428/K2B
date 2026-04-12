---
name: k2b-lint
description: Vault health maintenance -- find and fix structural issues, keep indexes current, detect orphans and stale content.
triggers:
  - /lint
  - vault health check
  - check vault
  - run lint
scope: project
---

# k2b-lint -- Vault Health Maintenance

Subsumes the backlogged `feature_vault-housekeeping-agent`. Run weekly (scheduled) or on-demand via `/lint`.

## Trigger

When Keith says `/lint`, "check vault health", "run lint", or when scheduled weekly.

## Lint Checks

Run all checks in order. Report findings grouped by severity.

### 1. Index Drift

For each `Notes/*/index.md`, `wiki/*/index.md`, and `raw/*/index.md`:
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
6. **Promotion candidates**: Read `~/.claude/projects/*/memory/self_improve_learnings.md`. Surface any learnings with a date newer than `Last promoted:` AND `Reinforced >= 2`. These are candidates for promotion to active rules.
7. Report format:
   ```
   [rules] Rule N references dead path `wiki/foo/` -- does not exist
   [rules] Rule N references legacy folder `Notes/Projects/` -- use `wiki/projects/`
   [rules] Last promoted 45 days ago -- review learnings for promotion candidates
   [rules] 3 promotion candidates: L-2026-04-02-001, L-2026-04-04-001, L-2026-04-07-003
   ```
8. Never auto-fix. Active rules are Keith's voice; he decides what to rewrite or retire.

### 12. Contradiction Detection (Cole's check #7, semantic)

MiniMax M2.7-powered semantic check -- only runs when explicitly requested (`/lint deep`):

```bash
~/Projects/K2B/scripts/minimax-lint-deep.sh [domain]
```

- Runs on MiniMax M2.7 (not Opus) -- cheap (~$0.02-0.05 per run)
- Script reads wiki pages, sends to MiniMax, returns JSON with contradiction pairs
- Opus parses JSON and presents findings to Keith
- Add confirmed contradictions to review/ queue for Keith's judgment
- If domain is specified, only scans pages with matching `domain:` frontmatter
- If omitted, scans all wiki pages (excluding context/)
- Note: only run on-demand, not weekly.

## Output Format

Every lint run produces two artifacts:
1. **Inline report** shown to Keith (for manual runs)
2. **Structured artifact** at `~/Projects/K2B-Vault/wiki/context/lint-report.md` -- overwritten each run, consumed by `/improve` and other skills

### Inline Report Format

```
# Vault Lint Report -- YYYY-MM-DD

## Summary
- Checks run: 12
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
run-mode: manual  # or weekly, deep
checks-run: 12
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

## Scheduled Execution

When run via weekly schedule:
1. Run all checks (1-11; check 12 is skipped in weekly runs)
2. Auto-fix what's safe
3. Write structured report to `wiki/context/lint-report.md` (overwrite)
4. Append lint summary to `wiki/log.md`
5. If any "needs review" items: leave report in vault for Keith

Checks 8-11 (orphan sources, sparse articles, backlink warnings, active rules staleness) run as part of the weekly schedule.
Check 12 (contradiction detection) only runs when Keith says `/lint deep` -- it is expensive and should not run automatically.

When run manually (`/lint`):
1. Run all checks
2. Show report inline
3. Ask Keith which auto-fixes to apply
4. Apply approved fixes
5. Write structured report to `wiki/context/lint-report.md` (overwrite)
6. Append to `wiki/log.md`

## Rules

- Never delete notes. Only flag for Keith's decision.
- Auto-fix is limited to: adding missing index entries, removing ghost index entries, creating stubs from templates.
- All other fixes require Keith's approval.
- Always update `wiki/log.md` after a lint pass.
- If lint finds 0 issues, still log it (proves the check ran).

## Usage Logging

After completing the main task:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-lint\t$(echo $RANDOM | md5sum | head -c 8)\tlint: MODE SUMMARY" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```
