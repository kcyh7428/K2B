---
name: k2b-insight-extractor
description: This skill should be used when Keith asks to find patterns, surface insights, review recent notes for themes, or uses the /insight or /content commands. It searches across the K2B Obsidian vault to synthesize connections and surface non-obvious patterns. Use this whenever Keith mentions "patterns", "insights", "themes", "what have I been", "content ideas", "what should I write about", "review my notes", or asks about trends across his work.
---

# K2B Insight Extractor

## Vault Path

`/Users/keithmbpm2/Projects/K2B-Vault`

## Workflow

### For /insight [topic]:
1. Search the vault for notes related to [topic]:
   - Search file contents using Grep tool across the vault
   - Look in Meetings, Insights, Daily notes, Projects, Decisions
2. Read the relevant notes
3. Synthesize:
   - What patterns appear across multiple notes?
   - What has changed over time?
   - What connections exist between different meetings/projects?
   - What is Keith repeatedly encountering?
4. Present findings as a brief synthesis (not a list of search results)
5. Offer to save as an insight note if the synthesis is valuable

### For /content (weekly content review):
1. Read all daily notes from the past 7 days
2. Read any new meeting notes from the past 7 days
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
6. Offer to create content idea notes for any Keith wants to pursue

## Insight Categories
When classifying insights, use these lenses:
- **Process**: How work gets done, workflow patterns
- **People**: Team dynamics, stakeholder management, leadership
- **Technology**: AI tools, automation, systems
- **Strategy**: Talent acquisition strategy, business direction
- **Culture**: Organizational culture, change management
- **Content**: Meta-observations about what resonates with audiences

## File Conventions

- Insight notes: `/Users/keithmbpm2/Projects/K2B-Vault/Notes/insight_topic.md`
- Content ideas: `/Users/keithmbpm2/Projects/K2B-Vault/Notes/Ideas/idea_short-slug.md`

## Cross-Linking

When creating insight or content idea notes, always add `[[wiki links]]`:

1. **Source notes**: Link to the daily notes, meeting notes, or other notes that contributed to this insight. E.g., `Observed across [[2026-03-20_Hiring-Sync]] and [[2026-03-22]]`.
2. **People**: If specific people are relevant to the insight, link as `[[person_Firstname-Lastname]]`.
3. **Projects**: Link related projects as `[[project_name]]`.
4. **Content ideas from insights**: When `/content` creates a content idea note, link it back to the source insight notes in the Source Experience section.
5. **Related insights**: If a new insight connects to an existing one, cross-link them.
6. Glob the vault before linking to confirm targets exist.

## Notes
- No em dashes, no AI cliches, no sycophancy
- Synthesize, don't just list search results
- Always flag content potential
