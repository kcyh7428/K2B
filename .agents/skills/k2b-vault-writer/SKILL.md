---
name: k2b-vault-writer
description: Default desktop path for creating or updating K2B vault notes with correct frontmatter, cross-links, and MOC integration. Use for "save this", "capture this", "update the vault", and project/person/decision updates unless Keith explicitly invokes a dormant capture lane.
---

# K2B Vault Writer

> **Host boundary:** Ordinary wiki notes (`wiki/work`, `wiki/people`, `wiki/projects`, `wiki/concepts`, `wiki/insights`, `wiki/reference`) save locally on either Mac through `scripts/vault-note-write.py` during active user-requested conversations -- no Home availability required. The helper's folder-index row and master-count updates are part of this explicit interactive exception. Policy/control state, MOCs, shared logs, raw capture, bulk compile and background memory remain Home-owned. On SJM, finish the ordinary save, report its local receipt, and state any Home-only follow-up separately; do not block the save, mutate those files, or invent a queue for them.

Create notes in the K2B Obsidian vault at `~/Projects/K2B-Vault/` with correct structure, frontmatter, cross-links, and MOC integration.

## Desktop Capture Default

Use this skill as the default desktop route for "save this", "capture this", "update K2B", "write this to the vault", and durable project/person/decision updates. Do not create a new capture skill for single-answer synthesis. Decide whether the material belongs directly in `wiki/` or should be saved as a raw source for later `/compile`:

- Stable facts, project status, people context, decisions, and operating rules: update the existing wiki page directly.
- External research briefings, transcript-like material, or broad session handoffs that need digestion across multiple pages: save to the appropriate `raw/` folder and leave `compiled: false` for `/compile`.
- If the user explicitly invokes `/meeting`, `/daily`, `/linkedin`, or `/youtube`, use that dormant lane. Otherwise keep desktop capture here.

## System Reference

Before writing any note, review the lifecycle rules in [[context_k2b-note-lifecycle]] (`wiki/context/context_k2b-note-lifecycle.md`). That note is the single source of truth for origin tagging, review properties, promote destinations, and the content pipeline.

## Ordinary Note Save Procedure (MANDATORY for wiki/ note create or update)

For ordinary notes under `wiki/work`, `wiki/people`, `wiki/projects`, `wiki/concepts`, `wiki/insights`, `wiki/reference`, do NOT rewrite the note body with Edit/Write or Obsidian patch tools. Instead:

1. **Prepare a private draft outside the vault** (for example under `/tmp` or `~/.local/state/k2b/drafts`) with the complete replacement note via apply_patch. The note must be valid UTF-8 Markdown with closed YAML frontmatter containing the core fields `tags`, `date`, `type`, `origin`, `up`, plus a nonempty body. One note per draft, at most 2 MiB.
2. **Read the current target** and compute its sha256 (`shasum -a 256`), or pass `missing` for a new note. Read its folder index. An existing row determines the owning table; for a new note with multiple eligible tables, supply `--section 'Exact existing heading'` (for example `Backlog` or `SJM Resorts`). Missing or ambiguous ownership fails before saving. Pick a one-line summary; `--status` is only for a Status-column table, never a section selector. Fields must not contain newlines or `|` delimiters.
3. **Invoke the writer:**

   ```bash
   python3 "$HOME/Projects/K2B/scripts/vault-note-write.py" write \
     --path wiki/work/work_example.md \
     --content-file /absolute/private-draft.md \
     --expected-sha256 <current-hash|missing> \
     --summary 'one line' \
     --source-ref 'conversation or source identifier' \
     [--status active] [--section 'Existing heading']
   ```

   `write` runs only for the approved operators (keithmbpm2, keithcheung) and needs PyYAML. On SJM use `$HOME/Projects/K2B/venv/k2b-note-save/bin/python`; elsewhere use the available project interpreter if default Python lacks PyYAML. Nothing is installed automatically. Success reports local saving, synchronization unverified. A completed identical operation is a no-op; changed summary/status is a new index update even when note bytes match. A `partial` result names the failed index or receipt step: retain the original draft, expected hash and command, resolve the reported cause, then retry that exact command. A persistently malformed index needs correction on Home; retries do not repair arbitrary malformed Markdown. Exit 2 means an observed conflict copy or stale expected hash: stop, preserve both versions, and surface it to Keith. This can occur after the note was saved; a partial receipt with `failed_step: conflict` requires manual resolution before retry, not repeated automatic retries.
4. **Never** bypass the writer for these notes: it holds the local writer and designated index locks, keeps durable private before/after recovery copies plus a receipt under `~/.local/state/k2b` (never in the vault), and refuses to overwrite `.sync-conflict-*.md` copies.
5. **Logging:** on Home, append the save to `wiki/log.md` via `scripts/wiki-log-append.sh` as usual. On SJM, do NOT run `wiki-log-append.sh` (shared hubs are Home-owned); the writer's local receipt is the record, and the small usage record may queue via `scripts/k2b-shared-append.py usage`.

A real Syncthing conflict copy preserves both versions; do not delete or hand-merge conflict files without Keith's decision.

## Vault Query Tools

- **Dataview DQL** (structured frontmatter queries): `~/Projects/K2B/scripts/vault-query.sh dql '<TABLE query>'`
- **Full-text search**: `mcp__obsidian__search` MCP tool or `vault-query.sh search "<term>"`
- **Read file**: `mcp__obsidian__get_file_contents` or Read tool
- **List files**: `mcp__obsidian__list_files_in_dir`
- **Patch content**: for ordinary notes, apply_patch a private replacement draft and use the writer. Other Home-only mutable notes retain their designated tools.

Use filesystem search or the available read-only Obsidian tools to verify wikilink targets. Ordinary frontmatter changes use the same private-draft writer procedure as body changes.

## Before Writing Any Note

1. **Read the appropriate template** from `Templates/` to get the base structure
2. **Check for duplicates** -- use `mcp__obsidian__search` or Glob to check if target note already exists
3. **Verify link targets** -- before writing `[[person_Firstname-Lastname]]`, use `mcp__obsidian__search` or Glob to confirm the file exists. If not, note it as a stub to create later.

## Updating Existing Notes

When progress is made on a project, person interaction occurs, or a decision evolves, update the existing note rather than creating a new one.

### When to Update
- After `/tldr` captures progress related to a project
- After `/meeting` processes a meeting tied to a project or person
- After `/daily` captures completed work items tied to a project
- Any time K2B works on implementation of a project and makes meaningful progress
- When new relationships or links are discovered between existing notes

### Update Workflow
1. **Glob** to find the target note (e.g., `wiki/projects/project_*.md`)
2. **Read** the current content
3. **Determine which sections need updates:**
   - `## Current Status` -- rewrite the blockquote to reflect current state
   - `## Key Milestones` -- check off completed items (`- [x]`), add new milestones if needed
   - `## Updates` -- append a new dated entry (`### YYYY-MM-DD`) with bullet points of what changed
   - `## Related Notes` -- add wikilinks to any new related notes (meetings, decisions, insights)
   - For person notes: append new interactions under `## Key Interactions`
4. **For ordinary notes:** copy current content into a private draft, apply_patch only the intended sections there, and save the complete draft through the writer. For other permitted Home-only mutable notes, use their designated surgical writer.
5. **Verify** wikilinks in new content point to existing notes (glob first)

### Update Rules
- Preserve unrelated sections and history verbatim in the replacement draft; the helper atomically replaces the file after its checks.
- Always append to `## Updates` -- never remove previous entries
- When checking off milestones, preserve the original text and just change `[ ]` to `[x]`
- Add the date to checked-off milestones: `- [x] KIRA AirTable migration (2026-03-23)`
- If a note doesn't have an `## Updates` section, add one at the bottom before the last section

## File Naming Conventions

| Note Type | Pattern | Example |
|-----------|---------|---------|
| Project | `project_lowercase-slug.md` | `project_signal-monitoring.md` |
| Person | `person_Firstname-Lastname.md` | `person_Gerard-Walker.md` |
| Insight | `insight_topic-slug.md` | `insight_two-stage-ai-prevents-hallucination.md` |
| Reference | `YYYY-MM-DD_source_topic-slug.md` | `2026-03-25_youtube_ai-writing-dan-koe.md` |
| Content Idea | `content_short-slug.md` | `content_corporate-ai-restrictions.md` |
| Meeting | `YYYY-MM-DD_Meeting-Topic.md` | `2026-03-22_Hiring-Sync.md` |
| MOC | `MOC_Topic-Name.md` | `MOC_SJM-Work.md` |
| Daily | `YYYY-MM-DD.md` | `2026-03-22.md` |
| Business | `entityname_overview.md` | `talentsignals_overview.md` |
| K2B Feature | `feature_short-slug.md` | `feature_content-feed-system.md` |
| Work (SJM) | `work_lowercase-slug.md` | `work_the-eight-chef-search.md` |

## File Locations (Raw/Wiki Architecture)

K2B uses a 3-layer vault architecture (Karpathy model):
- **raw/** -- Immutable source captures. LLM reads only, never modifies after creation.
- **wiki/** -- LLM-compiled knowledge pages. K2B owns and maintains this layer.
- **review/** -- Items requiring Keith's judgment (content ideas, contradictions).

### Capture Skills -> raw/

| Capture Type | Raw Destination | Then |
|--------------|----------------|------|
| YouTube video | `raw/youtube/` | Trigger k2b-compile |
| Meeting transcript | `raw/meetings/` | Trigger k2b-compile |
| Research briefing | `raw/research/` | Trigger k2b-compile |
| TLDR | `raw/tldrs/` | Trigger k2b-compile |
| Daily extracts | `raw/daily/` | Trigger k2b-compile |

### Wiki Pages (compiled output)

| Note Type | Wiki Destination | Created By |
|-----------|-----------------|------------|
| Project | `wiki/projects/` | k2b-compile or direct |
| Person | `wiki/people/` | k2b-compile or direct |
| Concept | `wiki/concepts/` | k2b-compile or direct |
| Insight | `wiki/insights/` | k2b-compile or /insight |
| Reference | `wiki/reference/` | k2b-compile |
| Work (SJM) | `wiki/work/` | k2b-compile or direct |
| Content idea (adopted) | `wiki/content-pipeline/` | /review promote |
| Context | `wiki/context/` | Direct write |

### Review Queue

| Type | Destination | Needs Keith's Review? |
|------|------------|----------------------|
| Content Idea (k2b-generate) | `review/` | **Yes** -- Keith decides |
| Compile conflicts | `review/` | **Yes** -- contradictions |
| Lint findings | `review/` | **Yes** -- semantic issues |

### Other Locations (unchanged)

| Note Type | Folder |
|-----------|--------|
| Daily note | `Daily/` |
| MOC | Vault root |
| Generated images | `Assets/images/` |
| Generated audio | `Assets/audio/` |
| Generated video | `Assets/video/` |
| Home | Vault root |

### TLDR Handling
TLDRs save to raw/tldrs/ as immutable captures. k2b-compile then digests them:
- Insights compiled into `wiki/insights/`
- Content seeds compiled into `wiki/content-pipeline/` or `review/` (if k2b-generate)
- Extract action items --> update relevant project/work notes
- Archive the TLDR shell --> `Archive/`

### What Goes to review/
ONLY items requiring Keith's judgment: `origin: k2b-generate` content suggestions, compile conflicts, lint contradictions. If review/ has anything else, something is misrouted.

## Frontmatter Conventions

### All notes get:
```yaml
---
tags: [type-tag, domain-tags...]
date: YYYY-MM-DD
type: project | work | person | concept | insight | content-idea | moc | daily | reference | k2b-feature
origin: keith | k2b-extract | k2b-generate
up: "[[relevant MOC or Home]]"
---
```

### Origin field guide:
- `keith` -- Keith's direct input, words, ideas, decisions
- `k2b-extract` -- K2B extracted/summarized from Keith's input (meeting summaries, video takeaways from Keith's reactions)
- `k2b-generate` -- K2B generated independently (connections, patterns, suggestions, recommendations)
- When a note mixes both, use the primary origin and distinguish sections with callouts: `> [!quote] Keith's input` and `> [!robot] K2B analysis`

### Review properties
All notes saved to review/ must include these properties for Keith's Obsidian review:
- `review-action:` -- empty until Keith decides (promote, archive, delete, revise)
- `review-notes: ""` -- Keith's feedback/comments

### Content Pipeline
- `/content` suggestions land in `review/` with `origin: k2b-generate`
- Only when Keith says "promote this" does it move to `wiki/content-pipeline/` with `origin: keith`
- `wiki/content-pipeline/` is Keith's curated list of adopted content ideas

### Type-specific fields:

**Project** (things Keith builds/creates personally):
```yaml
status: on | ongoing | simmering | sleeping | parked
priority: high | medium | low
domain: talentsignals | agency-at-scale | signhub | personal | k2b
```

**Work** (SJM role responsibilities Keith drives/oversees):
```yaml
status: on | active | simmering
priority: high | medium | low
domain: sjm
```
Work notes use a simpler structure: Context, Current Status, Key Decisions, People, Open Questions, Updates. No milestones -- Keith drives these through his team, not as personal deliverables.

**Person:**
```yaml
organization: Company Name
role: Their Role
relationship: [colleague, stakeholder, report, external, partner, client]
```

**Content Idea:**
```yaml
platform: [linkedin, youtube]
status: idea | outline | draft | ready | published
source: "[[source note]]"
```

**Decision:**
```yaml
project: "[[project_name]]"
status: active | superseded | revisit
```

**Insight:**
```yaml
domain: sjm | talentsignals | agency-at-scale | technical | career
content-potential: true | false
```

## Cross-Linking Rules

1. **Always use wikilinks**: `[[filename_without_extension]]` for all internal links
2. **People links**: `[[person_Firstname-Lastname]]`
3. **Project links**: `[[project_slug]]`
4. **Decision links**: `[[YYYY-MM-DD_decision-topic]]`
5. **MOC uplinks**: Every note must have an `up` field in frontmatter pointing to its parent MOC
6. **Bidirectional**: Save an ordinary referenced note's backlink via its own private draft, current hash and writer invocation. Home-only targets are a separate follow-up on SJM.
7. **Stub creation**: Create ordinary stubs through the same writer, with required frontmatter and a `> Stub -- to be populated` body. Do not create out-of-scope stubs on SJM.

## MOC Integration

On Home, add the note's link to the relevant MOC through the existing permitted path after the sync/policy checks. On SJM, leave MOCs unchanged and report the missing MOC link as a Home-only follow-up, not a failed ordinary save. The five MOCs are:

- `[[MOC_SJM-Work]]` -- SJM team, searches, decisions, meetings
- `[[MOC_TalentSignals]]` -- Signal Monitoring, R2, Reverse Recruiter, RecruitClaw, clients
- `[[MOC_Agency-at-Scale]]` -- Business overview, historical client work
- `[[MOC_Content-Pipeline]]` -- Content ideas, insights with content potential
- `[[MOC_K2B-System]]` -- Vault conventions, migration, system notes

A note can appear in multiple MOCs (e.g., an insight about AI that also has content potential goes in both TalentSignals and Content Pipeline MOCs).

## Writing Style

- No em dashes. Use -- (double hyphen) if needed.
- No AI cliches or filler.
- Write in Keith's voice: direct, specific, no generic language.
- Section headers match the template structure.
- Populate sections with real data, not placeholder text. If data isn't available, use `> [!todo] To be populated` callout.
- Keep notes scannable. Bullet points over paragraphs where appropriate.

## Obsidian Syntax Reference

For Obsidian-specific syntax beyond standard markdown (callouts, embeds, math, mermaid diagrams, footnotes, block IDs, comments, highlights), read `references/obsidian-syntax.md` in this skill's directory.

## Template Reference

Templates are at `~/Projects/K2B-Vault/Templates/`:
- `daily-note.md`
- `project-note.md`
- `person-note.md`
- `meeting-note.md`
- `content-idea.md`
- `decision-log.md`

For MOCs, insights, and reference docs, no template exists. Use the frontmatter conventions above and a clean markdown structure.

## Asset Embedding

When notes reference generated media (from `/media` or MiniMax MCP tools), use Obsidian embed syntax:

- Images: `![[Assets/images/YYYY-MM-DD_image_slug.png]]`
- Audio: `![[Assets/audio/YYYY-MM-DD_speech_slug.mp3]]`
- Video: `![[Assets/video/YYYY-MM-DD_video_slug.mp4]]`

Asset naming: `YYYY-MM-DD_type_slug.ext` where type is `image`, `speech`, `music`, or `video`.

Content ideas with generated assets should have a `## Generated Assets` section containing embed links.

## Review Queue Write Contract

**review/ holds ONLY items requiring Keith's judgment.** Content ideas (k2b-generate), compile conflicts, lint contradictions.

Skills that write to review/: `k2b-insight-extractor` (content suggestions), `k2b-compile` (conflicts), `k2b-lint` (contradictions).

**Every note saved to `review/` MUST have these frontmatter fields:**

```yaml
review-action:       # empty string -- Keith fills this in Obsidian
review-notes: ""     # empty string -- Keith fills this in Obsidian
```

**Before writing any review note, verify:**
1. The item genuinely requires Keith's judgment (not auto-promotable)
2. `review-action:` is present in frontmatter (empty value is correct)
3. `review-notes: ""` is present in frontmatter
4. File path starts with `review/`

If you're updating an existing review note, preserve any `review-action` or `review-notes` values Keith has already set.

## Index Update Contract (MANDATORY)

**Every note created or updated must also update the relevant index.md.** No exceptions.

For ordinary wiki notes the writer inserts or updates the owning folder-index row, refreshes the folder date/count and master counts through the designated compile helpers and lock. This interactive index exception applies on both Macs. It supports Summary, Status/Summary and people Role/Context tables, plus concept phase/backlog tables; non-summary concept metadata stays unchanged (new values use `-`). Existing rows determine ownership; new rows in multi-table indexes require `--section`. Shipped tables are excluded. Do not hand-edit these indexes during ordinary saves. A missing/malformed index fails explicitly; retry repairs only the interrupted operation once its reported cause is resolved.

For notes outside the ordinary roots (raw captures, review items, context pages), the manual steps below still apply:

1. **Read** the folder's `index.md` (e.g., `raw/youtube/index.md`)
2. **If new note**: Add a row to the index table with `[[filename]]`, one-line summary, and date
3. **If updated note**: Update the summary and date in the existing row if the summary changed
4. **If note moved/deleted**: Remove the old index entry, add to new location's index
5. **Update wiki/index.md** master counts on Home through the designated helper. SJM cannot perform these non-ordinary writes; report a Home-only follow-up without inventing a queued record.

Index format (one line per page, keep it short):
```markdown
| [[page-name]] | One-line summary | YYYY-MM-DD |
```

### Wiki indexes (primary):
- `wiki/index.md` (master catalog)
- `wiki/people/index.md`
- `wiki/projects/index.md`
- `wiki/work/index.md`
- `wiki/concepts/index.md`
- `wiki/insights/index.md`
- `wiki/reference/index.md`
- `wiki/content-pipeline/index.md`
- `wiki/context/index.md`

### Raw indexes:
- `raw/index.md` (master catalog)
- `raw/youtube/index.md`
- `raw/meetings/index.md`
- `raw/research/index.md`
- `raw/tldrs/index.md`
- `raw/daily/index.md`

### Legacy indexes (Notes/ -- kept as fallback):
- `Notes/People/index.md` through `Notes/Features/index.md`

## Post-Write Cross-Link Pass

After creating any note, run a cross-link pass to connect it to existing vault content:

1. **Scan the new note** for mentions of people, projects, work items, or concepts
2. **For each mentioned entity**:
   - Glob to check if a page exists (e.g., `wiki/people/person_*.md`)
   - If an ordinary entity page exists: if its wikilink is missing from the saved note, prepare another private draft and save that addition through the writer. Save the entity's dated backlink through its own writer invocation, preserving its history.
   - If absent: create an ordinary stub through the writer and its index procedure. On SJM, out-of-scope targets remain Home-only follow-ups.
3. **On Home only, update `wiki/log.md` via helper:**
   `scripts/wiki-log-append.sh /vault-writer <note-path> "created/updated: <summary>, linked: <targets>"`

The helper is the only permitted writer for wiki/log.md. On SJM never invoke it: use the ordinary save's local receipt and the permitted usage queue. Do not append the shared log directly.

This pass runs AFTER the note is written and validated, not during writing.

**For raw/ captures:** Cross-linking is minimal (just add `compiled: false` frontmatter). The full cross-link pass happens during k2b-compile, which updates wiki pages.

## Policy Ledger Check (MANDATORY -- runs before every mutation)

Before writing, editing, or deleting ANY vault note, check the policy ledger:

1. **Read** `wiki/context/policy-ledger.jsonl`
2. **Filter** entries where `scope` matches this skill (`k2b-vault-writer`) or is `*` (global)
3. **For each matching guard**: verify the action complies with the rule. If it doesn't, stop and adjust.
4. **For each matching autonomy entry**: if `auto_eligible` is true AND the action matches, proceed without asking Keith. Otherwise, ask Keith for approval.
5. **After Keith approves/rejects an autonomy action, on Home only**: update the ledger entry's `approved`/`rejected` count through its designated writer. When graduation criteria are met, propose auto-eligibility for Keith's confirmation. On SJM leave the ledger unchanged and report that Home-only follow-up separately.

The ledger is the executable form of K2B's learnings. Active rules and learnings are advisory text. The ledger is the gate.

## Pre-Write Validation (Safety Check)

Before writing or editing ANY vault note, run this checklist. Stop and fix issues before saving.

1. **Frontmatter completeness**: All required fields for the note type are present (tags, date, type, origin, up)
2. **Inbox contract**: If destination is `review/`, review-action and review-notes are present
3. **Folder placement**: File path matches the convention for its type (see File Locations table above)
4. **Wikilink integrity**: Use `mcp__obsidian__search` or Glob to verify each `[[target]]` exists. Create stubs for missing targets.
5. **MOC link**: `up:` points to a valid MOC that exists at vault root
6. **No em dashes**: Scan content for em dashes (--) and replace with double hyphens
7. **Date format**: `date:` field is YYYY-MM-DD

This is the "careful" pattern: validate before acting, not after.

## Quality Checklist

After writing, confirm:
- [ ] Frontmatter is valid YAML with all required fields
- [ ] Inbox notes have review-action and review-notes
- [ ] `up` link points to the correct MOC
- [ ] All `[[wikilinks]]` use correct file names (glob-verified)
- [ ] `date` field uses YYYY-MM-DD format
- [ ] No em dashes in the content
- [ ] File is saved in the correct folder
- [ ] `origin` field is set (keith, k2b-extract, or k2b-generate)
- [ ] Relevant MOC updated on Home, or explicitly reported as a Home-only follow-up on SJM

## Usage Logging

After completing the main task, log this skill invocation:
```bash
python3 "$HOME/Projects/K2B/scripts/k2b-shared-append.py" usage --skill k2b-vault-writer --summary "wrote/updated vault note: FILENAME"
```
