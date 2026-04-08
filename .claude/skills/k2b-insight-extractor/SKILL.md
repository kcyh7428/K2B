---
name: k2b-insight-extractor
description: Find patterns and surface content ideas -- searches the vault to synthesize connections and non-obvious patterns. Use when Keith says /insight, /content, "find patterns", "what should I write about", "review my notes", or asks about themes and trends across his work.
---

# K2B Insight Extractor

## Vault Path

`~/Projects/K2B-Vault`

## Vault Query Tools

- **Dataview DQL** (structured frontmatter queries): `~/Projects/K2B/scripts/vault-query.sh dql '<TABLE query>'`
- **Full-text search**: `mcp__obsidian__search` MCP tool or `vault-query.sh search "<term>"`
- **Read file**: `mcp__obsidian__get_file_contents` or Read tool
- **List files**: `mcp__obsidian__list_files_in_dir`

Prefer `mcp__obsidian__search` over Grep for vault-wide content search. Prefer DQL over Glob+Read+Filter for frontmatter queries.

## Workflow

### For /insight [topic]:
1. Search the vault for notes related to [topic]:
   - Use `mcp__obsidian__search` to find notes mentioning the topic across the vault (returns ranked results with context)
   - Look in Meetings, Insights, Daily notes, Projects, Decisions
2. Read the relevant notes (use `mcp__obsidian__get_file_contents` or Read tool)
3. Synthesize:
   - What patterns appear across multiple notes?
   - What has changed over time?
   - What connections exist between different meetings/projects?
   - What is Keith repeatedly encountering?
4. Present findings as a brief synthesis (not a list of search results)
5. Offer to save as an insight note if the synthesis is valuable

### For /content (weekly content review):
1. Query recent notes with DQL:
   ```bash
   ~/Projects/K2B/scripts/vault-query.sh dql 'TABLE type, tags, date FROM "Daily" WHERE date >= date(today) - dur(7 days)'
   ~/Projects/K2B/scripts/vault-query.sh dql 'TABLE type, tags, date FROM "Notes" WHERE date >= date(today) - dur(7 days)'
   ```
2. Read the matching daily notes and meeting notes
3. Read any new insight notes
4. Identify:
   - Recurring themes (3+ mentions = pattern)
   - Interesting single events with teaching potential
   - Contrasts between AI approach and traditional approach
   - Metrics or results worth sharing
5. Generate 3-5 content ideas with:
   - Working title
   - Hook / angle
   - Source notes (linked)
   - Suggested platform (LinkedIn short post, LinkedIn long-form, YouTube)
6. Offer to create content idea notes for any Keith wants to pursue (saved to `review/` with `origin: k2b-generate` -- the only skill output that goes to review queue)

## Insight Categories
When classifying insights, use these lenses:
- **Process**: How work gets done, workflow patterns
- **People**: Team dynamics, stakeholder management, leadership
- **Technology**: AI tools, automation, systems
- **Strategy**: Talent acquisition strategy, business direction
- **Culture**: Organizational culture, change management
- **Content**: Meta-observations about what resonates with audiences

## Origin Rules & Auto-Promote

- `/insight` notes get `origin: k2b-extract` -- saved directly to `wiki/insights/` (auto-promote, bypasses review queue)
- `/content` idea notes get `origin: k2b-generate` -- saved to `review/` (the ONLY type of content that goes to review queue)
- Only when Keith explicitly adopts an idea (says "promote this", "I like this one", "let's do this") should it be moved to `wiki/content-pipeline/` with `origin: keith`

## Post-Write Contract

After saving any note, run the vault-writer post-write pass:

**For /insight notes (wiki/insights/):**
1. Update `wiki/insights/index.md` with new row
2. Cross-link: update person/project pages mentioned in the insight
3. Append to `wiki/log.md`

**For /content idea notes (review/):**
1. No index update needed (review/ has no index)
2. Verify `review-action:` and `review-notes: ""` are present (review queue write contract)
3. Append to `wiki/log.md`

## Frontmatter Templates

### /insight notes (saved to wiki/insights/)
```yaml
---
tags: [insight, {domain-tags}]
date: YYYY-MM-DD
type: insight
origin: k2b-extract
domain: {sjm|talentsignals|agency-at-scale|technical|career}
content-potential: {true|false}
up: "[[relevant MOC]]"
---
```

### /content idea notes (saved to review/) -- MANDATORY review queue write contract
```yaml
---
tags: [content-idea, {topic-tags}]
date: YYYY-MM-DD
type: content-idea
origin: k2b-generate
platform: [linkedin]
status: idea
source: "[[source note]]"
up: "[[MOC_Content-Pipeline]]"
review-action:
review-notes: ""
---
```

Before saving any note to review/, verify: review-action and review-notes are present. All review queue notes require these for Keith's Obsidian review workflow.

## File Conventions

- Insight notes: `~/Projects/K2B-Vault/wiki/insights/insight_topic.md`
- Content ideas from /content: `~/Projects/K2B-Vault/review/content_short-slug.md` (with `origin: k2b-generate`)
- Promoted content ideas: `~/Projects/K2B-Vault/wiki/content-pipeline/content_short-slug.md` (with `origin: keith`)

## Cross-Linking

When creating insight or content idea notes, always add `[[wiki links]]`:

1. **Source notes**: Link to the daily notes, meeting notes, or other notes that contributed to this insight. E.g., `Observed across [[2026-03-20_Hiring-Sync]] and [[2026-03-22]]`.
2. **People**: If specific people are relevant to the insight, link as `[[person_Firstname-Lastname]]`.
3. **Projects**: Link related projects as `[[project_name]]`.
4. **Content ideas from insights**: When `/content` creates a content idea note, link it back to the source insight notes in the Source Experience section.
5. **Related insights**: If a new insight connects to an existing one, cross-link them.
6. Glob the vault before linking to confirm targets exist.

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-insight-extractor\t$(echo $RANDOM | md5sum | head -c 8)\textracted insights on TOPIC" >> ~/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
```

## Notes
- No em dashes, no AI cliches, no sycophancy
- Synthesize, don't just list search results
- Always flag content potential
- Use k2b-vault-writer conventions for all note creation and cross-linking
- After creating an insight note, consider whether the insight connects to raw sources that should trigger a compile pass for deeper integration.
- When /insight or ad-hoc analysis produces a substantial answer (3+ paragraphs with cross-references), offer to file it as a wiki page in wiki/insights/ or wiki/concepts/. This prevents good analysis from vanishing into chat history.
