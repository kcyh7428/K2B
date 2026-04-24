---
name: k2b-review
description: Review and process pending content ideas and LinkedIn drafts -- triage review queue items by promoting, archiving, deleting, or revising based on Keith's Obsidian review decisions. Use when Keith says /review, "check review", "process review", "what's in review", or wants to review/triage review queue items.
---

> [!warning] DEPRECATED in Ship 2 of k2b-integrated-loop
> The session-start dashboard routes review/ queue items via the same
> `a N / r N / d N` grammar used for observer candidates. Accept -> move to
> `review/Ready/`, reject -> move to `Archive/review-archive/YYYY-MM-DD/`,
> defer -> counter (auto-archive on third defer). Run `/review` directly only
> when Keith needs the full triage workflow -- video-feedback batch moves,
> crosslink application via k2b-vault-writer, content-idea promotion to
> `wiki/content-pipeline/`. Before proceeding with the legacy workflow, emit
> the sentence "DEPRECATED in Ship 2 of k2b-integrated-loop -- the session-
> start dashboard covers accept/reject/defer; continue only if you need the
> full triage workflow" and wait for Keith's explicit go-ahead.

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
- **Append to `wiki/log.md` via helper:** `scripts/wiki-log-append.sh /review <review-file> "promoted: <target-path>"`

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

## Video feedback from `/research videos` (run-level)

`review/videos_YYYY-MM-DD_<query-slug>.md` files are run-level notes dropped by `/research videos`. Each note carries 0-5 picks; each pick has a fenced ` ```yaml ... ``` ` block with K2B-managed state (`pick_id`, `video_id`, `suggested_category`, `category_override`, `decision`, `playlist_action`, `preference_logged`, `processed_at`, `notes`). Keith edits ONLY `decision`, `category_override`, and `notes` in Obsidian -- or sends a Telegram reaction, in which case the Telegram feedback path below edits the same block. Both paths share the flock and the atomic rewrite helper.

### Concurrency and atomicity

All processing of `videos_*.md` files takes a narrow flock around the per-file update:

```bash
exec 9>/tmp/k2b-review-videos.lock
flock -x 9
# ... process one file, edit YAML blocks in place, release ...
exec 9>&-
```

Preference-tail appends to `wiki/context/video-preferences.md` are atomic write-then-rename (write full file to `.tmp`, then `mv`). Never direct `>>` append -- the Telegram feedback path can be racing.

### Playlist map

All playlist IDs come from `~/Projects/K2B/scripts/k2b-playlists.json`. Lookup:
```bash
K2B_WATCH_ID=$(jq -r '."K2B Watch"' ~/Projects/K2B/scripts/k2b-playlists.json)
DEST_ID=$(jq -r --arg name "$EFFECTIVE_CATEGORY" '.[$name] // empty' ~/Projects/K2B/scripts/k2b-playlists.json)
```
If `DEST_ID` is empty, K2B suggested a new category that doesn't exist yet. Leave that pick `playlist_action: pending`, append `new category suggested: <name>` to its `notes:` field, surface it in the `/review` summary for Keith to approve. Do NOT run any Data API call to create the playlist.

### Processing flow

1. Glob `K2B-Vault/review/videos_*.md`.
2. For each file, acquire the flock, then:
   - Read the frontmatter (`query`, `run-date`, `review-action`, `picks-count`).
   - Parse pick YAML blocks with a Python helper. The parser locates each `^### \d+\. ` heading, finds the first fenced ` ```yaml ... ``` ` block inside that pick, and parses it with PyYAML. The prose above each YAML fence is ignored.
   - For each pick, derive `effective_category`: if `category_override` is non-empty, use it; otherwise use `suggested_category`.
3. For each pick where `decision != pending` **AND** (`playlist_action != done` OR `preference_logged != true`):
   - Resolve `effective_category` → YouTube playlist ID via the JSON map.
   - Extract `video_id` directly from the pick's YAML (already K2B-managed state).
   - `decision: keep`:
     - `scripts/yt-playlist-remove.sh "$K2B_WATCH_ID" "$VIDEO_ID"` (remove from K2B Watch; idempotent).
     - `scripts/yt-playlist-add.sh "$DEST_ID" "$VIDEO_ID"` (add to category).
     - Append distilled line to `video-preferences.md` via atomic write-rename: `<run-date> kept [<effective_category>]: <real_channel from pick YAML> -- <notes distilled to one sentence>`.
   - `decision: drop`:
     - `scripts/yt-playlist-remove.sh "$K2B_WATCH_ID" "$VIDEO_ID"`.
     - Append distilled line: `<run-date> dropped: <channel> -- <notes distilled or "no notes">`.
   - `decision: neutral`:
     - `scripts/yt-playlist-remove.sh "$K2B_WATCH_ID" "$VIDEO_ID"`.
     - Append distilled line: `<run-date> neutral: <channel> -- <notes or "nothing notable">`.
4. **Per-pick state updates after each action** (partial-state tracking prevents double-execution):
   - On success: edit the pick's YAML block in place -- set `playlist_action: done`, `processed_at: <ISO8601>`, and after a successful preference-tail append set `preference_logged: true`. Use a full file read + rewrite + atomic rename (never line-by-line edit in place).
   - On failure: set `playlist_action: failed`, append the error to `notes:` (do NOT overwrite Keith's existing notes text), leave `preference_logged` at its prior value. Log via helper: `scripts/wiki-log-append.sh /review <review-file> "(failed: <error>)"`. Leave the file in `review/`; next `/review` retries the failed pick only.
5. **File-level completion check.** After processing all picks in a file:
   - If EVERY pick has `decision != pending` AND `playlist_action == done` AND `preference_logged == true`: set the file's frontmatter `review-action: processed`, write a one-line summary into `review-notes` (e.g., `"2 kept to K2B Claude, 1 dropped"`), then move the file from `review/` to `raw/research/` as a durable audit trail. Do NOT delete -- the run note's K2B reasoning + rejects list + per-pick state history has audit value beyond the distilled lines.
   - Otherwise leave the file in place with `review-action: pending`. Next `/review` catches the remaining picks.
6. Release the flock and log via helper:
   `scripts/wiki-log-append.sh /review videos_<slug>.md "processed N picks: X kept, Y dropped, Z neutral, W failed"`

### Forbidden

- **Do NOT parse the prose above a pick's YAML fence.** It exists only for Keith to read.
- **Do NOT double-execute picks.** Always check `playlist_action == done` and `preference_logged == true` before skipping.
- **Do NOT direct-append to `video-preferences.md`.** Always use atomic write-then-rename; the Telegram feedback path is racing you.
- **Do NOT create YouTube playlists automatically** when K2B suggests a new category. Flag and wait for Keith.

## Video Feedback from Telegram (run-level)

When Keith reacts to a video in a Telegram conversation (examples: "the Wolfe video was great, move it to K2B Claude", "that Operator breakdown was shallow, drop Matthew Berman", "I liked pick #3 from today's batch", "loved all 3 today"), the interactive Claude session edits the same `videos_*.md` run-level notes this skill processes. The two paths share the same flock, the same atomic rewrite helper, and the same preference-tail append contract documented above. Do not ask for confirmation before acting on Telegram video feedback -- act.

### Telegram reaction flow

1. **Glob `K2B-Vault/review/videos_*.md`** (run-level notes; not `video_*.md`).
2. **Match the reaction to a pick.** Read candidates from the most recent `run-date` backwards. Within each file, parse picks via their fenced ` ```yaml ... ``` ` blocks (same contract as step 2 of "Processing flow" above: locate each `^### \d+\. ` heading, read the first YAML fence inside that pick). Match rule: URL if Keith pasted one → exact title → channel → "pick #N" ordinal within the most recent run. Bulk reactions ("loved all 3", "drop the last two") iterate through matched picks in sequence.
3. **For each matched pick**, acquire `flock -x 9` on `/tmp/k2b-review-videos.lock`, then:
   - Edit the pick's YAML block in place via full read + rewrite + atomic rename. Set `decision:` to `keep` / `drop` / `neutral` based on Keith's tone. Optionally set `category_override:` if Keith's reaction implies a different category ("move the Wolfe video to K2B Claude"). Append Keith's distilled reaction to `notes:` (do NOT overwrite existing text).
   - Immediately run the same playlist move logic as the main flow above: jq-lookup `K2B_WATCH_ID` and `DEST_ID` from `scripts/k2b-playlists.json`, call `scripts/yt-playlist-remove.sh`, and on `keep` also call `scripts/yt-playlist-add.sh`.
   - Append one distilled line to `wiki/context/video-preferences.md` via atomic write-then-rename. Same format as the main flow.
   - On success: update the pick's YAML block with `playlist_action: done`, `processed_at: <ISO8601>`, `preference_logged: true`. On failure: `playlist_action: failed`, append the error to `notes`, leave `preference_logged` at its prior value.
   - Release the flock.
4. **Reply in Telegram** with the concrete result for each pick processed: `"done -- moved <title> to K2B Claude"` / `"done -- dropped <title> from Watch"`. For bulk reactions, combine into one reply.
5. **Zero matches:** reply `"no matching pick in recent run notes -- want me to log this as a standalone preference line?"` and wait for Keith's answer.
6. **Ambiguous within one file:** list the candidate picks in Telegram with their `pick_id`s and ask Keith to disambiguate.

### Forbidden on the Telegram path (Liam Ottley bug prevention)

- **NEVER append directly to `wiki/context/video-preferences.md`.** The file is jointly owned by `/review` and the Telegram path. ALL writes go through the SAME atomic read-rewrite-rename helper -- never `>>`, never `echo >>`, never `obsidian_append_content`. If you catch yourself about to do a direct append, STOP: read the file, rewrite it in full to a `.tmp` sibling, then `mv` atomically. The direct-append pattern is exactly what produced the Liam Ottley bug during B8 testing and this rule exists to prevent recurrence.
- **NEVER hardcode playlist IDs.** Always `jq`-lookup from `scripts/k2b-playlists.json`.
- **NEVER skip the flock.** `/review` can be running concurrently; double-execution of a playlist move is exactly the failure mode this locking prevents.

The "is this video feedback?" decision is made by reading Telegram conversation context. No new MCP tool, no routing code, no keyword matching -- the interactive Claude session uses its built-in Edit/Glob/Grep/Bash tools.

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-review\t$(echo $RANDOM | md5sum | head -c 8)\tprocessed review: SUMMARY" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```
