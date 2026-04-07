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

For each `Notes/*/index.md`:
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

Count items in `Inbox/` older than 7 days:
- Report count and age of oldest item
- After Plan A ships: Inbox should only have content ideas. Flag anything else as misrouted.

### 7. log.md Health

Check `System/log.md`:
- Verify it exists and is parseable
- Report last 5 entries for Keith's awareness
- Flag if no entries in last 7 days (suggests captures aren't logging)

## Output Format

```
# Vault Lint Report -- YYYY-MM-DD

## Summary
- Checks run: 7
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
1. Run all checks
2. Auto-fix what's safe
3. Append lint summary to `System/log.md`
4. If any "needs review" items: leave report in vault for Keith

When run manually (`/lint`):
1. Run all checks
2. Show report inline
3. Ask Keith which auto-fixes to apply
4. Apply approved fixes
5. Append to `System/log.md`

## Rules

- Never delete notes. Only flag for Keith's decision.
- Auto-fix is limited to: adding missing index entries, removing ghost index entries, creating stubs from templates.
- All other fixes require Keith's approval.
- Always update `System/log.md` after a lint pass.
- If lint finds 0 issues, still log it (proves the check ran).
