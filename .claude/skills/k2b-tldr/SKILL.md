---
name: k2b-tldr
description: This skill should be used when Keith uses the /tldr command to capture a summary of the current conversation. It extracts key decisions, action items, and insights from the conversation and saves them to the appropriate location in the K2B Obsidian vault. Use this whenever Keith says "tldr", "summarize this", "capture this", "save this conversation", or wants to extract the key points from a session.
---

# K2B TLDR Capture

## Vault Path

`/Users/keithmbpm2/Projects/K2B-Vault`

## Workflow

1. Review the current conversation
2. Extract:
   - **Summary**: 3-5 bullet points of what was discussed/accomplished
   - **Decisions**: Any choices made
   - **Action Items**: Next steps with context
   - **Insights**: New understandings or connections discovered
   - **Content Seeds**: Anything worth turning into content
3. Always save to `Inbox/` with filename format `YYYY-MM-DD_tldr-topic-slug.md`
4. Save with proper frontmatter and linking
5. **Update related project notes**: If the conversation involved progress on a project, use the vault-writer update workflow to:
   - Append a dated entry to the project note's `## Updates` section
   - Update `## Current Status` if the status meaningfully changed
   - Check off any completed milestones in `## Key Milestones`
   - Add new `[[wikilinks]]` to `## Related Notes` if new notes were created
6. Confirm what was saved and where

## Frontmatter Format

```yaml
---
tags: [tldr, {context-tags}]
date: YYYY-MM-DD
type: tldr
source: claude-code-session
---
```

## Cross-Linking

When saving a TLDR note, always add `[[wiki links]]`:

1. **People**: Link any people mentioned as `[[person_Firstname-Lastname]]`.
2. **Projects**: Link any projects discussed as `[[project_name]]`.
3. **Meetings**: If the conversation was about a specific meeting, link as `[[YYYY-MM-DD_Meeting-Topic]]`.
4. **Source notes**: If referencing existing vault notes, link them directly.
5. Glob the vault before linking to confirm targets exist.

## Notes
- Be ruthlessly concise. TLDR means TLDR.
- Action items should be copy-pasteable as tasks.
- Always link to related existing notes when possible.
- No em dashes, no AI cliches, no sycophancy
