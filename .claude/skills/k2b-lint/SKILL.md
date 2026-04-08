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

### 11. Contradiction Detection (Cole's check #7, semantic)

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

```
# Vault Lint Report -- YYYY-MM-DD

## Summary
- Checks run: 11
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

## Scheduled Execution

When run via weekly schedule:
1. Run all checks (1-10; check 11 is skipped in weekly runs)
2. Auto-fix what's safe
3. Append lint summary to `wiki/log.md`
4. If any "needs review" items: leave report in vault for Keith

Checks 8-10 (orphan sources, sparse articles, backlink warnings) run as part of the weekly schedule.
Check 11 (contradiction detection) only runs when Keith says `/lint deep` -- it is expensive and should not run automatically.

When run manually (`/lint`):
1. Run all checks
2. Show report inline
3. Ask Keith which auto-fixes to apply
4. Apply approved fixes
5. Append to `wiki/log.md`

## Rules

- Never delete notes. Only flag for Keith's decision.
- Auto-fix is limited to: adding missing index entries, removing ghost index entries, creating stubs from templates.
- All other fixes require Keith's approval.
- Always update `wiki/log.md` after a lint pass.
- If lint finds 0 issues, still log it (proves the check ran).
