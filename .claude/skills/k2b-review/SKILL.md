---
name: k2b-review
description: Review and process pending content ideas and LinkedIn drafts -- triage review queue items by promoting, archiving, deleting, or revising based on Keith's Obsidian review decisions. Use when Keith says /review, "check review", "process review", "what's in review", or wants to review/triage review queue items.
---

# K2B Review Manager

## Narrowed Scope (Vault Redesign)

**review/ holds items requiring Keith's judgment:** `origin: k2b-generate` content suggestions, LinkedIn drafts, and anything needing human review before promotion. All other captures auto-promote to their destination folder. If non-review items appear in review/, they are misrouted -- flag and relocate them.

LinkedIn drafts also live in review/ temporarily (origin: k2b-extract) since they need Keith's approval before publishing.

## System Reference

All lifecycle rules (origin tagging, review properties, promote destinations, content pipeline) are defined in [[context_k2b-note-lifecycle]] (`wiki/context/context_k2b-note-lifecycle.md`). This skill follows those rules exactly.

## Vault Paths

- Review queue: `~/Projects/K2B-Vault/review/`
- Ready queue: `~/Projects/K2B-Vault/review/Ready/`
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

1. **`review/Ready/`** -- notes Keith dragged here in Obsidian (any note here is ready to process). Use `mcp__obsidian__list_files_in_dir` or Glob to check.
2. **`review/`** -- notes where `review-action:` is set. Query with:
   ```bash
   ~/Projects/K2B/scripts/vault-query.sh dql 'TABLE review-action AS "action", review-notes AS "notes", type, origin, date FROM "review" WHERE review-action != null AND review-action != ""'
   ```
   This returns all actionable items with their frontmatter in one call -- no need to glob and read each file individually.

### Step 2: Process actionable items

For each item with a decision:

#### crosslink-digest (special case -- delegate to /weave)

If the item has `type: crosslink-digest`, do NOT use the promote/archive flow below. These notes are cross-link proposals from `k2b-weave`, and the Decision column inside the note encodes per-pair actions (check / x / defer) rather than a single review-action.

Delegate processing to the `k2b-weave` skill:

```bash
~/Projects/K2B/scripts/k2b-weave.sh apply "$FILE_PATH"
```

The weave script reads the Decision column, applies approved proposals (adds `[[to_slug]]` to the FROM page's `related:` frontmatter field), marks rejected/deferred pairs in the ledger, and deletes the digest note on completion. Report the applied/rejected/deferred counts to Keith.

After delegation, skip the regular promote/archive/delete branches for this item. No feedback signal row is needed (weave owns its own metrics + ledger).

#### promote
Auto-detect destination from the `type:` frontmatter field:

| `type:` value | Destination folder | Notes |
|---------------|-------------------|-------|
| `content-idea` | `wiki/content-pipeline/` | Set `origin: keith` (Keith adopted it) |
| `linkedin-draft` | `wiki/content-pipeline/` | Keith approved the draft |
| `crosslink-digest` | delegated to `/weave apply` | Handled above, do NOT use this table |
| `project` | `wiki/projects/` | Misrouted -- should not be in review |
| `insight` | `wiki/insights/` | Misrouted -- should not be in review |
| `reference` | `wiki/reference/` | Misrouted -- should not be in review |
| `meeting-note` | `wiki/work/` | Misrouted -- should not be in review |
| Other | `wiki/` (flat) | Flag as misrouted |

On promote:
- Move file to destination folder
- Remove `review-action:` and `review-notes:` from frontmatter (clean up review properties)
- If content-idea: change `origin:` to `keith`
- If `review-notes:` has feedback, incorporate it into the note content before promoting
- Update the relevant MOC with a wikilink to the promoted note
- **Update the destination folder's `wiki/*/index.md`** (mandatory per vault-writer contract)
- **Append to `wiki/log.md`** recording the promote action

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
- Leave the note in review/ for re-review

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

echo '{"date":"'$(date +%Y-%m-%d)'","file":"'"$FILENAME"'","source_skill":"'"$SKILL_ORIGIN"'","type":"'"$TYPE"'","action":"'"$ACTION"'","days_in_inbox":'"$DAYS_IN_INBOX"',"has_feedback":"'"$HAS_REVIEW_NOTES"'","feedback":"'"$REVIEW_NOTES_TEXT"'"}' >> ~/Projects/K2B-Vault/wiki/context/preference-signals.jsonl
```

**How to determine source_skill from the note:**
- Filename starts with `linkedin_` --> `k2b-linkedin`
- Filename starts with `content_` and origin is `k2b-generate` --> `k2b-insight-extractor`
- Filename starts with `youtube_` or type is `video-capture` --> `k2b-youtube-capture`
- Type is `tldr` --> `k2b-tldr`
- Type is `research-briefing` --> `k2b-research`
- Otherwise --> `unknown`

If the file `preference-signals.jsonl` doesn't exist, create it (first run).

### Step 3: Show remaining review queue

After processing actionable items, query remaining review items with:
```bash
~/Projects/K2B/scripts/vault-query.sh dql 'TABLE type, origin, date, review-action AS "status" FROM "review" SORT date DESC'
```

Present as:
```
## Review Queue (X items)

| # | File | Type | Origin | Date | Review Status |
|---|------|------|--------|------|---------------|
| 1 | content_corporate-ai... | content-idea | k2b-generate | 2026-03-22 | pending |
```

Group by type if there are many items.

## Notes

- If a note in `review/Ready/` has no `review-action:` set, treat it as `promote` (Keith dragged it to Ready, most likely wants it processed)
- Always use the k2b-vault-writer conventions when moving notes
- After processing, report a summary: "Processed X items: Y promoted, Z archived, W revised"
- Cross-link promoted notes to relevant MOCs

## Video feedback from `/research videos`

`review/video_*.md` notes are dropped by `/research videos` when suitable videos are added to the K2B Watch playlist. Each note has frontmatter with `review-action: pending` initially. Keith watches the video, then updates the note (via Obsidian or Telegram) -- flipping `review-action` to `liked` / `disliked` / `neutral` and writing his reaction in `review-notes`.

### Processing

For each `review/video_*.md` file where `review-action != pending`:

1. Read the file frontmatter. Extract `review-action`, `review-notes`, `channel`, `video-title`, `added` date.
2. Compose one distilled line: `<added-date> <review-action>: <channel or title> -- <one-sentence distillation of review-notes>`.
   Example: `2026-04-13 liked: Matt Wolfe -- clear concrete examples, prefer tools demos with deployment numbers`.
   Keep the distillation under ~25 words -- this is for the NotebookLM filter prompt, which reads the tail of `video-preferences.md` each run.
3. Append the line to `K2B-Vault/wiki/context/video-preferences.md` (after the `## Preferences` heading, at the end of the list).
4. Delete the review note: `rm K2B-Vault/review/<file>.md`. The distilled line is the durable record -- the raw note is transient.
5. Log the action to `wiki/log.md` as usual ("processed N video feedback notes").

Review notes with `review-action: pending` are left untouched -- Keith hasn't watched those videos yet.

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-review\t$(echo $RANDOM | md5sum | head -c 8)\tprocessed review: SUMMARY" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```
