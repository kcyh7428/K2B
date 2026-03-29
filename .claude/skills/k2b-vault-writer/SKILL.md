---
name: k2b-vault-writer
description: Create or update notes in the K2B Obsidian vault with correct frontmatter, cross-links, and MOC integration. Use when creating any new note in the vault, or when updating an existing note after progress is made on a project, person, or decision. Also use when another skill (k2b-daily-capture, k2b-meeting-processor, k2b-tldr, k2b-insight-extractor) needs to create or update a linked note.
---

# K2B Vault Writer

Create notes in the K2B Obsidian vault at `~/Projects/K2B-Vault/` with correct structure, frontmatter, cross-links, and MOC integration.

## System Reference

Before writing any note, review the lifecycle rules in [[context_k2b-note-lifecycle]] (`Notes/Context/context_k2b-note-lifecycle.md`). That note is the single source of truth for origin tagging, review properties, promote destinations, and the content pipeline.

## Before Writing Any Note

1. **Read the appropriate template** from `Templates/` to get the base structure
2. **Glob the vault** to check if target note already exists (avoid duplicates)
3. **Glob for link targets** -- before writing `[[person_Firstname-Lastname]]`, confirm the file exists. If not, note it as a stub to create later.

## Updating Existing Notes

When progress is made on a project, person interaction occurs, or a decision evolves, update the existing note rather than creating a new one.

### When to Update
- After `/tldr` captures progress related to a project
- After `/meeting` processes a meeting tied to a project or person
- After `/daily` captures completed work items tied to a project
- Any time K2B works on implementation of a project and makes meaningful progress
- When new relationships or links are discovered between existing notes

### Update Workflow
1. **Glob** to find the target note (e.g., `Notes/Projects/project_*.md`)
2. **Read** the current content
3. **Determine which sections need updates:**
   - `## Current Status` -- rewrite the blockquote to reflect current state
   - `## Key Milestones` -- check off completed items (`- [x]`), add new milestones if needed
   - `## Updates` -- append a new dated entry (`### YYYY-MM-DD`) with bullet points of what changed
   - `## Related Notes` -- add wikilinks to any new related notes (meetings, decisions, insights)
   - For person notes: append new interactions under `## Key Interactions`
4. **Use Edit tool** (not Write) to surgically update sections without touching unrelated content
5. **Verify** wikilinks in new content point to existing notes (glob first)

### Update Rules
- Never overwrite the entire file -- only edit changed sections
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
| Decision | `YYYY-MM-DD_decision-topic.md` | `2026-03-19_hiring-freeze-communication.md` |
| Content Idea | `content_short-slug.md` | `content_corporate-ai-restrictions.md` |
| Meeting | `YYYY-MM-DD_Meeting-Topic.md` | `2026-03-22_Hiring-Sync.md` |
| MOC | `MOC_Topic-Name.md` | `MOC_SJM-Work.md` |
| Daily | `YYYY-MM-DD.md` | `2026-03-22.md` |
| Business | `entityname_overview.md` | `talentsignals_overview.md` |
| K2B Feature | `feature_short-slug.md` | `feature_content-feed-system.md` |
| Work (SJM) | `work_lowercase-slug.md` | `work_the-eight-chef-search.md` |

## File Locations

| Note Type | Folder |
|-----------|--------|
| Project | `Notes/Projects/` |
| Person | `Notes/People/` |
| Content Idea (unadopted) | `Inbox/` |
| Content Idea (adopted) | `Notes/Content-Ideas/` |
| Insight | `Notes/Insights/` |
| Decision | `Notes/` (flat) |
| Meeting | `Notes/` (flat) |
| Reference | `Notes/` (flat) |
| Context | `Notes/Context/` |
| Business overview | `Notes/Context/` |
| K2B Feature | `Notes/Features/` |
| Work (SJM) | `Notes/Work/` |
| MOC | Vault root |
| Daily | `Daily/` |
| TLDR | `Inbox/` |
| Generated images | `Assets/images/` |
| Generated audio | `Assets/audio/` |
| Generated video | `Assets/video/` |
| Home | Vault root |

## Frontmatter Conventions

### All notes get:
```yaml
---
tags: [type-tag, domain-tags...]
date: YYYY-MM-DD
type: project | work | person | insight | decision | content-idea | moc | meeting | daily | reference | k2b-feature
origin: keith | k2b-extract | k2b-generate
up: "[[relevant MOC or Home]]"
---
```

### Origin field guide:
- `keith` -- Keith's direct input, words, ideas, decisions
- `k2b-extract` -- K2B extracted/summarized from Keith's input (meeting summaries, video takeaways from Keith's reactions)
- `k2b-generate` -- K2B generated independently (connections, patterns, suggestions, recommendations)
- When a note mixes both, use the primary origin and distinguish sections with callouts: `> [!quote] Keith's input` and `> [!robot] K2B analysis`

### Inbox review properties
All notes saved to Inbox/ must include these properties for Keith's Obsidian review:
- `review-action:` -- empty until Keith decides (promote, archive, delete, revise)
- `review-notes: ""` -- Keith's feedback/comments

### Content Pipeline
- `/content` suggestions land in `Inbox/` with `origin: k2b-generate`
- Only when Keith says "promote this" does it move to `Notes/Content-Ideas/` with `origin: keith`
- `Notes/Content-Ideas/` is Keith's curated list of adopted content ideas

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
6. **Bidirectional**: When creating a note that references another note, add a backlink in the referenced note's Related Notes section if practical
7. **Stub creation**: If a link target doesn't exist, create a minimal stub note with just frontmatter and a `> Stub -- to be populated` callout

## MOC Integration

After creating any note, add its link to the relevant MOC under the appropriate section header. The five MOCs are:

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

## Inbox Write Contract (MANDATORY)

**Every note saved to `Inbox/` MUST have these frontmatter fields. No exceptions.**

```yaml
review-action:       # empty string -- Keith fills this in Obsidian
review-notes: ""     # empty string -- Keith fills this in Obsidian
```

This applies to ALL skills that write to Inbox: k2b-tldr, k2b-research, k2b-youtube-capture, k2b-insight-extractor (for /content), and any future skill. If a note lands in Inbox/ without these fields, Keith's review workflow breaks -- he can't triage it in Obsidian.

**Before writing any Inbox note, verify:**
1. `review-action:` is present in frontmatter (empty value is correct)
2. `review-notes: ""` is present in frontmatter
3. File path starts with `Inbox/`

If you're updating an existing Inbox note, preserve any `review-action` or `review-notes` values Keith has already set.

## Pre-Write Validation (Safety Check)

Before writing or editing ANY vault note, run this checklist. Stop and fix issues before saving.

1. **Frontmatter completeness**: All required fields for the note type are present (tags, date, type, origin, up)
2. **Inbox contract**: If destination is `Inbox/`, review-action and review-notes are present
3. **Folder placement**: File path matches the convention for its type (see File Locations table above)
4. **Wikilink integrity**: Glob to verify each `[[target]]` exists. Create stubs for missing targets.
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
- [ ] Relevant MOC has been updated with a link to this note

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-vault-writer\t$(echo $RANDOM | md5sum | head -c 8)\twrote/updated vault note: FILENAME" >> ~/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
```
