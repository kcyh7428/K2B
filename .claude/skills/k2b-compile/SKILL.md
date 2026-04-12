---
name: k2b-compile
description: Compile raw sources into wiki knowledge pages -- reads raw captures, identifies affected wiki pages, shows Keith a summary, updates wiki on approval. The knowledge compilation engine that turns filing into digestion.
triggers:
  - /compile
  - compile this
  - digest this
  - process raw
scope: project
---

# k2b-compile -- Knowledge Compilation Engine

Reads raw source captures and compiles them into wiki knowledge pages. Based on Karpathy's LLM Wiki architecture: the LLM owns the wiki layer, Keith curates sources and approves updates.

## Trigger

- `/compile` -- compile unprocessed raw sources
- `/compile <path>` -- compile a specific raw source
- `/compile batch` -- batch-compile multiple sources with one approval
- `/compile deep` -- deep analysis seeking cross-vault connections
- Automatically triggered by capture skills after writing to raw/

## Core Concept

**Filing vs compiling:** Filing creates one note and stops. Compiling reads a source, extracts entities/concepts/insights, and ripples updates across 5-15 wiki pages. A single YouTube video might update 3 person pages, 2 project pages, create 1 concept page, and add entries to 2 indexes.

## Paths

- Raw sources: `~/Projects/K2B-Vault/raw/` (youtube/, meetings/, research/, tldrs/, daily/)
- Wiki output: `~/Projects/K2B-Vault/wiki/` (people/, projects/, work/, concepts/, insights/, reference/, content-pipeline/, context/)
- Master index: `~/Projects/K2B-Vault/wiki/index.md`
- Activity log: `~/Projects/K2B-Vault/wiki/log.md`
- Review queue: `~/Projects/K2B-Vault/review/`

## Commander/Worker Architecture

**MiniMax M2.7** does the heavy cognitive work (reading, analyzing, generating structured output).
**Opus** orchestrates (calls the script, presents summary, applies file changes, updates indexes).

This is the same pattern used by the observer loop. ~30-50x cheaper than running everything on Opus.

## Compile Flow

### 1. Call MiniMax Compile Worker

```bash
~/Projects/K2B/scripts/minimax-compile.sh "<raw-source-path>"
```

The script:
1. Reads the raw source file
2. Reads wiki/index.md + relevant subfolder indexes (people, projects, work, concepts, insights, reference)
3. Sends everything to MiniMax M2.7 with a structured extraction prompt
4. Returns JSON with: pages_to_update, pages_to_create, content_seeds, summary

### 2. Parse and Present Summary

Opus parses the JSON and presents Keith with a concise summary he can approve in ~2 seconds:

```
## Compile: [Source Title]

**Will update:**
- wiki/people/person_John-Smith.md -- add meeting context from 2026-04-08
- wiki/projects/project_talent-signals.md -- append API integration update

**Will create:**
- wiki/concepts/concept_agent-memory-systems.md -- new concept page
- wiki/content-pipeline/content_ai-memory-for-executives.md -- content seed

**Total: 2 updates, 2 creates across 4 wiki pages**

Proceed? [approve/skip/edit]
```

If Keith says approve (or yes/ok/go/y): proceed with all updates.
If Keith says skip: mark source as `compiled: skipped` and move on.
If Keith gives specific feedback: adjust plan and re-present.

### 4. Execute Updates

For each planned change:

Opus applies changes from the MiniMax JSON output:

**For each entry in `pages_to_create`:**
1. **BEFORE creating:** Check if the raw source frontmatter has `related:` links pointing to existing wiki pages that cover this entity. If yes, ENRICH the existing page instead of creating a new one. MiniMax's create suggestion is a hint, not a directive -- Opus must verify against existing wiki state.
2. **BEFORE creating:** Grep `wiki/` for the entity name. If a page already exists, update it instead.
3. Only if steps 1-2 confirm no existing page: write the file using frontmatter + content from JSON
4. Verify wikilinks point to existing pages (glob check)
5. Create stubs for missing link targets

**For each entry in `pages_to_update`:**
1. Read the current wiki page
2. Find the section specified in the JSON
3. Append the content under that section using Edit tool
4. If section doesn't exist, create it before the last section

**Rules for updates:**
- NEVER overwrite existing content
- ALWAYS append under dated headers
- Preserve existing wikilinks and sections
- If new info contradicts existing info: flag in the update with `> [!warning] Potential conflict` and add to review/ queue
- Minimum 2 wikilinks per new page (soft target, not hard enforcement)

### 5. Update Indexes + Log (mandatory checklist -- do in this exact order)

After all wiki changes, execute this checklist top to bottom. Do NOT report "done" until all 5 items are checked off. The raw index is FIRST because it is the most-skipped step (2 failures in 4 days, see L-2026-04-12-002).

- [ ] **5a. Raw subfolder index FIRST:** Update `raw/<type>/index.md` -- mark the source row as compiled with today's date. This is the step that gets forgotten. Do it before anything else.
- [ ] **5b. Mark source compiled:** Add `compiled: true` and `compiled-date: YYYY-MM-DD` to raw source frontmatter.
- [ ] **5c. Wiki subfolder indexes:** Add/update rows in each affected `wiki/*/index.md`.
- [ ] **5d. Master index:** Update `wiki/index.md` entry counts if any new pages were created.
- [ ] **5e. Append to wiki/log.md:**

```markdown
## [YYYY-MM-DD HH:MM] compile | [Source Title]
- Source: raw/[type]/[filename]
- Updated: [list of wiki pages updated]
- Created: [list of wiki pages created]
- Indexes: [list of index.md files updated]
```

**Self-check before reporting done:** Mentally walk through 5a-5e. If you cannot confirm each one was executed, go back and fix it now.

## Compile Modes

### summary (default)
Shows plan, waits for Keith's approval. Best for interactive sessions.

### batch
Groups multiple uncompiled sources:
1. Read all raw files where `compiled:` is missing or false
2. Show combined summary: "5 sources, 12 wiki updates, 4 new pages"
3. One approval for all
4. Process sequentially

### deep
Manual trigger for deeper analysis:
1. Read the source AND all related wiki pages
2. Look for non-obvious connections across domains
3. Suggest new concept pages that bridge topics
4. Takes longer but finds richer cross-links

## Entity Handling

### People
- **Match by name:** Search wiki/people/index.md for existing entries
- **Disambiguation:** If multiple people share a name, check organization/role context
- **Stub creation:** New person -> create stub with name, organization, role if known, and `> Stub -- to be populated`

### Projects
- **Match by slug:** Search wiki/projects/index.md
- **Domain tagging:** Tag with domain (sjm, talentsignals, agency-at-scale, signhub, personal, k2b)
- **Stub creation:** New project -> create stub with name, domain, status: simmering

### Concepts
- **Match by topic:** Search wiki/concepts/index.md
- **Threshold:** Only create concept pages for topics mentioned in 2+ sources or with substantial depth in 1 source
- **Merge not duplicate:** If a concept page exists, enrich it -- don't create a second one

## Idempotency

Running compile on the same source twice must not create duplicates:
1. Check raw source frontmatter for `compiled: true`
2. If already compiled: report "Already compiled on YYYY-MM-DD" and skip
3. If Keith wants to re-compile: use `/compile deep <path>` which re-reads and enriches

## Error Handling

- If wiki/index.md is missing or corrupted: rebuild from folder contents before proceeding
- If a wiki page to update doesn't exist: create it (treat as new page)
- If raw source has no meaningful content: mark `compiled: empty` and skip
- If Keith rejects the compile plan: mark `compiled: skipped` in frontmatter

## Integration with Capture Skills

Capture skills trigger compile after writing to raw/:
- k2b-youtube-capture -> writes to raw/youtube/ -> triggers compile
- k2b-meeting-processor -> writes to raw/meetings/ -> triggers compile
- k2b-research -> writes to raw/research/ -> triggers compile
- k2b-tldr -> writes to raw/tldrs/ -> triggers compile
- k2b-daily-capture -> writes to raw/daily/ -> triggers compile

The trigger pattern: after the capture skill logs its raw source, it calls compile in summary mode. Keith approves the compilation plan inline.

## Frontmatter for Raw Sources (Post-Compile)

```yaml
compiled: true | false | skipped | empty
compiled-date: YYYY-MM-DD
compiled-pages: ["wiki/people/person_X.md", "wiki/concepts/concept_Y.md"]
```

## Frontmatter for Wiki Pages (Compiled)

```yaml
compiled-from: ["[[raw-source-1]]", "[[raw-source-2]]"]
```

This tracks provenance -- which raw sources contributed to this wiki page.

## Content Pipeline Integration

When a raw source contains content-worthy material:
1. Create a content idea in wiki/content-pipeline/ (not review/)
2. Set `origin: k2b-extract` (derived from Keith's source material)
3. If the content idea needs Keith's judgment (novel angle, risky take): also add to review/
4. Standard content ideas auto-promote to wiki/content-pipeline/

## Usage Logging

After completing compilation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-compile\t$(echo $RANDOM | md5sum | head -c 8)\tcompiled: SOURCE_FILE -> N wiki pages" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```
