---
name: k2b-inbox
description: Review and process pending content ideas and LinkedIn drafts -- triage Inbox notes (now content-ideas-only queue) by promoting, archiving, deleting, or revising based on Keith's Obsidian review decisions. Use when Keith says /inbox, "check inbox", "process inbox", "what's in inbox", or wants to review/triage Inbox items.
---

# K2B Inbox Manager

## Narrowed Scope (Vault Redesign)

**Inbox/ now ONLY contains `origin: k2b-generate` content suggestions** that need Keith's review. All other captures auto-promote to their destination folder (meeting notes to Notes/Work/, insights to Notes/Insights/, references to Notes/Reference/, etc.). If non-content items appear in Inbox, they are misrouted -- flag and relocate them.

LinkedIn drafts also live in Inbox temporarily (origin: k2b-extract) since they need Keith's approval before publishing.

## System Reference

All lifecycle rules (origin tagging, review properties, promote destinations, content pipeline) are defined in [[context_k2b-note-lifecycle]] (`Notes/Context/context_k2b-note-lifecycle.md`). This skill follows those rules exactly.

## Vault Paths

- Inbox: `~/Projects/K2B-Vault/Inbox/`
- Ready queue: `~/Projects/K2B-Vault/Inbox/Ready/`
- Archive: `~/Projects/K2B-Vault/Archive/`

## Vault Query Tools

- **Dataview DQL** (structured frontmatter queries): `~/Projects/K2B/scripts/vault-query.sh dql '<TABLE query>'`
- **Full-text search**: `mcp__obsidian__search` MCP tool or `vault-query.sh search "<term>"`
- **Read file**: `mcp__obsidian__get_file_contents` or Read tool
- **List files**: `mcp__obsidian__list_files_in_dir`

Prefer DQL queries over Glob+Read+Filter when querying frontmatter across multiple files.

## Workflow

### Step 1: Scan for actionable items

Check two locations for items Keith has reviewed:

1. **`Inbox/Ready/`** -- notes Keith dragged here in Obsidian (any note here is ready to process). Use `mcp__obsidian__list_files_in_dir` or Glob to check.
2. **`Inbox/`** -- notes where `review-action:` is set. Query with:
   ```bash
   ~/Projects/K2B/scripts/vault-query.sh dql 'TABLE review-action AS "action", review-notes AS "notes", type, origin, date FROM "Inbox" WHERE review-action != null AND review-action != ""'
   ```
   This returns all actionable items with their frontmatter in one call -- no need to glob and read each file individually.

### Step 2: Process actionable items

For each item with a decision:

#### promote
Auto-detect destination from the `type:` frontmatter field:

| `type:` value | Destination folder | Notes |
|---------------|-------------------|-------|
| `content-idea` | `Notes/Content-Ideas/` | Set `origin: keith` (Keith adopted it) |
| `linkedin-draft` | `Notes/Content-Ideas/` | Keith approved the draft |
| `project` | `Notes/Projects/` | Misrouted -- should not be in Inbox |
| `insight` | `Notes/Insights/` | Misrouted -- should not be in Inbox |
| `reference` | `Notes/Reference/` | Misrouted -- should not be in Inbox |
| `meeting-note` | `Notes/Work/` | Misrouted -- should not be in Inbox |
| Other | `Notes/` (flat) | Flag as misrouted |

On promote:
- Move file to destination folder
- Remove `review-action:` and `review-notes:` from frontmatter (clean up review properties)
- If content-idea: change `origin:` to `keith`
- If `review-notes:` has feedback, incorporate it into the note content before promoting
- Update the relevant MOC with a wikilink to the promoted note
- **Update the destination folder's `index.md`** (mandatory per vault-writer contract)
- **Append to `System/log.md`** recording the promote action

#### archive
- Move file to `Archive/`
- Keep `review-notes:` as context for why it was archived
- Remove `review-action:` from frontmatter

#### delete
- **Always confirm with Keith first**: "Delete [filename]? This cannot be undone."
- Only delete after explicit confirmation
- If Keith says yes, remove the file

#### revise
- Read `review-notes:` for Keith's feedback
- Rework the note content based on his feedback
- Clear `review-action:` (set back to empty)
- Keep `review-notes:` so Keith can see what was addressed
- Leave the note in Inbox for re-review

### Step 2.5: Log Feedback Signal

After processing each actionable item, append a feedback signal to the preference signals log:

```bash
# Variables from the item just processed:
# SKILL_ORIGIN = the skill that created this note (infer from rules below)
# ACTION = promote | archive | delete | revise
# TYPE = the note's type: frontmatter field
# DAYS_IN_INBOX = difference between today and the note's date: frontmatter field
# HAS_REVIEW_NOTES = yes | no (whether Keith wrote review-notes)
# REVIEW_NOTES_TEXT = the actual review-notes content (if any)
# FILENAME = the processed filename

echo '{"date":"'$(date +%Y-%m-%d)'","file":"'"$FILENAME"'","source_skill":"'"$SKILL_ORIGIN"'","type":"'"$TYPE"'","action":"'"$ACTION"'","days_in_inbox":'"$DAYS_IN_INBOX"',"has_feedback":"'"$HAS_REVIEW_NOTES"'","feedback":"'"$REVIEW_NOTES_TEXT"'"}' >> ~/Projects/K2B-Vault/Notes/Context/preference-signals.jsonl
```

**How to determine source_skill from the note:**
- Filename starts with `linkedin_` --> `k2b-linkedin`
- Filename starts with `content_` and origin is `k2b-generate` --> `k2b-insight-extractor`
- Filename starts with `youtube_` or type is `video-capture` --> `k2b-youtube-capture`
- Type is `tldr` --> `k2b-tldr`
- Type is `research-briefing` --> `k2b-research`
- Otherwise --> `unknown`

If the file `preference-signals.jsonl` doesn't exist, create it (first run).

### Step 3: Show remaining Inbox

After processing actionable items, query remaining Inbox items with:
```bash
~/Projects/K2B/scripts/vault-query.sh dql 'TABLE type, origin, date, review-action AS "status" FROM "Inbox" SORT date DESC'
```

Present as:
```
## Inbox (X items)

| # | File | Type | Origin | Date | Review Status |
|---|------|------|--------|------|---------------|
| 1 | content_corporate-ai... | content-idea | k2b-generate | 2026-03-22 | pending |
```

Group by type if there are many items.

## Notes

- If a note in `Inbox/Ready/` has no `review-action:` set, treat it as `promote` (Keith dragged it to Ready, most likely wants it processed)
- Always use the k2b-vault-writer conventions when moving notes
- After processing, report a summary: "Processed X items: Y promoted, Z archived, W revised"
- Cross-link promoted notes to relevant MOCs

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-inbox\t$(echo $RANDOM | md5sum | head -c 8)\tprocessed inbox: SUMMARY" >> ~/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
```
